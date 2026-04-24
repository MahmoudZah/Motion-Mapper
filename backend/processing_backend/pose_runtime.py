from __future__ import annotations

import contextlib
import io
import os
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .config import ModelConfig
from .model_manager import ensure_provider_model


NUM_KEYPOINTS = 17
# Map MediaPipe Pose Landmarker's 33-point schema into the 17-keypoint
# COCO/YOLO layout used everywhere else in the app.
# MediaPipe indices used here:
# 0 nose, 2 left_eye, 5 right_eye, 7 left_ear, 8 right_ear,
# 11/12 shoulders, 13/14 elbows, 15/16 wrists,
# 23/24 hips, 25/26 knees, 27/28 ankles.
MEDIAPIPE_TO_COCO = (
    (0, 0),
    (1, 2),
    (2, 5),
    (3, 7),
    (4, 8),
    (5, 11),
    (6, 12),
    (7, 13),
    (8, 14),
    (9, 15),
    (10, 16),
    (11, 23),
    (12, 24),
    (13, 25),
    (14, 26),
    (15, 27),
    (16, 28),
)


@dataclass(slots=True)
class PersonPose:
    keypoints: np.ndarray
    scores: np.ndarray

    @property
    def mean_confidence(self) -> float:
        if self.scores.size == 0:
            return 0.0
        return float(np.mean(self.scores))


@dataclass(slots=True)
class PoseResult:
    people: list[PersonPose]
    inference_ms: float

    @property
    def primary_person(self) -> PersonPose | None:
        if not self.people:
            return None
        return max(self.people, key=lambda person: person.mean_confidence)


class BasePoseRuntime:
    provider = "unknown"

    def __init__(self, config: ModelConfig):
        self.config = config
        self.asset_info: dict[str, str | bool] | None = None

    def initialize(self) -> None:
        raise NotImplementedError

    def predict(self, frame: np.ndarray, timestamp_ms: int | None = None) -> PoseResult:
        raise NotImplementedError

    def close(self) -> None:
        return

    def describe(self) -> dict[str, str | bool | int | float | None]:
        return {
            "provider": self.provider,
            "weights": self.config.weights,
            "taskPath": self.config.mediapipe_task_path,
            "imageSize": self.config.image_size,
            "confidence": self.config.confidence,
            "asset": self.asset_info,
        }


class YoloPoseRuntime(BasePoseRuntime):
    provider = "yolo"

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self._model = None

    def initialize(self) -> None:
        self.asset_info = ensure_provider_model(self.config)
        config_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".ultralytics"))
        os.makedirs(config_dir, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", config_dir)
        os.environ.setdefault("YOLO_VERBOSE", "false")

        from ultralytics import YOLO

        silent_buffer = io.StringIO()
        with contextlib.redirect_stdout(silent_buffer), contextlib.redirect_stderr(silent_buffer):
            self._model = YOLO(self.config.weights)
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            self._model.predict(
                dummy,
                imgsz=self.config.image_size,
                conf=self.config.confidence,
                verbose=False,
            )

    def predict(self, frame: np.ndarray, timestamp_ms: int | None = None) -> PoseResult:
        del timestamp_ms
        if self._model is None:
            raise RuntimeError("YOLO runtime used before initialization.")

        started_at = time.perf_counter()
        results = self._model.predict(
            frame,
            imgsz=self.config.image_size,
            conf=self.config.confidence,
            verbose=False,
        )
        inference_ms = (time.perf_counter() - started_at) * 1000.0

        people: list[PersonPose] = []
        for result in results:
            if result.keypoints is None:
                continue
            kp_data = result.keypoints.data.cpu().numpy()
            for person in kp_data:
                keypoints = person[:, :2]
                scores = person[:, 2]
                if keypoints.shape != (NUM_KEYPOINTS, 2):
                    continue
                people.append(PersonPose(keypoints=keypoints, scores=scores))

        return PoseResult(people=people, inference_ms=inference_ms)

    def close(self) -> None:
        self._model = None


class MediaPipePoseRuntime(BasePoseRuntime):
    provider = "mediapipe"

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self._landmarker = None
        self._mp = None
        self._last_timestamp_ms = -1

    def initialize(self) -> None:
        self.asset_info = ensure_provider_model(self.config)
        import mediapipe as mp

        BaseOptions = mp.tasks.BaseOptions
        PoseLandmarker = mp.tasks.vision.PoseLandmarker
        PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
        RunningMode = mp.tasks.vision.RunningMode

        options = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.config.mediapipe_task_path),
            running_mode=RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=self.config.confidence,
            min_pose_presence_confidence=self.config.confidence,
            min_tracking_confidence=self.config.confidence,
            output_segmentation_masks=False,
        )
        self._landmarker = PoseLandmarker.create_from_options(options)
        self._mp = mp

    def predict(self, frame: np.ndarray, timestamp_ms: int | None = None) -> PoseResult:
        if self._landmarker is None or self._mp is None:
            raise RuntimeError("MediaPipe runtime used before initialization.")

        timestamp = 0 if timestamp_ms is None else int(timestamp_ms)
        if timestamp <= self._last_timestamp_ms:
            timestamp = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb_frame)

        started_at = time.perf_counter()
        result = self._landmarker.detect_for_video(mp_image, timestamp)
        inference_ms = (time.perf_counter() - started_at) * 1000.0

        people: list[PersonPose] = []
        frame_h, frame_w = frame.shape[:2]
        for landmarks in (result.pose_landmarks or []):
            keypoints = np.zeros((NUM_KEYPOINTS, 2), dtype=np.float32)
            scores = np.zeros((NUM_KEYPOINTS,), dtype=np.float32)
            for coco_index, mp_index in MEDIAPIPE_TO_COCO:
                landmark = landmarks[mp_index]
                keypoints[coco_index] = self._landmark_to_pixels(landmark, frame_w, frame_h)
                scores[coco_index] = self._landmark_score(landmark)
            people.append(PersonPose(keypoints=keypoints, scores=scores))

        return PoseResult(people=people, inference_ms=inference_ms)

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
        self._landmarker = None
        self._mp = None

    def _landmark_to_pixels(self, landmark, frame_w: int, frame_h: int) -> np.ndarray:
        x = float(np.clip(float(landmark.x), 0.0, 1.0)) * frame_w
        y = float(np.clip(float(landmark.y), 0.0, 1.0)) * frame_h
        return np.array([x, y], dtype=np.float32)

    def _landmark_score(self, landmark) -> float:
        visibility = getattr(landmark, "visibility", None)
        presence = getattr(landmark, "presence", None)
        if visibility is not None:
            return float(np.clip(float(visibility), 0.0, 1.0))
        if presence is not None:
            return float(np.clip(float(presence), 0.0, 1.0))
        return 0.0


def create_pose_runtime(config: ModelConfig) -> BasePoseRuntime:
    if config.provider == "mediapipe":
        return MediaPipePoseRuntime(config)
    return YoloPoseRuntime(config)
