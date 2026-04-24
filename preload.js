const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('motionAPI', {
  getExerciseState: () => ipcRenderer.invoke('get-exercise-state'),
  updateKeyMapping: (exercise, key) => ipcRenderer.invoke('update-key-mapping', { exercise, key }),
  toggleTracking: (shouldTrack) => ipcRenderer.invoke('toggle-tracking', shouldTrack),
  startExercise: (name) => ipcRenderer.invoke('start-exercise', name),
  stopExercise: (name) => ipcRenderer.invoke('stop-exercise', name),

  calibrate: () => ipcRenderer.invoke('calibrate'),
  setSensitivity: (value) => ipcRenderer.invoke('set-sensitivity', value),
  setProvider: (provider) => ipcRenderer.invoke('set-provider', provider),
  getCalibration: () => ipcRenderer.invoke('get-calibration'),

  onExerciseDetection: (callback) => {
    const handler = (_, data) => callback(data);
    ipcRenderer.on('exercise-detection', handler);
    return () => ipcRenderer.removeListener('exercise-detection', handler);
  },
  onKeypressInjected: (callback) => {
    const handler = (_, data) => callback(data);
    ipcRenderer.on('keypress-injected', handler);
    return () => ipcRenderer.removeListener('keypress-injected', handler);
  },
  onTrackingStatus: (callback) => {
    const handler = (_, status) => callback(status);
    ipcRenderer.on('tracking-status', handler);
    return () => ipcRenderer.removeListener('tracking-status', handler);
  },
  onPoseFrame: (callback) => {
    const handler = (_, frame) => callback(frame);
    ipcRenderer.on('pose-frame', handler);
    return () => ipcRenderer.removeListener('pose-frame', handler);
  },
  processVideoFrame: (frame) => ipcRenderer.invoke('process-video-frame', frame),
  setPreviewActive: (active) => ipcRenderer.invoke('set-preview-active', active),

  windowMinimize: () => ipcRenderer.invoke('window-minimize'),
  windowMaximize: () => ipcRenderer.invoke('window-maximize'),
  windowClose: () => ipcRenderer.invoke('window-close'),
});
