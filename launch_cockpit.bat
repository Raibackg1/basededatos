@echo off
chcp 65001 >nul
title Pumpfun Cockpit - Lanzador Nativo Windows
cd /d "%~dp0pumpfun_detector"
echo =====================================================================
echo   PUMPFUN COCKPIT - ANALISIS CUANTITATIVO, IA REAL Y OBSERVABILIDAD
echo =====================================================================
echo.
echo [1/3] Verificando entorno nativo...
python -c "import webview" 2>nul
if %errorlevel% neq 0 (
    echo [*] Instalando pywebview para ventana nativa de escritorio...
    pip install pywebview
) else (
    echo [OK] pywebview (WebView2) listo.
)

echo [2/3] Verificando motor de IA y base de datos...
python -c "import sqlite3; print('[OK] Entorno Python e IA listo.')"

echo [3/3] Iniciando Cockpit en http://127.0.0.1:8787...
python -m cockpit.launch --port 8787
if %errorlevel% neq 0 (
    echo.
    echo [!] El servidor finalizo con codigo %errorlevel%.
)
pause
