from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from .config import DetectionConfig
from .geometry import (
    KEYPOINT_INDEX,
    angle_degrees,
    distance,
    mean_or_none,
    midpoint,
    normalized_distance,
    point,
    segment_angle_from_horizontal,
)
from .pose_runtime import PersonPose
from .protocol import now_ms


@dataclass(slots=True)
class ExerciseState:
    armed: bool = False
    rep_count: int = 0
    last_status: str | None = None
    last_emit_ms: int = 0
    squat_stage: int = 0
    depth_reached: bool = False
    bottom_reached: bool = False
    baseline_hip_y: float | None = None
    baseline_knee_y: float | None = None
    baseline_ankle_span: float | None = None
    standing_frames: int = 0
    idle_frames: int = 0
    raise_prev_abduction: float | None = None
    raise_prev_opposite_lean: float | None = None
    raise_peak_abduction: float = 0.0
    raise_baseline_head_shoulder_dist: float | None = None
    raise_invalidated: bool = False
    raise_peak_reached: bool = False


# ── Zone-based alert state machine ─────────────────────────────────
# Zone 1 (idle):  neutral standing pose → suppress all alerts
# Zone 2 (grace): just left idle → 2 s grace, only valid events pass
# Zone 3 (alert): grace expired without valid exercise → alerts fire
ZONE_IDLE = 1
ZONE_GRACE = 2
ZONE_ALERT = 3

GRACE_PERIOD_MS = 2000        # 2 seconds after leaving idle
POST_VALID_RETURN_MS = 3000   # 3 seconds after a valid exercise to return to idle
ALERT_REPEAT_MS = 3000        # repeat alert interval in zone 3


@dataclass(slots=True)
class ZoneState:
    """Global zone tracking — shared across all exercises."""
    zone: int = ZONE_IDLE
    zone_entered_ms: int = 0
    last_valid_ms: int = 0          # timestamp of last valid exercise
    last_alert_ms: int = 0          # timestamp of last zone-3 alert
    returning_to_idle: bool = False  # True during the 3-s post-valid window
    idle_frames: int = 0


@dataclass(slots=True)
class ExerciseCalibration:
    """Fixed reference captured once during explicit calibration.

    Unlike the EMA baselines in ExerciseState, these values do NOT drift
    frame-to-frame.  They are set by `calibrate_exercise()` and cleared
    by `remove_calibration()`.
    """
    hip_y: float
    knee_y: float
    torso_len: float
    timestamp_ms: int = 0


@dataclass(slots=True)
class ExerciseSignal:
    exercise: str
    status: str
    message: str
    angle: float | None
    confidence: float
    phase: str
    rep_count: int
    metrics: dict[str, float] = field(default_factory=dict)


SQUAT_STAGE_LABELS = {
    0: "standing",
    1: "quarter",
    2: "half",
    3: "bottom",
}

SQUAT_FRONT_VIEW_TOLERANCE = 0.18
SQUAT_CENTER_DRIFT_TOLERANCE = 0.2
SQUAT_MIN_HIP_WIDTH_RATIO = 0.45
SQUAT_MIN_ANKLE_WIDTH_RATIO = 0.55
SQUAT_EXCESSIVE_FORWARD_LEAN = 55.0
JUMP_PEAK_HIP_RISE = 0.16
JUMP_PEAK_KNEE_RISE = 0.12
JUMP_ACTION_HIP_RISE = 0.08
JUMP_ACTION_KNEE_RISE = 0.05
JUMP_RESET_HIP_RISE = 0.07
JUMP_RESET_KNEE_RISE = 0.05
JUMP_STANDING_KNEE_FLEX_MAX = 20.0
JUMP_STANDING_TRUNK_MIN = 75.0
# Brief lockout after a squat to ignore the natural hip/knee overshoot
# during squat recovery, which otherwise satisfies the jump rise rule.
SQUAT_RECOVERY_COOLDOWN_MS = 800
BICEP_CURL_TOP_MAX_ANGLE = 95.0
BICEP_CURL_RESET_MIN_ANGLE = 145.0
BICEP_CURL_ELBOW_TUCK_MAX = 0.3
BICEP_CURL_WRIST_TRIGGER_HEIGHT = -0.35
BICEP_CURL_WRIST_RESET_HEIGHT = -0.6
LATERAL_RAISE_START_MAX = 15.0
LATERAL_RAISE_VALID_MIN = 75.0
LATERAL_RAISE_VALID_MAX = 100.0
LATERAL_RAISE_ELBOW_MIN = 130.0
LATERAL_RAISE_TORSO_LEAN_MAX = 10.0
LATERAL_RAISE_SHRUG_DROP_RATIO = 0.15

SQUAT_TABLE_MEDIUM = {
    "quarter": {"knee": 45.0, "knee_sd": 0.0, "hip": 55.0, "hip_sd": 6.0, "trunk": 68.0, "trunk_sd": 4.0},
    "half": {"knee": 90.0, "knee_sd": 0.0, "hip": 98.0, "hip_sd": 6.0, "trunk": 60.0, "trunk_sd": 6.0},
    "bottom": {"knee": 102.0, "knee_sd": 7.0, "hip": 109.0, "hip_sd": 8.0, "trunk": 61.0, "trunk_sd": 6.0},
}


