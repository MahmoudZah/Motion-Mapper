import React, { useState } from 'react';
import {
  SlidersHorizontal,
  Camera,
  UserCheck,
  Target,
  CheckCircle2,
  ChevronRight,
  ChevronLeft,
  RotateCcw,
} from 'lucide-react';

const steps = [
  {
    id: 1,
    title: 'Camera Check',
    description: 'Ensure you are fully visible in the webcam frame. Stand 6–8 feet from the camera.',
    icon: Camera,
    instruction: 'Position yourself so your full body is visible from head to toe.',
  },
  {
    id: 2,
    title: 'Body Detection',
    description: 'The system will detect your body landmarks and confirm visibility.',
    icon: UserCheck,
    instruction: 'Hold still while we detect your joints. All 33 body landmarks must be visible.',
  },
  {
    id: 3,
    title: 'Neutral Pose Capture',
    description: 'Stand in a T-pose to calibrate your baseline limb lengths and joint centers.',
    icon: Target,
    instruction: 'Stand with arms extended horizontally. Hold the pose for 3 seconds.',
  },
];

export default function CalibrationWizard({ calibration, onCalibrate, onSensitivity }) {
  const [currentStep, setCurrentStep] = useState(0);
  const [stepComplete, setStepComplete] = useState([false, false, false]);

  const handleStepAction = () => {
    if (currentStep === 2) {
      onCalibrate();
    }
    const updated = [...stepComplete];
    updated[currentStep] = true;
    setStepComplete(updated);
  };

  const handleNext = () => {
    if (currentStep < 2) setCurrentStep(currentStep + 1);
  };

  const handlePrev = () => {
    if (currentStep > 0) setCurrentStep(currentStep - 1);
  };

  const handleReset = () => {
    setCurrentStep(0);
    setStepComplete([false, false, false]);
  };

  const allComplete = stepComplete.every(Boolean);
  const StepIcon = steps[currentStep].icon;

  return (
    <div className="space-y-6 animate-fade-in w-full">
      <div className="flex items-center gap-3 mb-2">
        <SlidersHorizontal size={22} className="text-neon" />
        <h2 className="font-display text-lg tracking-wider text-white uppercase">
          Calibration Wizard
        </h2>
      </div>

      {/* Step Indicators */}
      <div className="flex items-center gap-2">
        {steps.map((step, i) => (
          <React.Fragment key={step.id}>
            <button
              onClick={() => setCurrentStep(i)}
              className={`
                flex items-center gap-2 px-3 py-2 rounded-lg text-xs transition-all
                ${i === currentStep
                  ? 'bg-neon/10 text-neon border border-neon/30'
                  : stepComplete[i]
                    ? 'bg-neon/5 text-neon/60 border border-neon/10'
                    : 'bg-surface text-gray-500 border border-panel-border'}
              `}
            >
              {stepComplete[i] ? (
                <CheckCircle2 size={14} className="text-neon" />
              ) : (
                <span className="w-5 h-5 rounded-full border border-current flex items-center justify-center text-[10px]">
                  {step.id}
                </span>
              )}
              <span className="hidden sm:inline">{step.title}</span>
            </button>
            {i < steps.length - 1 && (
              <div className={`w-8 h-px ${stepComplete[i] ? 'bg-neon/40' : 'bg-panel-border'}`} />
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Current Step Content */}
      <div className="bg-surface border border-panel-border rounded-xl p-6">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-xl bg-neon/10 flex items-center justify-center shrink-0">
            <StepIcon size={24} className="text-neon" />
          </div>
          <div className="flex-1">
            <h3 className="text-white font-semibold mb-1">{steps[currentStep].title}</h3>
            <p className="text-sm text-gray-400 mb-4">{steps[currentStep].description}</p>

            {/* Webcam Placeholder */}
            <div className="relative w-full aspect-video bg-panel rounded-lg border border-panel-border overflow-hidden mb-4">
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="text-center">
                  <Camera size={40} className="text-gray-700 mx-auto mb-2" />
                  <p className="text-xs text-gray-600">Webcam feed will appear here</p>
                  <p className="text-[10px] text-gray-700 mt-1">MediaPipe skeleton overlay placeholder</p>
                </div>
              </div>
              <div className="absolute inset-0 scanline" />
              {/* Corner markers */}
              <div className="absolute top-2 left-2 w-6 h-6 border-l-2 border-t-2 border-neon/40" />
              <div className="absolute top-2 right-2 w-6 h-6 border-r-2 border-t-2 border-neon/40" />
              <div className="absolute bottom-2 left-2 w-6 h-6 border-l-2 border-b-2 border-neon/40" />
              <div className="absolute bottom-2 right-2 w-6 h-6 border-r-2 border-b-2 border-neon/40" />
            </div>

            <p className="text-xs text-gray-500 italic mb-4">{steps[currentStep].instruction}</p>

            <div className="flex items-center gap-3">
              <button
                onClick={handleStepAction}
                disabled={stepComplete[currentStep]}
                className={`
                  px-5 py-2 rounded-lg text-sm font-semibold transition-all
                  ${stepComplete[currentStep]
                    ? 'bg-neon/10 text-neon/50 cursor-not-allowed'
                    : 'bg-neon/20 text-neon border border-neon/30 hover:bg-neon/30 hover:shadow-neon'}
                `}
              >
                {stepComplete[currentStep]
                  ? '✓ Complete'
                  : currentStep === 2
                    ? 'Capture Pose'
                    : 'Confirm'}
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
                  disabled={currentStep === 2 || !stepComplete[currentStep]}
                  className="p-2 rounded-lg text-gray-500 hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronRight size={18} />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Sensitivity Slider */}
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
          onChange={(e) => onSensitivity(parseInt(e.target.value))}
          className="w-full h-2 rounded-full appearance-none cursor-pointer
                     bg-panel [&::-webkit-slider-thumb]:appearance-none
                     [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                     [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-neon
                     [&::-webkit-slider-thumb]:shadow-neon [&::-webkit-slider-thumb]:cursor-pointer"
          style={{
            background: `linear-gradient(to right, #00FF00 0%, #00FF00 ${calibration.sensitivity}%, #21262d ${calibration.sensitivity}%, #21262d 100%)`,
          }}
        />
        <div className="flex justify-between text-[10px] text-gray-600 mt-1">
          <span>Lenient</span>
          <span>Strict</span>
        </div>
      </div>

      {/* Calibration Status */}
      <div className="bg-surface border border-panel-border rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${calibration.calibrated ? 'bg-neon shadow-neon' : 'bg-gray-600'}`} />
          <span className="text-sm text-gray-300">
            {calibration.calibrated ? 'Calibrated — Neutral pose recorded' : 'Not calibrated'}
          </span>
        </div>
        {allComplete && (
          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-white transition-colors"
          >
            <RotateCcw size={12} />
            Recalibrate
          </button>
        )}
      </div>
    </div>
  );
}
