from __future__ import annotations

import math
from typing import Iterable

import numpy as np


KEYPOINT_INDEX = {
    "nose": 0,
    "left_eye": 1,
    "right_eye": 2,
    "left_ear": 3,
    "right_ear": 4,
    "left_shoulder": 5,
    "right_shoulder": 6,
    "left_elbow": 7,
    "right_elbow": 8,
    "left_wrist": 9,
    "right_wrist": 10,
    "left_hip": 11,
    "right_hip": 12,
    "left_knee": 13,
    "right_knee": 14,
    "left_ankle": 15,
    "right_ankle": 16,
}


def point(keypoints: np.ndarray, index: int) -> np.ndarray:
    return keypoints[index]


def distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def midpoint(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a + b) / 2.0


def mean_or_none(values: Iterable[float]) -> float | None:
    usable = [float(v) for v in values if v is not None and not math.isnan(v)]
    if not usable:
        return None
    return float(sum(usable) / len(usable))


def angle_degrees(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ab = a - b
    cb = c - b
    denom = np.linalg.norm(ab) * np.linalg.norm(cb)
    if denom <= 1e-6:
        return float("nan")
    cosine = float(np.clip(np.dot(ab, cb) / denom, -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


def segment_angle_from_horizontal(a: np.ndarray, b: np.ndarray) -> float:
    delta = b - a
    return float(abs(np.degrees(np.arctan2(delta[1], delta[0]))))


def normalized_distance(a: np.ndarray, b: np.ndarray, scale: float) -> float | None:
    if scale <= 1e-6:
        return None
    return distance(a, b) / scale
