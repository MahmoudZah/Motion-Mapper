const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const readline = require('readline');
const koffi = require('koffi');

// ── System-level keypress via Windows API ──
const user32 = koffi.load('user32.dll');
const keybd_event = user32.func('void keybd_event(uint8_t bVk, uint8_t bScan, uint32_t dwFlags, uintptr_t dwExtraInfo)');

const VK_MAP = {
  Space: 0x20, W: 0x57, A: 0x41, S: 0x53, D: 0x44,
  E: 0x45, Q: 0x51, R: 0x52, F: 0x46,
  Up: 0x26, Down: 0x28, Left: 0x25, Right: 0x27,
  Shift: 0x10, Ctrl: 0x11, Enter: 0x0D,
  '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34, '5': 0x35,
};

function simulateKeyPress(keyName) {
  const vk = VK_MAP[keyName];
  if (!vk) return;
  keybd_event(vk, 0, 0, 0);   // KEYEVENTF_KEYDOWN
  keybd_event(vk, 0, 2, 0);   // KEYEVENTF_KEYUP
}

function shouldSuppressInjectedInput() {
  return Boolean(mainWindow && !mainWindow.isDestroyed() && mainWindow.isFocused());
}

let mainWindow = null;
let tray = null;
let isTracking = false;
let previewActive = false;
let backendProcess = null;
let backendReady = false;
let backendStartupPromise = null;
let resolveBackendStartup = null;
let rejectBackendStartup = null;
let backendCommandCounter = 0;
const pendingBackendRequests = new Map();

const BACKEND_START_TIMEOUT_MS = 30000;
const BACKEND_COMMAND_TIMEOUT_MS = 5000;
const BACKEND_ROOT = path.join(__dirname, 'backend');
const BACKEND_ENTRY = path.join(BACKEND_ROOT, 'main.py');

const exerciseState = {
  squats: { key: 'Space', active: false, lastDetection: null },
  jumpingJacks: { key: 'W', active: false, lastDetection: null },
  rightDumbbellRaise: { key: 'D', active: false, lastDetection: null },
  leftDumbbellRaise: { key: 'A', active: false, lastDetection: null },
};

let calibrationData = {
  calibrated: false,
  sensitivity: 70,
  neutralPose: null,
};

function getPythonLaunchSpec() {
  if (process.env.MOTION_MAPPER_PYTHON) {
    return { command: process.env.MOTION_MAPPER_PYTHON, args: [] };
  }

  if (process.platform === 'win32') {
    return { command: 'py', args: ['-3'] };
  }

  return { command: 'python3', args: [] };
}

function updateExerciseActivity(active) {
  Object.keys(exerciseState).forEach((name) => {
    exerciseState[name].active = active;
  });
}

function clearPendingBackendRequests(error) {
  for (const [requestId, pending] of pendingBackendRequests.entries()) {
    clearTimeout(pending.timeout);
    pending.reject(error);
    pendingBackendRequests.delete(requestId);
  }
}

function settleBackendStartup(error = null) {
  if (error) {
    rejectBackendStartup?.(error);
  } else {
    resolveBackendStartup?.();
  }
  resolveBackendStartup = null;
  rejectBackendStartup = null;
}

function resolveBackendRequest(requestId, payload, ok = true) {
  if (!requestId) return;
  const pending = pendingBackendRequests.get(requestId);
  if (!pending) return;

  clearTimeout(pending.timeout);
  pendingBackendRequests.delete(requestId);

  if (ok) pending.resolve(payload);
  else pending.reject(new Error(payload?.message || 'Backend request failed.'));
}

function handleExerciseDetection(event) {
  const state = exerciseState[event.exercise];
  if (!state) return;

  const payload = { ...event, key: state.key };
  state.lastDetection = payload.timestamp || Date.now();

  if (isTracking) {
    mainWindow?.webContents.send('exercise-detection', payload);
  }

  if (isTracking && payload.status === 'valid' && state.active) {
    if (shouldSuppressInjectedInput()) {
      return;
    }
    simulateKeyPress(state.key);
    mainWindow?.webContents.send('keypress-injected', {
      exercise: payload.exercise,
      key: state.key,
    });
  }
}

function handleBackendEvent(event) {
  switch (event.type) {
    case 'backend_ready':
      backendReady = true;
      settleBackendStartup();
      return;
    case 'exercise_detection':
      handleExerciseDetection(event);
      return;
    case 'pose_frame':
      mainWindow?.webContents.send('pose-frame', event);
      resolveBackendRequest(event.requestId, event, Boolean(event.ok));
      return;
    case 'calibration_result':
      if (event.calibration) {
        calibrationData = {
          ...calibrationData,
          ...event.calibration,
        };
      }
      resolveBackendRequest(event.requestId, calibrationData, Boolean(event.ok));
      return;
    case 'config_updated':
      if (typeof event.sensitivity === 'number') {
        calibrationData = {
          ...calibrationData,
          sensitivity: event.sensitivity,
        };
      }
      resolveBackendRequest(event.requestId, calibrationData, Boolean(event.ok));
      return;
    case 'command_ack':
      resolveBackendRequest(event.requestId, event, Boolean(event.ok));
      return;
    case 'warning':
      console.warn('[processing-backend]', event.message || event.code || event.type);
      return;
    case 'error':
      console.error('[processing-backend]', event.message || event.code || event.type);
      if (!backendReady) {
        settleBackendStartup(new Error(event.message || 'Backend failed to start.'));
      }
      return;
    case 'shutdown':
      return;
    default:
      console.log('[processing-backend] event:', event);
  }
}

