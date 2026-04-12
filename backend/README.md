# Processing Backend

Local Python backend for Motion Mapper.

## What it does

- Opens the webcam
- Runs MediaPipe Pose inference in realtime
- Maps pose landmarks to exercise events
- Emits newline-delimited JSON on stdout
- Accepts simple control commands on stdin

## Current exercise outputs

- `squats`
- `jumpingJacks`
- `rightDumbbellRaise`
- `leftDumbbellRaise`

## Install

```powershell
pip install -r requirements.txt
```

MediaPipe Tasks also needs a local pose model bundle, for example:

- `models/pose_landmarker_full.task`

## Run directly

```powershell
python main.py --sensitivity 70
```

Optional flags:

- `--camera-index 0`
- `--width 640`
- `--height 480`
- `--model-asset models/pose_landmarker_full.task`
- `--confidence 0.5`
- `--presence-confidence 0.5`
- `--tracking-confidence 0.5`
- `--emit-pose`
- `--show`

## Output protocol

Primary detection event:

```json
{
  "type": "exercise_detection",
  "exercise": "squats",
  "status": "valid",
  "message": "Squat detected: valid rep",
  "angle": 158.4,
  "confidence": 0.91,
  "phase": "standing",
  "repCount": 2,
  "key": "Space",
  "inferenceMs": 18.6,
  "timestamp": 1775472000000
}
```

Other events include:

- `backend_ready`
- `camera_opened`
- `video_frame`
- `tracking_idle`
- `calibration_result`
- `config_updated`
- `command_ack`
- `shutdown`

## Control commands

Send newline-delimited JSON on stdin:

```json
{"type":"set_sensitivity","sensitivity":80,"requestId":"req-1"}
{"type":"calibrate","requestId":"req-2"}
{"type":"shutdown","requestId":"req-3"}
```

## Electron integration

`Motion-Mapper/main.js` now spawns this backend as a local child process, forwards `exercise_detection` events to the renderer, and keeps keypress injection in Electron.
