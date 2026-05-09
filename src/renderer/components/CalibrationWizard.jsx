import React, { useEffect, useMemo, useState, useCallback } from 'react';
import {
  SlidersHorizontal,
  UserCheck,
  Target,
  CheckCircle2,
  ChevronRight,
  ChevronLeft,
  RotateCcw,
  Crosshair,
  Trash2,
  Loader2,
} from 'lucide-react';
import PoseCameraViewport from './PoseCameraViewport';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

const CALIBRATION_KEYPOINTS = [5, 6, 11, 12, 13, 14];

const EXERCISE_LABELS = {
  squats: 'Squats',
  jumpingJacks: 'Jump',
  rightDumbbellRaise: 'Right Bicep Curl',
  leftDumbbellRaise: 'Left Bicep Curl',
};

const EXERCISE_HINTS = {
  squats: 'Locks standing hip position for squat depth tracking.',
  jumpingJacks: 'Prevents squat recovery from triggering false jumps.',
  rightDumbbellRaise: 'Locks neutral arm position for right curl detection.',
  leftDumbbellRaise: 'Locks neutral arm position for left curl detection.',
};

const steps = [
  {
    id: 1,
    title: 'Camera Check',
    description: 'Ensure you are fully visible in the webcam frame. Stand 6 to 8 feet from the camera.',
    icon: UserCheck,
    instruction: 'Position yourself so your full body is visible from head to toe.',
  },
  {
    id: 2,
    title: 'Body Detection',
    description: 'The system checks that the main torso and leg joints are visible enough to calibrate.',
    icon: UserCheck,
    instruction: 'Hold still while the tracker locks your shoulders, hips, and knees.',
  },
  {
    id: 3,
    title: 'Neutral Pose Capture',
    description: 'Capture your current neutral stance so thresholds can be normalized to your frame.',
    icon: Target,
    instruction: 'Stand naturally and hold still for a moment, then capture the pose.',
  },
];

function StatusChip({ label, done }) {
  return (
    <div
      className={`rounded-full border px-3 py-1 text-[10px] uppercase tracking-wider ${
        done
          ? 'border-neon/40 bg-neon/10 text-neon'
          : 'border-panel-border bg-panel text-gray-500'
      }`}
    >
      {label}
    </div>
  );
}