class ExerciseMapper:
    def __init__(self, config: DetectionConfig):
        self.config = config
        self.state = {
            name: ExerciseState()
            for name in config.exercise_keys
        }
        # Per-exercise calibration store.  Keys are exercise names;
        # values are ExerciseCalibration snapshots captured by the user.
        self._calibrations: dict[str, ExerciseCalibration] = {}
        # Global zone state for the 3-zone alert system
        self._zone = ZoneState()
        # Timestamp of the last frame where a squat was in progress; used
        # to suppress jump detection during squat recovery.
        self._last_squat_active_ms: int = 0

    # ------------------------------------------------------------------
    # Calibration API
    # ------------------------------------------------------------------

    def calibrate_exercise(
        self,
        exercise: str,
        pose: PersonPose,
        timestamp_ms: int = 0,
    ) -> dict[str, Any]:
        """Capture a fixed standing-position reference for *exercise*.

        Returns a status dict.  Fails if the exercise is already
        calibrated (must call ``remove_calibration`` first) or if the
        required joints are not visible.
        """
        if exercise in self._calibrations:
            return {
                "ok": False,
                "exercise": exercise,
                "message": (
                    f"'{exercise}' is already calibrated.  "
                    "Remove the existing calibration before re-calibrating."
                ),
            }

        required = [
            KEYPOINT_INDEX["left_hip"],
            KEYPOINT_INDEX["right_hip"],
            KEYPOINT_INDEX["left_knee"],
            KEYPOINT_INDEX["right_knee"],
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
        ]
        if not self._has_confidence(pose, required):
            return {
                "ok": False,
                "exercise": exercise,
                "message": "Required joints are not visible — stand in frame.",
            }

        left_hip = point(pose.keypoints, KEYPOINT_INDEX["left_hip"])
        right_hip = point(pose.keypoints, KEYPOINT_INDEX["right_hip"])
        left_knee = point(pose.keypoints, KEYPOINT_INDEX["left_knee"])
        right_knee = point(pose.keypoints, KEYPOINT_INDEX["right_knee"])
        left_shoulder = point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"])
        right_shoulder = point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"])

        hip_mid = midpoint(left_hip, right_hip)
        knee_mid = midpoint(left_knee, right_knee)
        torso_len_val = mean_or_none([
            distance(left_shoulder, left_hip),
            distance(right_shoulder, right_hip),
        ])
        if torso_len_val is None or torso_len_val <= 1e-6:
            return {
                "ok": False,
                "exercise": exercise,
                "message": "Could not compute torso length — check pose.",
            }

        cal = ExerciseCalibration(
            hip_y=float(hip_mid[1]),
            knee_y=float(knee_mid[1]),
            torso_len=torso_len_val,
            timestamp_ms=timestamp_ms,
        )
        self._calibrations[exercise] = cal

        # Also seed the EMA baselines so the first frame after calibration
        # doesn't produce a spike.
        if exercise in self.state:
            self.state[exercise].baseline_hip_y = cal.hip_y
            self.state[exercise].baseline_knee_y = cal.knee_y

        return {
            "ok": True,
            "exercise": exercise,
            "message": "Calibration captured.",
            "hipY": round(cal.hip_y, 2),
            "kneeY": round(cal.knee_y, 2),
            "torsoLen": round(cal.torso_len, 2),
        }

    def remove_calibration(self, exercise: str) -> dict[str, Any]:
        """Remove calibration for *exercise*, allowing re-calibration."""
        if exercise not in self._calibrations:
            return {
                "ok": False,
                "exercise": exercise,
                "message": f"'{exercise}' has no calibration to remove.",
            }
        del self._calibrations[exercise]
        # Reset the EMA baselines so the detector re-learns from scratch.
        if exercise in self.state:
            self.state[exercise].baseline_hip_y = None
            self.state[exercise].baseline_knee_y = None
        return {
            "ok": True,
            "exercise": exercise,
            "message": "Calibration removed.  You may re-calibrate.",
        }

    def is_calibrated(self, exercise: str) -> bool:
        return exercise in self._calibrations

    def calibration_status(self) -> dict[str, bool]:
        """Return calibration state for every known exercise."""
        return {
            name: name in self._calibrations
            for name in self.config.exercise_keys
        }

    def summarize_pose(self, pose: PersonPose | None) -> dict[str, Any]:
        return {
            "idle": self._summarize_idle(pose),
            "squats": self._summarize_squat(pose),
            "jumpingJacks": self._summarize_jumping_jack(pose),
            "rightDumbbellRaise": self._summarize_raise(pose, side="right"),
            "leftDumbbellRaise": self._summarize_raise(pose, side="left"),
            "rightLateralRaise": self._summarize_lateral_raise(pose, side="right"),
            "leftLateralRaise": self._summarize_lateral_raise(pose, side="left"),
        }

    def evaluate(
        self,
        pose: PersonPose | None,
        inference_ms: float,
        timestamp_ms: int | None = None,
        active_exercises: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        now = now_ms() if timestamp_ms is None else timestamp_ms
        if pose is None or pose.mean_confidence < self.config.min_person_confidence:
            return []

        enabled = (
            set(active_exercises)
            if active_exercises is not None
            else set(self.config.exercise_keys)
        )
        enabled = enabled.intersection(self.config.exercise_keys)
        if not enabled:
            return []

        # ── Zone state machine ──────────────────────────────────────
        z = self._zone
        idle = self._is_idle_pose(pose)

        # Count consecutive idle frames (need several to confirm idle)
        if idle:
            z.idle_frames = min(z.idle_frames + 1, 30)
        else:
            z.idle_frames = 0

        confirmed_idle = z.idle_frames >= 5

        # ── Zone transitions ────────────────────────────────────────
        if z.zone == ZONE_IDLE:
            if confirmed_idle:
                # Stay idle — no alerts
                return []
            else:
                # Just left idle → enter grace period
                z.zone = ZONE_GRACE
                z.zone_entered_ms = now
                z.returning_to_idle = False

        elif z.zone == ZONE_GRACE:
            if confirmed_idle:
                # Returned to idle during grace
                z.zone = ZONE_IDLE
                z.zone_entered_ms = now
                return []
            # Check if grace period expired
            if not z.returning_to_idle and (now - z.zone_entered_ms) >= GRACE_PERIOD_MS:
                # Grace expired — move to alert zone
                z.zone = ZONE_ALERT
                z.zone_entered_ms = now
                z.last_alert_ms = 0  # allow immediate first alert

        elif z.zone == ZONE_ALERT:
            if confirmed_idle:
                # Returned to idle
                z.zone = ZONE_IDLE
                z.zone_entered_ms = now
                return []

        # ── Detect exercises ────────────────────────────────────────
        signals: list[ExerciseSignal | None] = []
        squat_signal = self._detect_squat(pose) if "squats" in enabled else None
        if squat_signal is not None:
            signals.append(squat_signal)

        squat_state = self.state["squats"]
        squat_in_progress = (
            "squats" in enabled
            and (
                squat_signal is not None
                or squat_state.armed
                or squat_state.squat_stage >= 1
                or squat_state.depth_reached
                or squat_state.bottom_reached
            )
        )
        if squat_in_progress:
            self._last_squat_active_ms = now

        squat_recovery_active = (
            "squats" in enabled
            and (now - self._last_squat_active_ms) < SQUAT_RECOVERY_COOLDOWN_MS
        )

        if not squat_in_progress:
            if "jumpingJacks" in enabled and not squat_recovery_active:
                signals.append(self._detect_jumping_jack(pose))
            if "rightDumbbellRaise" in enabled:
                signals.append(self._detect_raise(pose, side="right"))
            if "leftDumbbellRaise" in enabled:
                signals.append(self._detect_raise(pose, side="left"))
            if "rightLateralRaise" in enabled:
                signals.append(self._detect_lateral_raise(pose, side="right"))
            if "leftLateralRaise" in enabled:
                signals.append(self._detect_lateral_raise(pose, side="left"))

        # ── Filter signals through zone rules ───────────────────────
        events: list[dict[str, Any]] = []
        has_valid = False

        for signal in signals:
            if signal is None:
                continue

            if signal.status == "valid":
                has_valid = True
                # Valid exercises always pass through
                event = self._build_event(signal, inference_ms, now)
                if event is not None:
                    events.append(event)
            elif signal.status == "invalid":
                if z.zone == ZONE_GRACE:
                    # Suppress invalid alerts during grace period
                    continue
                elif z.zone == ZONE_ALERT:
                    # Only emit alerts at the repeat interval
                    if (now - z.last_alert_ms) >= ALERT_REPEAT_MS:
                        event = self._build_event(signal, inference_ms, now)
                        if event is not None:
                            events.append(event)
                            z.last_alert_ms = now
                else:
                    # ZONE_IDLE — shouldn't reach here, but just in case
                    continue
            else:
                # Other statuses pass through normally
                event = self._build_event(signal, inference_ms, now)
                if event is not None:
                    events.append(event)

        # ── Post-valid: re-enter grace for return to idle ───────────
        if has_valid:
            z.last_valid_ms = now
            z.zone = ZONE_GRACE
            z.zone_entered_ms = now
            z.returning_to_idle = True

        # If in the post-valid return window and time expired, go to alert
        if z.returning_to_idle and z.zone == ZONE_GRACE:
            if (now - z.zone_entered_ms) >= POST_VALID_RETURN_MS and not confirmed_idle:
                z.zone = ZONE_ALERT
                z.zone_entered_ms = now
                z.returning_to_idle = False
                z.last_alert_ms = 0

        return events

    def _build_event(
        self,
        signal: ExerciseSignal,
        inference_ms: float,
        now: int,
    ) -> dict[str, Any] | None:
        state = self.state[signal.exercise]
        should_emit = False

        if signal.status == "valid":
            should_emit = True
        elif signal.status != state.last_status:
            should_emit = True
        elif now - state.last_emit_ms >= self.config.event_cooldown_ms * 2:
            should_emit = True

        if not should_emit:
            return None

        state.last_status = signal.status
        state.last_emit_ms = now

        return {
            "type": "exercise_detection",
            "exercise": signal.exercise,
            "status": signal.status,
            "message": signal.message,
            "angle": None if signal.angle is None else round(signal.angle, 1),
            "confidence": round(signal.confidence, 3),
            "phase": signal.phase,
            "repCount": signal.rep_count,
            "key": self.config.exercise_keys[signal.exercise],
            "inferenceMs": round(inference_ms, 2),
            "timestamp": now,
            "metrics": {key: round(value, 3) for key, value in signal.metrics.items()},
        }

    def _detect_squat(self, pose: PersonPose) -> ExerciseSignal | None:
        state = self.state["squats"]
        metrics = self._compute_squat_metrics(pose, state)
        if metrics is None:
            return None

        current_stage = int(metrics["stage"])
        standing_candidate = bool(metrics["standingCandidate"])
        if standing_candidate:
            state.standing_frames += 1
        else:
            state.standing_frames = 0

        if state.armed and current_stage == 0 and not standing_candidate:
            current_stage = max(state.squat_stage, 1)

        state.squat_stage = current_stage
        confidence = float(metrics["confidence"])
        knee_flex = float(metrics["kneeFlex"])
        hip_flex = float(metrics["hipFlex"])
        trunk_angle = float(metrics["trunkAngle"])

        if bool(metrics["frontFacing"]) is False and current_stage >= 1:
            return ExerciseSignal(
                exercise="squats",
                status="invalid",
                message="Face the camera for front-view squat tracking",
                angle=knee_flex,
                confidence=confidence,
                phase=SQUAT_STAGE_LABELS.get(current_stage, "transition"),
                rep_count=state.rep_count,
                metrics=self._squat_event_metrics(metrics),
            )

        if trunk_angle < SQUAT_EXCESSIVE_FORWARD_LEAN and current_stage >= 1:
            state.armed = True
            if current_stage >= 2:
                state.depth_reached = True
            return ExerciseSignal(
                exercise="squats",
                status="invalid",
                message="Excessive forward lean: keep trunk above 55 deg",
                angle=knee_flex,
                confidence=confidence,
                phase=SQUAT_STAGE_LABELS.get(current_stage, "transition"),
                rep_count=state.rep_count,
                metrics=self._squat_event_metrics(metrics),
            )

        if current_stage >= 1:
            state.armed = True
        if current_stage >= 2:
            state.depth_reached = True

        # A squat rep requires the hip markers to be at or below knee
        # markers (hip_y >= knee_y in screen coords where Y grows down).
        hip_at_knee = bool(metrics.get("hipAtKneeLevel", False))

        if state.armed and current_stage >= 3 and hip_at_knee and not state.bottom_reached:
            state.bottom_reached = True
            state.rep_count += 1
            return ExerciseSignal(
                exercise="squats",
                status="valid",
                message="Squat bottom reached: Stage 3 confirmed",
                angle=knee_flex,
                confidence=confidence,
                phase="bottom",
                rep_count=state.rep_count,
                metrics=self._squat_event_metrics(metrics),
            )

        if state.bottom_reached and current_stage <= 1:
            state.bottom_reached = False

        if state.armed and standing_candidate and state.standing_frames >= 2:
            state.armed = False
            state.depth_reached = False
            state.bottom_reached = False
            state.squat_stage = 0
            state.standing_frames = 0

        return None

    def _detect_jumping_jack(self, pose: PersonPose) -> ExerciseSignal | None:
        required = [
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
            KEYPOINT_INDEX["left_hip"],
            KEYPOINT_INDEX["right_hip"],
            KEYPOINT_INDEX["left_knee"],
            KEYPOINT_INDEX["right_knee"],
            KEYPOINT_INDEX["left_ankle"],
            KEYPOINT_INDEX["right_ankle"],
        ]
        if not self._has_confidence(pose, required):
            return None

        state = self.state["jumpingJacks"]
        metrics = self._compute_jump_metrics(pose, state)
        if metrics is None:
            return None

        hip_rise = float(metrics["hipRise"])
        knee_rise = float(metrics["kneeRise"])
        knee_flex = float(metrics["kneeFlex"])
        trunk_angle = float(metrics["trunkAngle"])
        standing_candidate = bool(metrics["standingCandidate"])
        confidence = float(metrics["confidence"])

        if hip_rise >= JUMP_ACTION_HIP_RISE and knee_rise >= JUMP_ACTION_KNEE_RISE and knee_flex <= JUMP_STANDING_KNEE_FLEX_MAX:
            if not state.armed:
                state.armed = True
                state.rep_count += 1
                return ExerciseSignal(
                    exercise="jumpingJacks",
                    status="valid",
                    message="Jump detected",
                    angle=hip_rise * 100.0,
                    confidence=confidence,
                    phase="takeoff",
                    rep_count=state.rep_count,
                    metrics=self._jump_event_metrics(metrics),
                )
            return None

        if state.armed and hip_rise <= JUMP_RESET_HIP_RISE and knee_rise <= JUMP_RESET_KNEE_RISE:
            state.armed = False

        if not standing_candidate and hip_rise <= JUMP_RESET_HIP_RISE and knee_flex > 35.0:
            return ExerciseSignal(
                exercise="jumpingJacks",
                status="invalid",
                message="Stand tall to reset before the next jump",
                angle=knee_flex,
                confidence=confidence,
                phase="loading",
                rep_count=state.rep_count,
                metrics=self._jump_event_metrics(metrics),
            )

        return None

    def _detect_raise(self, pose: PersonPose, side: str) -> ExerciseSignal | None:
        is_right = side == "right"
        shoulder = KEYPOINT_INDEX[f"{side}_shoulder"]
        elbow = KEYPOINT_INDEX[f"{side}_elbow"]
        wrist = KEYPOINT_INDEX[f"{side}_wrist"]
        hip = KEYPOINT_INDEX[f"{side}_hip"]

        if not self._has_confidence(pose, [shoulder, elbow, wrist, hip]):
            return None

        scale = self.config.strictness_scale()
        shoulder_point = point(pose.keypoints, shoulder)
        elbow_point = point(pose.keypoints, elbow)
        wrist_point = point(pose.keypoints, wrist)
        shoulder_width = distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
        )
        if shoulder_width <= 1e-6:
            return None

        elbow_angle = angle_degrees(shoulder_point, elbow_point, wrist_point)
        wrist_height = (shoulder_point[1] - wrist_point[1]) / shoulder_width
        elbow_height = (shoulder_point[1] - elbow_point[1]) / shoulder_width
        elbow_tuck_max = BICEP_CURL_ELBOW_TUCK_MAX * scale
        wrist_trigger_height = BICEP_CURL_WRIST_TRIGGER_HEIGHT / scale
        wrist_reset_height = BICEP_CURL_WRIST_RESET_HEIGHT * scale
        elbow_tuck = abs(float(elbow_point[0]) - float(shoulder_point[0])) / shoulder_width

        exercise_name = "rightDumbbellRaise" if is_right else "leftDumbbellRaise"
        state = self.state[exercise_name]
        reset_pose = wrist_height <= wrist_reset_height
        curled_pose = wrist_height >= wrist_trigger_height
        elbow_tucked = elbow_tuck <= elbow_tuck_max

        if reset_pose:
            state.armed = False

        if curled_pose:
            if not state.armed:
                state.armed = True
                state.rep_count += 1
                label = "Right curl" if is_right else "Left curl"
                return ExerciseSignal(
                    exercise=exercise_name,
                    status="valid",
                    message=f"{label}: angle {int(round(elbow_angle))} deg",
                    angle=elbow_angle,
                    confidence=self._confidence_for(pose, [shoulder, elbow, wrist]),
                    phase="curled",
                    rep_count=state.rep_count,
                    metrics={
                        "elbowTuck": elbow_tuck,
                        "wristHeight": wrist_height,
                        "elbowHeight": elbow_height,
                    },
                )
            return None

        if state.armed:
            return None

        if wrist_height >= wrist_trigger_height and not elbow_tucked:
            label = "Right arm" if is_right else "Left arm"
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{label}: keep the elbow tucked by your side",
                angle=elbow_angle,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist]),
                phase="curled",
                rep_count=state.rep_count,
                metrics={"elbowTuck": elbow_tuck, "wristHeight": wrist_height},
            )

        if elbow_tucked and wrist_height < wrist_trigger_height:
            label = "Right arm" if is_right else "Left arm"
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{label}: bring the wrist above the shoulder",
                angle=elbow_angle,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist]),
                phase="curled",
                rep_count=state.rep_count,
                metrics={"elbowTuck": elbow_tuck, "wristHeight": wrist_height, "elbowHeight": elbow_height},
            )

        return None

    def _detect_lateral_raise(self, pose: PersonPose, side: str) -> ExerciseSignal | None:
        is_right = side == "right"
        shoulder = KEYPOINT_INDEX[f"{side}_shoulder"]
        elbow = KEYPOINT_INDEX[f"{side}_elbow"]
        wrist = KEYPOINT_INDEX[f"{side}_wrist"]
        hip = KEYPOINT_INDEX[f"{side}_hip"]
        opposite_shoulder = KEYPOINT_INDEX["left_shoulder" if is_right else "right_shoulder"]
        opposite_hip = KEYPOINT_INDEX["left_hip" if is_right else "right_hip"]
        side_ear = KEYPOINT_INDEX[f"{side}_ear"]
        nose = KEYPOINT_INDEX["nose"]

        required = [shoulder, elbow, wrist, hip, opposite_shoulder, opposite_hip]
        if not self._has_confidence(pose, required):
            return None

        shoulder_point = point(pose.keypoints, shoulder)
        elbow_point = point(pose.keypoints, elbow)
        wrist_point = point(pose.keypoints, wrist)
        hip_point = point(pose.keypoints, hip)
        opposite_shoulder_point = point(pose.keypoints, opposite_shoulder)
        opposite_hip_point = point(pose.keypoints, opposite_hip)
        shoulder_mid = midpoint(shoulder_point, opposite_shoulder_point)
        hip_mid = midpoint(hip_point, opposite_hip_point)
        torso_vec = shoulder_mid - hip_mid
        if abs(float(torso_vec[1])) <= 1e-6:
            return None

        shoulder_abduction = angle_degrees(hip_point, shoulder_point, elbow_point)
        elbow_angle = angle_degrees(shoulder_point, elbow_point, wrist_point)
        trunk_angle = segment_angle_from_horizontal(hip_mid, shoulder_mid)
        torso_lean_abs = abs(90.0 - trunk_angle)
        torso_lean_signed = float(np.degrees(np.arctan2(float(torso_vec[0]), abs(float(torso_vec[1])))))
        opposite_lean = -torso_lean_signed if is_right else torso_lean_signed

        head_index = side_ear if pose.scores[side_ear] >= self.config.min_joint_confidence else nose
        if pose.scores[head_index] < self.config.min_joint_confidence:
            return None
        head_point = point(pose.keypoints, head_index)
        head_shoulder_dist = abs(float(shoulder_point[1]) - float(head_point[1]))

        exercise_name = "rightLateralRaise" if is_right else "leftLateralRaise"
        state = self.state[exercise_name]
        side_label = "Right" if is_right else "Left"

        if shoulder_abduction < 75.0:
            state.raise_prev_abduction = shoulder_abduction
            state.raise_prev_opposite_lean = max(0.0, opposite_lean)
            state.raise_peak_abduction = 0.0
            state.raise_invalidated = False
            state.raise_peak_reached = False
            state.armed = False
            state.raise_baseline_head_shoulder_dist = self._ema(
                state.raise_baseline_head_shoulder_dist,
                head_shoulder_dist,
            )
            return None

        if not state.armed and shoulder_abduction >= 75.0:
            state.armed = True
            state.raise_prev_abduction = shoulder_abduction
            state.raise_prev_opposite_lean = max(0.0, opposite_lean)
            state.raise_peak_abduction = shoulder_abduction
            state.raise_invalidated = False
            state.raise_peak_reached = False

        state.raise_peak_abduction = max(state.raise_peak_abduction, shoulder_abduction)

        if (
            state.armed
            and not state.raise_peak_reached
            and LATERAL_RAISE_VALID_MIN <= shoulder_abduction <= LATERAL_RAISE_VALID_MAX
        ):
            # ── Elbow gate: reject the rep if elbow is too bent ──
            if elbow_angle < LATERAL_RAISE_ELBOW_MIN:
                state.raise_invalidated = True
                return ExerciseSignal(
                    exercise=exercise_name,
                    status="invalid",
                    message=f"{side_label} lateral raise: keep elbow angle >= {LATERAL_RAISE_ELBOW_MIN:.0f} deg",
                    angle=elbow_angle,
                    confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip]),
                    phase=self._raise_phase_label(shoulder_abduction),
                    rep_count=state.rep_count,
                    metrics=self._raise_event_metrics(
                        shoulder_abduction,
                        elbow_angle,
                        torso_lean_abs,
                        opposite_lean,
                        head_shoulder_dist,
                        state.raise_baseline_head_shoulder_dist,
                    ),
                )

            state.raise_peak_reached = True
            state.rep_count += 1
            return ExerciseSignal(
                exercise=exercise_name,
                status="valid",
                message=f"{side_label} lateral raise: rep counted at {int(round(shoulder_abduction))} deg",
                angle=shoulder_abduction,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip]),
                phase="top",
                rep_count=state.rep_count,
                metrics=self._raise_event_metrics(
                    shoulder_abduction,
                    elbow_angle,
                    torso_lean_abs,
                    opposite_lean,
                    head_shoulder_dist,
                    state.raise_baseline_head_shoulder_dist,
                ),
            )

        if opposite_lean > LATERAL_RAISE_TORSO_LEAN_MAX:
            state.raise_invalidated = True
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{side_label} lateral raise: avoid torso leaning (>{LATERAL_RAISE_TORSO_LEAN_MAX:.0f} deg)",
                angle=shoulder_abduction,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip]),
                phase=self._raise_phase_label(shoulder_abduction),
                rep_count=state.rep_count,
                metrics=self._raise_event_metrics(
                    shoulder_abduction,
                    elbow_angle,
                    torso_lean_abs,
                    opposite_lean,
                    head_shoulder_dist,
                    state.raise_baseline_head_shoulder_dist,
                ),
            )

        if elbow_angle < LATERAL_RAISE_ELBOW_MIN and shoulder_abduction >= 75.0:
            state.raise_invalidated = True
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{side_label} lateral raise: keep elbow angle >= {LATERAL_RAISE_ELBOW_MIN:.0f} deg",
                angle=elbow_angle,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip]),
                phase=self._raise_phase_label(shoulder_abduction),
                rep_count=state.rep_count,
                metrics=self._raise_event_metrics(
                    shoulder_abduction,
                    elbow_angle,
                    torso_lean_abs,
                    opposite_lean,
                    head_shoulder_dist,
                    state.raise_baseline_head_shoulder_dist,
                ),
            )

        baseline = state.raise_baseline_head_shoulder_dist
        if (
            baseline is not None
            and baseline > 1e-6
            and 45.0 <= shoulder_abduction <= LATERAL_RAISE_VALID_MAX
            and head_shoulder_dist < baseline * (1.0 - LATERAL_RAISE_SHRUG_DROP_RATIO)
        ):
            state.raise_invalidated = True
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{side_label} lateral raise: avoid shrugging the shoulder",
                angle=shoulder_abduction,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip, head_index]),
                phase=self._raise_phase_label(shoulder_abduction),
                rep_count=state.rep_count,
                metrics=self._raise_event_metrics(
                    shoulder_abduction,
                    elbow_angle,
                    torso_lean_abs,
                    opposite_lean,
                    head_shoulder_dist,
                    baseline,
                ),
            )

        prev_abduction = state.raise_prev_abduction
        prev_opposite_lean = state.raise_prev_opposite_lean
        if prev_abduction is not None and prev_opposite_lean is not None:
            delta_abduction = shoulder_abduction - prev_abduction
            delta_opposite_lean = max(0.0, opposite_lean) - prev_opposite_lean
            if (
                30.0 <= shoulder_abduction <= LATERAL_RAISE_VALID_MIN
                and delta_abduction > 2.0
                and delta_opposite_lean > 1.5
                and opposite_lean > 6.0
            ):
                state.raise_invalidated = True
                return ExerciseSignal(
                    exercise=exercise_name,
                    status="invalid",
                    message=f"{side_label} lateral raise: control mid-range, no momentum lean",
                    angle=shoulder_abduction,
                    confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip]),
                    phase="mid-range",
                    rep_count=state.rep_count,
                    metrics=self._raise_event_metrics(
                        shoulder_abduction,
                        elbow_angle,
                        torso_lean_abs,
                        opposite_lean,
                        head_shoulder_dist,
                        baseline,
                    ),
                )

        state.raise_prev_abduction = shoulder_abduction
        state.raise_prev_opposite_lean = max(0.0, opposite_lean)



        if state.armed and not state.raise_peak_reached and shoulder_abduction < 30.0 and state.raise_peak_abduction > 30.0:
            peak_angle = state.raise_peak_abduction
            state.raise_invalidated = False
            state.raise_peak_abduction = 0.0
            state.armed = False
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{side_label} lateral raise: half rep (peak must reach {LATERAL_RAISE_VALID_MIN:.0f} deg)",
                angle=peak_angle,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist, hip]),
                phase="reset",
                rep_count=state.rep_count,
                metrics=self._raise_event_metrics(
                    shoulder_abduction,
                    elbow_angle,
                    torso_lean_abs,
                    opposite_lean,
                    head_shoulder_dist,
                    baseline,
                ),
            )

        return None

    def _has_confidence(self, pose: PersonPose, indices: list[int]) -> bool:
        return all(pose.scores[index] >= self.config.min_joint_confidence for index in indices)

    def _is_idle_pose(self, pose: PersonPose) -> bool:
        """Return True when the user is in a neutral standing pose.

        Idle = upright trunk, straight legs, both wrists below hips
        (arms hanging at sides), AND the body is NOT rising above
        baseline (which would indicate a jump in progress).
        """
        required = [
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
            KEYPOINT_INDEX["left_hip"],
            KEYPOINT_INDEX["right_hip"],
            KEYPOINT_INDEX["left_knee"],
            KEYPOINT_INDEX["right_knee"],
            KEYPOINT_INDEX["left_wrist"],
            KEYPOINT_INDEX["right_wrist"],
            KEYPOINT_INDEX["left_elbow"],
            KEYPOINT_INDEX["right_elbow"],
        ]
        if not self._has_confidence(pose, required):
            return False

        left_shoulder = point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"])
        right_shoulder = point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"])
        left_hip = point(pose.keypoints, KEYPOINT_INDEX["left_hip"])
        right_hip = point(pose.keypoints, KEYPOINT_INDEX["right_hip"])
        left_knee = point(pose.keypoints, KEYPOINT_INDEX["left_knee"])
        right_knee = point(pose.keypoints, KEYPOINT_INDEX["right_knee"])
        left_wrist = point(pose.keypoints, KEYPOINT_INDEX["left_wrist"])
        right_wrist = point(pose.keypoints, KEYPOINT_INDEX["right_wrist"])
        left_elbow = point(pose.keypoints, KEYPOINT_INDEX["left_elbow"])
        right_elbow = point(pose.keypoints, KEYPOINT_INDEX["right_elbow"])
        left_ankle = point(pose.keypoints, KEYPOINT_INDEX["left_ankle"])
        right_ankle = point(pose.keypoints, KEYPOINT_INDEX["right_ankle"])

        hip_mid = midpoint(left_hip, right_hip)
        shoulder_mid = midpoint(left_shoulder, right_shoulder)

        # Upright trunk (>= 78 deg from horizontal)
        trunk_angle = segment_angle_from_horizontal(hip_mid, shoulder_mid)
        if trunk_angle < 78.0:
            return False

        # Legs relatively straight (knee flex <= 18 deg)
        knee_flex = mean_or_none([
            max(0.0, 180.0 - angle_degrees(left_hip, left_knee, left_ankle)),
            max(0.0, 180.0 - angle_degrees(right_hip, right_knee, right_ankle)),
        ])
        if knee_flex is None or knee_flex > 18.0:
            return False

        # Arms hanging: both wrists at or below hip level
        # (in screen coords Y grows down, so wrist_y >= hip_y)
        if left_wrist[1] < left_hip[1] or right_wrist[1] < right_hip[1]:
            return False

        # Elbows relatively straight (arm angle >= 140 deg)
        left_arm_angle = angle_degrees(left_shoulder, left_elbow, left_wrist)
        right_arm_angle = angle_degrees(right_shoulder, right_elbow, right_wrist)
        if left_arm_angle < 140.0 or right_arm_angle < 140.0:
            return False

        # ── Anti-jump guard ─────────────────────────────────────────
        # During a jump the user's arms stay at their sides and legs
        # are straight — superficially identical to idle.  Check the
        # jump baseline: if hips or knees are significantly ABOVE the
        # baseline, the user is airborne, NOT idle.
        jump_state = self.state.get("jumpingJacks")
        if jump_state is not None:
            torso_len = mean_or_none([
                distance(left_shoulder, left_hip),
                distance(right_shoulder, right_hip),
            ])
            if torso_len is not None and torso_len > 1e-6:
                knee_mid = midpoint(left_knee, right_knee)
                if jump_state.baseline_hip_y is not None:
                    hip_rise = (jump_state.baseline_hip_y - float(hip_mid[1])) / torso_len
                    if hip_rise >= JUMP_ACTION_HIP_RISE:
                        return False
                if jump_state.baseline_knee_y is not None:
                    knee_rise = (jump_state.baseline_knee_y - float(knee_mid[1])) / torso_len
                    if knee_rise >= JUMP_ACTION_KNEE_RISE:
                        return False

        return True

    def _summarize_idle(self, pose: PersonPose | None) -> dict[str, Any]:
        """Return idle-pose status for the guidance HUD."""
        if pose is None:
            return {"detected": False, "active": False, "zone": self._zone.zone}
        idle = self._is_idle_pose(pose)
        active = self._zone.zone == ZONE_IDLE and self._zone.idle_frames >= 5
        return {"detected": idle, "active": active, "zone": self._zone.zone}

    def _confidence_for(self, pose: PersonPose, indices: list[int]) -> float:
        values = [float(pose.scores[index]) for index in indices]
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    def _compute_squat_metrics(
        self,
        pose: PersonPose,
        state: ExerciseState,
    ) -> dict[str, Any] | None:
        required = [
            KEYPOINT_INDEX["left_hip"],
            KEYPOINT_INDEX["left_knee"],
            KEYPOINT_INDEX["left_ankle"],
            KEYPOINT_INDEX["right_hip"],
            KEYPOINT_INDEX["right_knee"],
            KEYPOINT_INDEX["right_ankle"],
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
        ]
        if not self._has_confidence(pose, required):
            return None

        left_shoulder = point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"])
        right_shoulder = point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"])
        left_hip = point(pose.keypoints, KEYPOINT_INDEX["left_hip"])
        right_hip = point(pose.keypoints, KEYPOINT_INDEX["right_hip"])
        left_knee = point(pose.keypoints, KEYPOINT_INDEX["left_knee"])
        right_knee = point(pose.keypoints, KEYPOINT_INDEX["right_knee"])
        left_ankle = point(pose.keypoints, KEYPOINT_INDEX["left_ankle"])
        right_ankle = point(pose.keypoints, KEYPOINT_INDEX["right_ankle"])

        shoulder_mid = midpoint(left_shoulder, right_shoulder)
        hip_mid = midpoint(left_hip, right_hip)
        knee_mid = midpoint(left_knee, right_knee)

        shoulder_width = distance(left_shoulder, right_shoulder)
        hip_width = distance(left_hip, right_hip)
        ankle_width = distance(left_ankle, right_ankle)
        torso_len = mean_or_none(
            [
                distance(left_shoulder, left_hip),
                distance(right_shoulder, right_hip),
            ],
        )
        if shoulder_width <= 1e-6 or torso_len is None or torso_len <= 1e-6:
            return None

        knee_flex = mean_or_none(
            [
                max(0.0, 180.0 - angle_degrees(left_hip, left_knee, left_ankle)),
                max(0.0, 180.0 - angle_degrees(right_hip, right_knee, right_ankle)),
            ],
        )
        hip_flex = mean_or_none(
            [
                max(0.0, 180.0 - angle_degrees(left_shoulder, left_hip, left_knee)),
                max(0.0, 180.0 - angle_degrees(right_shoulder, right_hip, right_knee)),
            ],
        )
        if knee_flex is None or hip_flex is None:
            return None

        trunk_angle = segment_angle_from_horizontal(hip_mid, shoulder_mid)
        front_alignment = max(
            abs(left_shoulder[1] - right_shoulder[1]),
            abs(left_hip[1] - right_hip[1]),
            abs(left_knee[1] - right_knee[1]),
        ) / shoulder_width
        center_drift = abs(shoulder_mid[0] - hip_mid[0]) / shoulder_width
        hip_width_ratio = hip_width / shoulder_width
        ankle_width_ratio = ankle_width / shoulder_width
        front_facing = (
            front_alignment <= SQUAT_FRONT_VIEW_TOLERANCE
            and center_drift <= SQUAT_CENTER_DRIFT_TOLERANCE
            and hip_width_ratio >= SQUAT_MIN_HIP_WIDTH_RATIO
            and ankle_width_ratio >= SQUAT_MIN_ANKLE_WIDTH_RATIO
        )

        standing_candidate = (
            front_facing
            and knee_flex <= 15.0
            and hip_flex <= 20.0
            and trunk_angle >= 80.0
        )
        if standing_candidate:
            state.baseline_hip_y = self._ema(state.baseline_hip_y, float(hip_mid[1]))
            state.baseline_knee_y = self._ema(state.baseline_knee_y, float(knee_mid[1]))
            state.baseline_ankle_span = self._ema(state.baseline_ankle_span, float(ankle_width))

        hip_drop = None
        knee_drop = None
        if state.baseline_hip_y is not None and state.baseline_knee_y is not None:
            hip_drop = (float(hip_mid[1]) - state.baseline_hip_y) / torso_len
            knee_drop = (float(knee_mid[1]) - state.baseline_knee_y) / torso_len

        proxy_stage = self._squat_proxy_stage(hip_drop, knee_drop)
        angle_stage = self._squat_angle_stage(knee_flex, hip_flex)
        angle_mode_reliable = front_facing and (knee_flex >= 25.0 or hip_flex >= 35.0)
        if front_facing:
            stage = max(proxy_stage, angle_stage if angle_mode_reliable else 0)
        else:
            stage = 0

        # Check whether hips are at or below knees (screen Y grows down).
        hip_at_knee_level = float(hip_mid[1]) >= float(knee_mid[1])

        return {
            "stage": stage,
            "proxyStage": proxy_stage,
            "angleStage": angle_stage,
            "standingCandidate": standing_candidate,
            "frontFacing": front_facing,
            "angleModeReliable": angle_mode_reliable,
            "kneeFlex": knee_flex,
            "hipFlex": hip_flex,
            "trunkAngle": trunk_angle,
            "hipDrop": hip_drop,
            "kneeDrop": knee_drop,
            "hipAtKneeLevel": hip_at_knee_level,
            "confidence": self._confidence_for(
                pose,
                [
                    KEYPOINT_INDEX["left_hip"],
                    KEYPOINT_INDEX["left_knee"],
                    KEYPOINT_INDEX["left_ankle"],
                    KEYPOINT_INDEX["right_hip"],
                    KEYPOINT_INDEX["right_knee"],
                    KEYPOINT_INDEX["right_ankle"],
                    KEYPOINT_INDEX["left_shoulder"],
                    KEYPOINT_INDEX["right_shoulder"],
                ],
            ),
        }

    def _squat_angle_stage(self, knee_flex: float, hip_flex: float) -> int:
        quarter_hip_min = SQUAT_TABLE_MEDIUM["quarter"]["hip"] - SQUAT_TABLE_MEDIUM["quarter"]["hip_sd"]
        half_hip_min = 90.0
        bottom_knee_min = SQUAT_TABLE_MEDIUM["bottom"]["knee"] - SQUAT_TABLE_MEDIUM["bottom"]["knee_sd"]
        bottom_hip_min = SQUAT_TABLE_MEDIUM["bottom"]["hip"] - SQUAT_TABLE_MEDIUM["bottom"]["hip_sd"]

        if knee_flex >= bottom_knee_min and hip_flex >= bottom_hip_min:
            return 3
        if knee_flex >= SQUAT_TABLE_MEDIUM["half"]["knee"] and hip_flex >= half_hip_min:
            return 2
        if knee_flex >= 35.0 and hip_flex >= quarter_hip_min:
            return 1
        return 0

    def _squat_proxy_stage(self, hip_drop: float | None, knee_drop: float | None) -> int:
        if hip_drop is None or knee_drop is None:
            return 0
        proxy_scale = 0.9 + (max(0, min(100, self.config.sensitivity)) / 100.0) * 0.2
        quarter_hip = 0.08 * proxy_scale
        quarter_knee = 0.03 * proxy_scale
        half_hip = 0.16 * proxy_scale
        half_knee = 0.07 * proxy_scale
        bottom_hip = 0.20 * proxy_scale
        bottom_knee = 0.10 * proxy_scale

        if hip_drop >= bottom_hip and knee_drop >= bottom_knee:
            return 3
        if hip_drop >= half_hip and knee_drop >= half_knee:
            return 2
        if hip_drop >= quarter_hip and knee_drop >= quarter_knee:
            return 1
        return 0

    def _squat_event_metrics(self, metrics: dict[str, Any]) -> dict[str, float]:
        values = {
            "kneeFlex": float(metrics["kneeFlex"]),
            "hipFlex": float(metrics["hipFlex"]),
            "trunkAngle": float(metrics["trunkAngle"]),
            "proxyStage": float(metrics["proxyStage"]),
            "angleStage": float(metrics["angleStage"]),
            "frontFacing": 1.0 if metrics["frontFacing"] else 0.0,
        }
        if metrics["hipDrop"] is not None:
            values["hipDrop"] = float(metrics["hipDrop"])
        if metrics["kneeDrop"] is not None:
            values["kneeDrop"] = float(metrics["kneeDrop"])
        return values

    def _compute_jump_metrics(self, pose: PersonPose, state: ExerciseState) -> dict[str, Any] | None:
        left_shoulder = point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"])
        right_shoulder = point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"])
        left_hip = point(pose.keypoints, KEYPOINT_INDEX["left_hip"])
        right_hip = point(pose.keypoints, KEYPOINT_INDEX["right_hip"])
        left_knee = point(pose.keypoints, KEYPOINT_INDEX["left_knee"])
        right_knee = point(pose.keypoints, KEYPOINT_INDEX["right_knee"])
        left_ankle = point(pose.keypoints, KEYPOINT_INDEX["left_ankle"])
        right_ankle = point(pose.keypoints, KEYPOINT_INDEX["right_ankle"])

        shoulder_mid = midpoint(left_shoulder, right_shoulder)
        hip_mid = midpoint(left_hip, right_hip)
        knee_mid = midpoint(left_knee, right_knee)
        shoulder_width = distance(left_shoulder, right_shoulder)
        torso_len = mean_or_none(
            [
                distance(left_shoulder, left_hip),
                distance(right_shoulder, right_hip),
            ],
        )
        if shoulder_width <= 1e-6 or torso_len is None or torso_len <= 1e-6:
            return None

        knee_flex = mean_or_none(
            [
                max(0.0, 180.0 - angle_degrees(left_hip, left_knee, left_ankle)),
                max(0.0, 180.0 - angle_degrees(right_hip, right_knee, right_ankle)),
            ],
        )
        if knee_flex is None:
            return None

        trunk_angle = segment_angle_from_horizontal(hip_mid, shoulder_mid)
        front_alignment = max(
            abs(left_shoulder[1] - right_shoulder[1]),
            abs(left_hip[1] - right_hip[1]),
            abs(left_knee[1] - right_knee[1]),
        ) / shoulder_width
        center_drift = abs(shoulder_mid[0] - hip_mid[0]) / shoulder_width
        front_facing = (
            front_alignment <= SQUAT_FRONT_VIEW_TOLERANCE
            and center_drift <= SQUAT_CENTER_DRIFT_TOLERANCE
        )
        standing_candidate = (
            front_facing
            and knee_flex <= JUMP_STANDING_KNEE_FLEX_MAX
            and trunk_angle >= JUMP_STANDING_TRUNK_MIN
        )
        if standing_candidate:
            state.baseline_hip_y = self._ema(state.baseline_hip_y, float(hip_mid[1]))
            state.baseline_knee_y = self._ema(state.baseline_knee_y, float(knee_mid[1]))

        if state.baseline_hip_y is None or state.baseline_knee_y is None:
            return None

        # --- Calibration-aware rise calculation ---
        # If the user has calibrated "jumpingJacks", use the fixed
        # calibrated hip/knee Y as the reference ceiling.  A "jump" is
        # only counted when the current position rises ABOVE the
        # calibrated standing level, not merely above the drifting EMA.
        # This prevents squat-recovery from being mistaken for a jump.
        cal = self._calibrations.get("jumpingJacks")
        if cal is not None:
            ref_hip_y = min(state.baseline_hip_y, cal.hip_y)
            ref_knee_y = min(state.baseline_knee_y, cal.knee_y)
        else:
            ref_hip_y = state.baseline_hip_y
            ref_knee_y = state.baseline_knee_y

        hip_rise = (ref_hip_y - float(hip_mid[1])) / torso_len
        knee_rise = (ref_knee_y - float(knee_mid[1])) / torso_len

        return {
            "hipRise": max(0.0, hip_rise),
            "kneeRise": max(0.0, knee_rise),
            "kneeFlex": knee_flex,
            "trunkAngle": trunk_angle,
            "standingCandidate": standing_candidate,
            "frontFacing": front_facing,
            "confidence": self._confidence_for(
                pose,
                [
                    KEYPOINT_INDEX["left_shoulder"],
                    KEYPOINT_INDEX["right_shoulder"],
                    KEYPOINT_INDEX["left_hip"],
                    KEYPOINT_INDEX["right_hip"],
                    KEYPOINT_INDEX["left_knee"],
                    KEYPOINT_INDEX["right_knee"],
                ],
            ),
        }

    def _jump_event_metrics(self, metrics: dict[str, Any]) -> dict[str, float]:
        return {
            "hipRise": float(metrics["hipRise"]),
            "kneeRise": float(metrics["kneeRise"]),
            "kneeFlex": float(metrics["kneeFlex"]),
            "trunkAngle": float(metrics["trunkAngle"]),
            "frontFacing": 1.0 if metrics["frontFacing"] else 0.0,
        }

    def _ema(self, current: float | None, new_value: float, alpha: float = 0.2) -> float:
        if current is None:
            return new_value
        return current + alpha * (new_value - current)

    def _summarize_squat(self, pose: PersonPose | None) -> dict[str, Any]:
        state = self.state["squats"]
        if pose is None:
            return self._empty_guidance(
                "Squats",
                [
                    ("quarter", "Initiate quarter squat", False, None, "Knee ~45 deg / Hip 55 +/- 6"),
                    ("half", "Reach half squat depth gate", False, None, "Knee >= 90 deg and Hip >= 90 deg"),
                    ("bottom", "Hit bottom position", False, None, "Knee 95-109 deg and Hip 101-117 deg"),
                    ("trunk", "Keep trunk above lean limit", False, None, "Trunk >= 55 deg"),
                ],
            )

        metrics = self._compute_squat_metrics(pose, state)
        if metrics is None:
            return self._empty_guidance(
                "Squats",
                [
                    ("quarter", "Initiate quarter squat", False, None, "Knee ~45 deg / Hip 55 +/- 6"),
                    ("half", "Reach half squat depth gate", False, None, "Knee >= 90 deg and Hip >= 90 deg"),
                    ("bottom", "Hit bottom position", False, None, "Knee 95-109 deg and Hip 101-117 deg"),
                    ("trunk", "Keep trunk above lean limit", False, None, "Trunk >= 55 deg"),
                ],
            )

        proxy_value = None
        if metrics["hipDrop"] is not None and metrics["kneeDrop"] is not None:
            proxy_value = f"hip {metrics['hipDrop']:.2f} / knee {metrics['kneeDrop']:.2f}"

        return {
            "label": "Squats",
            "tracked": True,
            "phase": SQUAT_STAGE_LABELS.get(int(metrics["stage"]), "transition"),
            "summary": (
                "Squat tracking is front-view only and uses the PDF stage targets with calibrated "
                "descent proxies for a camera-facing user."
            ),
            "metrics": {
                "frontFacing": bool(metrics["frontFacing"]),
                "angleModeReliable": bool(metrics["angleModeReliable"]),
                "kneeFlex": round(float(metrics["kneeFlex"]), 1),
                "hipFlex": round(float(metrics["hipFlex"]), 1),
                "trunkAngle": round(float(metrics["trunkAngle"]), 1),
                "hipDrop": round(float(metrics["hipDrop"]), 3) if metrics["hipDrop"] is not None else None,
                "kneeDrop": round(float(metrics["kneeDrop"]), 3) if metrics["kneeDrop"] is not None else None,
                "proxyStage": int(metrics["proxyStage"]),
                "angleStage": int(metrics["angleStage"]),
            },
            "steps": self._build_steps(
                [
                    (
                        "quarter",
                        "Initiate quarter squat",
                        int(metrics["stage"]) >= 1,
                        proxy_value or float(metrics["hipFlex"]),
                        "Knee ~45 deg / Hip 55 +/- 6 / Trunk 68 +/- 4",
                    ),
                    (
                        "half",
                        "Reach half squat depth gate",
                        int(metrics["stage"]) >= 2,
                        proxy_value or float(metrics["hipFlex"]),
                        "Knee >= 90 deg and Hip >= 90 deg",
                    ),
                    (
                        "bottom",
                        "Hit bottom position",
                        int(metrics["stage"]) >= 3,
                        proxy_value or float(metrics["kneeFlex"]),
                        "Knee 95-109 deg and Hip 101-117 deg",
                    ),
                    (
                        "trunk",
                        "Keep trunk above lean limit",
                        float(metrics["trunkAngle"]) >= SQUAT_EXCESSIVE_FORWARD_LEAN,
                        float(metrics["trunkAngle"]),
                        "Trunk >= 55 deg",
                    ),
                ],
                formatter=self._format_metric_value,
            ),
        }

    def _summarize_jumping_jack(self, pose: PersonPose | None) -> dict[str, Any]:
        required = [
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
            KEYPOINT_INDEX["left_hip"],
            KEYPOINT_INDEX["right_hip"],
            KEYPOINT_INDEX["left_knee"],
            KEYPOINT_INDEX["right_knee"],
            KEYPOINT_INDEX["left_ankle"],
            KEYPOINT_INDEX["right_ankle"],
        ]
        if pose is None or not self._has_confidence(pose, required):
            return self._empty_guidance(
                "Jump",
                [
                    ("reset", "Stand tall to reset", False, None, f"knee flex <= {JUMP_STANDING_KNEE_FLEX_MAX:.0f} deg"),
                    ("drive", "Drive hips upward", False, None, f">= {JUMP_PEAK_HIP_RISE:.2f} torso lengths"),
                    ("peak", "Reach jump peak", False, None, f">= {JUMP_PEAK_KNEE_RISE:.2f} torso lengths"),
                ],
            )
        metrics = self._compute_jump_metrics(pose, self.state["jumpingJacks"])
        if metrics is None:
            return self._empty_guidance(
                "Jump",
                [
                    ("reset", "Stand tall to reset", False, None, f"knee flex <= {JUMP_STANDING_KNEE_FLEX_MAX:.0f} deg"),
                    ("drive", "Drive hips upward", False, None, f">= {JUMP_PEAK_HIP_RISE:.2f} torso lengths"),
                    ("peak", "Reach jump peak", False, None, f">= {JUMP_PEAK_KNEE_RISE:.2f} torso lengths"),
                ],
            )

        hip_rise = float(metrics["hipRise"])
        knee_rise = float(metrics["kneeRise"])
        knee_flex = float(metrics["kneeFlex"])
        standing_candidate = bool(metrics["standingCandidate"])
        action_pose = hip_rise >= JUMP_ACTION_HIP_RISE and knee_rise >= JUMP_ACTION_KNEE_RISE
        peak_pose = hip_rise >= JUMP_PEAK_HIP_RISE and knee_rise >= JUMP_PEAK_KNEE_RISE
        phase = "transition"
        if peak_pose:
            phase = "peak"
        elif action_pose:
            phase = "takeoff"
        elif standing_candidate:
            phase = "reset"
        elif knee_flex > 35.0:
            phase = "loading"

        return {
            "label": "Jump",
            "tracked": True,
            "phase": phase,
            "summary": "Stand tall to reset, then drive straight up until the hips and knees reach the jump peak.",
            "metrics": {
                "hipRise": round(hip_rise, 3),
                "kneeRise": round(knee_rise, 3),
                "kneeFlex": round(knee_flex, 1),
                "trunkAngle": round(float(metrics["trunkAngle"]), 1),
            },
            "steps": self._build_steps(
                [
                    ("reset", "Stand tall to reset", standing_candidate, knee_flex, f"knee flex <= {JUMP_STANDING_KNEE_FLEX_MAX:.0f} deg"),
                    ("drive", "Drive hips upward", hip_rise >= JUMP_ACTION_HIP_RISE, hip_rise, f">= {JUMP_ACTION_HIP_RISE:.2f} torso lengths"),
                    ("peak", "Reach jump peak", knee_rise >= JUMP_PEAK_KNEE_RISE, knee_rise, f">= {JUMP_PEAK_KNEE_RISE:.2f} torso lengths"),
                ],
                formatter=self._format_metric_value,
            ),
        }

    def _summarize_raise(self, pose: PersonPose | None, side: str) -> dict[str, Any]:
        scale = self.config.strictness_scale()
        elbow_tuck_max = BICEP_CURL_ELBOW_TUCK_MAX * scale
        wrist_trigger_height = BICEP_CURL_WRIST_TRIGGER_HEIGHT / scale
        wrist_reset_height = BICEP_CURL_WRIST_RESET_HEIGHT * scale
        shoulder = KEYPOINT_INDEX[f"{side}_shoulder"]
        elbow = KEYPOINT_INDEX[f"{side}_elbow"]
        wrist = KEYPOINT_INDEX[f"{side}_wrist"]
        hip = KEYPOINT_INDEX[f"{side}_hip"]
        label = "Right Bicep Curl" if side == "right" else "Left Bicep Curl"

        if pose is None or not self._has_confidence(pose, [shoulder, elbow, wrist, hip]):
            return self._empty_guidance(
                label,
                [
                    ("reset", "Lower wrist to reset", False, None, f"wrist height <= {wrist_reset_height:.2f}"),
                    ("tuck", "Keep elbow tucked", False, None, f"elbow tuck <= {elbow_tuck_max:.2f}"),
                    ("curl", "Raise wrist to shoulder", False, None, f"wrist height >= {wrist_trigger_height:.2f}"),
                ],
            )

        shoulder_point = point(pose.keypoints, shoulder)
        elbow_point = point(pose.keypoints, elbow)
        wrist_point = point(pose.keypoints, wrist)
        shoulder_width = distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
        )
        if shoulder_width <= 1e-6:
            return self._empty_guidance(label, [])

        elbow_angle = angle_degrees(shoulder_point, elbow_point, wrist_point)
        wrist_height = (shoulder_point[1] - wrist_point[1]) / shoulder_width
        elbow_height = (shoulder_point[1] - elbow_point[1]) / shoulder_width
        elbow_tuck = abs(float(elbow_point[0]) - float(shoulder_point[0])) / shoulder_width
        reset_pose = wrist_height <= wrist_reset_height
        curled_pose = wrist_height >= wrist_trigger_height
        elbow_tucked = elbow_tuck <= elbow_tuck_max
        phase = "transition"
        if curled_pose:
            phase = "curled"
        elif reset_pose:
            phase = "reset"
        elif wrist_height > wrist_reset_height:
            phase = "curling"

        return {
            "label": label,
            "tracked": True,
            "phase": phase,
            "summary": "Start from a long arm, keep the elbow tucked by the torso, then curl the hand toward the shoulder.",
            "metrics": {
                "elbowAngle": round(elbow_angle, 1),
                "elbowTuck": round(elbow_tuck, 2),
                "wristHeight": round(wrist_height, 2),
                "elbowHeight": round(elbow_height, 2),
            },
            "steps": self._build_steps(
                [
                    ("reset", "Lower wrist to reset", reset_pose, wrist_height, f"wrist height <= {wrist_reset_height:.2f}"),
                    ("tuck", "Keep elbow tucked", elbow_tucked, elbow_tuck, f"elbow tuck <= {elbow_tuck_max:.2f}"),
                    ("curl", "Raise wrist to shoulder", wrist_height >= wrist_trigger_height, wrist_height, f"wrist height >= {wrist_trigger_height:.2f}"),
                ],
                formatter=self._format_metric_value,
            ),
        }

    def _summarize_lateral_raise(self, pose: PersonPose | None, side: str) -> dict[str, Any]:
        shoulder = KEYPOINT_INDEX[f"{side}_shoulder"]
        elbow = KEYPOINT_INDEX[f"{side}_elbow"]
        wrist = KEYPOINT_INDEX[f"{side}_wrist"]
        hip = KEYPOINT_INDEX[f"{side}_hip"]
        is_right = side == "right"
        opposite_shoulder = KEYPOINT_INDEX["left_shoulder" if is_right else "right_shoulder"]
        opposite_hip = KEYPOINT_INDEX["left_hip" if is_right else "right_hip"]
        side_ear = KEYPOINT_INDEX[f"{side}_ear"]
        nose = KEYPOINT_INDEX["nose"]
        label = "Right Lateral Raise" if is_right else "Left Lateral Raise"

        if pose is None or not self._has_confidence(pose, [shoulder, elbow, wrist, hip, opposite_shoulder, opposite_hip]):
            return self._empty_guidance(
                label,
                [
                    ("start", "Start with arm by your side", False, None, f"abduction <= {LATERAL_RAISE_START_MAX:.0f} deg"),
                    ("mid", "Control mid-range lift", False, None, "30-75 deg without torso lean"),
                    ("top", "Reach top with straight arm", False, None, f"abduction {LATERAL_RAISE_VALID_MIN:.0f}-{LATERAL_RAISE_VALID_MAX:.0f} deg"),
                ],
            )

        shoulder_point = point(pose.keypoints, shoulder)
        elbow_point = point(pose.keypoints, elbow)
        wrist_point = point(pose.keypoints, wrist)
        hip_point = point(pose.keypoints, hip)
        opposite_shoulder_point = point(pose.keypoints, opposite_shoulder)
        opposite_hip_point = point(pose.keypoints, opposite_hip)
        shoulder_mid = midpoint(shoulder_point, opposite_shoulder_point)
        hip_mid = midpoint(hip_point, opposite_hip_point)
        trunk_angle = segment_angle_from_horizontal(hip_mid, shoulder_mid)
        torso_lean_abs = abs(90.0 - trunk_angle)
        torso_vec = shoulder_mid - hip_mid
        torso_lean_signed = float(np.degrees(np.arctan2(float(torso_vec[0]), max(abs(float(torso_vec[1])), 1e-6))))
        opposite_lean = -torso_lean_signed if is_right else torso_lean_signed
        shoulder_abduction = angle_degrees(hip_point, shoulder_point, elbow_point)
        elbow_angle = angle_degrees(shoulder_point, elbow_point, wrist_point)
        head_index = side_ear if pose.scores[side_ear] >= self.config.min_joint_confidence else nose
        baseline = self.state["rightLateralRaise" if is_right else "leftLateralRaise"].raise_baseline_head_shoulder_dist
        head_shoulder_dist = None
        shrug_ratio = None
        if pose.scores[head_index] >= self.config.min_joint_confidence:
            head_shoulder_dist = abs(float(shoulder_point[1]) - float(point(pose.keypoints, head_index)[1]))
            if baseline is not None and baseline > 1e-6:
                shrug_ratio = max(0.0, (baseline - head_shoulder_dist) / baseline)

        phase = self._raise_phase_label(shoulder_abduction)
        if shoulder_abduction <= LATERAL_RAISE_START_MAX:
            phase = "reset"

        return {
            "label": label,
            "tracked": True,
            "phase": phase,
            "summary": (
                "Lift in the frontal plane to shoulder level (75-100 deg), keep elbow >= 140 deg, "
                "avoid opposite-side trunk lean > 10 deg, and avoid shrugging."
            ),
            "metrics": {
                "abductionAngle": round(shoulder_abduction, 1),
                "elbowAngle": round(elbow_angle, 1),
                "torsoLeanDeg": round(torso_lean_abs, 1),
                "oppositeLeanDeg": round(max(0.0, opposite_lean), 1),
                "shrugRatio": round(float(shrug_ratio), 3) if shrug_ratio is not None else None,
            },
            "steps": self._build_steps(
                [
                    (
                        "start",
                        "Start with arm by your side",
                        shoulder_abduction <= LATERAL_RAISE_START_MAX,
                        shoulder_abduction,
                        f"abduction <= {LATERAL_RAISE_START_MAX:.0f} deg",
                    ),
                    (
                        "mid",
                        "Control mid-range lift",
                        30.0 <= shoulder_abduction <= LATERAL_RAISE_VALID_MIN and max(0.0, opposite_lean) <= LATERAL_RAISE_TORSO_LEAN_MAX,
                        max(0.0, opposite_lean),
                        f"opposite lean <= {LATERAL_RAISE_TORSO_LEAN_MAX:.0f} deg",
                    ),
                    (
                        "top",
                        "Reach top with straight arm",
                        (
                            LATERAL_RAISE_VALID_MIN <= shoulder_abduction <= LATERAL_RAISE_VALID_MAX
                            and elbow_angle >= LATERAL_RAISE_ELBOW_MIN
                        ),
                        shoulder_abduction,
                        (
                            f"abduction {LATERAL_RAISE_VALID_MIN:.0f}-{LATERAL_RAISE_VALID_MAX:.0f} deg, "
                            f"elbow >= {LATERAL_RAISE_ELBOW_MIN:.0f} deg"
                        ),
                    ),
                ],
                formatter=self._format_metric_value,
            ),
        }

    def _raise_phase_label(self, shoulder_abduction: float) -> str:
        if shoulder_abduction <= LATERAL_RAISE_START_MAX:
            return "start"
        if shoulder_abduction < 30.0:
            return "phase1"
        if shoulder_abduction < LATERAL_RAISE_VALID_MIN:
            return "mid-range"
        if shoulder_abduction <= LATERAL_RAISE_VALID_MAX:
            return "top"
        return "above-target"

    def _raise_event_metrics(
        self,
        shoulder_abduction: float,
        elbow_angle: float,
        torso_lean_abs: float,
        opposite_lean: float,
        head_shoulder_dist: float,
        baseline_head_shoulder_dist: float | None,
    ) -> dict[str, float]:
        values: dict[str, float] = {
            "abductionAngle": shoulder_abduction,
            "elbowAngle": elbow_angle,
            "torsoLeanDeg": torso_lean_abs,
            "oppositeLeanDeg": max(0.0, opposite_lean),
            "headShoulderDist": head_shoulder_dist,
        }
        if baseline_head_shoulder_dist is not None and baseline_head_shoulder_dist > 1e-6:
            values["shrugRatio"] = max(0.0, (baseline_head_shoulder_dist - head_shoulder_dist) / baseline_head_shoulder_dist)
        return values

    def _empty_guidance(
        self,
        label: str,
        step_specs: list[tuple[str, str, bool, float | None, str]],
    ) -> dict[str, Any]:
        return {
            "label": label,
            "tracked": False,
            "phase": "untracked",
            "summary": "Move into frame so the joints for this exercise are visible.",
            "metrics": {},
            "steps": self._build_steps(step_specs, formatter=self._format_metric_value),
        }

    def _build_steps(
        self,
        step_specs: list[tuple[str, str, bool, float | None, str]],
        formatter=None,
    ) -> list[dict[str, Any]]:
        render_value = formatter or self._format_metric_value
        steps: list[dict[str, Any]] = []
        for step_id, label, done, value, target in step_specs:
            steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "done": bool(done),
                    "value": render_value(value),
                    "target": target,
                }
            )
        return steps

    def _format_metric_value(self, value: float | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "yes" if value else "no"
        return f"{value:.1f}"
