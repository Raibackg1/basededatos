"""Lanzador del cockpit: snapshot + servidor + ventana nativa.

Uso
---
    python -m cockpit.launch                 # construye si falta y abre ventana
    python -m cockpit.launch --rebuild       # fuerza reconstruir el snapshot
    python -m cockpit.launch --no-window     # solo servidor (abre el navegador)
    python -m cockpit.launch --port 8787

Ventana nativa: si ``pywebview`` está instalado, se abre una ventana de escritorio
(WebView2 en Windows). Si no, se abre el navegador por defecto. No se instala nada
automáticamente.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from typing import Optional

from . import snapshot as snap_mod
from .server import CockpitState, make_server

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
DEFAULT_SNAPSHOT = os.path.join(RUNTIME_DIR, "snapshot.json")


def _build_if_needed(state: CockpitState, rebuild: bool, sample: int) -> None:
    exists = os.path.exists(state.snapshot_path)
    if rebuild or not exists:
        print(f"[cockpit] construyendo snapshot desde {state.db_path} (solo lectura)...")
        print("[cockpit] escaneo de la base en curso; puede tardar ~1 min.")
        t0 = time.time()
        data = state.rebuild(sample=sample)
        print(f"[cockpit] snapshot {'OK' if data.get('ok') else 'ERROR'} "
              f"en {time.time()-t0:.1f}s -> {state.snapshot_path}")
        if not data.get("ok"):
            print(f"[cockpit] detalle: {data.get('error')}")
    else:
        print(f"[cockpit] snapshot existente: {state.snapshot_path}")


def _open_app_window(url: str) -> Optional[Any]:
    """Abre un navegador Chromium (Brave, Chrome, Edge) en modo app nativa (sin pestañas ni barra de URL)."""
    import subprocess
    candidates = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for exe in candidates:
        if os.path.isfile(exe):
            try:
                proc = subprocess.Popen([exe, f"--app={url}", "--window-size=1520,940"])
                return proc
            except Exception:
                pass
    return None


def _open_window(url: str, no_window: bool) -> bool:
    """Abre la ventana. Retorna True si se debe mantener el bucle vivo (servidor), o False si la ventana ya terminó."""
    if no_window:
        print(f"[cockpit] abre en tu navegador: {url}")
        webbrowser.open(url)
        return True

    # 1. Intentar primero Chromium App Mode (Brave / Chrome / Edge) para ventana nativa limpia
    proc = _open_app_window(url)
    if proc is not None:
        print("[cockpit] ventana nativa abierta vía Chromium App Mode (manteniendo servidor HTTP activo).", flush=True)
        return True

    # 2. Fallback a pywebview si está disponible
    try:
        import webview  # pywebview
        print("[cockpit] abriendo ventana nativa (WebView2)...")
        webview.create_window("Pumpfun Cockpit — Terminal IA & Detector de Patrones", url,
                              width=1520, height=940)
        webview.start()
        return True
    except Exception as exc:
        print(f"[cockpit] pywebview exception ({exc}) -> intentando navegador por defecto...", flush=True)

    print("[cockpit] abriendo en el navegador...", flush=True)
    webbrowser.open(url)
    return True


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cockpit de observabilidad (solo lectura).")
    p.add_argument("--db", default=snap_mod.DEFAULT_DB, help="Base canónica (solo lectura).")
    p.add_argument("--live-store", default=None, help="LiveStore forward (opcional).")
    p.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="Ruta del snapshot JSON.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--sample", type=int, default=3000, help="Puntos 3D a muestrear.")
    p.add_argument("--rebuild", action="store_true", help="Reconstruir el snapshot.")
    p.add_argument("--no-window", action="store_true", help="No abrir ventana nativa.")
    return p


def main(argv: Optional[list] = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = _build_parser().parse_args(argv)
    state = CockpitState(args.snapshot, args.db, args.live_store, auto_loop=True)
    _build_if_needed(state, args.rebuild, args.sample)

    srv = make_server(state, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[cockpit] servidor en {url}  (Ctrl+C para salir)", flush=True)
    try:
        keep_running = _open_window(url, args.no_window)
        if keep_running:
            while True:
                time.sleep(0.5)
    except (KeyboardInterrupt, SystemExit):
        print("\n[cockpit] cerrando servidor...", flush=True)
    finally:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
