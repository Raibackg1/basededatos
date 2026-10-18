/**
 * Pumpfun Cockpit — Native Windows Desktop Application (Electron)
 * 
 * SSOT: AGENTS.md, STATUS.md, PLAN_MAESTRO_COCKPIT_V2.md
 * Mode: Dry-Run / Paper Forward (Zero mocks, fail-closed)
 */
const { app, BrowserWindow, Tray, Menu, ipcMain, shell, Notification, dialog, globalShortcut } = require('electron');
const path = require('path');
const http = require('http');
const { spawn, exec } = require('child_process');
const fs = require('fs');

// Control de instancia única (Single Instance Lock)
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  console.log('[ELECTRON] Ya hay otra instancia de Pumpfun Cockpit en ejecución. Enfocando ventana...');
  app.quit();
}

const APP_PORT = 8787;
const BACKEND_URL = `http://127.0.0.1:${APP_PORT}`;
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const DETECTOR_DIR = path.join(REPO_ROOT, 'pumpfun_detector');

let mainWindow = null;
let tray = null;
let pythonProcess = null;
let isQuitting = false;
let backendLogs = [];
const MAX_LOGS = 600;
let lastKnownStatus = {
  alive: false,
  equity_usd: null,
  open_count: 0,
  bot_enabled: false,
  true_saves: 0
};

