from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_EXERCISE_KEYS = {
    "squats": "Space",
    "jumpingJacks": "W",
    "rightDumbbellRaise": "D",
    "leftDumbbellRaise": "A",
}


@dataclass(slots=True)
class CameraConfig:
    index: int = 0
    width: int = 640
    height: int = 480
    mirror: bool = True


@dataclass(slots=True)
class ModelConfig:
    weights: str = "models/yolov8n-pose.pt"
    image_size: int = 480
    confidence: float = 0.5


@dataclass(slots=True)
class StreamConfig:
    emit_pose_events: bool = False
    emit_video_frames: bool = True
    emit_idle_events: bool = True
    no_person_interval_seconds: float = 1.0
    video_frame_interval_ms: int = 125
    video_quality: int = 72
    debug_window: bool = False


@dataclass(slots=True)
class DetectionConfig:
    min_joint_confidence: float = 0.35
    min_person_confidence: float = 0.45
    event_cooldown_ms: int = 900
    sensitivity: int = 70
    exercise_keys: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_EXERCISE_KEYS),
    )

    def strictness_scale(self) -> float:
        # Map 0..100 into a stable 0.8..1.2 range for threshold tuning.
        return 0.8 + (max(0, min(100, self.sensitivity)) / 100.0) * 0.4


@dataclass(slots=True)
class BackendConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
