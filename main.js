const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, screen, session } = require('electron');
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
let overlayWindow = null;
let alertsWindow = null;
let tray = null;
let isTracking = false;
let previewActive = false;
let backendProcess = null;
let backendReady = false;
let backendStartupPromise = null;
let resolveBackendStartup = null;
let rejectBackendStartup = null;
let backendCommandCounter = 0;
let restartingBackend = false;
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
  provider: 'yolo',
  availableProviders: [
    { id: 'yolo', label: 'YOLO Pose', description: 'Fast COCO-style 17-joint pose model.' },
    { id: 'mediapipe', label: 'MediaPipe Pose', description: 'Google pose landmarker with built-in tracking.' },
  ],
  model: null,
};

function getPythonLaunchSpec() {
  if (process.env.MOTION_MAPPER_PYTHON) {
    return { command: process.env.MOTION_MAPPER_PYTHON, args: [] };
  }

  if (process.platform === 'win32') {
    return { command: 'py', args: ['-3.13'] };
  }

  return { command: 'python3', args: [] };
}

function updateExerciseActivity(active) {
  Object.keys(exerciseState).forEach((name) => {
    exerciseState[name].active = active;
  });
}

function getActiveExercises() {
  return Object.entries(exerciseState)
    .filter(([, state]) => state.active)
    .map(([exercise]) => exercise);
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

  if (isTracking && state.active) {
    mainWindow?.webContents.send('exercise-detection', payload);
    if (overlayWindow && !overlayWindow.isDestroyed()) {
      overlayWindow.webContents.send('exercise-detection', payload);
    }
    if (alertsWindow && !alertsWindow.isDestroyed()) {
      alertsWindow.webContents.send('exercise-detection', payload);
    }
  }

  if (isTracking && state.active && payload.status === 'valid') {
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
      calibrationData = {
        ...calibrationData,
        provider: event.model?.provider || calibrationData.provider,
        model: event.model || calibrationData.model,
      };
      settleBackendStartup();
      return;
    case 'exercise_detection':
      handleExerciseDetection(event);
      return;
    case 'pose_frame':
      mainWindow?.webContents.send('pose-frame', event);
      if (overlayWindow && !overlayWindow.isDestroyed()) {
        overlayWindow.webContents.send('pose-frame', event);
      }
      resolveBackendRequest(event.requestId, event, Boolean(event.ok));
      return;
    case 'calibration_result':
      if (event.command === 'calibrate_exercise' || event.command === 'remove_calibration') {
        // Per-exercise calibration — resolve with the raw result so the
        // renderer gets { ok, exercise, message, calibrationStatus }.
        resolveBackendRequest(event.requestId, event, Boolean(event.ok));
      } else {
        // Global neutral-pose calibration.
        if (event.calibration) {
          calibrationData = {
            ...calibrationData,
            ...event.calibration,
          };
        }
        resolveBackendRequest(event.requestId, calibrationData, Boolean(event.ok));
      }
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
    '--provider',
    calibrationData.provider,
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

      if (isTracking && !restartingBackend) {
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

async function restartBackendPreservingState() {
  const shouldResumeTracking = isTracking;
  const shouldResumePreview = previewActive;
  const shouldRestart = Boolean(backendProcess) || shouldResumeTracking || shouldResumePreview;
  if (!shouldRestart) {
    return;
  }

  restartingBackend = true;
  try {
    await stopBackend();
    if (shouldResumeTracking || shouldResumePreview) {
      await startBackend();
    }
    if (shouldResumeTracking) {
      isTracking = true;
      updateExerciseActivity(true);
      mainWindow?.webContents.send('tracking-status', true);
    }
  } catch (error) {
    if (shouldResumeTracking) {
      isTracking = false;
      updateExerciseActivity(false);
      mainWindow?.webContents.send('tracking-status', false);
    }
    throw error;
  } finally {
    restartingBackend = false;
    createTray();
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
    backgroundColor: '#000000',
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

// Shared overlay layout constants
const SHARED_W = 256;
const EDGE_X = 20;       // px from right edge
const EDGE_Y = 48;       // px from bottom (above taskbar)
const STACK_GAP = 6;     // gap between camera and alerts windows

const OVERLAY_H_EXPANDED = 180;
const OVERLAY_H_COLLAPSED = 30;
const ALERTS_H = 400;

function overlayX(bounds) { return bounds.x + bounds.width - SHARED_W - EDGE_X; }
function cameraY(bounds)  { return bounds.y + bounds.height - OVERLAY_H_EXPANDED - EDGE_Y; }
function alertsY(bounds)  { return cameraY(bounds) - STACK_GAP - ALERTS_H; }

function createOverlayWindow() {
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.focus();
    return;
  }

  const { bounds } = screen.getPrimaryDisplay();

  overlayWindow = new BrowserWindow({
    x: overlayX(bounds),
    y: cameraY(bounds),
    width: SHARED_W,
    height: OVERLAY_H_EXPANDED,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    overlayWindow.loadURL('http://localhost:9000?overlay=1');
  } else {
    overlayWindow.loadFile(path.join(__dirname, 'dist', 'index.html'), { query: { overlay: '1' } });
  }

  overlayWindow.on('closed', () => {
    overlayWindow = null;
  });
}

function createAlertsWindow() {
  if (alertsWindow && !alertsWindow.isDestroyed()) {
    alertsWindow.focus();
    return;
  }

  const { bounds } = screen.getPrimaryDisplay();

  alertsWindow = new BrowserWindow({
    x: overlayX(bounds),
    y: alertsY(bounds),
    width: SHARED_W,
    height: ALERTS_H,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    alertsWindow.loadURL('http://localhost:9000?alerts=1');
  } else {
    alertsWindow.loadFile(path.join(__dirname, 'dist', 'index.html'), { query: { alerts: '1' } });
  }

  alertsWindow.on('closed', () => { alertsWindow = null; });
}

function createTray() {
  if (tray) {
    tray.destroy();
  }

  const icon = nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip('Gamecha - Virtual Controller');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show Gamecha',
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
  { exercise: 'jumpingJacks', status: 'valid', message: 'Jump peak reached', angle: 18 },
  { exercise: 'rightDumbbellRaise', status: 'valid', message: 'Right curl: angle 82 deg', angle: 82 },
  { exercise: 'rightDumbbellRaise', status: 'invalid', message: 'Right arm: keep the elbow tucked by your side', angle: 108 },
  { exercise: 'leftDumbbellRaise', status: 'valid', message: 'Left curl: angle 79 deg', angle: 79 },
  { exercise: 'leftDumbbellRaise', status: 'invalid', message: 'Left arm: curl higher toward the shoulder', angle: 118 },
  { exercise: 'squats', status: 'valid', message: 'Squat Detected: Valid', angle: 95 },
  { exercise: 'jumpingJacks', status: 'invalid', message: 'Stand tall to reset before the next jump', angle: 42 },
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

ipcMain.handle('set-provider', async (_, provider) => {
  if (!['yolo', 'mediapipe'].includes(provider)) {
    return calibrationData;
  }

  calibrationData = {
    ...calibrationData,
    provider,
    calibrated: false,
    neutralPose: null,
  };

  try {
    await restartBackendPreservingState();
  } catch (error) {
    console.error('[processing-backend] provider update failed:', error);
  }

  return calibrationData;
});

ipcMain.handle('calibrate-exercise', async (_, exercise) => {
  if (!backendProcess || !backendReady) {
    return { ok: false, exercise, message: 'Backend is not running. Start tracking first.' };
  }
  try {
    const result = await sendBackendCommand({
      type: 'calibrate_exercise',
      exercise,
    });
    return result;
  } catch (error) {
    console.error('[processing-backend] exercise calibration failed:', error);
    return { ok: false, exercise, message: error.message };
  }
});

ipcMain.handle('remove-calibration', async (_, exercise) => {
  if (!backendProcess || !backendReady) {
    return { ok: false, exercise, message: 'Backend is not running.' };
  }
  try {
    const result = await sendBackendCommand({
      type: 'remove_calibration',
      exercise,
    });
    return result;
  } catch (error) {
    console.error('[processing-backend] remove calibration failed:', error);
    return { ok: false, exercise, message: error.message };
  }
});

ipcMain.handle('get-calibration', () => calibrationData);
ipcMain.handle('process-video-frame', async (_, frame) => {
  try {
    await startBackend();
    return await sendBackendCommand({
      type: 'process_frame',
      image: frame.image,
      timestamp: frame.timestamp,
      activeExercises: isTracking ? getActiveExercises() : [],
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

ipcMain.handle('toggle-overlays', () => {
  const overlayOpen = overlayWindow && !overlayWindow.isDestroyed();
  const alertsOpen = alertsWindow && !alertsWindow.isDestroyed();
  if (overlayOpen || alertsOpen) {
    overlayWindow?.close();
    alertsWindow?.close();
  } else {
    createOverlayWindow();
    createAlertsWindow();
  }
});
ipcMain.handle('close-overlays', () => {
  overlayWindow?.close();
  alertsWindow?.close();
});
ipcMain.handle('overlay-set-collapsed', (_, collapsed) => {
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    const h = collapsed ? OVERLAY_H_COLLAPSED : OVERLAY_H_EXPANDED;
    overlayWindow.setSize(SHARED_W, h);
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
  // Grant camera/media permissions to the renderer process
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    const allowed = ['media', 'mediaKeySystem'].includes(permission);
    callback(allowed);
  });
  session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
    return ['media', 'mediaKeySystem'].includes(permission);
  });

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