function ExerciseCalibrationPanel() {
  const [calibrationStatus, setCalibrationStatus] = useState({});
  const [loading, setLoading] = useState(null);
  const [message, setMessage] = useState(null);

  const handleCalibrate = useCallback(async (exercise) => {
    if (!api?.calibrateExercise) return;
    setLoading(exercise);
    setMessage(null);
    try {
      const result = await api.calibrateExercise(exercise);
      if (result.calibrationStatus) {
        setCalibrationStatus(result.calibrationStatus);
      } else {
        setCalibrationStatus((prev) => ({
          ...prev,
          [exercise]: Boolean(result.ok),
        }));
      }
      setMessage({
        exercise,
        ok: result.ok,
        text: result.message || (result.ok ? 'Calibrated' : 'Failed'),
      });
    } catch (err) {
      setMessage({ exercise, ok: false, text: err.message || 'Calibration failed.' });
    } finally {
      setLoading(null);
    }
  }, []);

  const handleRemove = useCallback(async (exercise) => {
    if (!api?.removeCalibration) return;
    setLoading(exercise);
    setMessage(null);
    try {
      const result = await api.removeCalibration(exercise);
      if (result.calibrationStatus) {
        setCalibrationStatus(result.calibrationStatus);
      } else {
        setCalibrationStatus((prev) => ({
          ...prev,
          [exercise]: false,
        }));
      }
      setMessage({
        exercise,
        ok: result.ok,
        text: result.message || (result.ok ? 'Removed' : 'Failed'),
      });
    } catch (err) {
      setMessage({ exercise, ok: false, text: err.message || 'Removal failed.' });
    } finally {
      setLoading(null);
    }
  }, []);

  return (
    <div className="bg-surface border border-panel-border rounded-xl p-3 flex flex-col h-full">
      <div className="flex items-center gap-2 mb-3 px-1">
        <Crosshair size={14} className="text-neon shrink-0" />
        <h3 className="text-xs text-white font-semibold truncate">Exercise Calibration</h3>
      </div>

      <div className="space-y-1.5 flex-1 overflow-y-auto">
        {Object.entries(EXERCISE_LABELS).map(([key, label]) => {
          const isCalibrated = Boolean(calibrationStatus[key]);
          const isLoading = loading === key;

          return (
            <div
              key={key}
              className={`rounded-lg border px-3 py-2.5 transition-all ${
                isCalibrated
                  ? 'border-neon/30 bg-neon/5'
                  : 'border-panel-border bg-panel'
              }`}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <div
                  className={`w-2 h-2 rounded-full shrink-0 transition-colors ${
                    isCalibrated ? 'bg-neon shadow-neon' : 'bg-gray-600'
                  }`}
                />
                <span className={`text-xs font-medium truncate ${isCalibrated ? 'text-neon' : 'text-white'}`}>
                  {label}
                </span>
                {isCalibrated && (
                  <span className="text-[8px] uppercase tracking-wider text-neon/70 bg-neon/10 rounded px-1 py-0.5 shrink-0">
                    Locked
                  </span>
                )}

              </div>
              <p className="text-[9px] text-gray-500 mb-2 leading-tight">{EXERCISE_HINTS[key]}</p>
              {!isCalibrated ? (
                <button
                  onClick={() => handleCalibrate(key)}
                  disabled={isLoading}
                  className={`
                    w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md text-[11px] font-medium transition-all
                    ${isLoading
                      ? 'bg-panel text-gray-500 cursor-wait border border-panel-border'
                      : 'bg-neon/15 text-neon border border-neon/30 hover:bg-neon/25 hover:shadow-neon'}
                  `}
                >
                  {isLoading ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <Crosshair size={11} />
                  )}
                  Calibrate
                </button>
              ) : (
                <button
                  onClick={() => handleRemove(key)}
                  disabled={isLoading}
                  className={`
                    w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md text-[11px] font-medium transition-all
                    ${isLoading
                      ? 'bg-panel text-gray-500 cursor-wait border border-panel-border'
                      : 'bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20'}
                  `}
                >
                  {isLoading ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <Trash2 size={11} />
                  )}
                  Remove
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Feedback toast */}
      {message && (
        <div
          className={`mt-2 rounded-md px-2 py-1.5 text-[10px] flex items-center gap-1.5 transition-all ${
            message.ok
              ? 'bg-neon/10 text-neon border border-neon/20'
              : 'bg-red-500/10 text-red-400 border border-red-500/20'
          }`}
        >
          {message.ok ? <CheckCircle2 size={12} /> : <span className="text-red-400">&#x2715;</span>}
          <span className="truncate">{message.text}</span>
        </div>
      )}
    </div>
  );
}

export default function CalibrationWizardPrototype({
  calibration,
  onCalibrate,
  onSensitivity,
  onProvider,
  poseFrame,
}) {
  const [currentStep, setCurrentStep] = useState(0);
  const scores = poseFrame?.scores || [];
  const cameraLive = Boolean(poseFrame?.width && poseFrame?.height);
  const bodyDetected = Boolean(poseFrame?.poseDetected);
  const calibrationJointsVisible = useMemo(
    () => CALIBRATION_KEYPOINTS.every((index) => (scores[index] || 0) >= 0.35),
    [scores]
  );

  const completion = [
    cameraLive,
    bodyDetected,
    Boolean(calibration.calibrated),
  ];

  useEffect(() => {
    if (currentStep === 0 && cameraLive) {
      setCurrentStep(1);
    } else if (currentStep === 1 && bodyDetected) {
      setCurrentStep(2);
    }
  }, [cameraLive, bodyDetected, currentStep]);

  const handleStepAction = async () => {
    if (currentStep < 2) {
      setCurrentStep((value) => Math.min(value + 1, 2));
      return;
    }
    await onCalibrate();
  };

  const handleNext = () => {
    setCurrentStep((value) => Math.min(value + 1, 2));
  };

  const handlePrev = () => {
    setCurrentStep((value) => Math.max(value - 1, 0));
  };

  const handleReset = () => {
    setCurrentStep(0);
  };

  const currentStepReady = [
    cameraLive,
    bodyDetected,
    calibrationJointsVisible,
  ][currentStep];

  const allComplete = completion.every(Boolean);
  const StepIcon = steps[currentStep].icon;

  return (
    <div className="space-y-6 animate-fade-in w-full">
      <div className="flex items-center gap-3 mb-2">
        <SlidersHorizontal size={22} className="text-neon" />
        <h2 className="font-display text-lg tracking-wider text-white uppercase">
          Calibration Wizard
        </h2>
      </div>

      <div className="flex items-center gap-2">
        {steps.map((step, index) => (
          <React.Fragment key={step.id}>
            <button
              onClick={() => setCurrentStep(index)}
              className={`
                flex items-center gap-2 px-3 py-2 rounded-lg text-xs transition-all
                ${index === currentStep
                  ? 'bg-neon/10 text-neon border border-neon/30'
                  : completion[index]
                    ? 'bg-neon/5 text-neon/60 border border-neon/10'
                    : 'bg-surface text-gray-500 border border-panel-border'}
              `}
            >
              {completion[index] ? (
                <CheckCircle2 size={14} className="text-neon" />
              ) : (
                <span className="w-5 h-5 rounded-full border border-current flex items-center justify-center text-[10px]">
                  {step.id}
                </span>
              )}
              <span className="hidden sm:inline">{step.title}</span>
            </button>
            {index < steps.length - 1 && (
              <div className={`w-8 h-px ${completion[index] ? 'bg-neon/40' : 'bg-panel-border'}`} />
            )}
          </React.Fragment>
        ))}
      </div>

      <div className="bg-surface border border-panel-border rounded-xl p-6">
        <div className="flex items-start gap-4 mb-4">
          <div className="w-12 h-12 rounded-xl bg-neon/10 flex items-center justify-center shrink-0">
            <StepIcon size={24} className="text-neon" />
          </div>
          <div className="flex-1">
            <h3 className="text-white font-semibold mb-1">{steps[currentStep].title}</h3>
            <p className="text-sm text-gray-400">{steps[currentStep].description}</p>
          </div>
        </div>

        {/* ── Camera + Calibration Sidebar (side by side) ── */}
        <div className="flex gap-4 mb-4">
          {/* Camera viewport — left */}
          <div className="flex-1 min-w-0">
            <div className="relative w-full aspect-video bg-panel rounded-lg border border-panel-border overflow-hidden">
              <PoseCameraViewport
                poseFrame={poseFrame}
                emptyTitle="Stand where your full body is visible"
                emptySubtitle="The frontend owns the camera. Calibration lights up as the required joints appear."
                badge={(
                  <div className="absolute bottom-3 left-3 rounded-md bg-black/60 px-2.5 py-1.5 text-[10px] text-gray-200 backdrop-blur-sm">
                    <div>Inference {poseFrame?.inferenceMs ?? '--'} ms</div>
                    <div>{poseFrame?.poseDetected ? 'Pose detected' : 'No person yet'}</div>
                  </div>
                )}
                hud={(
                  <div className="absolute inset-x-3 top-3 z-10 flex flex-wrap gap-2 pointer-events-none">
                    <StatusChip label="Camera live" done={cameraLive} />
                    <StatusChip label="Body detected" done={bodyDetected} />
                    <StatusChip label="Calibration joints visible" done={calibrationJointsVisible} />
                  </div>
                )}
                footer={(
                  <>
                    <div className="absolute top-2 left-2 w-6 h-6 border-l-2 border-t-2 border-neon/40" />
                    <div className="absolute top-2 right-2 w-6 h-6 border-r-2 border-t-2 border-neon/40" />
                    <div className="absolute bottom-2 left-2 w-6 h-6 border-l-2 border-b-2 border-neon/40" />
                    <div className="absolute bottom-2 right-2 w-6 h-6 border-r-2 border-b-2 border-neon/40" />
                  </>
                )}
              />
            </div>
          </div>

          {/* Exercise calibration sidebar — right */}
          <div className="w-56 shrink-0">
            <ExerciseCalibrationPanel />
          </div>
        </div>

        <p className="text-xs text-gray-500 italic mb-3">{steps[currentStep].instruction}</p>
        <div className="flex flex-wrap gap-2 mb-4">
          <StatusChip label="Camera live" done={cameraLive} />
          <StatusChip label="Body detected" done={bodyDetected} />
          <StatusChip label="Calibration joints visible" done={calibrationJointsVisible} />
          <StatusChip label="Captured" done={calibration.calibrated} />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleStepAction}
            disabled={!currentStepReady && currentStep < 2}
            className={`
              px-5 py-2 rounded-lg text-sm font-semibold transition-all
              ${!currentStepReady && currentStep < 2
                ? 'bg-panel text-gray-500 cursor-not-allowed border border-panel-border'
                : 'bg-neon/20 text-neon border border-neon/30 hover:bg-neon/30 hover:shadow-neon'}
            `}
          >
            {currentStep === 2 ? 'Capture Pose' : 'Continue'}
          </button>

          <div className="flex gap-2 ml-auto">
            <button
              onClick={handlePrev}
              disabled={currentStep === 0}
              className="p-2 rounded-lg text-gray-500 hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft size={18} />
            </button>
            <button
              onClick={handleNext}
              disabled={currentStep === 2}
              className="p-2 rounded-lg text-gray-500 hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
      </div>

      <div className="bg-surface border border-panel-border rounded-xl p-5">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h3 className="text-sm text-white font-semibold">Pose Runtime</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Switch between YOLO and MediaPipe pose backends
            </p>
          </div>
          <span className="text-xs uppercase tracking-wider text-neon">
            {calibration.model?.provider || calibration.provider}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(calibration.availableProviders || []).map((provider) => {
            const active = calibration.provider === provider.id;
            return (
              <button
                key={provider.id}
                onClick={() => onProvider(provider.id)}
                className={`rounded-xl border p-4 text-left transition-all ${
                  active
                    ? 'border-neon/40 bg-neon/10 shadow-neon'
                    : 'border-panel-border bg-panel hover:border-neon/20'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className={`text-sm font-semibold ${active ? 'text-neon' : 'text-white'}`}>
                      {provider.label}
                    </p>
                    <p className="mt-1 text-xs text-gray-500">{provider.description}</p>
                  </div>
                  <div className={`w-2.5 h-2.5 rounded-full ${active ? 'bg-neon shadow-neon' : 'bg-gray-600'}`} />
                </div>
              </button>
            );
          })}
        </div>

        <div className="mt-4 rounded-xl border border-panel-border bg-panel px-4 py-3 text-xs text-gray-400">
          <div>Active provider: {calibration.model?.provider || calibration.provider}</div>
          <div>Model asset: {calibration.model?.asset?.path || calibration.model?.taskPath || calibration.model?.weights || '--'}</div>
          <div>
            First-run download: {calibration.model?.asset?.downloaded ? 'completed this session' : 'already available or pending first launch'}
          </div>
        </div>
      </div>

      <div className="bg-surface border border-panel-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm text-white font-semibold">Form Strictness</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              How closely you must match the biomechanical thresholds
            </p>
          </div>
          <span className="text-2xl font-display text-neon">{calibration.sensitivity}%</span>
        </div>
        <input
          type="range"
          min="0"
          max="100"
          value={calibration.sensitivity}
          onChange={(event) => onSensitivity(parseInt(event.target.value, 10))}
          className="w-full h-2 rounded-full appearance-none cursor-pointer bg-panel [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-neon [&::-webkit-slider-thumb]:shadow-neon [&::-webkit-slider-thumb]:cursor-pointer"
          style={{
            background: `linear-gradient(to right, #00FF00 0%, #00FF00 ${calibration.sensitivity}%, #21262d ${calibration.sensitivity}%, #21262d 100%)`,
          }}
        />
        <div className="flex justify-between text-[10px] text-gray-600 mt-1">
          <span>Lenient</span>
          <span>Strict</span>
        </div>
      </div>

      <div className="bg-surface border border-panel-border rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${calibration.calibrated ? 'bg-neon shadow-neon' : 'bg-gray-600'}`} />
          <span className="text-sm text-gray-300">
            {calibration.calibrated ? 'Calibrated - neutral pose recorded' : 'Not calibrated'}
          </span>
        </div>
        {allComplete && (
          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-white transition-colors"
          >
            <RotateCcw size={12} />
            Revisit steps
          </button>
        )}
      </div>
    </div>
  );
}
