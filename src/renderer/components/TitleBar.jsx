import React from 'react';
import { Minus, Square, X, Gamepad2, PictureInPicture2 } from 'lucide-react';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

export default function TitleBar() {
  return (
    <div
      className="h-10 bg-panel border-b border-panel-border flex items-center justify-between px-4 shrink-0"
      style={{ WebkitAppRegion: 'drag' }}
    >
      <div className="flex items-center gap-2">
        <Gamepad2 size={18} className="text-neon" />
        <span className="font-display text-xs tracking-widest text-neon/80 uppercase">
          Gamecha
        </span>
      </div>

      <div className="flex items-center gap-1" style={{ WebkitAppRegion: 'no-drag' }}>
        <button
          onClick={() => api?.toggleOverlays()}
          title="Toggle overlays"
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-neon/10 transition-colors"
        >
          <PictureInPicture2 size={14} className="text-neon/60 hover:text-neon" />
        </button>
        <div className="w-px h-4 bg-panel-border mx-0.5" />
        <button
          onClick={() => api?.windowMinimize()}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-white/10 transition-colors"
        >
          <Minus size={14} className="text-gray-400" />
        </button>
        <button
          onClick={() => api?.windowMaximize()}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-white/10 transition-colors"
        >
          <Square size={11} className="text-gray-400" />
        </button>
        <button
          onClick={() => api?.windowClose()}
          className="w-8 h-8 flex items-center justify-center rounded hover:bg-red-500/20 transition-colors group"
        >
          <X size={14} className="text-gray-400 group-hover:text-red-400" />
        </button>
      </div>
    </div>
  );
}
