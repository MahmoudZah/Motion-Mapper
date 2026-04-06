import React, { useState } from 'react';
import { Video, User, Activity, Dumbbell } from 'lucide-react';

const exerciseRefs = [
  { id: 'squats', label: 'Squats', tips: ['Feet shoulder-width apart', 'Back straight', 'Knees behind toes', 'Thighs parallel to floor'] },
  { id: 'jumpingJacks', label: 'Jumping Jacks', tips: ['Full arm extension overhead', 'Feet together on return', 'Smooth rhythm', 'Arms touch above head'] },
  { id: 'rightDumbbellRaise', label: 'Right Dumbbell Raise', tips: ['Elbow at 90° at top', 'Controlled motion', 'Shoulder stays down', 'Full range of motion'] },
  { id: 'leftDumbbellRaise', label: 'Left Dumbbell Raise', tips: ['Mirror right arm form', 'Controlled motion', 'Shoulder stays down', 'Full range of motion'] },
];

export default function LiveView({ isTracking, detections, exerciseState }) {
  const [selectedExercise, setSelectedExercise] = useState('squats');
  const refInfo = exerciseRefs.find((e) => e.id === selectedExercise) || exerciseRefs[0];

  const recentValid = detections.filter((d) => d.status === 'valid').length;
  const recentInvalid = detections.filter((d) => d.status === 'invalid').length;
  const accuracy = detections.length > 0 ? Math.round((recentValid / detections.length) * 100) : 0;

  return (
    <div className="space-y-4 animate-fade-in h-full flex flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Video size={22} className="text-neon" />
          <h2 className="font-display text-lg tracking-wider text-white uppercase">Live View</h2>
        </div>

        <select
          value={selectedExercise}
          onChange={(e) => setSelectedExercise(e.target.value)}
          className="bg-surface border border-panel-border rounded-lg px-3 py-1.5 text-sm text-gray-300
                     focus:outline-none focus:border-neon/30 cursor-pointer"
        >
          {exerciseRefs.map((ex) => (
            <option key={ex.id} value={ex.id}>{ex.label}</option>
          ))}
        </select>
      </div>

      {/* Dual Pane */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-0">
        {/* Left: Live Webcam */}
        <div className="bg-surface border border-panel-border rounded-xl overflow-hidden relative flex flex-col">
          <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${isTracking ? 'bg-red-500 animate-pulse' : 'bg-gray-600'}`} />
            <span className="text-[10px] text-gray-400 uppercase tracking-wider">
              {isTracking ? 'Live' : 'Paused'}
            </span>
          </div>

          <div className="flex-1 bg-panel flex items-center justify-center relative">
            <div className="text-center">
              <User size={48} className="text-gray-700 mx-auto mb-3" />
              <p className="text-xs text-gray-600">Live webcam feed</p>
              <p className="text-[10px] text-gray-700 mt-1">
                MediaPipe skeletal overlay
              </p>
            </div>
            <div className="absolute inset-0 scanline" />
            <div className="absolute top-3 left-3 w-8 h-8 border-l-2 border-t-2 border-neon/30" />
            <div className="absolute top-3 right-3 w-8 h-8 border-r-2 border-t-2 border-neon/30" />
            <div className="absolute bottom-3 left-3 w-8 h-8 border-l-2 border-b-2 border-neon/30" />
            <div className="absolute bottom-3 right-3 w-8 h-8 border-r-2 border-b-2 border-neon/30" />
          </div>

          {/* Stats bar */}
          <div className="h-10 border-t border-panel-border flex items-center px-4 gap-4 bg-surface shrink-0">
            <div className="flex items-center gap-1.5">
              <Activity size={12} className="text-neon" />
              <span className="text-[10px] text-gray-400">Accuracy</span>
              <span className="text-xs text-neon font-mono">{accuracy}%</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-gray-400">Valid</span>
              <span className="text-xs text-neon font-mono">{recentValid}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-gray-400">Invalid</span>
              <span className="text-xs text-warn font-mono">{recentInvalid}</span>
            </div>
          </div>
        </div>

        {/* Right: Reference / Perfect Form */}
        <div className="bg-surface border border-panel-border rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-panel-border flex items-center gap-2 shrink-0">
            <Dumbbell size={14} className="text-neon" />
            <span className="text-xs text-gray-400 uppercase tracking-wider font-display">
              Reference — {refInfo.label}
            </span>
          </div>

          <div className="flex-1 bg-panel flex items-center justify-center relative">
            <div className="text-center px-4">
              <div className="w-20 h-20 rounded-full bg-neon/5 border border-neon/20 flex items-center justify-center mx-auto mb-4">
                <Dumbbell size={32} className="text-neon/40" />
              </div>
              <p className="text-xs text-gray-500 mb-1">Perfect form animation</p>
              <p className="text-[10px] text-gray-600">Reference video placeholder</p>
            </div>
            <div className="absolute inset-0 scanline opacity-50" />
          </div>

          <div className="p-4 border-t border-panel-border shrink-0">
            <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Form Checklist</p>
            <div className="grid grid-cols-2 gap-1.5">
              {refInfo.tips.map((tip, i) => (
                <div key={i} className="flex items-center gap-1.5 text-xs text-gray-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-neon/40 shrink-0" />
                  {tip}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Live Detection Feed */}
      <div className="bg-surface border border-panel-border rounded-xl p-3 shrink-0">
        <div className="flex gap-2 overflow-x-auto pb-1">
          {detections.length === 0 && (
            <p className="text-xs text-gray-600 italic px-2">Start tracking to see live detections</p>
          )}
          {detections.slice(0, 12).map((d, i) => (
            <div
              key={d.timestamp + i}
              className={`
                shrink-0 px-3 py-1.5 rounded-lg text-[10px] font-mono border
                ${d.status === 'valid'
                  ? 'bg-neon/5 border-neon/20 text-neon'
                  : 'bg-warn/5 border-warn/20 text-warn'}
              `}
            >
              {d.message}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