function addLog(source, msg) {
  const line = `[${new Date().toLocaleTimeString()}] [${source}] ${msg.trim()}`;
  console.log(line);
  backendLogs.push(line);
  if (backendLogs.length > MAX_LOGS) backendLogs.shift();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend-log', line);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. CHEQUEO Y SUPERVISOR DEL BACKEND PYTHON
// ─────────────────────────────────────────────────────────────────────────────

function checkBackendHealth(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/api/bot/status`, { timeout: timeoutMs }, (res) => {
      let raw = '';
      res.on('data', chunk => raw += chunk);
      res.on('end', () => {
        try {
          const json = JSON.parse(raw);
          const dash = json.dashboard || {};
          const wallet = dash.paper_wallet || {};
          const evo = dash.evolution || {};
          const rt = dash.runtime || {};

          lastKnownStatus = {
            alive: true,
            equity_usd: wallet.equity_usd,
            open_count: wallet.open_count || 0,
            bot_enabled: Boolean(json.auto_bot_enabled),
            true_saves: evo.true_saves || 0,
            stale: rt.stale
          };
          updateTray();
          resolve(true);
        } catch {
          resolve(res.statusCode === 200);
        }
      });
    });
    req.on('error', () => {
      lastKnownStatus.alive = false;
      updateTray();
      resolve(false);
    });
    req.on('timeout', () => {
      req.destroy();
      lastKnownStatus.alive = false;
      updateTray();
      resolve(false);
    });
  });
}

function findPythonExecutable() {
  const candidates = [
    'D:\\Scripts\\python.exe',
    'D:\\python.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Programs\\Python\\Python314\\python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs\\Python\\Python313\\python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs\\Python\\Python312\\python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs\\Python\\Python311\\python.exe'),
    'C:\\Python314\\python.exe',
    'C:\\Python313\\python.exe',
    'C:\\Python312\\python.exe',
    'C:\\Python311\\python.exe',
    'python'
  ];

  for (const cmd of candidates) {
    if (cmd === 'python' || fs.existsSync(cmd)) return cmd;
  }
  return 'python';
}

function spawnPythonBackend() {
  if (pythonProcess) return;

  const pythonExe = findPythonExecutable();
  addLog('SUPERVISOR', `Iniciando motor de backend Python con: ${pythonExe}`);

  // python -m cockpit.launch --headless --port 8787
  const args = ['-m', 'cockpit.launch', '--headless', '--port', String(APP_PORT)];

  try {
    pythonProcess = spawn(pythonExe, args, {
      cwd: DETECTOR_DIR,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8'
      },
      windowsHide: true
    });

    pythonProcess.stdout.on('data', (data) => {
      const text = data.toString('utf-8');
      text.split('\n').filter(Boolean).forEach(l => addLog('PYTHON', l));
    });

    pythonProcess.stderr.on('data', (data) => {
      const text = data.toString('utf-8');
      text.split('\n').filter(Boolean).forEach(l => addLog('PYTHON_ERR', l));
    });

    pythonProcess.on('error', (err) => {
      addLog('SUPERVISOR_ERROR', `Fallo al arrancar backend: ${err.message}`);
      pythonProcess = null;
    });

    pythonProcess.on('exit', (code, signal) => {
      addLog('SUPERVISOR', `Backend Python finalizado con código: ${code}, señal: ${signal}`);
      pythonProcess = null;
      lastKnownStatus.alive = false;
      updateTray();
    });
  } catch (err) {
    addLog('SUPERVISOR_CRITICAL', `Excepción al spawnear backend: ${err.message}`);
  }
}

function killPythonBackend() {
  if (pythonProcess && pythonProcess.pid) {
    addLog('SUPERVISOR', `Deteniendo subproceso Python (PID ${pythonProcess.pid})...`);
    try {
      if (process.platform === 'win32') {
        exec(`taskkill /pid ${pythonProcess.pid} /T /F`);
      } else {
        pythonProcess.kill('SIGTERM');
      }
    } catch (e) {
      // Ignorar errores al matar
    }
    pythonProcess = null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. VENTANA PRINCIPAL
// ─────────────────────────────────────────────────────────────────────────────

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1540,
    height: 960,
    minWidth: 1200,
    minHeight: 750,
    backgroundColor: '#07090e',
    title: 'Pumpfun Cockpit — Terminal IA & Observabilidad Cuantitativa',
    icon: path.join(__dirname, '..', 'assets', 'icon.png'),
    frame: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: true
    },
    show: true
  });

  const splashPath = path.join(__dirname, 'splash.html');

  // Enfoque garantizado al estar listo
  mainWindow.once('ready-to-show', () => {
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
  });

  // Cargar splash UI de inicio
  mainWindow.loadFile(splashPath).catch(err => {
    addLog('SUPERVISOR_WARN', `Error cargando splash: ${err.message}`);
  });

  // Supervisor de fallos al cargar
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL) => {
    addLog('SUPERVISOR_WARN', `did-fail-load en ${validatedURL} (código ${errorCode}): ${errorDescription}`);
    if (validatedURL && validatedURL.includes(String(APP_PORT))) {
      mainWindow.loadFile(splashPath).then(() => {
        mainWindow.webContents.send('init-error', {
          message: `Reconectando al backend local (código ${errorCode}: ${errorDescription})...`
        });
        setTimeout(() => initBackendAndLoadApp(), 2500);
      }).catch(() => {});
    }
  });

  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow.hide();
      showDesktopNotification('Pumpfun Cockpit', 'La terminal sigue activa y vigilando en la bandeja de sistema.');
    }
  });

  // Abrir enlaces externos en el navegador del sistema
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http:') || url.startsWith('https:')) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  // Iniciar supervisor del backend de forma inmediata
  initBackendAndLoadApp();

  // Inyectar CSS y script desktop cuando termine de cargar
  mainWindow.webContents.on('did-finish-load', () => {
    const currentUrl = mainWindow.webContents.getURL();
    if (currentUrl.includes(String(APP_PORT))) {
      mainWindow.webContents.insertCSS(`
        /* Desktop titlebar spacing para botones de Windows 11 */
        #top-bar {
          padding-left: 16px !important;
          padding-right: 140px !important;
          -webkit-app-region: drag;
        }
        #top-bar button, #top-bar input, #top-bar select, .nav-modes, .ticker-pill {
          -webkit-app-region: no-drag;
        }
        .brand {
          user-select: none;
        }
      `);

      mainWindow.webContents.executeJavaScript(`
        (function() {
          if (window.__electron_enhanced) return;
          window.__electron_enhanced = true;
          
          // 1. Desktop Badge en el header
          const brand = document.querySelector('.brand');
          if (brand && !document.getElementById('electron-desktop-badge')) {
            const badge = document.createElement('span');
            badge.id = 'electron-desktop-badge';
            badge.className = 'brand-tag';
            badge.style.background = 'rgba(0, 229, 255, 0.15)';
            badge.style.color = '#00e5ff';
            badge.style.borderColor = '#00e5ff';
            badge.style.marginLeft = '6px';
            badge.innerHTML = '⚡ DESKTOP NATIVE';
            brand.appendChild(badge);
          }

          // 2. Sintetizador de Audio Web (Chimes Cuantitativos)
          let audioCtx = null;
          function getAudioContext() {
            if (!audioCtx) {
              audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            return audioCtx;
          }

          function playChime(freqs, times, type = 'sine', gainVal = 0.08) {
            try {
              const ctx = getAudioContext();
              if (ctx.state === 'suspended') ctx.resume();
              const now = ctx.currentTime;
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.type = type;
              gain.gain.setValueAtTime(gainVal, now);
              freqs.forEach((f, idx) => {
                osc.frequency.setValueAtTime(f, now + (times[idx] || 0));
              });
              gain.gain.exponentialRampToValueAtTime(0.0001, now + (times[times.length - 1] || 0.3) + 0.1);
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.start(now);
              osc.stop(now + (times[times.length - 1] || 0.3) + 0.15);
            } catch (e) {
              console.warn('Audio chime error:', e);
            }
          }

          window.desktopAudio = {
            buyChime: () => playChime([440, 880], [0, 0.1], 'triangle', 0.1),
            tpChime: () => playChime([587, 880, 1174], [0, 0.08, 0.18], 'sine', 0.12),
            slChime: () => playChime([220, 160], [0, 0.12], 'sawtooth', 0.08),
            vetoChime: () => playChime([1200, 600], [0, 0.08], 'sine', 0.06)
          };

          // 3. Monitor de eventos para notificaciones de escritorio y sonidos
          let prevOpenCount = null;
          let prevTrueSaves = null;

          setInterval(async () => {
            if (!window.electronAPI) return;
            try {
              const st = await window.electronAPI.getServerStatus();
              if (st && st.open_count !== undefined) {
                if (prevOpenCount !== null && st.open_count > prevOpenCount) {
                  window.desktopAudio.buyChime();
                  window.electronAPI.notify('Nueva Posición Paper Abierta', 'El bot abrió una nueva posición forward.');
                }
                prevOpenCount = st.open_count;
              }
              if (st && st.true_saves !== undefined) {
                if (prevTrueSaves !== null && st.true_saves > prevTrueSaves) {
                  window.desktopAudio.vetoChime();
                  window.electronAPI.notify('🛡️ Shadow Veto Salvó Capital', 'Se evitó una caída gracias a las reglas adaptativas.');
                }
                prevTrueSaves = st.true_saves;
              }
            } catch (err) {}
          }, 3500);

          // 4. Manejador del botón de pánico
          if (window.electronAPI && window.electronAPI.onPanicCloseTriggered) {
            window.electronAPI.onPanicCloseTriggered(() => {
              if (window.desktopAudio) window.desktopAudio.slChime();
              if (typeof window.refreshAll === 'function') window.refreshAll();
            });
          }

          // 5. Cambio de pestaña desde la bandeja de sistema
          if (window.electronAPI && window.electronAPI.onOpenTab) {
            window.electronAPI.onOpenTab((tabId) => {
              if (typeof window.switchMainMode === 'function') {
                window.switchMainMode(tabId);
              }
            });
          }
        })();
      `);
    }
  });
}

async function initBackendAndLoadApp() {
  addLog('SYSTEM', 'Verificando estado del servidor cuantitativo...');
  let alive = await checkBackendHealth(800);

  if (!alive) {
    addLog('SYSTEM', 'Backend no detectado. Iniciando supervisor automático...');
    spawnPythonBackend();

    // Esperar hasta que responda (máximo 25 intentos = ~25 segs)
    let attempts = 0;
    while (attempts < 25) {
      await new Promise(r => setTimeout(r, 1000));
      attempts++;
      alive = await checkBackendHealth(1000);
      if (alive) break;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('init-progress', {
          step: `Arrancando servidor cuantitativo (intento ${attempts}/25)...`,
          percent: Math.min(95, attempts * 4)
        });
      }
    }
  }

  if (alive) {
    addLog('SYSTEM', `Servidor listo en ${BACKEND_URL}. Cargando interfaz de usuario...`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(BACKEND_URL);
    }
  } else {
    addLog('SYSTEM_ERROR', 'No se pudo conectar al servidor tras 25 segundos.');
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('init-error', {
        message: 'No se pudo inicializar el motor Python de Pumpfun Cockpit. Revisa los logs de arranque en el visor.'
      });
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. SYSTEM TRAY (BANDEJA DEL SISTEMA)
// ─────────────────────────────────────────────────────────────────────────────

function createTray() {
  try {
    const iconPath = path.join(__dirname, '..', 'assets', 'tray.png');
    // Si no existe el icono aún, usar icono general
    const fallbackIcon = path.join(__dirname, '..', 'assets', 'icon.png');
    const finalIcon = fs.existsSync(iconPath) ? iconPath : fallbackIcon;

    tray = new Tray(finalIcon);
    tray.setToolTip('Pumpfun Cockpit — Terminal Cuantitativa');

    tray.on('double-click', () => {
      if (mainWindow) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        if (!mainWindow.isVisible()) mainWindow.show();
        mainWindow.focus();
      }
    });

    updateTray();
  } catch (err) {
    console.warn('[TRAY] Advertencia al crear icono en bandeja de sistema:', err.message);
    tray = null;
  }
}

function updateTray() {
  if (!tray) return;

  try {
    const statusText = lastKnownStatus.alive
      ? '🟢 Backend: En Línea (127.0.0.1:8787)'
      : '🔴 Backend: Desconectado';

    const equityText = lastKnownStatus.equity_usd !== null
      ? `💰 Equity Paper: $${Number(lastKnownStatus.equity_usd).toLocaleString('en-US', { minimumFractionDigits: 2 })}`
      : '💰 Equity Paper: N/D — sin dato';

    const posText = `📊 Posiciones Abiertas: ${lastKnownStatus.open_count}`;
    const botText = lastKnownStatus.bot_enabled ? '⚡ Bot Auto-Sniper: ACTIVO' : '💤 Bot Auto-Sniper: PAUSADO';
    const savesText = `🛡️ Shadow Saves: ${lastKnownStatus.true_saves}`;

    const contextMenu = Menu.buildFromTemplate([
      { label: 'Pumpfun Cockpit v2.0 (Electron)', enabled: false },
      { type: 'separator' },
      { label: statusText, enabled: false },
      { label: equityText, enabled: false },
      { label: posText, enabled: false },
      { label: botText, enabled: false },
      { label: savesText, enabled: false },
      { type: 'separator' },
      {
        label: '👁️ Mostrar / Traer al Frente',
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        }
      },
      {
        label: lastKnownStatus.bot_enabled ? '⏸️ Pausar Bot Auto-Sniper' : '▶️ Activar Bot Auto-Sniper',
        click: () => toggleAutoBot()
      },
      {
        label: '🚨 BOTÓN DE PÁNICO (Cerrar Todo)',
        click: () => triggerPanicClose()
      },
      { type: 'separator' },
      {
        label: '🔄 Reiniciar Servidor Python',
        click: () => {
          killPythonBackend();
          setTimeout(() => spawnPythonBackend(), 1000);
        }
      },
      {
        label: '📋 Ver Logs en Vivo',
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.webContents.send('open-tab', 'pipeline');
          }
        }
      },
      { type: 'separator' },
      {
        label: '❌ Salir Completamente',
        click: () => {
          isQuitting = true;
          app.quit();
        }
      }
    ]);

    tray.setContextMenu(contextMenu);
    tray.setToolTip(`Pumpfun Cockpit\n${lastKnownStatus.alive ? 'En Línea' : 'Desconectado'} | Pos: ${lastKnownStatus.open_count}`);
  } catch (err) {
    console.warn('[TRAY] Advertencia al actualizar menu de bandeja:', err.message);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. ACCIONES DE CONTROL & COMUNICACIÓN IPC
// ─────────────────────────────────────────────────────────────────────────────

function postJson(endpoint, body) {
  return new Promise((resolve) => {
    const data = JSON.stringify(body || {});
    const req = http.request(`${BACKEND_URL}${endpoint}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data)
      },
      timeout: 3000
    }, (res) => {
      let raw = '';
      res.on('data', chunk => raw += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(raw));
        } catch {
          resolve({ ok: res.statusCode === 200, raw });
        }
      });
    });

    req.on('error', (err) => resolve({ ok: false, error: err.message }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, error: 'timeout' }); });
    req.write(data);
    req.end();
  });
}

