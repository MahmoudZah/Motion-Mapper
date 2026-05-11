import React, { useState } from 'react';
import { Keyboard, Activity, Zap, ChevronDown } from 'lucide-react';
import KeySelector from './KeySelector';

const exercises = [
  { id: 'squats', label: 'Squats', defaultKey: 'Space' },
  { id: 'jumpingJacks', label: 'Jump', defaultKey: 'W' },
  { id: 'rightDumbbellRaise', label: 'Right Bicep Curl', defaultKey: 'D' },
  { id: 'leftDumbbellRaise', label: 'Left Bicep Curl', defaultKey: 'A' },
  { id: 'rightLateralRaise', label: 'Right Lateral Raise', defaultKey: 'E' },
  { id: 'leftLateralRaise', label: 'Left Lateral Raise', defaultKey: 'Q' },
];

export default function Dashboard({ exerciseState, isTracking, onUpdateKey, onToggleEnabled, keypresses, detections }) {
  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center gap-3 mb-2">
        <Keyboard size={22} className="text-neon" />
        <h2 className="font-display text-lg tracking-wider text-white uppercase">
          Movement → Key Matrix
        </h2>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {exercises.map((ex) => {
          const state = exerciseState[ex.id] || {};
          return (
            <div
              key={ex.id}
              className={`
                rounded-xl p-4 transition-all duration-300
                ${state.active
                  ? 'glow-border bg-surface'
                  : (state.enabled !== false ? 'bg-surface border border-panel-border' : 'bg-surface/50 border border-panel-border/50 opacity-60')}
              `}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => onToggleEnabled && onToggleEnabled(ex.id, state.enabled === false ? true : false)}
                        className={`w-3 h-3 rounded-full flex-shrink-0 transition-colors ${
                          state.enabled !== false ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]' : 'bg-gray-600'
                        }`}
                        title={state.enabled !== false ? 'Disable Exercise' : 'Enable Exercise'}
                      />
                      <h3 className={`text-sm font-semibold transition-colors ${state.enabled !== false ? 'text-white' : 'text-gray-500'}`}>
                        {ex.label}
                      </h3>
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          state.active
                            ? 'bg-neon shadow-neon animate-pulse-neon'
                            : 'bg-gray-600'
                        }`}
                      />
                      <span className={`text-xs ${state.active ? 'text-neon' : 'text-gray-500'}`}>
                        {state.active ? 'Tracking' : (state.enabled !== false ? 'Ready' : 'Disabled')}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-500 mr-1">→</span>
                  <KeySelector
                    currentKey={state.key || ex.defaultKey}
                    onChange={(key) => onUpdateKey(ex.id, key)}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-6">
        <div className="bg-surface border border-panel-border rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={16} className="text-neon" />
            <h3 className="text-xs font-display tracking-wider text-gray-400 uppercase">
              Recent Keypresses
            </h3>
          </div>
          <div className="space-y-1.5 max-h-40 overflow-y-auto">
            {keypresses.length === 0 && (
              <p className="text-xs text-gray-600 italic">No keypresses yet. Start tracking to see activity.</p>
            )}
            {keypresses.slice(0, 8).map((kp, i) => (
              <div
                key={kp.timestamp + i}
                className="flex items-center justify-between text-xs px-2 py-1.5 rounded bg-panel"
              >
                <span className="text-gray-400">
                  {exercises.find((e) => e.id === kp.exercise)?.label || kp.exercise}
                </span>
                <kbd className="px-2 py-0.5 bg-neon/10 text-neon rounded text-xs font-mono border border-neon/20">
                  {kp.key}
                </kbd>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-surface border border-panel-border rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={16} className="text-warn" />
            <h3 className="text-xs font-display tracking-wider text-gray-400 uppercase">
              Detection Feed
            </h3>
          </div>
          <div className="space-y-1.5 max-h-40 overflow-y-auto">
            {detections.length === 0 && (
              <p className="text-xs text-gray-600 italic">No detections yet. Start tracking to see data.</p>
            )}
            {detections.slice(0, 8).map((d, i) => (
              <div
                key={d.timestamp + i}
                className={`flex items-center justify-between text-xs px-2 py-1.5 rounded ${
                  d.status === 'valid' ? 'bg-neon/5' : 'bg-warn/5'
                }`}
              >
                <span className={d.status === 'valid' ? 'text-neon' : 'text-warn'}>
                  {d.message}
                </span>
                <span className="text-gray-600 text-[10px]">
                  {d.angle}°
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
