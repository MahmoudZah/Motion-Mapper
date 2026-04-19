from __future__ import annotations

import contextlib
import io
import os
import time
from dataclasses import dataclass

import numpy as np

from .config import ModelConfig


NUM_KEYPOINTS = 17


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


class YoloPoseRuntime:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._model = None

    def initialize(self) -> None:
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
