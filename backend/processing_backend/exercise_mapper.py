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

        if state.armed and standing_candidate and state.standing_frames >= 2:
            state.armed = False
            state.depth_reached = False
            state.bottom_reached = False
            state.squat_stage = 0
            state.standing_frames = 0

        return None

    def _detect_jumping_jack(self, pose: PersonPose) -> ExerciseSignal | None:
        required = [
            KEYPOINT_INDEX["nose"],
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
            KEYPOINT_INDEX["left_elbow"],
            KEYPOINT_INDEX["right_elbow"],
            KEYPOINT_INDEX["left_wrist"],
            KEYPOINT_INDEX["right_wrist"],
            KEYPOINT_INDEX["left_ankle"],
            KEYPOINT_INDEX["right_ankle"],
        ]
        if not self._has_confidence(pose, required):
            return None

        scale = self.config.strictness_scale()
        shoulders = distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
        )
        ankles = distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_ankle"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_ankle"]),
        )
        ankle_span = normalized_distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_ankle"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_ankle"]),
            shoulders,
        )
        if ankle_span is None:
            return None

        nose_y = point(pose.keypoints, KEYPOINT_INDEX["nose"])[1]
        left_wrist_y = point(pose.keypoints, KEYPOINT_INDEX["left_wrist"])[1]
        right_wrist_y = point(pose.keypoints, KEYPOINT_INDEX["right_wrist"])[1]
        wrists_above_head = left_wrist_y < nose_y and right_wrist_y < nose_y
        arms_extension = mean_or_none(
            [
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_elbow"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_wrist"]),
                ),
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_elbow"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_wrist"]),
                ),
            ],
        )
        if arms_extension is None:
            return None

        open_threshold = 1.65 / scale
        close_threshold = 1.05 / scale
        state = self.state["jumpingJacks"]

        if ankle_span >= open_threshold and wrists_above_head and arms_extension >= 145.0 / scale:
            if not state.armed:
                state.armed = True
                state.rep_count += 1
                return ExerciseSignal(
                    exercise="jumpingJacks",
                    status="valid",
                    message="Jumping jack: full extension",
                    angle=arms_extension,
                    confidence=self._confidence_for(
                        pose,
                        [
                            KEYPOINT_INDEX["left_wrist"],
                            KEYPOINT_INDEX["right_wrist"],
                            KEYPOINT_INDEX["left_ankle"],
                            KEYPOINT_INDEX["right_ankle"],
                        ],
                    ),
                    phase="open",
                    rep_count=state.rep_count,
                    metrics={"armAngle": arms_extension, "ankleSpan": ankle_span, "anklesPx": ankles},
                )
            return None

        if ankle_span >= open_threshold and not wrists_above_head:
            return ExerciseSignal(
                exercise="jumpingJacks",
                status="invalid",
                message="Extend arms fully overhead",
                angle=arms_extension,
                confidence=self._confidence_for(
                    pose,
                    [KEYPOINT_INDEX["left_wrist"], KEYPOINT_INDEX["right_wrist"]],
                ),
                phase="open",
                rep_count=state.rep_count,
                metrics={"armAngle": arms_extension, "ankleSpan": ankle_span},
            )

        if ankle_span <= close_threshold:
            state.armed = False

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
        upper_arm_center = midpoint(shoulder_point, elbow_point)
        wrist_height = (shoulder_point[1] - wrist_point[1]) / shoulder_width
        elbow_height = (shoulder_point[1] - elbow_point[1]) / shoulder_width
        arm_lift = (hip_point[1] - upper_arm_center[1]) / shoulder_width

        raised_wrist_threshold = -0.08 / scale
        raised_elbow_threshold = -0.12 / scale
        start_wrist_threshold = -0.28 * scale
        start_elbow_threshold = -0.18 * scale
        elbow_min = 70.0 / scale
        elbow_max = 120.0 * scale

        exercise_name = "rightDumbbellRaise" if is_right else "leftDumbbellRaise"
        state = self.state[exercise_name]
        start_pose = wrist_height <= start_wrist_threshold and elbow_height <= start_elbow_threshold
        raised_pose = wrist_height >= raised_wrist_threshold and elbow_height >= raised_elbow_threshold
        elbow_on_target = elbow_min <= elbow_angle <= elbow_max

        if raised_pose and elbow_on_target:
            if not state.armed:
                state.armed = True
                state.rep_count += 1
                label = "Right raise" if is_right else "Left raise"
                return ExerciseSignal(
                    exercise=exercise_name,
                    status="valid",
                    message=f"{label}: angle {int(round(elbow_angle))} deg",
                    angle=elbow_angle,
                    confidence=self._confidence_for(pose, [shoulder, elbow, wrist]),
                    phase="raised",
                    rep_count=state.rep_count,
                    metrics={
                        "armLift": arm_lift,
                        "wristHeight": wrist_height,
                        "elbowHeight": elbow_height,
                    },
                )
            return None

        if raised_pose and elbow_angle < elbow_min:
            label = "Right arm" if is_right else "Left arm"
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{label}: open the elbow a bit more",
                angle=elbow_angle,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist]),
                phase="raised",
                rep_count=state.rep_count,
                metrics={"armLift": arm_lift, "wristHeight": wrist_height},
            )

        if raised_pose and elbow_angle > elbow_max:
            label = "Right arm" if is_right else "Left arm"
            return ExerciseSignal(
                exercise=exercise_name,
                status="invalid",
                message=f"{label}: bend the elbow a touch more",
                angle=elbow_angle,
                confidence=self._confidence_for(pose, [shoulder, elbow, wrist]),
                phase="raised",
                rep_count=state.rep_count,
                metrics={"armLift": arm_lift, "wristHeight": wrist_height, "elbowHeight": elbow_height},
            )

        if start_pose:
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
        scale = self.config.strictness_scale()
        open_threshold = 1.65 / scale
        close_threshold = 1.05 / scale
        arm_threshold = 145.0 / scale
        required = [
            KEYPOINT_INDEX["nose"],
            KEYPOINT_INDEX["left_shoulder"],
            KEYPOINT_INDEX["right_shoulder"],
            KEYPOINT_INDEX["left_elbow"],
            KEYPOINT_INDEX["right_elbow"],
            KEYPOINT_INDEX["left_wrist"],
            KEYPOINT_INDEX["right_wrist"],
            KEYPOINT_INDEX["left_ankle"],
            KEYPOINT_INDEX["right_ankle"],
        ]
        if pose is None or not self._has_confidence(pose, required):
            return self._empty_guidance(
                "Jumping Jacks",
                [
                    ("closed", "Feet back together", False, None, f"<= {close_threshold:.2f} x shoulder width"),
                    ("feet", "Feet wide", False, None, f">= {open_threshold:.2f} x shoulder width"),
                    ("arms", "Arms overhead and straight", False, None, f">= {arm_threshold:.0f} deg"),
                ],
            )

        shoulders = distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
        )
        ankle_span = normalized_distance(
            point(pose.keypoints, KEYPOINT_INDEX["left_ankle"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_ankle"]),
            shoulders,
        )
        arms_extension = mean_or_none(
            [
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_elbow"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_wrist"]),
                ),
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_elbow"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_wrist"]),
                ),
            ],
        )
        nose_y = point(pose.keypoints, KEYPOINT_INDEX["nose"])[1]
        wrists_above_head = (
            point(pose.keypoints, KEYPOINT_INDEX["left_wrist"])[1] < nose_y
            and point(pose.keypoints, KEYPOINT_INDEX["right_wrist"])[1] < nose_y
        )
        open_pose = (
            ankle_span is not None
            and ankle_span >= open_threshold
            and wrists_above_head
            and arms_extension is not None
            and arms_extension >= arm_threshold
        )
        closed_pose = ankle_span is not None and ankle_span <= close_threshold
        phase = "transition"
        if open_pose:
            phase = "open"
        elif closed_pose:
            phase = "closed"

        return {
            "label": "Jumping Jacks",
            "tracked": True,
            "phase": phase,
            "summary": "Hit the closed stance, then open wide with straight arms overhead.",
            "metrics": {
                "armAngle": round(arms_extension, 1) if arms_extension is not None else None,
                "ankleSpan": round(ankle_span, 2) if ankle_span is not None else None,
                "wristsAboveHead": wrists_above_head,
            },
            "steps": self._build_steps(
                [
                    ("closed", "Feet back together", closed_pose, ankle_span, f"<= {close_threshold:.2f} x shoulder width"),
                    ("feet", "Feet wide", ankle_span is not None and ankle_span >= open_threshold, ankle_span, f">= {open_threshold:.2f} x shoulder width"),
                    ("arms", "Arms overhead and straight", wrists_above_head and arms_extension is not None and arms_extension >= arm_threshold, arms_extension, f">= {arm_threshold:.0f} deg"),
                ],
                formatter=self._format_metric_value,
            ),
        }

    def _summarize_raise(self, pose: PersonPose | None, side: str) -> dict[str, Any]:
        scale = self.config.strictness_scale()
        raised_wrist_threshold = -0.08 / scale
        raised_elbow_threshold = -0.12 / scale
        start_wrist_threshold = -0.28 * scale
        start_elbow_threshold = -0.18 * scale
        elbow_min = 70.0 / scale
        elbow_max = 120.0 * scale
        shoulder = KEYPOINT_INDEX[f"{side}_shoulder"]
        elbow = KEYPOINT_INDEX[f"{side}_elbow"]
        wrist = KEYPOINT_INDEX[f"{side}_wrist"]
        hip = KEYPOINT_INDEX[f"{side}_hip"]
        label = "Right Dumbbell Raise" if side == "right" else "Left Dumbbell Raise"

        if pose is None or not self._has_confidence(pose, [shoulder, elbow, wrist, hip]):
            return self._empty_guidance(
                label,
                [
                    ("start", "Return arm to start", False, None, f"wrist <= {start_wrist_threshold:.2f}"),
                    ("lift", "Lift upper arm", False, None, f"elbow >= {raised_elbow_threshold:.2f}"),
                    ("elbow", "Hold about 90 deg", False, None, f"{elbow_min:.0f}-{elbow_max:.0f} deg"),
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
        upper_arm_center = midpoint(shoulder_point, elbow_point)
        wrist_height = (shoulder_point[1] - wrist_point[1]) / shoulder_width
        arm_lift = (hip_point[1] - upper_arm_center[1]) / shoulder_width
        elbow_height = (shoulder_point[1] - elbow_point[1]) / shoulder_width
        start_pose = wrist_height <= start_wrist_threshold and elbow_height <= start_elbow_threshold
        raised_pose = wrist_height >= raised_wrist_threshold and elbow_height >= raised_elbow_threshold
        elbow_on_target = elbow_min <= elbow_angle <= elbow_max
        phase = "transition"
        if raised_pose and elbow_on_target:
            phase = "raised"
        elif start_pose:
            phase = "start"

        return {
            "label": label,
            "tracked": True,
            "phase": phase,
            "summary": "Return to start, then lift the arm and hold the elbow near 90 deg.",
            "metrics": {
                "elbowAngle": round(elbow_angle, 1),
                "armLift": round(arm_lift, 2),
                "wristHeight": round(wrist_height, 2),
                "elbowHeight": round(elbow_height, 2),
            },
            "steps": self._build_steps(
                [
                    ("start", "Return arm to start", start_pose, wrist_height, f"wrist <= {start_wrist_threshold:.2f}"),
                    ("lift", "Lift upper arm", raised_pose, elbow_height, f"elbow >= {raised_elbow_threshold:.2f}"),
                    ("elbow", "Hold about 90 deg", elbow_on_target, elbow_angle, f"{elbow_min:.0f}-{elbow_max:.0f} deg"),
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
