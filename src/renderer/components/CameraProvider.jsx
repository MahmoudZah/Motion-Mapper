import React, { createContext, useContext, useEffect, useRef, useState } from 'react';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

const CameraContext = createContext({
  stream: null,
  cameraReady: false,
  cameraError: '',
});

export function useCameraStream() {
  return useContext(CameraContext);
}

/**
 * CameraProvider – lives at the App level and persists across tab switches.
 *
 * Props:
 *   active  – true when the Calibrate or Live View tab is visible.
 *             Controls frame-sending and setPreviewActive, but the camera
 *             stream itself stays alive once acquired so that switching
 *             between those two tabs is instant (no YOLO re-init).
 */
export default function CameraProvider({ active, children }) {
  const streamRef = useRef(null);
  const videoRef = useRef(null);
  const captureCanvasRef = useRef(null);
  const captureContextRef = useRef(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState('');
  const [stream, setStream] = useState(null);

  // ── Notify main process whether camera preview is needed ──
  useEffect(() => {
    if (!api?.setPreviewActive) return;
    api.setPreviewActive(active).catch(() => {});

    // On unmount (or when active flips to false), tell backend we're done
    if (!active) return;
    return () => {
      api.setPreviewActive(false).catch(() => {});
    };
  }, [active]);

  // ── Acquire camera stream once (first time active becomes true) ──
  useEffect(() => {
    if (!active || streamRef.current) return;

    let cancelled = false;
    const MAX_RETRIES = 3;
    const RETRY_DELAY_MS = 1500;

    async function startCamera() {
      if (!navigator?.mediaDevices?.getUserMedia) {
        setCameraError('This environment does not expose webcam access.');
        return;
      }

      let lastError = null;
      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (cancelled) return;
        try {
          const s = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 360 } },
          });
          if (cancelled) {
            s.getTracks().forEach((t) => t.stop());
            return;
          }

          streamRef.current = s;
          setStream(s);

          const video = videoRef.current;
          if (video) {
            video.srcObject = s;
            video.onloadedmetadata = async () => {
              try { await video.play(); } catch { /* autoplay edge case */ }
              if (!cancelled) setCameraReady(true);
            };
          }
          return;
        } catch (error) {
          lastError = error;
          console.warn(`[Camera] Attempt ${attempt}/${MAX_RETRIES} failed:`, error?.message);
          if (attempt < MAX_RETRIES && !cancelled) {
            await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
          }
        }
      }

      if (!cancelled) {
        setCameraError(lastError?.message || 'Unable to access the camera.');
      }
    }

    startCamera();
    return () => { cancelled = true; };
  }, [active]);

  // ── Release camera hardware only when provider fully unmounts (app close) ──
  useEffect(() => {
    return () => {
      const s = streamRef.current;
      if (s) {
        s.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
    };
  }, []);

  // ── Frame-sending loop — runs only while active && cameraReady ──
  useEffect(() => {
    if (!cameraReady || !api || !active) return;

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
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;

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
        await api.processVideoFrame({ image, width, height, timestamp: Date.now() });
      } catch {
        // The UI already reflects backend failures via missing overlays.
      } finally {
        busy = false;
        if (!cancelled) timer = window.setTimeout(sendFrame, frameDelayMs);
      }
    };

    sendFrame();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [cameraReady, active]);

  const value = { stream, cameraReady, cameraError };

  return (
    <CameraContext.Provider value={value}>
      {/* Hidden video + canvas used only for frame capture → backend */}
      <video ref={videoRef} muted playsInline style={{ display: 'none' }} />
      <canvas ref={captureCanvasRef} style={{ display: 'none' }} />
      {children}
    </CameraContext.Provider>
  );
}
