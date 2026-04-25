import React, { useState } from 'react';
import { Bell, AlertTriangle, CheckCircle2, Trash2 } from 'lucide-react';

const EXERCISE_LABELS = {
  squats: 'Squats',
  jumpingJacks: 'Jump',
  rightDumbbellRaise: 'Right Bicep Curl',
  leftDumbbellRaise: 'Left Bicep Curl',
};

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'invalid', label: 'Warnings' },
  { id: 'valid', label: 'Valid' },
];

function formatTime(ts) {
  if (!ts) return '--';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function AlertsLog({ detections, onClear }) {
  const [filter, setFilter] = useState('all');

  const filtered = detections.filter((d) => filter === 'all' || d.status === filter);
  const invalidCount = detections.filter((d) => d.status === 'invalid').length;
  const validCount = detections.filter((d) => d.status === 'valid').length;

  return (
    <div className="space-y-4 animate-fade-in flex flex-col h-full">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Bell size={22} className="text-neon" />
          <h2 className="font-display text-lg tracking-wider text-white uppercase">Alerts Log</h2>
        </div>
        {detections.length > 0 && (
          <button
            onClick={onClear}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors px-2 py-1 rounded hover:bg-white/5"
          >
            <Trash2 size={13} />
            Clear
          </button>
        )}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-surface border border-panel-border rounded-xl p-3 text-center">
          <p className="text-2xl font-display text-white">{detections.length}</p>
          <p className="text-[10px] text-gray-500 uppercase tracking-wider mt-0.5">Total</p>
        </div>
        <div className="bg-surface border border-neon/20 rounded-xl p-3 text-center">
          <p className="text-2xl font-display text-neon">{validCount}</p>
          <p className="text-[10px] text-gray-500 uppercase tracking-wider mt-0.5">Valid</p>
        </div>
        <div className="bg-surface border border-warn/20 rounded-xl p-3 text-center">
          <p className="text-2xl font-display text-warn">{invalidCount}</p>
          <p className="text-[10px] text-gray-500 uppercase tracking-wider mt-0.5">Warnings</p>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 bg-surface border border-panel-border rounded-lg p-1 w-fit">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`px-3 py-1 rounded text-xs font-medium transition-all ${
              filter === f.id
                ? 'bg-neon/15 text-neon'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Log list */}
      <div className="flex-1 overflow-y-auto space-y-1.5 min-h-0">
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 text-center">
            <Bell size={32} className="text-gray-700 mb-3" />
            <p className="text-sm text-gray-600">
              {detections.length === 0 ? 'No detections yet. Start tracking.' : 'No entries for this filter.'}
            </p>
          </div>
        )}

        {filtered.map((d, i) => (
          <div
            key={d.timestamp + i}
            className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-all ${
              d.status === 'invalid'
                ? 'bg-warn/5 border-warn/15'
                : 'bg-neon/5 border-neon/10'
            }`}
          >
            {d.status === 'invalid'
              ? <AlertTriangle size={14} className="text-warn shrink-0" />
              : <CheckCircle2 size={14} className="text-neon shrink-0" />}

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-bold uppercase tracking-wider ${d.status === 'invalid' ? 'text-warn' : 'text-neon'}`}>
                  {EXERCISE_LABELS[d.exercise] || d.exercise}
                </span>
                {d.angle != null && (
                  <span className="text-[10px] text-gray-600 font-mono">{d.angle}°</span>
                )}
              </div>
              <p className="text-xs text-gray-400 truncate mt-0.5">{d.message}</p>
            </div>

            <span className="text-[10px] text-gray-600 font-mono shrink-0">
              {formatTime(d.timestamp)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
