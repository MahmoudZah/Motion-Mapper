import React, { useEffect, useRef, useState, useCallback } from 'react';
import { X, ChevronDown, ChevronUp, Gamepad2 } from 'lucide-react';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

const CONNECTIONS = [
  [0, 1], [0, 2], [1, 3], [2, 4],
  [5, 7], [7, 9], [6, 8], [8, 10], [5, 6],
  [5, 11], [6, 12], [11, 12],
  [11, 13], [13, 15], [12, 14], [14, 16],
];

export default function OverlayView() {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const captureCanvasRef = useRef(null);
  const captureContextRef = useRef(null);

  const [poseFrame, setPoseFrame] = useState(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const toggleCollapse = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      api?.setOverlayCollapsed(next).catch(() => {});
      return next;
    });
  }, []);

  useEffect(() => {
    if (!api?.setPreviewActive) return undefined;
    api.setPreviewActive(true).catch(() => {});
    return () => { api.setPreviewActive(false).catch(() => {}); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    navigator.mediaDevices?.getUserMedia({
      audio: false,
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 360 } },
    }).then((stream) => {
      if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
      streamRef.current = stream;
      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        video.onloadedmetadata = () => {
          video.play().catch(() => {});
          if (!cancelled) setCameraReady(true);
        };
      }
    }).catch(() => {});
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!cameraReady || !api) return undefined;
    let cancelled = false;
    let busy = false;
    let timer = null;
    const frameDelayMs = 16;
    const jpegQuality = 0.5;

    const sendFrame = async () => {
      if (cancelled) return;
      const video = videoRef.current;
      const canvas = captureCanvasRef.current;
      if (!video || !canvas || video.readyState < 2 || busy) {
        timer = window.setTimeout(sendFrame, frameDelayMs);
        return;
      }
      const w = video.videoWidth;
      const h = video.videoHeight;
      if (!w || !h) { timer = window.setTimeout(sendFrame, frameDelayMs); return; }

      busy = true;
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      let ctx = captureContextRef.current;
      if (!ctx) {
        ctx = canvas.getContext('2d', { willReadFrequently: true });
        captureContextRef.current = ctx;
      }
      if (!ctx) { busy = false; timer = window.setTimeout(sendFrame, frameDelayMs); return; }

      ctx.drawImage(video, 0, 0, w, h);
      const image = canvas.toDataURL('image/jpeg', jpegQuality);
      try {
        await api.processVideoFrame({ image, width: w, height: h, timestamp: Date.now() });
      } catch { /* degrade gracefully */ } finally {
        busy = false;
        if (!cancelled) timer = window.setTimeout(sendFrame, frameDelayMs);
      }
    };

    sendFrame();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [cameraReady]);

  useEffect(() => {
    if (!api) return undefined;
    return api.onPoseFrame(setPoseFrame);
  }, []);

  const frameWidth = poseFrame?.width || 640;
  const frameHeight = poseFrame?.height || 360;
  const keypoints = poseFrame?.keypoints || [];
  const scores = poseFrame?.scores || [];
  const poseDetected = Boolean(poseFrame?.poseDetected);

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col rounded-xl" style={{ background: 'transparent' }}>
      {/* Drag bar */}
      <div
        className="shrink-0 h-[30px] flex items-center justify-between px-2 rounded-t-xl"
        style={{ WebkitAppRegion: 'drag', background: 'rgba(10,13,18,0.9)' }}
      >
        <div className="flex items-center gap-1.5" style={{ WebkitAppRegion: 'no-drag' }}>
          <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${poseDetected ? 'bg-neon animate-pulse' : 'bg-gray-600'}`} />
          <Gamepad2 size={10} className="text-neon/70" />
          <span className="text-[9px] text-neon/70 font-display uppercase tracking-widest leading-none">Gamecha</span>
        </div>
        <div className="flex items-center gap-0.5" style={{ WebkitAppRegion: 'no-drag' }}>
          <button onClick={toggleCollapse} className="w-5 h-5 flex items-center justify-center rounded hover:bg-white/10 transition-colors">
            {collapsed ? <ChevronUp size={10} className="text-gray-400" /> : <ChevronDown size={10} className="text-gray-400" />}
          </button>
          <button onClick={() => api?.closeOverlays()} className="w-5 h-5 flex items-center justify-center rounded hover:bg-red-500/30 transition-colors">
            <X size={10} className="text-gray-400" />
          </button>
        </div>
      </div>

      {/* Camera body */}
      {!collapsed && (
        <div className="flex-1 relative overflow-hidden rounded-b-xl">
          <video
            ref={videoRef}
            muted
            playsInline
            className="absolute inset-0 w-full h-full object-cover"
            style={{ transform: 'scaleX(-1)' }}
          />
          <div className="absolute inset-0 pointer-events-none" style={{ background: 'rgba(10,13,18,0.2)' }} />

          {cameraReady && (
            <svg
              viewBox={`0 0 ${frameWidth} ${frameHeight}`}
              className="absolute inset-0 w-full h-full pointer-events-none"
              preserveAspectRatio="xMidYMid slice"
              style={{ transform: 'scaleX(-1)' }}
            >
              {CONNECTIONS.map(([s, e]) => {
                if (!keypoints[s] || !keypoints[e]) return null;
                if ((scores[s] || 0) < 0.35 || (scores[e] || 0) < 0.35) return null;
                return (
                  <line key={`${s}-${e}`}
                    x1={keypoints[s][0]} y1={keypoints[s][1]}
                    x2={keypoints[e][0]} y2={keypoints[e][1]}
                    stroke="rgba(200,40,40,0.9)" strokeWidth="3" strokeLinecap="round"
                  />
                );
              })}
              {keypoints.map((coords, i) => {
                if (!coords || (scores[i] || 0) < 0.35) return null;
                return (
                  <circle key={i} cx={coords[0]} cy={coords[1]}
                    r={i >= 5 ? 5 : 4} fill="rgba(255,255,255,0.9)"
                    stroke="rgba(200,40,40,0.95)" strokeWidth="2"
                  />
                );
              })}
            </svg>
          )}

          {cameraReady && !poseDetected && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <p className="text-[9px] text-gray-500">Move into view…</p>
            </div>
          )}
        </div>
      )}

      <canvas ref={captureCanvasRef} className="hidden" />
    </div>
  );
}
