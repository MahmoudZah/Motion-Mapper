import React, { useEffect, useRef, useState } from 'react';
import { Camera, LoaderCircle } from 'lucide-react';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

const CONNECTIONS = [
  [0, 1], [0, 2], [1, 3], [2, 4],
  [5, 7], [7, 9],
  [6, 8], [8, 10],
  [5, 6],
  [5, 11], [6, 12],
  [11, 12],
  [11, 13], [13, 15],
  [12, 14], [14, 16],
];

export default function PoseCameraViewport({
  poseFrame,
  badge,
  emptyTitle,
  emptySubtitle,
  footer,
  hud,
}) {
  const videoRef = useRef(null);
  const captureCanvasRef = useRef(null);
  const captureContextRef = useRef(null);
  const streamRef = useRef(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState('');

  useEffect(() => {
    if (!api?.setPreviewActive) return undefined;
    api.setPreviewActive(true).catch(() => {});
    return () => {
      api.setPreviewActive(false).catch(() => {});
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const MAX_RETRIES = 3;
    const RETRY_DELAY_MS = 1500;

    async function attemptCameraAccess() {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: 'user',
          width: { ideal: 640 },
          height: { ideal: 360 },
        },
      });
      return stream;
    }

    async function startCamera() {
      if (!navigator?.mediaDevices?.getUserMedia) {
        setCameraError('This environment does not expose webcam access.');
        return;
      }

      let lastError = null;
      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (cancelled) return;
        try {
          const stream = await attemptCameraAccess();
          if (cancelled) {
            stream.getTracks().forEach((track) => track.stop());
            return;
          }
          streamRef.current = stream;
          const video = videoRef.current;
          if (video) {
            video.srcObject = stream;
            video.onloadedmetadata = async () => {
              try {
                await video.play();
              } catch {
                // autoplay failures are rare in Electron, but the preview still exists.
              }
              if (!cancelled) {
                setCameraReady(true);
              }
            };
          }
          return; // success – exit the retry loop
        } catch (error) {
          lastError = error;
          console.warn(`[Camera] Attempt ${attempt}/${MAX_RETRIES} failed:`, error?.message);
          if (attempt < MAX_RETRIES && !cancelled) {
            await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
          }
        }
      }

      // All retries exhausted
      if (!cancelled) {
        setCameraError(
          lastError?.message || 'Unable to access the camera.'
        );
      }
    }

    startCamera();

    return () => {
      cancelled = true;
      setCameraReady(false);
      const stream = streamRef.current;
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!cameraReady || !api) return undefined;
    let cancelled = false;
    let busy = false;
    let timer = null;
    const frameDelayMs = 16;
    const jpegQuality = 0.55;

    const sendFrame = async () => {
      if (cancelled) return;
      const video = videoRef.current;
      const canvas = captureCanvasRef.current;
      if (!video || !canvas || video.readyState < 2 || busy) {
        timer = window.setTimeout(sendFrame, frameDelayMs);
        return;
      }

      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) {
        timer = window.setTimeout(sendFrame, frameDelayMs);
        return;
      }

      busy = true;
      if (canvas.width !== width) {
        canvas.width = width;
      }
      if (canvas.height !== height) {
        canvas.height = height;
      }
      let context = captureContextRef.current;
      if (!context) {
        context = canvas.getContext('2d', { willReadFrequently: true });
        captureContextRef.current = context;
      }
      if (!context) {
        busy = false;
        timer = window.setTimeout(sendFrame, frameDelayMs);
        return;
      }
      context.drawImage(video, 0, 0, width, height);
      const image = canvas.toDataURL('image/jpeg', jpegQuality);

      try {
        await api.processVideoFrame({
          image,
          width,
          height,
          timestamp: Date.now(),
        });
      } catch {
        // The UI already reflects backend failures via missing overlays.
      } finally {
        busy = false;
        if (!cancelled) {
          timer = window.setTimeout(sendFrame, frameDelayMs);
        }
      }
    };

    sendFrame();

    return () => {
      cancelled = true;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [cameraReady]);

  const width = poseFrame?.width || 640;
  const height = poseFrame?.height || 360;
  const keypoints = poseFrame?.keypoints || [];
  const scores = poseFrame?.scores || [];

  return (
    <div className="relative h-full w-full overflow-hidden bg-panel">
      <video
        ref={videoRef}
        muted
        playsInline
        className="absolute inset-0 h-full w-full object-cover"
        style={{ transform: 'scaleX(-1)' }}
      />
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="absolute inset-0 h-full w-full"
        preserveAspectRatio="xMidYMid slice"
        style={{ transform: 'scaleX(-1)' }}
      >
        {CONNECTIONS.map(([start, end]) => {
          if (!keypoints[start] || !keypoints[end]) return null;
          if ((scores[start] || 0) < 0.35 || (scores[end] || 0) < 0.35) return null;
          return (
            <line
              key={`${start}-${end}`}
              x1={keypoints[start][0]}
              y1={keypoints[start][1]}
              x2={keypoints[end][0]}
              y2={keypoints[end][1]}
              stroke="rgba(200,40,40,0.9)"
              strokeWidth="3"
              strokeLinecap="round"
            />
          );
        })}
        {keypoints.map((coords, index) => {
          if (!coords || (scores[index] || 0) < 0.35) return null;
          return (
            <circle
              key={index}
              cx={coords[0]}
              cy={coords[1]}
              r={index >= 5 ? 5 : 4}
              fill="white"
              stroke="rgba(200,40,40,0.95)"
              strokeWidth="2"
            />
          );
        })}
      </svg>

      {!cameraReady && !cameraError && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center">
            <LoaderCircle size={36} className="text-neon/70 animate-spin mx-auto mb-3" />
            <p className="text-xs text-gray-400">Starting camera preview</p>
          </div>
        </div>
      )}

      {cameraError && (
        <div className="absolute inset-0 flex items-center justify-center px-6 text-center">
          <div>
            <Camera size={42} className="text-warn mx-auto mb-3" />
            <p className="text-sm text-warn">Camera access failed</p>
            <p className="text-xs text-gray-500 mt-1">{cameraError}</p>
          </div>
        </div>
      )}

      {!cameraError && cameraReady && !poseFrame?.poseDetected && (
        <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center px-4">
          <p className="text-xs text-gray-300">{emptyTitle}</p>
          <p className="text-[10px] text-gray-500 mt-1">{emptySubtitle}</p>
        </div>
      )}

      {badge}
      {hud}
      {footer}
      <canvas ref={captureCanvasRef} className="hidden" />
      <div className="absolute inset-0 scanline pointer-events-none" />
    </div>
  );
}