function sendBackendCommand(command, timeoutMs = BACKEND_COMMAND_TIMEOUT_MS) {
  if (!backendProcess || !backendReady || !backendProcess.stdin || backendProcess.stdin.destroyed) {
    return Promise.reject(new Error('Backend is not ready.'));
  }

  const requestId = `backend-${Date.now()}-${++backendCommandCounter}`;
  const payload = { ...command, requestId };

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pendingBackendRequests.delete(requestId);
      reject(new Error(`Timed out waiting for backend command '${command.type}'.`));
    }, timeoutMs);

    pendingBackendRequests.set(requestId, { resolve, reject, timeout });

    try {
      backendProcess.stdin.write(`${JSON.stringify(payload)}\n`);
    } catch (error) {
      clearTimeout(timeout);
      pendingBackendRequests.delete(requestId);
      reject(error);
    }
  });
}

function startBackend() {
  if (backendProcess && backendReady) {
    return Promise.resolve();
  }
  if (backendStartupPromise) {
    return backendStartupPromise;
  }

  const python = getPythonLaunchSpec();
  const args = [
    ...python.args,
    BACKEND_ENTRY,
    '--sensitivity',
    String(calibrationData.sensitivity),
  ];

  backendReady = false;
  backendStartupPromise = new Promise((resolve, reject) => {
    resolveBackendStartup = resolve;
    rejectBackendStartup = reject;

    const child = spawn(python.command, args, {
      cwd: BACKEND_ROOT,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
      },
      windowsHide: true,
    });

    backendProcess = child;

    const startupTimer = setTimeout(() => {
      settleBackendStartup(new Error('Timed out waiting for the processing backend to become ready.'));
      if (backendProcess && !backendReady) {
        backendProcess.kill();
      }
    }, BACKEND_START_TIMEOUT_MS);

    const stdout = readline.createInterface({ input: child.stdout });
    const stderr = readline.createInterface({ input: child.stderr });

    stdout.on('line', (line) => {
      if (!line.trim()) return;
      try {
        handleBackendEvent(JSON.parse(line));
      } catch (error) {
        console.error('[processing-backend] failed to parse stdout:', line, error);
      }
    });

    stderr.on('line', (line) => {
      if (line.trim()) {
        console.log('[processing-backend]', line);
      }
    });

    child.once('error', (error) => {
      clearTimeout(startupTimer);
      settleBackendStartup(error);
    });

    child.once('close', (code, signal) => {
      clearTimeout(startupTimer);
      const startupError = !backendReady
        ? new Error(`Processing backend exited before becoming ready (code=${code}, signal=${signal}).`)
        : null;

      backendProcess = null;
      backendReady = false;
      clearPendingBackendRequests(new Error('Processing backend stopped.'));

      if (startupError) {
        settleBackendStartup(startupError);
      } else {
        settleBackendStartup();
      }

      if (isTracking) {
        isTracking = false;
        updateExerciseActivity(false);
        mainWindow?.webContents.send('tracking-status', false);
        createTray();
      }
    });
  }).finally(() => {
    backendStartupPromise = null;
  });

  return backendStartupPromise;
}

async function stopBackend() {
  if (!backendProcess) {
    backendReady = false;
    return;
  }

  const processRef = backendProcess;
  if (backendReady) {
    try {
      await sendBackendCommand({ type: 'shutdown' }, 1500);
    } catch (error) {
      console.warn('[processing-backend] graceful shutdown failed:', error.message);
    }
  }

  if (processRef.exitCode === null && !processRef.killed) {
    processRef.kill();
  }
}

async function setTrackingState(shouldTrack) {
  if (shouldTrack) {
    await startBackend();
    isTracking = true;
    updateExerciseActivity(true);
  } else {
    isTracking = false;
    updateExerciseActivity(false);
    if (!previewActive) {
      await stopBackend();
    }
  }

  mainWindow?.webContents.send('tracking-status', isTracking);
  createTray();
  return { isTracking, exerciseState };
}

