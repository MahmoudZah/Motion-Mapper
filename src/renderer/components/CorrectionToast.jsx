import React, { useEffect } from 'react';
import { AlertTriangle, X } from 'lucide-react';

const exerciseLabels = {
  squats: 'Squats',
  jumpingJacks: 'Jumping Jacks',
  rightDumbbellRaise: 'Right Dumbbell',
  leftDumbbellRaise: 'Left Dumbbell',
};

export default function CorrectionToast({ correction, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 4000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div className="animate-slide-up glow-border-warn bg-surface rounded-lg px-4 py-3 flex items-start gap-3">
      <AlertTriangle size={18} className="text-warn shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-warn font-semibold uppercase tracking-wide">
          {exerciseLabels[correction.exercise] || correction.exercise}
        </p>
        <p className="text-sm text-gray-300 mt-0.5">{correction.message}</p>
      </div>
      <button onClick={onDismiss} className="text-gray-500 hover:text-gray-300 shrink-0">
        <X size={14} />
      </button>
    </div>
  );
}
