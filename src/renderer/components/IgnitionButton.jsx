import React from 'react';
import { Power } from 'lucide-react';

export default function IgnitionButton({ isTracking, onToggle }) {
  const handleClick = (event) => {
    event.currentTarget.blur();
    onToggle();
  };

  return (
    <div className="h-20 border-t border-panel-border bg-panel flex items-center justify-center px-6 shrink-0">
      <button
        type="button"
        tabIndex={-1}
        onClick={handleClick}
        className={`
          group relative flex items-center gap-3 px-8 py-3 rounded-xl font-display text-sm
          tracking-wider uppercase transition-all duration-300
          ${isTracking
            ? 'bg-neon/10 text-neon border border-neon/40 shadow-neon hover:bg-neon/20'
            : 'bg-surface text-gray-400 border border-panel-border hover:border-neon/30 hover:text-neon/70'}
        `}
      >
        <Power
          size={22}
          className={`transition-all duration-300 ${
            isTracking ? 'text-neon drop-shadow-[0_0_8px_rgba(0,255,0,0.6)]' : ''
          }`}
        />
        <span>{isTracking ? 'Engine Running' : 'Ignition'}</span>

        {isTracking && (
          <span className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-neon animate-ping" />
        )}
      </button>
    </div>
  );
}
