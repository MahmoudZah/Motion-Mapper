from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from .config import ModelConfig


NUM_KEYPOINTS = 17
BODY_CONFIDENCE_START_INDEX = 5

COCO_TO_MEDIAPIPE = {
    0: 0,
    1: 2,
    2: 5,
    3: 7,
    4: 8,
    5: 11,
    6: 12,
    7: 13,
    8: 14,
    9: 15,
    10: 16,
    11: 23,
    12: 24,
    13: 25,
    14: 26,
    15: 27,
    16: 28,
}


@dataclass(slots=True)
class PersonPose:
    keypoints: np.ndarray
    scores: np.ndarray

    @property
    def mean_confidence(self) -> float:
        if self.scores.size == 0:
            return 0.0
        body_scores = self.scores[BODY_CONFIDENCE_START_INDEX:]
        if body_scores.size == 0:
            return 0.0
        return float(np.mean(body_scores))


@dataclass(slots=True)
class PoseResult:
    people: list[PersonPose]
    inference_ms: float

    @property
    def primary_person(self) -> PersonPose | None:
        if not self.people:
            return None
        return max(self.people, key=lambda person: person.mean_confidence)


class MediaPipePoseRuntime:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._landmarker = None
        self._last_timestamp_ms = -1

    def initialize(self) -> None:
        model_path = Path(self.config.model_asset_path).resolve()
        if not model_path.is_file():
            raise FileNotFoundError(
                f"MediaPipe pose model not found at '{model_path}'. "
                "Place a pose_landmarker.task file there or pass --model-asset."
            )

        base_options = mp.tasks.BaseOptions(model_asset_path=str(model_path))
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=float(self.config.confidence),
            min_pose_presence_confidence=float(self.config.presence_confidence),
            min_tracking_confidence=float(self.config.tracking_confidence),
            output_segmentation_masks=False,
        )
        self._landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)

        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self.predict(dummy, 0)

    def predict(self, frame: np.ndarray, timestamp_ms: int | None = None) -> PoseResult:
        if self._landmarker is None:
            raise RuntimeError("MediaPipe runtime used before initialization.")

        started_at = time.perf_counter()
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb_frame.flags.writeable = False
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        ts = self._normalize_timestamp(timestamp_ms)
        result = self._landmarker.detect_for_video(mp_image, ts)
        inference_ms = (time.perf_counter() - started_at) * 1000.0

        if not result.pose_landmarks:
            return PoseResult(people=[], inference_ms=inference_ms)

        frame_h, frame_w = frame.shape[:2]
        people: list[PersonPose] = []
        for landmarks in result.pose_landmarks:
            keypoints = np.zeros((NUM_KEYPOINTS, 2), dtype=np.float32)
            scores = np.zeros((NUM_KEYPOINTS,), dtype=np.float32)
            for coco_index, mp_index in COCO_TO_MEDIAPIPE.items():
                landmark = landmarks[mp_index]
                keypoints[coco_index] = [landmark.x * frame_w, landmark.y * frame_h]
                visibility = float(getattr(landmark, "visibility", 0.0))
                presence = float(getattr(landmark, "presence", visibility))
                scores[coco_index] = float(max(0.0, min(1.0, max(visibility, presence))))
            people.append(PersonPose(keypoints=keypoints, scores=scores))

        return PoseResult(people=people, inference_ms=inference_ms)

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
        self._landmarker = None

    def _normalize_timestamp(self, timestamp_ms: int | None) -> int:
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        normalized = int(timestamp_ms)
        if normalized <= self._last_timestamp_ms:
            normalized = self._last_timestamp_ms + 1
        self._last_timestamp_ms = normalized
        return normalized
