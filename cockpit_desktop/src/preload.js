/**
 * Preload Script — Exposición segura de APIs Nativas de Electron v2.1
 * Modo: Zero-trust, Context Isolation
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  version: '2.1.0',
  
  // Consultas de estado
  getServerStatus: () => ipcRenderer.invoke('get-server-status'),
  getBackendLogs: () => ipcRenderer.invoke('get-backend-logs'),
  getSystemTelemetry: () => ipcRenderer.invoke('get-system-telemetry'),
  
  // Acciones de escritorio
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  toggleBot: () => ipcRenderer.invoke('toggle-bot'),
  panicClose: () => ipcRenderer.invoke('panic-close'),
  notify: (title, body) => ipcRenderer.invoke('notify', { title, body }),
  exportFile: (defaultName, content, ext) => ipcRenderer.invoke('export-file', { defaultName, content, ext }),
  takeScreenshot: () => ipcRenderer.invoke('take-screenshot'),
  popoutWindow: (targetTab, title) => ipcRenderer.invoke('popout-window', { targetTab, title }),
  
  // Eventos entrantes desde el Main Process (Supervisión & Atajos)
  onBackendLog: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('backend-log', sub);
    return () => ipcRenderer.removeListener('backend-log', sub);
  },
  onInitProgress: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('init-progress', sub);
    return () => ipcRenderer.removeListener('init-progress', sub);
  },
  onInitError: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('init-error', sub);
    return () => ipcRenderer.removeListener('init-error', sub);
  },
  onBotStatusChanged: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('bot-status-changed', sub);
    return () => ipcRenderer.removeListener('bot-status-changed', sub);
  },
  onPanicCloseTriggered: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('panic-close-triggered', sub);
    return () => ipcRenderer.removeListener('panic-close-triggered', sub);
  },
  onOpenTab: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('open-tab', sub);
    return () => ipcRenderer.removeListener('open-tab', sub);
  },
  onTriggerCommandPalette: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('trigger-command-palette', sub);
    return () => ipcRenderer.removeListener('trigger-command-palette', sub);
  },
  onTriggerAiCopilot: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('trigger-ai-copilot', sub);
    return () => ipcRenderer.removeListener('trigger-ai-copilot', sub);
  },
  onTriggerShortcuts: (callback) => {
    const sub = (event, val) => callback(val);
    ipcRenderer.on('trigger-shortcuts', sub);
    return () => ipcRenderer.removeListener('trigger-shortcuts', sub);
  }
});
