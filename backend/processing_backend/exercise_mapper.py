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
)
from .pose_runtime import PersonPose
from .protocol import now_ms


@dataclass(slots=True)
class ExerciseState:
    armed: bool = False
    rep_count: int = 0
    last_status: str | None = None
    last_emit_ms: int = 0


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

        scale = self.config.strictness_scale()
        left_angle = angle_degrees(
            point(pose.keypoints, KEYPOINT_INDEX["left_hip"]),
            point(pose.keypoints, KEYPOINT_INDEX["left_knee"]),
            point(pose.keypoints, KEYPOINT_INDEX["left_ankle"]),
        )
        right_angle = angle_degrees(
            point(pose.keypoints, KEYPOINT_INDEX["right_hip"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_knee"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_ankle"]),
        )
        knee_angle = mean_or_none([left_angle, right_angle])
        if knee_angle is None:
            return None

        left_torso = angle_degrees(
            point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["left_hip"]),
            point(pose.keypoints, KEYPOINT_INDEX["left_knee"]),
        )
        right_torso = angle_degrees(
            point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_hip"]),
            point(pose.keypoints, KEYPOINT_INDEX["right_knee"]),
        )
        torso_angle = mean_or_none([left_torso, right_torso])
        if torso_angle is None:
            return None

        state = self.state["squats"]
        squat_depth_threshold = 125.0 / scale
        stand_threshold = 155.0 / scale
        torso_guard = 118.0 / scale

        if knee_angle <= squat_depth_threshold and torso_angle < torso_guard:
            state.armed = True
            return ExerciseSignal(
                exercise="squats",
                status="invalid",
                message="Keep your back straighter during the squat",
                angle=knee_angle,
                confidence=self._confidence_for(
                    pose,
                    [
                        KEYPOINT_INDEX["left_hip"],
                        KEYPOINT_INDEX["left_knee"],
                        KEYPOINT_INDEX["left_ankle"],
                        KEYPOINT_INDEX["right_hip"],
                        KEYPOINT_INDEX["right_knee"],
                        KEYPOINT_INDEX["right_ankle"],
                    ],
                ),
                phase="descending",
                rep_count=state.rep_count,
                metrics={"torsoAngle": torso_angle, "kneeAngle": knee_angle},
            )

        if knee_angle <= squat_depth_threshold:
            state.armed = True
            return None

        if state.armed and knee_angle >= stand_threshold:
            state.armed = False
            state.rep_count += 1
            return ExerciseSignal(
                exercise="squats",
                status="valid",
                message="Squat detected: valid rep",
                angle=knee_angle,
                confidence=self._confidence_for(
                    pose,
                    [
                        KEYPOINT_INDEX["left_hip"],
                        KEYPOINT_INDEX["left_knee"],
                        KEYPOINT_INDEX["left_ankle"],
                        KEYPOINT_INDEX["right_hip"],
                        KEYPOINT_INDEX["right_knee"],
                        KEYPOINT_INDEX["right_ankle"],
                    ],
                ),
                phase="standing",
                rep_count=state.rep_count,
                metrics={"torsoAngle": torso_angle, "kneeAngle": knee_angle},
            )

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

    def _summarize_squat(self, pose: PersonPose | None) -> dict[str, Any]:
        scale = self.config.strictness_scale()
        squat_depth_threshold = 125.0 / scale
        stand_threshold = 155.0 / scale
        torso_guard = 118.0 / scale
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
        if pose is None or not self._has_confidence(pose, required):
            return self._empty_guidance(
                "Squats",
                [
                    ("start", "Return to standing", False, None, f">= {stand_threshold:.0f} deg"),
                    ("depth", "Reach squat depth", False, None, f"<= {squat_depth_threshold:.0f} deg"),
                    ("torso", "Keep torso tall", False, None, f">= {torso_guard:.0f} deg"),
                ],
            )

        knee_angle = mean_or_none(
            [
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["left_hip"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_knee"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_ankle"]),
                ),
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["right_hip"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_knee"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_ankle"]),
                ),
            ],
        )
        torso_angle = mean_or_none(
            [
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["left_shoulder"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_hip"]),
                    point(pose.keypoints, KEYPOINT_INDEX["left_knee"]),
                ),
                angle_degrees(
                    point(pose.keypoints, KEYPOINT_INDEX["right_shoulder"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_hip"]),
                    point(pose.keypoints, KEYPOINT_INDEX["right_knee"]),
                ),
            ],
        )
        phase = "transition"
        if knee_angle is not None:
            if knee_angle <= squat_depth_threshold:
                phase = "bottom"
            elif knee_angle >= stand_threshold:
                phase = "standing"

        return {
            "label": "Squats",
            "tracked": True,
            "phase": phase,
            "summary": "Drop to squat depth, keep the torso tall, then stand back up.",
            "metrics": {
                "kneeAngle": round(knee_angle, 1) if knee_angle is not None else None,
                "torsoAngle": round(torso_angle, 1) if torso_angle is not None else None,
            },
            "steps": self._build_steps(
                [
                    ("start", "Return to standing", knee_angle is not None and knee_angle >= stand_threshold, knee_angle, f">= {stand_threshold:.0f} deg"),
                    ("depth", "Reach squat depth", knee_angle is not None and knee_angle <= squat_depth_threshold, knee_angle, f"<= {squat_depth_threshold:.0f} deg"),
                    ("torso", "Keep torso tall", torso_angle is not None and torso_angle >= torso_guard, torso_angle, f">= {torso_guard:.0f} deg"),
                ]
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
        return f"{value:.1f}"
