const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, globalShortcut } = require('electron');
const path = require('path');
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

let mainWindow = null;
let tray = null;
let isTracking = false;
let mockInterval = null;

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
      click: () => {
        isTracking = !isTracking;
        mainWindow?.webContents.send('tracking-status', isTracking);
        if (isTracking) startMockStream();
        else stopMockStream();
        createTray();
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

function startMockStream() {
  let idx = 0;
  mockInterval = setInterval(() => {
    if (!mainWindow) return;
    const data = mockExerciseData[idx % mockExerciseData.length];
    const payload = {
      ...data,
      timestamp: Date.now(),
      key: exerciseState[data.exercise]?.key || 'Space',
    };
    mainWindow.webContents.send('exercise-detection', payload);

    if (data.status === 'valid') {
      const key = exerciseState[data.exercise]?.key;
      simulateKeyPress(key);
      mainWindow.webContents.send('keypress-injected', {
        exercise: data.exercise,
        key,
      });
    }

    idx++;
  }, 1800);
}

function stopMockStream() {
  if (mockInterval) {
    clearInterval(mockInterval);
    mockInterval = null;
  }
}

// ── IPC Handlers ──

ipcMain.handle('get-exercise-state', () => exerciseState);

ipcMain.handle('update-key-mapping', (_, { exercise, key }) => {
  if (exerciseState[exercise]) {
    exerciseState[exercise].key = key;
  }
  return exerciseState;
});

ipcMain.handle('toggle-tracking', (_, shouldTrack) => {
  isTracking = shouldTrack;
  if (isTracking) {
    Object.keys(exerciseState).forEach((k) => (exerciseState[k].active = true));
    startMockStream();
  } else {
    Object.keys(exerciseState).forEach((k) => (exerciseState[k].active = false));
    stopMockStream();
  }
  return { isTracking, exerciseState };
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

ipcMain.handle('calibrate', () => {
  calibrationData.calibrated = true;
  calibrationData.neutralPose = {
    leftShoulder: { x: 0.3, y: 0.4 },
    rightShoulder: { x: 0.7, y: 0.4 },
    leftHip: { x: 0.35, y: 0.7 },
    rightHip: { x: 0.65, y: 0.7 },
    leftKnee: { x: 0.35, y: 0.85 },
    rightKnee: { x: 0.65, y: 0.85 },
  };
  return calibrationData;
});

ipcMain.handle('set-sensitivity', (_, value) => {
  calibrationData.sensitivity = value;
  return calibrationData;
});

ipcMain.handle('get-calibration', () => calibrationData);

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

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
