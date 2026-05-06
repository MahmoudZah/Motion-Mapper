import React, { useEffect, useRef } from 'react';
import { Camera, LoaderCircle } from 'lucide-react';
import { useCameraStream } from './CameraProvider';

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

/**
 * PoseCameraViewport – display-only component.
 *
 * Camera acquisition, frame sending, and backend preview state are all
 * managed by the parent CameraProvider.  This component simply attaches
 * the shared MediaStream to its own <video> element and draws the pose
 * skeleton overlay.
 */
export default function PoseCameraViewport({
  poseFrame,
  badge,
  emptyTitle,
  emptySubtitle,
  footer,
  hud,
}) {
  const videoRef = useRef(null);
  const { stream, cameraReady, cameraError } = useCameraStream();

  // Attach the shared MediaStream to this viewport's <video> element
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !stream) return;

    video.srcObject = stream;
    video.onloadedmetadata = async () => {
      try { await video.play(); } catch { /* autoplay edge case */ }
    };
  }, [stream]);

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
      <div className="absolute inset-0 scanline pointer-events-none" />
    </div>
  );
}
