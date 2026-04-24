import React, { useState } from 'react';
import { Video, Activity, Dumbbell, CheckCircle2 } from 'lucide-react';
import PoseCameraViewport from './PoseCameraViewport';

const exerciseRefs = [
  {
    id: 'squats',
    label: 'Squats',
    tips: ['Feet shoulder-width apart', 'Back straight', 'Knees behind toes', 'Thighs near parallel'],
  },
  {
    id: 'jumpingJacks',
    label: 'Jump',
    tips: ['Stand tall before each rep', 'Drive straight up', 'Land and reset cleanly', 'Keep the torso tall'],
  },
  {
    id: 'rightDumbbellRaise',
    label: 'Right Bicep Curl',
    tips: ['Keep the elbow close to your side', 'Curl toward the shoulder', 'Lower under control', 'Stand tall through the torso'],
  },
  {
    id: 'leftDumbbellRaise',
    label: 'Left Bicep Curl',
    tips: ['Mirror the right arm', 'Keep the elbow close to your side', 'Lower under control', 'Stand tall through the torso'],
  },
];

function formatMetricValue(value) {
  if (value === null || value === undefined || value === '') return '--';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

export default function LiveViewPrototype({ isTracking, detections, exerciseState, poseFrame }) {
  const [selectedExercise, setSelectedExercise] = useState('squats');
  const refInfo = exerciseRefs.find((exercise) => exercise.id === selectedExercise) || exerciseRefs[0];
  const guidance = poseFrame?.guidance?.[selectedExercise];
  const metrics = Object.entries(guidance?.metrics || {});

  const recentValid = detections.filter((detection) => detection.status === 'valid').length;
  const recentInvalid = detections.filter((detection) => detection.status === 'invalid').length;
  const accuracy = detections.length > 0 ? Math.round((recentValid / detections.length) * 100) : 0;
  const trackedExercises = Object.values(exerciseState || {}).filter((entry) => entry.active).length;

  return (
    <div className="space-y-4 animate-fade-in h-full flex flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Video size={22} className="text-neon" />
          <h2 className="font-display text-lg tracking-wider text-white uppercase">Live View</h2>
        </div>

        <select
          value={selectedExercise}
          onChange={(event) => setSelectedExercise(event.target.value)}
          className="bg-surface border border-panel-border rounded-lg px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-neon/30 cursor-pointer"
        >
          {exerciseRefs.map((exercise) => (
            <option key={exercise.id} value={exercise.id}>
              {exercise.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-0">
        <div className="bg-surface border border-panel-border rounded-xl overflow-hidden relative flex flex-col">
          <div className="flex-1 bg-panel relative">
            <PoseCameraViewport
              poseFrame={poseFrame}
              emptyTitle="Move into view so the skeleton can lock on"
              emptySubtitle="The renderer owns the camera feed. The backend returns pose values only."
              badge={(
                <>
                  <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${isTracking ? 'bg-red-500 animate-pulse' : 'bg-gray-600'}`} />
                    <span className="text-[10px] text-gray-300 uppercase tracking-wider">
                      {isTracking ? 'Tracking' : 'Preview'}
                    </span>
                  </div>
                  <div className="absolute top-3 right-3 z-10 rounded-md bg-black/60 px-2.5 py-1.5 text-[10px] text-gray-200 backdrop-blur-sm">
                    <div>Inference {poseFrame?.inferenceMs ?? '--'} ms</div>
                    <div>Pose {poseFrame?.poseDetected ? `${Math.round((poseFrame.meanConfidence || 0) * 100)}%` : 'not found'}</div>
                  </div>
                </>
              )}
              hud={(
                <div className="absolute inset-x-3 bottom-3 z-10 space-y-2 pointer-events-none">
                  <div className="rounded-xl border border-panel-border bg-black/55 p-3 backdrop-blur-sm">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-gray-500">{refInfo.label}</p>
                        <p className="text-sm text-white font-display uppercase">{guidance?.phase || 'untracked'}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] uppercase tracking-wider text-gray-500">Status</p>
                        <p className={`text-xs font-semibold ${guidance?.tracked ? 'text-neon' : 'text-gray-400'}`}>
                          {guidance?.tracked ? 'Joints locked' : 'Searching'}
                        </p>
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2">
                      {(guidance?.steps || []).slice(0, 3).map((step) => (
                        <div
                          key={step.id}
                          className={`rounded-lg border px-3 py-2 ${
                            step.done ? 'border-neon/40 bg-neon/15' : 'border-panel-border bg-panel/80'
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <CheckCircle2 size={13} className={step.done ? 'text-neon' : 'text-gray-600'} />
                            <span className={`text-[11px] ${step.done ? 'text-neon' : 'text-gray-300'}`}>{step.label}</span>
                          </div>
                          <div className="mt-1 flex items-center justify-between text-[10px] text-gray-500">
                            <span>{step.target}</span>
                            <span>{formatMetricValue(step.value)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              footer={(
                <>
                  <div className="absolute top-3 left-3 w-8 h-8 border-l-2 border-t-2 border-neon/30" />
                  <div className="absolute top-3 right-3 w-8 h-8 border-r-2 border-t-2 border-neon/30" />
                  <div className="absolute bottom-3 left-3 w-8 h-8 border-l-2 border-b-2 border-neon/30" />
                  <div className="absolute bottom-3 right-3 w-8 h-8 border-r-2 border-b-2 border-neon/30" />
                </>
              )}
            />
          </div>

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
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-gray-400">Mapped</span>
              <span className="text-xs text-neon font-mono">{trackedExercises}</span>
            </div>
          </div>
        </div>

        <div className="bg-surface border border-panel-border rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-panel-border flex items-center gap-2 shrink-0">
            <Dumbbell size={14} className="text-neon" />
            <span className="text-xs text-gray-400 uppercase tracking-wider font-display">
              Guidance - {refInfo.label}
            </span>
          </div>

          <div className="flex-1 bg-panel p-4 overflow-y-auto">
            <div className="rounded-xl border border-neon/15 bg-neon/5 p-4">
              <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Current phase</p>
              <p className="text-lg text-white font-display uppercase">{guidance?.phase || 'untracked'}</p>
              <p className="text-xs text-gray-400 mt-2">
                {guidance?.summary || 'Move into view so the system can evaluate this exercise.'}
              </p>
            </div>

            <div className="mt-4 space-y-2">
              {(guidance?.steps || []).map((step) => (
                <div
                  key={step.id}
                  className={`rounded-lg border px-3 py-2 transition-all ${
                    step.done ? 'border-neon/40 bg-neon/10' : 'border-panel-border bg-surface'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={14} className={step.done ? 'text-neon' : 'text-gray-600'} />
                    <span className={`text-sm ${step.done ? 'text-neon' : 'text-gray-300'}`}>{step.label}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between text-[10px] text-gray-500">
                    <span>Target {step.target}</span>
                    <span>{formatMetricValue(step.value)}</span>
                  </div>
                </div>
              ))}
              {(!guidance || guidance.steps?.length === 0) && (
                <p className="text-xs text-gray-500">No guidance available yet for this pose.</p>
              )}
            </div>

            {metrics.length > 0 && (
              <div className="mt-4">
                <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Live metrics</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {metrics.map(([key, value]) => (
                    <div key={key} className="rounded-lg border border-panel-border bg-surface px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500">{key}</p>
                      <p className="mt-1 text-sm text-white font-mono">{formatMetricValue(value)}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="p-4 border-t border-panel-border shrink-0">
            <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Current Placeholder Form</p>
            <div className="grid grid-cols-2 gap-1.5">
              {refInfo.tips.map((tip, index) => (
                <div key={index} className="flex items-center gap-1.5 text-xs text-gray-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-neon/40 shrink-0" />
                  {tip}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-surface border border-panel-border rounded-xl p-3 shrink-0">
        <div className="flex gap-2 overflow-x-auto pb-1">
          {detections.length === 0 && (
            <p className="text-xs text-gray-600 italic px-2">Start tracking to see live detections</p>
          )}
          {detections.slice(0, 12).map((detection, index) => (
            <div
              key={detection.timestamp + index}
              className={`shrink-0 px-3 py-1.5 rounded-lg text-[10px] font-mono border ${
                detection.status === 'valid'
                  ? 'bg-neon/5 border-neon/20 text-neon'
                  : 'bg-warn/5 border-warn/20 text-warn'
              }`}
            >
              {detection.message}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
