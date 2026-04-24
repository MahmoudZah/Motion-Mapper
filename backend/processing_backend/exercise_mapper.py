from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
BICEP_CURL_TOP_MAX_ANGLE = 95.0
BICEP_CURL_RESET_MIN_ANGLE = 145.0
BICEP_CURL_ELBOW_TUCK_MAX = 0.3

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

    def summarize_pose(self, pose: PersonPose | None) -> dict[str, Any]:
        return {
            "squats": self._summarize_squat(pose),
            "jumpingJacks": self._summarize_jumping_jack(pose),
            "rightDumbbellRaise": self._summarize_raise(pose, side="right"),
            "leftDumbbellRaise": self._summarize_raise(pose, side="left"),
        }

    def evaluate(
        self,
        pose: PersonPose | None,
        inference_ms: float,
        timestamp_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        now = now_ms() if timestamp_ms is None else timestamp_ms
        if pose is None or pose.mean_confidence < self.config.min_person_confidence:
            return []

        signals = [
            self._detect_squat(pose),
            self._detect_jumping_jack(pose),
            self._detect_raise(pose, side="right"),
            self._detect_raise(pose, side="left"),
        ]
        events: list[dict[str, Any]] = []
        for signal in signals:
            if signal is None:
                continue
            event = self._build_event(signal, inference_ms, now)
            if event is not None:
                events.append(event)
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

        if state.armed and current_stage >= 3 and not state.bottom_reached:
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

        if hip_rise >= JUMP_ACTION_HIP_RISE and knee_rise >= JUMP_ACTION_KNEE_RISE:
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
        hip_point = point(pose.keypoints, hip)
        shoulder_width = distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
        )
        if shoulder_width <= 1e-6:
            return None

        elbow_angle = angle_degrees(shoulder_point, elbow_point, wrist_point)
        wrist_height = (shoulder_point[1] - wrist_point[1]) / shoulder_width
        elbow_height = (shoulder_point[1] - elbow_point[1]) / shoulder_width
        curl_top_max = BICEP_CURL_TOP_MAX_ANGLE * scale
        reset_min = BICEP_CURL_RESET_MIN_ANGLE / scale
        elbow_tuck_max = BICEP_CURL_ELBOW_TUCK_MAX * scale
        elbow_tuck = abs(float(elbow_point[0]) - float(shoulder_point[0])) / shoulder_width

        exercise_name = "rightDumbbellRaise" if is_right else "leftDumbbellRaise"
        state = self.state[exercise_name]
        reset_pose = elbow_angle >= reset_min and wrist_height <= 0.0
        curled_pose = wrist_height > 0.0 and elbow_tuck <= elbow_tuck_max
        elbow_tucked = elbow_tuck <= elbow_tuck_max

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

        if elbow_angle <= curl_top_max and not elbow_tucked:
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

        if elbow_tucked and wrist_height <= 0.0:
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

        if reset_pose:
            state.armed = False

        return None

    def _has_confidence(self, pose: PersonPose, indices: list[int]) -> bool:
        return all(pose.scores[index] >= self.config.min_joint_confidence for index in indices)

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

        hip_rise = (state.baseline_hip_y - float(hip_mid[1])) / torso_len
        knee_rise = (state.baseline_knee_y - float(knee_mid[1])) / torso_len

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
        curl_top_max = BICEP_CURL_TOP_MAX_ANGLE * scale
        reset_min = BICEP_CURL_RESET_MIN_ANGLE / scale
        elbow_tuck_max = BICEP_CURL_ELBOW_TUCK_MAX * scale
        shoulder = KEYPOINT_INDEX[f"{side}_shoulder"]
        elbow = KEYPOINT_INDEX[f"{side}_elbow"]
        wrist = KEYPOINT_INDEX[f"{side}_wrist"]
        hip = KEYPOINT_INDEX[f"{side}_hip"]
        label = "Right Bicep Curl" if side == "right" else "Left Bicep Curl"

        if pose is None or not self._has_confidence(pose, [shoulder, elbow, wrist, hip]):
            return self._empty_guidance(
                label,
                [
                    ("reset", "Return to full extension", False, None, f"elbow >= {reset_min:.0f} deg"),
                    ("tuck", "Keep elbow tucked", False, None, f"elbow tuck <= {elbow_tuck_max:.2f}"),
                    ("curl", "Bring wrist above shoulder", False, None, "wrist above shoulder"),
                ],
            )

        shoulder_point = point(pose.keypoints, shoulder)
        elbow_point = point(pose.keypoints, elbow)
        wrist_point = point(pose.keypoints, wrist)
        hip_point = point(pose.keypoints, hip)
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
        reset_pose = elbow_angle >= reset_min and wrist_height <= 0.0
        curled_pose = wrist_height > 0.0 and elbow_tuck <= elbow_tuck_max
        elbow_tucked = elbow_tuck <= elbow_tuck_max
        phase = "transition"
        if curled_pose:
            phase = "curled"
        elif reset_pose:
            phase = "reset"
        elif elbow_angle < reset_min:
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
                    ("reset", "Return to full extension", reset_pose, elbow_angle, f"elbow >= {reset_min:.0f} deg"),
                    ("tuck", "Keep elbow tucked", elbow_tucked, elbow_tuck, f"elbow tuck <= {elbow_tuck_max:.2f}"),
                    ("curl", "Bring wrist above shoulder", wrist_height > 0.0, wrist_height, "wrist above shoulder"),
                ],
                formatter=self._format_metric_value,
            ),
        }

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