async function setPreviewState(shouldPreview) {
  previewActive = shouldPreview;
  if (previewActive) {
    await startBackend();
  } else if (!isTracking) {
    await stopBackend();
  }

  return { previewActive, backendReady: backendReady || Boolean(backendProcess) };
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 700,
    frame: false,
    transparent: false,
    backgroundColor: '#0d1117',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    icon: path.join(__dirname, 'assets', 'icon.png'),
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    mainWindow.loadURL('http://localhost:9000');
  } else {
    mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));
  }

  mainWindow.on('close', (e) => {
    if (tray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createTray() {
  if (tray) {
    tray.destroy();
  }

  const icon = nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip('MotionMapper - Virtual Controller');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show MotionMapper',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
    {
      label: isTracking ? 'Stop Tracking' : 'Start Tracking',
      click: async () => {
        try {
          await setTrackingState(!isTracking);
        } catch (error) {
          console.error('[processing-backend] failed to toggle tracking:', error);
          isTracking = false;
          updateExerciseActivity(false);
          mainWindow?.webContents.send('tracking-status', false);
          createTray();
        }
      },
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        tray.destroy();
        tray = null;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on('double-click', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// ── Mock Data Stream (simulates Python backend) ──

const mockExerciseData = [
  { exercise: 'squats', status: 'valid', message: 'Squat Detected: Valid', angle: 92 },
  { exercise: 'squats', status: 'invalid', message: 'Keep your back straight', angle: 65 },
  { exercise: 'jumpingJacks', status: 'valid', message: 'Jumping Jack: Full Extension', angle: 175 },
  { exercise: 'rightDumbbellRaise', status: 'valid', message: 'Right Raise: Angle 90°', angle: 90 },
  { exercise: 'rightDumbbellRaise', status: 'invalid', message: 'Raise arm higher', angle: 45 },
  { exercise: 'leftDumbbellRaise', status: 'valid', message: 'Left Raise: Angle 88°', angle: 88 },
  { exercise: 'leftDumbbellRaise', status: 'invalid', message: 'Lower your shoulder', angle: 52 },
  { exercise: 'squats', status: 'valid', message: 'Squat Detected: Valid', angle: 95 },
  { exercise: 'jumpingJacks', status: 'invalid', message: 'Extend arms fully', angle: 130 },
];

// ── IPC Handlers ──

ipcMain.handle('get-exercise-state', () => exerciseState);

ipcMain.handle('update-key-mapping', (_, { exercise, key }) => {
  if (exerciseState[exercise]) {
    exerciseState[exercise].key = key;
  }
  return exerciseState;
});

ipcMain.handle('toggle-tracking', async (_, shouldTrack) => {
  try {
    return await setTrackingState(shouldTrack);
  } catch (error) {
    console.error('[processing-backend] failed to toggle tracking:', error);
    isTracking = false;
    updateExerciseActivity(false);
    mainWindow?.webContents.send('tracking-status', false);
    createTray();
    return { isTracking, exerciseState };
  }
});

ipcMain.handle('start-exercise', (_, exerciseName) => {
  if (exerciseState[exerciseName]) {
    exerciseState[exerciseName].active = true;
  }
  return exerciseState;
});

ipcMain.handle('stop-exercise', (_, exerciseName) => {
  if (exerciseState[exerciseName]) {
    exerciseState[exerciseName].active = false;
  }
  return exerciseState;
});

ipcMain.handle('calibrate', async () => {
  const shouldStopAfter = !backendProcess;
  try {
    await startBackend();
    calibrationData = await sendBackendCommand({ type: 'calibrate' });
  } catch (error) {
    console.error('[processing-backend] calibration failed:', error);
    calibrationData = {
      ...calibrationData,
      calibrated: false,
    };
  } finally {
    if (shouldStopAfter && !isTracking) {
      await stopBackend();
    }
  }
  return calibrationData;
});

ipcMain.handle('set-sensitivity', async (_, value) => {
  calibrationData.sensitivity = value;
  if (backendProcess && backendReady) {
    try {
      calibrationData = await sendBackendCommand({
        type: 'set_sensitivity',
        sensitivity: value,
      });
    } catch (error) {
      console.error('[processing-backend] sensitivity update failed:', error);
    }
  }
  return calibrationData;
});

ipcMain.handle('get-calibration', () => calibrationData);
ipcMain.handle('process-video-frame', async (_, frame) => {
  try {
    await startBackend();
    return await sendBackendCommand({
      type: 'process_frame',
      image: frame.image,
      timestamp: frame.timestamp,
    }, 10000);
  } catch (error) {
    console.error('[processing-backend] frame processing failed:', error);
    return { ok: false, poseDetected: false };
  }
});
ipcMain.handle('set-preview-active', async (_, shouldPreview) => {
  try {
    return await setPreviewState(Boolean(shouldPreview));
  } catch (error) {
    console.error('[processing-backend] failed to update preview state:', error);
    if (!isTracking) {
      await stopBackend();
    }
    return { previewActive, backendReady: backendReady || Boolean(backendProcess) };
  }
});

ipcMain.handle('window-minimize', () => mainWindow?.minimize());
ipcMain.handle('window-maximize', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize();
  else mainWindow?.maximize();
});
ipcMain.handle('window-close', () => mainWindow?.close());

// ── App Lifecycle ──

app.whenReady().then(() => {
  createWindow();
  createTray();
});

app.on('before-quit', () => {
  if (backendProcess && backendProcess.exitCode === null) {
    backendProcess.kill();
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
