"""Servidor HTTP local del cockpit (solo stdlib, sin dependencias).

Sirve:
- ``/``            -> visor 3D (HTML autocontenido, sin CDNs).
- ``/api/state``   -> snapshot JSON (recargado en cada petición).
- ``/api/rebuild`` -> reconstruye el snapshot (POST).
- ``/static/*``    -> assets.

Se enlaza SOLO a 127.0.0.1 por defecto: es una herramienta local de
observabilidad, no un servicio expuesto.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socketserver
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from detector.ai_engine import AIEngine
from detector.gmgn_bridge import GMGNBridge
from detector.market_feed import MarketFeed
from . import snapshot as snap_mod

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class CockpitState:
    """Estado compartido entre el servidor y el builder."""

    def __init__(self, snapshot_path: str, db_path: str, live_store_db: Optional[str] = None,
                 demo_mode: bool = False, feed: Optional[Any] = None,
                 store: Optional[Any] = None, auto_loop: bool = False):
        self.snapshot_path = snapshot_path
        self.db_path = db_path
        self.live_store_db = live_store_db
        self.filter_path = os.path.join(os.path.dirname(snapshot_path), "active_filter.json")
        # cycle.json lo escribe cockpit.cycle (recolectar -> snapshot -> barrido).
        self.cycle_path = os.path.join(os.path.dirname(snapshot_path), "cycle.json")
        # Feed de precios REAL (DexScreener) para el paper-forward del bot.
        self.feed = feed if feed is not None else MarketFeed()
        # Persistencia del paper-forward, aislada del run_live.py real por origin.
        self.store = store if store is not None else self._open_default_store(snapshot_path)
        self.gmgn = GMGNBridge(feed=self.feed, store=self.store, demo_mode=demo_mode)
        self.ai = AIEngine()
        self._lock = threading.Lock()
        self._tokens_cache: Optional[list] = None
        self._paper_cache: Optional[dict] = None
        self._stop_loop = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        if auto_loop:
            self.start_autonomous_loop()

    def start_autonomous_loop(self) -> None:
        """Inicia el bucle autónomo de trading cuantitativo en segundo plano (cero manual)."""
        if self._loop_thread is not None and self._loop_thread.is_alive():
            return
        self._stop_loop.clear()
        self._loop_thread = threading.Thread(
            target=self._run_autonomous_loop, daemon=True, name="AutonomousTradingLoop"
        )
        self._loop_thread.start()

    def stop_autonomous_loop(self) -> None:
        self._stop_loop.set()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2.0)

    def _run_autonomous_loop(self) -> None:
        """Monitoreo continuo, inferencia ML de confluencia, auto-apertura paper y seguimiento en vivo."""
        last_screen_time = 0.0
        while not self._stop_loop.is_set():
            try:
                now = time.time()
                # 1. Descubrir y evaluar tokens en vivo cada 10 segundos
                if now - last_screen_time >= 10.0:
                    last_screen_time = now
                    self.gmgn.screen_tokens_to_buy()
                # 2. Actualizar precios en tiempo real y evaluar salidas disciplinadas cada 3 segundos
                self.gmgn.paper_trader.tick_prices()
            except Exception:
                pass
            self._stop_loop.wait(timeout=3.0)

    @staticmethod
    def _open_default_store(snapshot_path: str) -> Optional[Any]:
        """LiveStore junto al snapshot. Si pandas falta, el bot sigue sin persistir."""
        try:
            from detector.live_store import LiveStore
            directory = os.path.dirname(os.path.abspath(snapshot_path))
            os.makedirs(directory, exist_ok=True)
            return LiveStore(os.path.join(directory, "cockpit_forward.db"))
        except Exception:
            return None

    def state(self) -> Dict[str, Any]:
        with self._lock:
            data = snap_mod.load_snapshot(self.snapshot_path)
            cycle = snap_mod.load_snapshot(self.cycle_path)
        if data is None:
            return {
                "ok": False,
                "error": "snapshot no construido todavía",
                "hint": "ejecuta: python -m cockpit.cycle",
            }
        if cycle:
            data["cycle"] = cycle
        return data

    def rebuild(self, sample: int = 3000) -> Dict[str, Any]:
        with self._lock:
            data = snap_mod.build_snapshot(
                self.db_path, sample=sample, live_store_db=self.live_store_db)
            snap_mod.write_snapshot(data, self.snapshot_path)
            # Invalida caché de tokens para que refresque en la próxima evaluación
            self._tokens_cache = None
            self._paper_cache = None
        return data

    def _ensure_cache(self) -> Tuple[list, dict]:
        if self._tokens_cache is not None and self._paper_cache is not None:
            return self._tokens_cache, self._paper_cache
        if not os.path.exists(self.db_path):
            return [], {}
        try:
            con = snap_mod._open_ro(self.db_path)
            try:
                self._tokens_cache = snap_mod._scan_tokens(con)
                self._paper_cache = snap_mod._scan_paper(con)
            finally:
                con.close()
        except Exception:
            return [], {}
        return self._tokens_cache, self._paper_cache

    def evaluate_filter(self, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            tokens, paper = self._ensure_cache()
            import math

            def _to_float(v: Any, default: float) -> float:
                if v is None:
                    return default
                try:
                    val = float(v)
                    if math.isnan(val) or math.isinf(val):
                        return default
                    return val
                except (ValueError, TypeError):
                    return default

            fmc_min = _to_float(params.get("fmc_min"), 0.0)
            fmc_max = _to_float(params.get("fmc_max"), 4285.0)
            fliq_min = _to_float(params.get("fliq_min"), 0.0)
            fliq_max = _to_float(params.get("fliq_max"), 1725.0)
            min_liq_ratio = _to_float(params.get("min_liq_ratio"), 0.0)

            raw_final = params.get("min_final_mc")
            min_final_mc = _to_float(raw_final, 0.0) if raw_final is not None else None
            if min_final_mc is not None and min_final_mc <= 0:
                min_final_mc = None

            return snap_mod.evaluate_custom_filter(
                self.db_path,
                fmc_min=fmc_min,
                fmc_max=fmc_max,
                fliq_min=fliq_min,
                fliq_max=fliq_max,
                min_final_mc=min_final_mc,
                min_liq_ratio=min_liq_ratio,
                tokens=tokens if tokens else None,
                paper=paper if paper else None,
            )

    def save_filter(self, params: Dict[str, Any]) -> bool:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.filter_path), exist_ok=True)
                with open(self.filter_path, "w", encoding="utf-8") as fh:
                    json.dump(params, fh, ensure_ascii=False, indent=2)
                return True
            except Exception:
                return False

    def get_filter(self) -> Dict[str, Any]:
        with self._lock:
            if os.path.exists(self.filter_path):
                try:
                    with open(self.filter_path, "r", encoding="utf-8") as fh:
                        return json.load(fh)
                except Exception:
                    pass
        # Defaults canónicos (Rama A observable)
        return {
            "fmc_min": 0.0,
            "fmc_max": 4285.0,
            "fliq_min": 0.0,
            "fliq_max": 1725.0,
            "min_liq_ratio": 0.0,
            "min_final_mc": None,
            "use_lookahead": False,
        }


def _make_handler(state: CockpitState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PumpfunCockpit/1.0"

        def log_message(self, fmt, *args):  # silencio: es una UI local
            return

        def _send_json(self, obj: Dict[str, Any], code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: str) -> None:
            if not os.path.isfile(path):
                self.send_error(404, "not found")
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw)
            except Exception:
                return {}

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route in ("/", "/index.html"):
                self._send_file(os.path.join(STATIC_DIR, "index.html"))
            elif route == "/api/state":
                self._send_json(state.state())
            elif route == "/api/filter":
                self._send_json(state.get_filter())
            elif route == "/api/gmgn/status":
                self._send_json({
                    "ok": True,
                    "available": state.gmgn.is_available(),
                    "authenticated": state.gmgn.is_authenticated(),
                    "cli_path": state.gmgn.cli_path,
                    "skills_count": 17,
                    "demo_mode": state.gmgn.demo_mode,
                    "feed": state.feed.health() if state.feed is not None else {"source": None},
                })
            elif route == "/api/gmgn/wallets":
                wallets = state.gmgn.rank_wallets_to_follow()
                self._send_json({"ok": True, "wallets": [asdict(w) for w in wallets]})
            elif route == "/api/gmgn/tokens":
                tokens = state.gmgn.screen_tokens_to_buy()
                self._send_json({"ok": True, "source": state.gmgn.last_screen_source,
                                 "tokens": [asdict(t) for t in tokens]})
            elif route == "/api/bot/status":
                state.gmgn.paper_trader.tick_prices()
                bot_summary = state.gmgn.paper_trader.get_summary()
                if not bot_summary.get("feed") or not bot_summary["feed"].get("source"):
                    bot_summary["feed"] = state.feed.health() if state.feed is not None else {"source": None}
                self._send_json({"ok": True, **bot_summary})
            elif route == "/api/patterns/summary":
                tokens = state.gmgn.screen_tokens_to_buy()
                patterns_map = {}
                for t in tokens:
                    pid = t.pattern.pattern_id
                    if pid not in patterns_map:
                        patterns_map[pid] = {
                            "pattern_id": pid,
                            "label": t.pattern.label,
                            "bias": t.pattern.bias,
                            "expected_ev": t.pattern.expected_ev,
                            "count": 0,
                            "sample_token": t.symbol,
                        }
                    patterns_map[pid]["count"] += 1
                self._send_json({"ok": True, "source": state.gmgn.last_screen_source,
                                 "patterns": list(patterns_map.values())})
            elif route == "/favicon.ico":
                icon_svg = (
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                    '<circle cx="16" cy="16" r="14" fill="#0c121d" stroke="#00e5ff" stroke-width="2"/>'
                    '<path d="M17 5L9 17h6l-2 10 10-13h-6z" fill="#00ffaa"/>'
                    '</svg>'
                )
                body = icon_svg.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/ai/status":
                self._send_json(state.ai.get_status())
            elif route == "/api/ai/goals":
                self._send_json({"ok": True, "goals": state.ai.goals})
            elif route == "/api/ai/ollama/models":
                models = state.ai.detect_ollama_models()
                self._send_json({"ok": True, "models": models})
            elif route == "/api/ai/browser/screenshot":
                snap_path = state.ai.browser_agent.screenshot_path
                if os.path.exists(snap_path):
                    self._send_file(snap_path)
                else:
                    placeholder_svg = (
                        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300">'
                        '<rect width="600" height="300" fill="#080d16" stroke="#1a2538"/>'
                        '<text x="50%" y="45%" text-anchor="middle" fill="#7f8ea3" font-family="sans-serif" font-size="14">'
                        '🌐 Sin captura web todavía'
                        '</text>'
                        '<text x="50%" y="60%" text-anchor="middle" fill="#00e5ff" font-family="sans-serif" font-size="12">'
                        'Ejecuta /browser &lt;mint&gt; o haz clic en Inspeccionar'
                        '</text>'
                        '</svg>'
                    )
                    body = placeholder_svg.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "image/svg+xml")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            elif route.startswith("/static/"):
                rel = route[len("/static/"):]
                safe = os.path.normpath(rel).replace("..", "")
                self._send_file(os.path.join(STATIC_DIR, safe))
            else:
                self.send_error(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route == "/api/rebuild":
                try:
                    data = state.rebuild()
                    self._send_json({"ok": data.get("ok", False), "universe": data.get("universe")})
                except Exception as exc:  # pragma: no cover - defensivo
                    self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
            elif route == "/api/evaluate":
                try:
                    params = self._read_json_body()
                    result = state.evaluate_filter(params)
                    code = 200 if result.get("ok", True) else 400
                    self._send_json(result, code)
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            elif route == "/api/filter":
                try:
                    params = self._read_json_body()
                    ok = state.save_filter(params)
                    self._send_json({"ok": ok})
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
            elif route == "/api/bot/toggle":
                try:
                    state.gmgn.paper_trader.auto_bot_enabled = not state.gmgn.paper_trader.auto_bot_enabled
                    self._send_json({"ok": True, "auto_bot_enabled": state.gmgn.paper_trader.auto_bot_enabled})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/bot/close":
                try:
                    params = self._read_json_body()
                    mint = params.get("mint", "")
                    reason = params.get("reason", "MANUAL_UI_EXIT")
                    pos = state.gmgn.paper_trader.close_trade(mint, reason=reason)
                    self._send_json({"ok": pos is not None, "position": asdict(pos) if pos else None})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/bot/snipe":
                try:
                    params = self._read_json_body()
                    mint = params.get("mint", "unknown")
                    sym = params.get("symbol", "N/A")
                    mc = float(params.get("market_cap_usd", 10000.0))
                    size = float(params.get("size_sol", 1.0))
                    pos = state.gmgn.paper_trader.open_trade(mint, sym, mc, size_sol=size)
                    self._send_json({"ok": True, "position": asdict(pos)})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/gmgn/audit":
                try:
                    params = self._read_json_body()
                    addr = params.get("address", "")
                    res = state.gmgn.audit_token(addr)
                    self._send_json({"ok": True, "audit": res})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/gmgn/paper_trade":
                try:
                    params = self._read_json_body()
                    mint = params.get("mint", "unknown")
                    entry_mc = float(params.get("market_cap_usd", 10000.0))
                    order = {
                        "ok": True,
                        "mode": "PAPER_FORWARD",
                        "status": "OPEN",
                        "mint": mint,
                        "entry_market_cap_usd": entry_mc,
                        "tp1_target": round(entry_mc * 1.50, 2),
                        "breakeven_lock": round(entry_mc * 1.25, 2),
                        "tp2_runner": round(entry_mc * 2.50, 2),
                        "sl_panic_slippage_model": round(entry_mc * (1.0 - 0.3349), 2),
                        "expected_slippage_pct": -33.49,
                        "max_holding_seconds": 300.0,
                    }
                    self._send_json(order)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/ai/chat":
                try:
                    params = self._read_json_body()
                    msg = params.get("message", "")
                    ctx = params.get("context")
                    res = state.ai.process_chat(msg, context=ctx)
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/config":
                try:
                    params = self._read_json_body()
                    ok = state.ai.save_config(params)
                    self._send_json({"ok": ok, "config": state.ai.config})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/test":
                try:
                    params = self._read_json_body()
                    res = state.ai.test_provider(params.get("provider"))
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/goal/eval":
                try:
                    summary = state.gmgn.paper_trader.get_summary()
                    goals = state.ai.evaluate_goals_autonomously(context=summary)
                    self._send_json({"ok": True, "goals": goals})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/goal":
                try:
                    params = self._read_json_body()
                    action = params.get("action", "")
                    text = params.get("text", "")
                    res = state.ai.process_chat(f"/goal {action} {text}")
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/boost":
                try:
                    params = self._read_json_body()
                    cmd = "/boost"
                    if params.get("mode"):
                        cmd += f" {params['mode']}"
                    if params.get("lr"):
                        cmd += f" lr={params['lr']}"
                    if params.get("iter"):
                        cmd += f" iter={params['iter']}"
                    res = state.ai.process_chat(cmd)
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/browser":
                try:
                    params = self._read_json_body()
                    target = params.get("target", "")
                    res = state.ai.process_chat(f"/browser {target}")
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            elif route == "/api/ai/audit":
                try:
                    params = self._read_json_body()
                    res = state.ai.audit_token_narrative(params)
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            else:
                self.send_error(404, "not found")

    return Handler


class _FastHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer sin la resolución DNS inversa de ``server_bind``.

    ``HTTPServer.server_bind`` llama a ``socket.getfqdn()``, que en Windows puede
    tardar decenas de segundos. Para una UI local no hace falta el FQDN.
    """
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:  # type: ignore[override]
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def make_server(state: CockpitState, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    return _FastHTTPServer((host, port), _make_handler(state))


def serve_forever(state: CockpitState, host: str = "127.0.0.1", port: int = 8787) -> None:
    srv = make_server(state, host, port)
    srv.serve_forever()
