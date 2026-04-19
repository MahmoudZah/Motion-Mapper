import React, { useState, useEffect, useCallback } from 'react';
import TitleBar from './components/TitleBar';
import Sidebar from './components/Sidebar';
import Dashboard from './components/Dashboard';
import CalibrationWizard from './components/CalibrationWizard';
import LiveView from './components/LiveView';
import IgnitionButton from './components/IgnitionButton';
import CorrectionToast from './components/CorrectionToast';

const api = typeof window !== 'undefined' && window.motionAPI ? window.motionAPI : null;

function normalizeMappedKey(key) {
  if (!key) return '';

  const aliases = {
    ' ': 'Space',
    ArrowUp: 'Up',
    ArrowDown: 'Down',
    ArrowLeft: 'Left',
    ArrowRight: 'Right',
    Control: 'Ctrl',
  };

  if (aliases[key]) {
    return aliases[key];
  }

  if (key.length === 1) {
    return key.toUpperCase();
  }

  return key;
}

function isMappedActionKey(key, exerciseState) {
  const normalizedKey = normalizeMappedKey(key);
  return Object.values(exerciseState).some((exercise) => (
    exercise?.active && exercise.key === normalizedKey
  ));
}

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [isTracking, setIsTracking] = useState(false);
  const [poseFrame, setPoseFrame] = useState(null);
  const [exerciseState, setExerciseState] = useState({
    squats: { key: 'Space', active: false, lastDetection: null },
    jumpingJacks: { key: 'W', active: false, lastDetection: null },
    rightDumbbellRaise: { key: 'D', active: false, lastDetection: null },
    leftDumbbellRaise: { key: 'A', active: false, lastDetection: null },
  });
  const [calibration, setCalibration] = useState({
    calibrated: false,
    sensitivity: 70,
    neutralPose: null,
  });
  const [detections, setDetections] = useState([]);
  const [corrections, setCorrections] = useState([]);
  const [keypresses, setKeypresses] = useState([]);

  useEffect(() => {
    if (!api) return;
    api.getExerciseState().then(setExerciseState);
    api.getCalibration().then(setCalibration);

    const unsubDetection = api.onExerciseDetection((data) => {
      setDetections((prev) => [data, ...prev].slice(0, 50));
      if (data.status === 'invalid') {
        setCorrections((prev) => [
          { id: Date.now(), message: data.message, exercise: data.exercise },
          ...prev,
        ].slice(0, 5));
      }
    });

    const unsubKeypress = api.onKeypressInjected((data) => {
      setKeypresses((prev) => [{ ...data, timestamp: Date.now() }, ...prev].slice(0, 20));
    });

    const unsubTracking = api.onTrackingStatus((status) => {
      setIsTracking(status);
    });

    const unsubPoseFrame = api.onPoseFrame((frame) => {
      setPoseFrame(frame);
    });

    return () => {
      unsubDetection();
      unsubKeypress();
      unsubTracking();
      unsubPoseFrame();
    };
  }, []);

  useEffect(() => {
    if (!isTracking) return undefined;

    const swallowMappedKey = (event) => {
      if (!isMappedActionKey(event.key, exerciseState)) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
      }

      if (document.activeElement && typeof document.activeElement.blur === 'function') {
        document.activeElement.blur();
      }
    };

    window.addEventListener('keydown', swallowMappedKey, true);
    window.addEventListener('keyup', swallowMappedKey, true);

    return () => {
      window.removeEventListener('keydown', swallowMappedKey, true);
      window.removeEventListener('keyup', swallowMappedKey, true);
    };
  }, [exerciseState, isTracking]);

  useEffect(() => {
    if (!isTracking) return;
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
  }, [isTracking]);

  const handleToggleTracking = useCallback(async () => {
    const next = !isTracking;
    setIsTracking(next);
    if (api) {
      const result = await api.toggleTracking(next);
      setExerciseState(result.exerciseState);
    } else {
      setExerciseState((prev) => {
        const updated = { ...prev };
        Object.keys(updated).forEach((k) => {
          updated[k] = { ...updated[k], active: next };
        });
        return updated;
      });
    }
  }, [isTracking]);

  const handleUpdateKey = useCallback(async (exercise, key) => {
    if (api) {
      const result = await api.updateKeyMapping(exercise, key);
      setExerciseState(result);
    } else {
      setExerciseState((prev) => ({
        ...prev,
        [exercise]: { ...prev[exercise], key },
      }));
    }
  }, []);

  const handleCalibrate = useCallback(async () => {
    if (api) {
      const result = await api.calibrate();
      setCalibration(result);
    } else {
      setCalibration((prev) => ({ ...prev, calibrated: true, neutralPose: {} }));
    }
  }, []);

  const handleSensitivity = useCallback(async (value) => {
    if (api) {
      const result = await api.setSensitivity(value);
      setCalibration(result);
    } else {
      setCalibration((prev) => ({ ...prev, sensitivity: value }));
    }
  }, []);

  const dismissCorrection = useCallback((id) => {
    setCorrections((prev) => prev.filter((c) => c.id !== id));
  }, []);

  return (
    <div className="h-screen w-screen flex flex-col bg-panel overflow-hidden">
      <TitleBar />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar activeTab={activeTab} onTabChange={setActiveTab} isTracking={isTracking} />

        <main className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto p-6">
            {activeTab === 'dashboard' && (
              <Dashboard
                exerciseState={exerciseState}
                isTracking={isTracking}
                onUpdateKey={handleUpdateKey}
                keypresses={keypresses}
                detections={detections}
              />
            )}
            {activeTab === 'calibration' && (
              <CalibrationWizard
                calibration={calibration}
                onCalibrate={handleCalibrate}
                onSensitivity={handleSensitivity}
                poseFrame={poseFrame}
              />
            )}
            {activeTab === 'live' && (
              <LiveView
                isTracking={isTracking}
                detections={detections}
                exerciseState={exerciseState}
                poseFrame={poseFrame}
              />
            )}
          </div>

          <IgnitionButton isTracking={isTracking} onToggle={handleToggleTracking} />
        </main>
      </div>

      <div className="fixed top-16 right-6 z-50 flex flex-col gap-2 max-w-sm">
        {corrections.map((c) => (
          <CorrectionToast key={c.id} correction={c} onDismiss={() => dismissCorrection(c.id)} />
        ))}
      </div>
    </div>
  );
}