async function toggleAutoBot() {
  addLog('CONTROL', 'Alternando estado del Bot Auto-Sniper...');
  const res = await postJson('/api/bot/control', {});
  if (res && res.ok !== undefined) {
    lastKnownStatus.bot_enabled = Boolean(res.auto_bot_enabled);
    updateTray();
    showDesktopNotification(
      'Bot Auto-Sniper',
      lastKnownStatus.bot_enabled ? 'Bot ACTIVADO en modo autónomo.' : 'Bot PAUSADO. Operaciones suspendidas.'
    );
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('bot-status-changed', lastKnownStatus.bot_enabled);
    }
  }
}

async function triggerPanicClose() {
  const choice = dialog.showMessageBoxSync(mainWindow || null, {
    type: 'warning',
    buttons: ['Cancelar', 'CERRAR TODAS LAS POSICIONES'],
    defaultId: 0,
    title: 'Confirmación de Pánico',
    message: '¿Estás seguro de ejecutar el BOTÓN DE PÁNICO?',
    detail: 'Esto cerrará inmediatamente todas las posiciones paper abiertas a precio de mercado.'
  });

  if (choice === 1) {
    addLog('PANIC', 'Ejecutando Panic Close en el servidor...');
    const res = await postJson('/api/bot/panic_close', {});
    addLog('PANIC', `Resultado de Panic Close: ${JSON.stringify(res)}`);
    showDesktopNotification('Botón de Pánico', 'Se ha enviado la orden de cierre para todas las posiciones abiertas.');
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('panic-close-triggered');
    }
  }
}

