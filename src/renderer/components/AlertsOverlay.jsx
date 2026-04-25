import React, { useEffect, useState } from 'react';
import { X, Bell, AlertTriangle, CheckCircle2, Trash2 } from 'lucide-react';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

const EXERCISE_LABELS = {
  squats: 'Squats',
  jumpingJacks: 'Jump',
  rightDumbbellRaise: 'Right Curl',
  leftDumbbellRaise: 'Left Curl',
};

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function AlertsOverlay() {
  const [alerts, setAlerts] = useState([]);
  const [filter, setFilter] = useState('all'); // 'all' | 'invalid' | 'valid'

  useEffect(() => {
    if (!api) return undefined;
    return api.onExerciseDetection((data) => {
      setAlerts((prev) => [
        { id: Date.now(), ...data, ts: Date.now() },
        ...prev,
      ].slice(0, 80));
    });
  }, []);

  const filtered = filter === 'all' ? alerts : alerts.filter((a) => a.status === filter);
  const warnCount = alerts.filter((a) => a.status === 'invalid').length;

  return (
    <>
      <style>{`
        @keyframes slideIn {
          from { opacity: 0; transform: translateX(8px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        .alert-row { animation: slideIn 0.15s ease-out both; }
      `}</style>

      <div
        className="h-screen w-screen flex flex-col rounded-xl overflow-hidden"
        style={{ background: 'rgba(10,13,18,0.93)', border: '1px solid rgba(255,255,255,0.07)' }}
      >
        {/* Drag bar */}
        <div
          className="shrink-0 h-10 flex items-center justify-between px-3 border-b rounded-t-xl"
          style={{ WebkitAppRegion: 'drag', borderColor: 'rgba(255,255,255,0.07)' }}
        >
          <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' }}>
            <Bell size={13} className="text-warn/80" />
            <span className="text-[10px] font-display uppercase tracking-widest text-warn/80">Alerts</span>
            {warnCount > 0 && (
              <span className="text-[9px] bg-warn/20 text-warn rounded-full px-1.5 py-0.5 font-mono">
                {warnCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1" style={{ WebkitAppRegion: 'no-drag' }}>
            {alerts.length > 0 && (
              <button
                onClick={() => setAlerts([])}
                className="w-7 h-7 flex items-center justify-center rounded hover:bg-white/10 transition-colors"
                title="Clear"
              >
                <Trash2 size={11} className="text-gray-500" />
              </button>
            )}
            <button
              onClick={() => api?.closeOverlays()}
              className="w-7 h-7 flex items-center justify-center rounded hover:bg-red-500/30 transition-colors"
            >
              <X size={11} className="text-gray-400" />
            </button>
          </div>
        </div>

        {/* Filter strip */}
        <div className="shrink-0 flex gap-1 px-2 py-1.5 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
          {[['all', 'All'], ['invalid', 'Warnings'], ['valid', 'Valid']].map(([id, label]) => (
            <button
              key={id}
              onClick={() => setFilter(id)}
              className={`px-2.5 py-0.5 rounded text-[10px] font-medium transition-all ${
                filter === id
                  ? id === 'invalid' ? 'bg-warn/15 text-warn' : 'bg-neon/15 text-neon'
                  : 'text-gray-600 hover:text-gray-400'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Alert list */}
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <CheckCircle2 size={24} className="text-neon/20" />
              <p className="text-[10px] text-gray-600">
                {alerts.length === 0 ? 'No detections yet — start tracking.' : 'Nothing here for this filter.'}
              </p>
            </div>
          ) : (
            <div className="flex flex-col divide-y" style={{ '--tw-divide-opacity': 1, borderColor: 'rgba(255,255,255,0.04)' }}>
              {filtered.map((a, i) => (
                <div
                  key={a.id}
                  className="alert-row flex items-start gap-2.5 px-3 py-2.5"
                  style={{ animationDelay: i === 0 ? '0ms' : '0ms' }}
                >
                  {a.status === 'invalid'
                    ? <AlertTriangle size={13} className="shrink-0 mt-0.5 text-warn" />
                    : <CheckCircle2 size={13} className="shrink-0 mt-0.5 text-neon" />}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={`text-[9px] font-bold uppercase tracking-wider ${a.status === 'invalid' ? 'text-warn' : 'text-neon'}`}>
                        {EXERCISE_LABELS[a.exercise] || a.exercise}
                      </span>
                      {a.angle != null && (
                        <span className="text-[9px] text-gray-600 font-mono">{a.angle}°</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-400 leading-snug">{a.message}</p>
                  </div>
                  <span className="text-[9px] text-gray-700 font-mono shrink-0 mt-0.5">{formatTime(a.ts)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
