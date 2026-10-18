@echo off
chcp 65001 >nul
title Pumpfun Cockpit — Terminal Desktop Nativa (Electron v2.1)
cd /d "%~dp0cockpit_desktop"

echo =====================================================================
echo   PUMPFUN COCKPIT — TERMINAL DESKTOP NATIVA EN ELECTRON (v2.1)
echo =====================================================================
echo.
echo [*] Verificando entorno Node.js y runtime nativo...
node -v >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] ERROR: Node.js no esta instalado o no se encuentra en el PATH del sistema.
    echo     Descargalo gratuitamente desde https://nodejs.org/
    echo     O ejecuta el lanzador Python directo: launch_cockpit.bat
    pause
    exit /b 1
)

if not exist "node_modules\electron\dist\electron.exe" (
    echo [*] Instalando dependencias de Electron (primera ejecucion)...
    call npm install --no-audit --no-fund
)

echo [OK] Entorno listo. Iniciando Cockpit Desktop...
if exist "node_modules\electron\dist\electron.exe" (
    ".\node_modules\electron\dist\electron.exe" .
) else (
    call npm start
)

if %errorlevel% neq 0 (
    echo.
    echo [!] La aplicacion cerro con codigo de salida %errorlevel%.
    echo [*] Abriendo version web en tu navegador como respaldo...
    start http://127.0.0.1:8787
    pause
)
