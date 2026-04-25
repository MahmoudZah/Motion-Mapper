from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from .config import ModelConfig


YOLO_DEFAULT_URL = "https://github.com/ultralytics/assets/releases/latest/download/yolov8n-pose.pt"
MEDIAPIPE_DEFAULT_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)


def ensure_provider_model(config: ModelConfig) -> dict[str, str | bool]:
    if config.provider == "mediapipe":
        model_path = Path(config.mediapipe_task_path)
        url = config.mediapipe_task_url
        label = "MediaPipe"
    else:
        model_path = Path(config.weights)
        url = config.yolo_weights_url
        label = "YOLO"

    downloaded = _ensure_file(model_path, url)
    return {
        "provider": config.provider,
        "path": str(model_path),
        "downloaded": downloaded,
        "sourceUrl": url,
        "label": label,
    }


def _ensure_file(path: Path, url: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return False

    temp_path = path.with_suffix(path.suffix + ".download")
    try:
        with urllib.request.urlopen(url) as response, temp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return True
