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
from .server import CockpitState, make_server, probe_cockpit

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
DEFAULT_SNAPSHOT = os.path.join(RUNTIME_DIR, "snapshot.json")


def _build_if_needed(state: CockpitState, rebuild: bool, sample: int) -> None:
    """Construye el snapshot si falta, está CORRUPTO, RANCIO o se pidió a mano.

    El bug anterior: solo se construía cuando el archivo no existía, así que un
    ``snapshot.json`` envenenado (``ok:false``) sobrevivía para siempre.
    """
    if rebuild:
        print(f"[cockpit] reconstruyendo snapshot desde {state.db_path} (solo lectura)...")
        t0 = time.time()
        data = state.rebuild(sample=sample)
        print(f"[cockpit] snapshot {'OK' if data.get('ok') else 'ERROR'} "
              f"en {time.time()-t0:.1f}s -> {state.snapshot_path}")
        if not data.get("ok"):
            print(f"[cockpit] detalle: {data.get('error')}")
        return
    result = state.ensure_snapshot(sample=sample)
    if result.get("rebuilt"):
        print(f"[cockpit] snapshot auto-reparado (estaba '{result.get('was')}') "
              f"-> ok={result.get('ok')}")
    else:
        print(f"[cockpit] snapshot '{result.get('state')}' (sin cambios)")


def _print_wiring(state: CockpitState) -> None:
    """Banner del cableado: qué se conectó y qué no, sin adornos ni falsos OK."""
    w = state.wiring()
    db = w.get("database") or {}
    snap = w.get("snapshot") or {}
    loop = w.get("loop") or {}
    cyc = w.get("cycle") or {}
    print("[cockpit] ── cableado ──", flush=True)
    print(f"[cockpit]   base     : {db.get('path')}  ({db.get('resolved_from')})", flush=True)
    missing = db.get("missing_token_columns") or []
    if missing:
        print(f"[cockpit]   aviso    : faltan columnas {missing}; esas métricas saldrán "
              f"como 'no medible', nunca como 0", flush=True)
    print(f"[cockpit]   snapshot : {snap.get('state')} (ok={snap.get('ok')})", flush=True)
    print(f"[cockpit]   feed     : {(w.get('feed') or {}).get('source')}", flush=True)
    print(f"[cockpit]   auto     : loop={'activo' if loop.get('alive') else 'caído'} · "
          f"ciclo={('activo' if cyc.get('alive') else 'caído')} · "
          f"paper={'activo' if (w.get('paper') or {}).get('auto_bot_enabled') else 'pausado'}",
          flush=True)


def _open_app_window(url: str) -> Optional[Any]:
    """Abre un navegador Chromium (Brave, Chrome, Edge) en modo app nativa (sin pestañas ni barra de URL)."""
    import subprocess
    candidates = [
        os.environ.get("BRAVE_PATH", ""),
        os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.environ.get("CHROME_PATH", ""),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.environ.get("EDGE_PATH_X86", ""),
        os.environ.get("EDGE_PATH", ""),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for exe in candidates:
        if exe and os.path.isfile(exe):
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
    p.add_argument("--no-window", action="store_true", help="No abrir ventana nativa (abre el navegador).")
    p.add_argument("--headless", action="store_true",
                   help="Solo servidor: ni ventana nativa ni navegador (sigue siendo autónomo).")
    p.add_argument("--no-loops", action="store_true",
                   help="Servidor pasivo: sin bucle de trading ni ciclo de automejora (CI/depuración).")
    p.add_argument("--allow-duplicate", action="store_true",
                   help="Permitir un segundo servidor en el mismo puerto (solo pruebas).")
    return p


def main(argv: Optional[list] = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = _build_parser().parse_args(argv)

    # Instancia única: si ya hay un cockpit vivo en ese puerto, se REUTILIZA.
    # Sin esto, Windows permite un segundo bind sobre el mismo puerto y el
    # navegador recibe respuestas vacías de forma aleatoria.
    if not args.allow_duplicate:
        live = probe_cockpit(args.host, args.port)
        if live is not None:
            url = f"http://{args.host}:{args.port}/"
            print(f"[cockpit] ya hay un cockpit vivo en {url} (DB: "
                  f"{(live.get('source') or {}).get('path')}); se reutiliza.", flush=True)
            if not args.headless:
                _open_window(url, args.no_window)
                while True:
                    time.sleep(0.5)
            return 0

    # Cableado automático: DB resuelta sola + snapshot auto-reparado + loop + ciclo.
    state = CockpitState(args.snapshot, args.db, args.live_store,
                         auto_loop=not args.no_loops, auto_repair=False)
    _build_if_needed(state, args.rebuild, args.sample)
    _print_wiring(state)

    try:
        srv = make_server(state, args.host, args.port)
    except OSError as exc:
        # Carrera: alguien ganó el puerto entre el sondeo y el bind. Fail-closed:
        # no se arranca un servidor degradado; se reporta y se sale con error.
        print(f"[cockpit] no se pudo ocupar {args.host}:{args.port} ({exc}). "
              f"Otro proceso lo tiene; revisa el puerto o usa --port.", file=sys.stderr, flush=True)
        return 2
    url = f"http://{args.host}:{args.port}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[cockpit] servidor en {url}  (Ctrl+C para salir)", flush=True)
    try:
        if args.headless:
            print("[cockpit] modo headless: sin ventana ni navegador.", flush=True)
        else:
            _open_window(url, args.no_window)
        while True:
            time.sleep(0.5)
    except (KeyboardInterrupt, SystemExit):
        print("\n[cockpit] cerrando servidor...", flush=True)
    finally:
        try:
            state.stop_cycle_thread()
            state.stop_autonomous_loop()
        except Exception:
            pass
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