function showDesktopNotification(title, body) {
  if (Notification.isSupported()) {
    new Notification({
      title,
      body,
      icon: path.join(__dirname, '..', 'assets', 'icon.png')
    }).show();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. REGISTRO DE ATAJOS GLOBALES
// ─────────────────────────────────────────────────────────────────────────────

function registerShortcuts() {
  // Pánico: Ctrl+Shift+P
  globalShortcut.register('CommandOrControl+Shift+P', () => {
    triggerPanicClose();
  });

  // Toggle Bot: Ctrl+Shift+B
  globalShortcut.register('CommandOrControl+Shift+B', () => {
    toggleAutoBot();
  });

  // Toggle DevTools: Ctrl+Shift+I
  globalShortcut.register('CommandOrControl+Shift+I', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.toggleDevTools();
    }
  });

  // Command Palette: Ctrl+K
  globalShortcut.register('CommandOrControl+K', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('trigger-command-palette');
    }
  });

  // Toggle AI Co-Pilot: Ctrl+J
  globalShortcut.register('CommandOrControl+J', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('trigger-ai-copilot');
    }
  });

  // Keyboard Shortcuts HUD: F1
  globalShortcut.register('F1', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('trigger-shortcuts');
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. IPC HANDLERS PARA EL RENDERER
// ─────────────────────────────────────────────────────────────────────────────

let secondaryWindows = [];

ipcMain.handle('get-system-telemetry', async () => {
  const os = require('os');
  const cpus = os.cpus();
  const totalMem = os.totalmem();
  const freeMem = os.freemem();
  const usedMem = totalMem - freeMem;
  const procMem = process.memoryUsage();
  return {
    cpuCount: cpus.length,
    cpuModel: cpus[0] ? cpus[0].model : 'Unknown',
    totalMemMb: Math.round(totalMem / (1024 * 1024)),
    usedMemMb: Math.round(usedMem / (1024 * 1024)),
    freeMemMb: Math.round(freeMem / (1024 * 1024)),
    memPercent: Math.round((usedMem / totalMem) * 100),
    electronHeapUsedMb: Math.round(procMem.heapUsed / (1024 * 1024)),
    pythonPid: pythonProcess ? pythonProcess.pid : null,
    pythonRunning: Boolean(pythonProcess && !pythonProcess.killed),
    platform: process.platform,
    arch: process.arch
  };
});

ipcMain.handle('take-screenshot', async () => {
  if (!mainWindow || mainWindow.isDestroyed()) return { ok: false };
  const { clipboard } = require('electron');
  const image = await mainWindow.webContents.capturePage();
  clipboard.writeImage(image);
  return { ok: true, dataUrl: image.toDataURL() };
});

ipcMain.handle('popout-window', async (event, { targetTab, title }) => {
  const popout = new BrowserWindow({
    width: 1100,
    height: 750,
    backgroundColor: '#07090e',
    title: title || `Pumpfun Cockpit Popout — ${targetTab.toUpperCase()}`,
    icon: path.join(__dirname, '..', 'assets', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  popout.loadURL(`${BACKEND_URL}/#mode=${targetTab}`);
  secondaryWindows.push(popout);
  popout.on('closed', () => {
    secondaryWindows = secondaryWindows.filter(w => w !== popout);
  });
  return { ok: true };
});

ipcMain.handle('get-server-status', async () => {
  await checkBackendHealth(800);
  return lastKnownStatus;
});

ipcMain.handle('get-backend-logs', () => {
  return backendLogs;
});

ipcMain.handle('restart-backend', async () => {
  killPythonBackend();
  await new Promise(r => setTimeout(r, 1000));
  spawnPythonBackend();
  return { ok: true };
});

ipcMain.handle('toggle-bot', async () => {
  await toggleAutoBot();
  return lastKnownStatus.bot_enabled;
});

ipcMain.handle('panic-close', async () => {
  await triggerPanicClose();
  return { ok: true };
});

ipcMain.handle('notify', (event, { title, body }) => {
  showDesktopNotification(title, body);
  return true;
});

ipcMain.handle('export-file', async (event, { defaultName, content, ext }) => {
  const { filePath } = await dialog.showSaveDialog(mainWindow, {
    title: 'Exportar Datos de Pumpfun Cockpit',
    defaultPath: defaultName || `cockpit_export_${Date.now()}.${ext || 'csv'}`,
    filters: [
      { name: ext ? ext.toUpperCase() : 'Archivos', extensions: [ext || 'csv', 'json', 'txt'] }
    ]
  });

  if (filePath) {
    fs.writeFileSync(filePath, content, 'utf-8');
    return { ok: true, path: filePath };
  }
  return { ok: false, canceled: true };
});

// ─────────────────────────────────────────────────────────────────────────────
// 7. CICLO DE VIDA DE LA APLICACIÓN
// ─────────────────────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createMainWindow();
  createTray();
  registerShortcuts();

  // Polling de salud cada 4 segundos para mantener tray y estado sincronizados
  setInterval(() => {
    checkBackendHealth(1200);
  }, 4000);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    } else if (mainWindow) {
      mainWindow.show();
    }
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  killPythonBackend();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // Si no está configurado para quedarse en bandeja, o si isQuitting = true
    if (isQuitting) {
      app.quit();
    }
  }
});
