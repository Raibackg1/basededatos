# Pumpfun Quant Cockpit — Aplicación Desktop Nativa en Electron (v2.0)

> **Terminal Cuantitativa y Cockpit de Trading Algorítmico en Solana (Pump.fun)**  
> **Modo Operativo Obligatorio:** DRY-RUN / PAPER FORWARD (Fondos reales estrictamente bloqueados)  
> **Arquitectura:** Electron v44 + Node.js v24 + Python 3.14 + Chromium acelerado por GPU + Three.js WebGL

---

## 1. Salto Cualitativo (+200% de Mejora sobre WinForms)

| Dimensión | Antes (`PumpfunCockpitLauncher.cs` / WinForms) | Ahora (`cockpit_desktop` / Electron v2.0) |
| :--- | :--- | :--- |
| **Motor Gráfico & Render** | GDI+ arcaico, parpadeos, sin aceleración por hardware ni WebGL | **Chromium v44 / V8 acelerado por GPU**. Renderiza fluido el Visor 3D Causal Forense a 60 FPS, graficado dinámico y transiciones CSS fluidas |
| **Supervisión de Procesos** | Si Python no estaba activo mostraba datos vacíos; dejaba procesos zombies al cerrar | **Supervisor Integrado en `main.js`**: comprueba `127.0.0.1:8787`, arranca el backend automáticamente, captura logs y mata limpiamente todos los subprocesos huérfanos |
| **Tiempo Real & Latencia** | Polling HTTP rígido cada 3.5s perdiendo picos de liquidez | **Streaming Directo Server-Sent Events (SSE)** con latencia <50ms + reconexión automática |
| **Bandeja del Sistema (Tray)** | Ninguna / tosca | **System Tray completo**: estado en vivo, saldo equity, control del bot, botón de pánico y minimización en segundo plano |
| **Notificaciones Nativas** | Ventanas emergentes modales bloqueantes | **Notificaciones Nativas de Windows (Action Center)** ante entradas paper, TP1 (+50%), SL (-33.49%) y Shadow Vetos |
| **Atajos Globales de Teclado** | Ninguno fuera de foco | `Ctrl+Shift+P` (Pánico inmediato), `Ctrl+Shift+B` (Toggle Bot), `Ctrl+Shift+I` (DevTools) |
| **Alertas Auditivas** | Silencio total | **Sintetizador Web Audio API**: arpegios de compra, pings de toma de ganancia y chimes discretos estilo terminal Bloomberg |
| **Anti-falacias & Mocks (§3)** | Tarjetas con valores inventados ("$10,000.00") si fallaba el fetch | **Caja de Cristal estricta**: si el dato no está medido se muestra `N/D — sin dato` |

---

## 2. Puesta en Marcha Rápida (1 Clic)

### Opción A: Lanzador Directo Windows (.bat)
Doble clic en el archivo raíz:
```cmd
launch_cockpit_electron.bat
```

### Opción B: Desde la Consola de Comandos
```powershell
cd cockpit_desktop
npm start
```

---

## 3. Atajos de Teclado Integrados

- **`Ctrl + Shift + P`**: **BOTÓN DE PÁNICO**. Cierra inmediatamente todas las posiciones paper abiertas a precio de mercado.
- **`Ctrl + Shift + B`**: Alternar Bot Auto-Sniper (Activar / Pausar).
- **`F5` / `Ctrl + R`**: Recargar la terminal manteniendo el estado del servidor.
- **`F11`**: Modo Pantalla Completa.
- **`Ctrl + Shift + I`**: Abrir consola de desarrollo (DevTools).

---

## 4. Estructura de Componentes

```
cockpit_desktop/
├── package.json               # Configuración y scripts npm
├── test_desktop.js            # Suite de verificación de integridad
├── README.md                  # Documentación canónica
├── assets/
│   ├── icon.png               # Icono de alta resolución 256x256
│   └── tray.png               # Icono para la bandeja de sistema de Windows
└── src/
    ├── main.js                # Proceso principal: supervisor de Python, ventana, tray, IPC y hotkeys
    ├── preload.js             # Puente seguro contextBridge (window.electronAPI)
    └── splash.html            # Pantalla de carga y diagnóstico de inicio del backend
```
