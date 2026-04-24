from __future__ import annotations

import argparse
import base64
import json
import signal
import sys
import threading
from dataclasses import asdict
from queue import Empty, Queue

import cv2
import numpy as np

from .config import BackendConfig
from .exercise_mapper import ExerciseMapper
from .geometry import KEYPOINT_INDEX
from .pose_runtime import PersonPose, create_pose_runtime
from .protocol import emit_event, log, now_ms


class MotionProcessingService:
    def __init__(self, config: BackendConfig):
        self.config = config
        self.runtime = create_pose_runtime(config.model)
        self.mapper = ExerciseMapper(config.detection)
        self._running = True
        self._commands: Queue[dict] = Queue()
        self._control_thread: threading.Thread | None = None
        self._latest_pose: PersonPose | None = None
        self._latest_frame_shape: tuple[int, int] | None = None

    def stop(self, *_args) -> None:
        self._running = False

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        self._start_control_reader()

        log("[backend] Initializing YOLO pose runtime...")
        self.runtime.initialize()
        log("[backend] YOLO pose runtime ready.")

        emit_event(
            {
                "type": "backend_ready",
                "mode": "frontend_camera",
                "camera": asdict(self.config.camera),
                "model": self.runtime.describe(),
                "timestamp": now_ms(),
            }
        )

        try:
            while self._running:
                try:
                    command = self._commands.get(timeout=0.1)
                except Empty:
                    continue
                self._handle_command(command, now_ms())
        finally:
            self.runtime.close()
            emit_event({"type": "shutdown", "timestamp": now_ms()})

        return 0

    def _start_control_reader(self) -> None:
        if sys.stdin.closed:
            return
        self._control_thread = threading.Thread(
            target=self._control_reader,
            name="processing-backend-control",
            daemon=True,
        )
        self._control_thread.start()

    def _control_reader(self) -> None:
        while self._running:
            line = sys.stdin.readline()
            if line == "":
                return
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                log(f"[backend] Ignoring malformed control payload: {raw!r}")
                continue
            self._commands.put(payload)

    def _handle_command(self, command: dict, timestamp_ms: int) -> None:
        command_type = command.get("type")
        request_id = command.get("requestId")

        if command_type == "shutdown":
            self._running = False
            emit_event(
                {
                    "type": "command_ack",
                    "requestId": request_id,
                    "command": "shutdown",
                    "ok": True,
                    "timestamp": timestamp_ms,
                }
            )
            return

        if command_type == "set_sensitivity":
            sensitivity = int(command.get("sensitivity", self.config.detection.sensitivity))
            self.config.detection.sensitivity = sensitivity
            emit_event(
                {
                    "type": "config_updated",
                    "requestId": request_id,
                    "ok": True,
                    "sensitivity": sensitivity,
                    "timestamp": timestamp_ms,
                }
            )
            return

        if command_type == "calibrate":
            calibration = self._build_calibration_payload()
            emit_event(
                {
                    "type": "calibration_result",
                    "requestId": request_id,
                    "ok": calibration["calibrated"],
                    "calibration": calibration,
                    "timestamp": timestamp_ms,
                }
            )
            return

        if command_type == "process_frame":
            self._process_frame(command, timestamp_ms)
            return

        emit_event(
            {
                "type": "command_ack",
                "requestId": request_id,
                "command": command_type,
                "ok": False,
                "message": f"Unknown command '{command_type}'.",
                "timestamp": timestamp_ms,
            }
        )

    def _process_frame(self, command: dict, timestamp_ms: int) -> None:
        request_id = command.get("requestId")
        frame = self._decode_frame_payload(command.get("image"))
        if frame is None:
            emit_event(
                {
                    "type": "pose_frame",
                    "requestId": request_id,
                    "timestamp": timestamp_ms,
                    "ok": False,
                    "poseDetected": False,
                    "message": "Frame payload could not be decoded.",
                    "guidance": self.mapper.summarize_pose(None),
                }
            )
            return

        result = self.runtime.predict(frame, timestamp_ms=int(command.get("timestamp", timestamp_ms)))
        primary = result.primary_person
        self._latest_pose = primary
        self._latest_frame_shape = frame.shape[:2]
        squat_events = self.mapper.evaluate(
            pose=primary,
            inference_ms=result.inference_ms,
            timestamp_ms=timestamp_ms,
        )
        guidance = self.mapper.summarize_pose(primary)

        emit_event(
            {
                "type": "pose_frame",
                "requestId": request_id,
                "timestamp": int(command.get("timestamp", timestamp_ms)),
                "ok": True,
                "poseDetected": primary is not None,
                "inferenceMs": round(result.inference_ms, 2),
                "meanConfidence": round(primary.mean_confidence, 3) if primary is not None else 0.0,
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "keypoints": primary.keypoints.round(2).tolist() if primary is not None else [],
                "scores": primary.scores.round(4).tolist() if primary is not None else [],
                "guidance": guidance,
            }
        )

        for event in squat_events:
            emit_event(event)

    def _decode_frame_payload(self, image_payload: str | None):
        if not image_payload:
            return None
        try:
            payload = image_payload.split(",", 1)[1] if "," in image_payload else image_payload
            raw = base64.b64decode(payload)
            buffer = np.frombuffer(raw, dtype=np.uint8)
            return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        except Exception as exc:
            log(f"[backend] Failed to decode frame payload: {exc}")
            return None

    def _build_calibration_payload(self) -> dict:
        if self._latest_pose is None or self._latest_frame_shape is None:
            return {
                "calibrated": False,
                "sensitivity": self.config.detection.sensitivity,
                "neutralPose": None,
                "message": "No visible person available for calibration.",
            }

        frame_h, frame_w = self._latest_frame_shape
        if frame_w <= 0 or frame_h <= 0:
            return {
                "calibrated": False,
                "sensitivity": self.config.detection.sensitivity,
                "neutralPose": None,
                "message": "Frame dimensions are invalid for calibration.",
            }

        point_names = {
            "leftShoulder": KEYPOINT_INDEX["left_shoulder"],
            "rightShoulder": KEYPOINT_INDEX["right_shoulder"],
            "leftHip": KEYPOINT_INDEX["left_hip"],
            "rightHip": KEYPOINT_INDEX["right_hip"],
            "leftKnee": KEYPOINT_INDEX["left_knee"],
            "rightKnee": KEYPOINT_INDEX["right_knee"],
        }

        neutral_pose: dict[str, dict[str, float]] = {}
        for name, index in point_names.items():
            coords = self._latest_pose.keypoints[index]
            neutral_pose[name] = {
                "x": round(float(coords[0]) / frame_w, 4),
                "y": round(float(coords[1]) / frame_h, 4),
                "confidence": round(float(self._latest_pose.scores[index]), 4),
            }

        min_confidence = min(point["confidence"] for point in neutral_pose.values())
        return {
            "calibrated": min_confidence >= self.config.detection.min_joint_confidence,
            "sensitivity": self.config.detection.sensitivity,
            "neutralPose": neutral_pose,
            "message": "Neutral pose captured." if min_confidence >= self.config.detection.min_joint_confidence else "Hold still so all calibration joints are visible.",
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime motion processing backend.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--provider", choices=["yolo", "mediapipe"], default="yolo")
    parser.add_argument("--weights", default="models/yolov8n-pose.pt")
    parser.add_argument("--task-model", default="models/pose_landmarker_full.task")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=320)
    parser.add_argument("--sensitivity", type=int, default=70)
    parser.add_argument("--emit-pose", action="store_true")
    parser.add_argument("--show", action="store_true")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> BackendConfig:
    config = BackendConfig()
    config.camera.index = args.camera_index
    config.camera.width = args.width
    config.camera.height = args.height
    config.model.provider = args.provider
    config.model.weights = args.weights
    config.model.mediapipe_task_path = args.task_model
    config.model.confidence = args.confidence
    config.model.image_size = args.imgsz
    config.detection.sensitivity = args.sensitivity
    config.stream.emit_pose_events = args.emit_pose
    config.stream.debug_window = args.show
    return config


def main() -> int:
    args = parse_args()
    service = MotionProcessingService(build_config(args))
    return service.run()
