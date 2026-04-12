from __future__ import annotations

import json
import math
import sys
import time
from typing import Any

import numpy as np


def now_ms() -> int:
    return int(time.time() * 1000)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def emit_event(payload: dict[str, Any]) -> None:
    print(json.dumps(_json_safe(payload), separators=(",", ":"), allow_nan=False), flush=True)
