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
import math
import mimetypes
import os
import socketserver
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from pathlib import Path
import sys
_pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from detector.ai_engine import AIEngine
from detector.auto_evolution import AutoEvolutionEngine
from detector.gmgn_bridge import GMGNBridge
from detector.market_feed import MarketFeed
from . import snapshot as snap_mod
from .events import EVENT_KINDS, EventBus

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ── Contrato canónico del bloque `dashboard` (P0.1 del Plan Maestro V2) ─────
# Fuente única de verdad para el exe de escritorio y la UI web. Ley anti-falacia:
# todo campo sin medición real viaja como `None` → la UI muestra "N/D — sin dato",
# jamás un default numérico (10,000 USD, 0.91, 85.0) presentado como métrica.
DASHBOARD_SCHEMA = "dashboard/1"
PAPER_INITIAL_USD = 10_000.0
DASHBOARD_STALE_AFTER_S = 30.0
PIPELINE_SCHEMA = "pipeline/1"
WIRING_SCHEMA = "wiring/1"


def summarize_pipeline_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Resume una traza E0→E6: veredicto de la decisión, etapa del veto y si ejecutó.

    - ``VETO``: falló una etapa forense (E1 métricas / E3 escudos).
    - ``NEUTRAL``: pasó todo pero el EV neto no fue positivo (no admisible, sin veto).
    - ``ADMITTED``: admisible por el modelo.
    - ``executed``: la etapa E6 abrió posición paper real (precio en vivo verificado).
    """
    stages = (trace or {}).get("stages") or []
    veto_stage = None
    for s in stages:
        stage_name = str(s.get("stage") or "")
        if s.get("verdict") == "fail" and stage_name.startswith(("E1_", "E3_")):
            veto_stage = stage_name
            break
    if veto_stage is not None:
        verdict = "VETO"
    else:
        e4 = next((s for s in stages if s.get("stage") == "E4_ev_net"), None)
        verdict = "NEUTRAL" if (e4 is not None and e4.get("verdict") == "fail") else "ADMITTED"
    executed = any(s.get("stage") == "E6_execution" and s.get("verdict") == "pass"
                   for s in stages)
    return {"verdict": verdict, "veto_stage": veto_stage, "executed": executed}


def _fnum(val: Any) -> Optional[float]:
    """Float finito o ``None`` (nunca NaN/inf: eso no es un dato)."""
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def compose_dashboard(
    summary: Optional[Dict[str, Any]],
    *,
    sol_price_usd: Optional[float] = None,
    sol_price_source: Optional[str] = None,
    feed_age_s: Optional[float] = None,
    loop_alive: bool = False,
    last_stage: Optional[Dict[str, float]] = None,
    now: Optional[float] = None,
    stale_after_s: float = DASHBOARD_STALE_AFTER_S,
) -> Dict[str, Any]:
    """Compone el bloque `dashboard` desde el resumen del paper-forward.

    Función pura (sin IO) para que sea testeable y para que exista UN solo
    contrato entre el servidor y el exe. Cualquier métrica ausente viaja como
    ``None``; el campo ``metrics_known`` del bloque declara qué se pudo medir.
    """
    now_ts = float(time.time() if now is None else now)
    summary = dict(summary or {})
    open_positions = [p for p in (summary.get("open_positions") or []) if isinstance(p, dict)]

    open_count = _fnum(summary.get("open_count"))
    if open_count is None:
        open_count = float(len(open_positions))
    closed_count = _fnum(summary.get("closed_count"))

    pnl_usd = _fnum(summary.get("total_pnl_usd"))
    pnl_sol = _fnum(summary.get("total_pnl_sol"))
    equity_usd: Optional[float] = None if pnl_usd is None else round(PAPER_INITIAL_USD + pnl_usd, 2)

    sol_price = _fnum(sol_price_usd)
    exposure_usd: Optional[float] = None
    if sol_price is not None:
        total = 0.0
        for pos in open_positions:
            size_sol = _fnum(pos.get("size_sol"))
            if size_sol is None:
                total = None  # type: ignore[assignment]
                break
            total += size_sol * sol_price
        exposure_usd = None if total is None else round(total, 2)

    exposure_pct: Optional[float] = None
    if exposure_usd is not None and equity_usd:
        exposure_pct = round(exposure_usd / abs(equity_usd) * 100.0, 2)

    evolution = summary.get("evolution") if isinstance(summary.get("evolution"), dict) else {}
    shadow = evolution.get("shadow_summary") if isinstance(evolution.get("shadow_summary"), dict) else {}
    rules = evolution.get("adaptive_rules") if isinstance(evolution.get("adaptive_rules"), dict) else {}
    shadow_vetos = [r for r in (evolution.get("shadow_vetos") or []) if isinstance(r, dict)]

    # Listas canónicas: el exe pinta EXCLUSIVAMENTE estas (dashboard.open_positions /
    # dashboard.shadow_vetos), nunca las listas crudas del summary.
    normalized_positions: List[Dict[str, Any]] = []
    for pos in open_positions:
        normalized_positions.append({
            "id": pos.get("id"),
            "mint": pos.get("mint"),
            "symbol": pos.get("symbol"),
            "entry_mc": _fnum(pos.get("entry_mc")),
            "current_mc": _fnum(pos.get("current_mc")),
            "size_sol": _fnum(pos.get("size_sol")),
            "pnl_pct": _fnum(pos.get("pnl_pct")),
            "pnl_usd": _fnum(pos.get("pnl_usd")),
            "opened_at": _fnum(pos.get("opened_at")),
            "status": pos.get("status"),
            "tp1_hit": bool(pos.get("tp1_hit")),
            "be_hit": bool(pos.get("be_hit")),
            "tp2_hit": bool(pos.get("tp2_hit")),
            "peak_mc": _fnum(pos.get("peak_mc")),
        })

    true_saves = _fnum(shadow.get("true_saves"))
    false_vetos = _fnum(shadow.get("false_vetos"))
    avoided_loss_pct = _fnum(shadow.get("avoided_loss_sum_pct"))
    evolutions_count = _fnum(rules.get("evolutions_count"))

    last_rule_change: Optional[Dict[str, Any]] = None
    for event in reversed(evolution.get("evolution_events") or []):
        if not isinstance(event, dict):
            continue
        rule = str(event.get("rule_adjusted") or "").strip()
        if rule:
            last_rule_change = {
                "rule": rule,
                "old": event.get("old_val"),
                "new": event.get("new_val"),
                "ts": _fnum(event.get("ts")),
            }
            break

    feed = summary.get("feed") if isinstance(summary.get("feed"), dict) else {}
    feed_source = feed.get("source")
    feed_source = str(feed_source) if feed_source else None

    feed_age = _fnum(feed_age_s)
    if feed_age is not None:
        stale = bool(feed_age > float(stale_after_s))
    else:
        stale = not bool(loop_alive)

    stage_ts = {str(k): _fnum(v) for k, v in dict(last_stage or {}).items()}
    stage_age_s = {
        key: (round(now_ts - ts, 2) if ts is not None else None) for key, ts in stage_ts.items()
    }

    return {
        "schema": DASHBOARD_SCHEMA,
        "ts": now_ts,
        "paper_wallet": {
            "initial_usd": PAPER_INITIAL_USD,
            "equity_usd": equity_usd,
            "pnl_usd": pnl_usd,
            "pnl_sol": pnl_sol,
            "open_count": int(open_count) if open_count is not None else None,
            "closed_count": int(closed_count) if closed_count is not None else None,
            "exposure_usd": exposure_usd,
            "exposure_pct": exposure_pct,
            # El precio SOL es una asunción declarada (no una medición en vivo) salvo
            # que una fuente real lo actualice: la UI debe poder etiquetarlo.
            "sol_price_usd": sol_price,
            "sol_price_source": str(sol_price_source) if sol_price_source else None,
        },
        "evolution": {
            "true_saves": int(true_saves) if true_saves is not None else None,
            "false_vetos": int(false_vetos) if false_vetos is not None else None,
            "avoided_loss_pct": avoided_loss_pct,
            "evolutions_count": int(evolutions_count) if evolutions_count is not None else None,
            "last_rule_change": last_rule_change,
        },
        "runtime": {
            "feed_source": feed_source,
            "feed_age_s": feed_age,
            "stale": stale,
            "loop_alive": bool(loop_alive),
            "last_stage": {k: v for k, v in stage_ts.items()},
            "stage_age_s": stage_age_s,
            "feed_misses": _fnum(summary.get("feed_misses")),
            "last_feed_error": summary.get("last_feed_error"),
        },
        "metrics_known": {
            "pnl": pnl_usd is not None,
            "evolution": bool(evolution),
            "feed": feed_source is not None and feed_age is not None,
            "loop": bool(loop_alive),
            "exposure": exposure_usd is not None,
            "sol_price_live": str(sol_price_source or "") == "live",
        },
        "open_positions": normalized_positions,
        "shadow_vetos": shadow_vetos,
    }


def compose_provenance(dashboard: Dict[str, Any]) -> Dict[str, Any]:
    """Provenance del payload: qué fuente, cuándo y si el dato está vencido."""
    runtime = dashboard.get("runtime") or {}
    age = _fnum(runtime.get("feed_age_s"))
    return {
        "source": runtime.get("feed_source") or "none",
        "ts": dashboard.get("ts"),
        "age_s": age,
        "stale": bool(runtime.get("stale")),
    }


def compose_bot_dashboard(state: CockpitState) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Bloque canónico `dashboard` (contrato único exe/web/IA).

    Usado por /api/bot/status y /api/ai/context para que TODOS los consumidores
    lean el mismo `paper_wallet`/`evolution` reales (ley anti-falacia: sin defaults).
    `feed_age_s` = segundos desde el último ciclo del pipeline que consultó el feed real.
    """
    bot_summary = state.gmgn.paper_trader.get_summary()
    if not bot_summary.get("feed") or not bot_summary["feed"].get("source"):
        bot_summary["feed"] = state.feed.health() if state.feed is not None else {"source": None}
    bot_summary["evolution"] = state.evolution_engine.get_status_summary()
    dashboard = compose_dashboard(
        bot_summary,
        sol_price_usd=getattr(state.gmgn.paper_trader, "sol_price_usd", None),
        # LivePaperTrader usa 150 USD/SOL como asunción declarada y hoy no
        # hay feed que lo actualice: se publica como tal (ley anti-falacia).
        sol_price_source="assumed_default",
        feed_age_s=state.stage_age("screen"),
        loop_alive=state.loop_alive(),
        last_stage=state.stage_timestamps(),
    )
    return bot_summary, dashboard


class CockpitState:
    """Estado compartido entre el servidor y el builder."""

    # Intervalos del cableado automático (se pueden ajustar antes de arrancar).
    CYCLE_INTERVAL_S = 1800.0     # snapshot + barrido causal OOS cada 30 min
    SNAPSHOT_MAX_AGE_S = 900.0    # un snapshot de más de 15 min se considera rancio

    def __init__(self, snapshot_path: str, db_path: str, live_store_db: Optional[str] = None,
                 demo_mode: bool = False, feed: Optional[Any] = None,
                 store: Optional[Any] = None, auto_loop: bool = False,
                 auto_repair: bool = False):
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
        self.annotation_db = self._open_default_annotation_db(snapshot_path)
        runtime_dir = Path(os.path.dirname(os.path.abspath(snapshot_path)))
        self.evolution_engine = AutoEvolutionEngine(
            store=self.store, annotation_db=self.annotation_db, runtime_dir=runtime_dir
        )
        self.gmgn = GMGNBridge(
            feed=self.feed, store=self.store, demo_mode=demo_mode,
            evolution_engine=self.evolution_engine,
        )
        self.ai = AIEngine()
        if hasattr(self.ai, "browser_agent"):
            self.ai.browser_agent.store = self.store
        if hasattr(self.ai, "evolution_engine"):
            self.ai.evolution_engine = self.evolution_engine
        self._lock = threading.Lock()
        self._tokens_cache: Optional[list] = None
        self._paper_cache: Optional[dict] = None
        self._stop_loop = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        # Bus de eventos (P0.3): ring buffer 500 + JSONL runtime/events.jsonl + SSE.
        self.events = EventBus(
            ring_size=500,
            jsonl_path=str(runtime_dir / "events.jsonl"),
        )
        self._stage_ts: Dict[str, float] = {}
        self._stop_cycle = threading.Event()
        self._cycle_thread: Optional[threading.Thread] = None
        self._cycle_last: Optional[Dict[str, Any]] = None
        # Cableado automático: la base se resuelve sola (env -> rutas candidatas),
        # así que el lanzador oficial no depende del cwd en que se ejecute.
        self.resolve_db()
        if auto_repair:
            self.ensure_snapshot()
        if auto_loop:
            self.gmgn.paper_trader.restore_from_store()
            self.start_autonomous_loop()
            self.start_cycle_thread()

    # ════════════════════════════════════════════ cableado automático (P3)

    def resolve_db(self) -> Dict[str, Any]:
        """Resuelve la base canónica sin configuración manual (idempotente)."""
        resolved = snap_mod.resolve_db_path(self.db_path)
        path = resolved.get("path")
        if path and path != self.db_path:
            self.db_path = path
            self._tokens_cache = None
            self._paper_cache = None
        return resolved

    def snapshot_health(self) -> Dict[str, Any]:
        """Estado del snapshot en disco: ausente / corrupto / rancio / fresco."""
        data = snap_mod.load_snapshot(self.snapshot_path)
        try:
            age: Optional[float] = round(max(0.0, time.time() - os.path.getmtime(self.snapshot_path)), 1)
        except OSError:
            age = None
        if data is None:
            state = "missing"
        elif not data.get("ok"):
            state = "corrupt"
        elif age is None or age > self.SNAPSHOT_MAX_AGE_S:
            state = "stale"
        else:
            state = "fresh"
        return {
            "state": state,
            "path": os.path.basename(self.snapshot_path),
            "age_s": age,
            "ok": bool(data and data.get("ok")),
            "error": (data or {}).get("error"),
            "max_age_s": self.SNAPSHOT_MAX_AGE_S,
        }

    def ensure_snapshot(self, sample: int = 3000) -> Dict[str, Any]:
        """Auto-reparación: reconstruye el snapshot si falta, está corrupto o rancio.

        Antes, un ``snapshot.json`` envenenado (``ok:false``) sobrevivía para
        siempre porque solo se reconstruía cuando el archivo NO existía.
        """
        health = self.snapshot_health()
        if health["state"] == "fresh":
            return {"rebuilt": False, **health}
        self.resolve_db()
        try:
            data = self.rebuild(sample=sample)
        except Exception as exc:  # pragma: no cover - defensivo
            return {"rebuilt": False, "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}"}
        ok = bool(data.get("ok"))
        try:
            self.emit_event("stage", {"stage": "snapshot", "state": "rebuilt", "ok": ok,
                                      "db": os.path.basename(self.db_path)})
        except Exception:
            pass
        return {"rebuilt": True, "state": "rebuilt", "ok": ok,
                "error": data.get("error"), "was": health["state"]}

    def cycle_alive(self) -> bool:
        return self._cycle_thread is not None and self._cycle_thread.is_alive()

    def cycle_state(self) -> Dict[str, Any]:
        with self._lock:
            last = dict(self._cycle_last or {})
        return {"alive": self.cycle_alive(), "interval_s": self.CYCLE_INTERVAL_S,
                "last": last or None}

    def start_cycle_thread(self) -> None:
        """Hilo de AUTOMEJORA: universo + barrido causal con gate OOS, desatendido."""
        if self._cycle_thread is not None and self._cycle_thread.is_alive():
            return
        self._stop_cycle.clear()
        self._cycle_thread = threading.Thread(
            target=self._run_cycle_loop, daemon=True, name="AutoImprovementCycle")
        self._cycle_thread.start()

    def stop_cycle_thread(self) -> None:
        self._stop_cycle.set()
        if self._cycle_thread is not None:
            self._cycle_thread.join(timeout=2.0)

    def _run_cycle_loop(self) -> None:
        """Una pasada del ciclo completo por intervalo (sin intervención manual)."""
        out_dir = os.path.dirname(os.path.abspath(self.snapshot_path))
        n = 0
        while not self._stop_cycle.is_set():
            n += 1
            try:
                from . import cycle as cycle_mod
                # Refresca features cada 4 pasadas (ver datos nuevos sin cargar el CPU).
                result = cycle_mod.run_cycle(
                    self.db_path, mapping="mapping_pumpscope.json", out_dir=out_dir,
                    sample=3000, cycle_path=self.cycle_path,
                    refresh_features=(n > 1 and n % 4 == 0),
                )
                sweep = result.get("sweep") or {}
                last = {
                    "pass": n,
                    "generated_at": result.get("generated_at"),
                    "seconds": result.get("seconds"),
                    "universe_tokens": (result.get("universe") or {}).get("n_tokens"),
                    "n_candidates": sweep.get("n_candidates"),
                    "n_promoted": sweep.get("n_promoted"),
                    "verdict": sweep.get("verdict") or sweep.get("error"),
                }
                with self._lock:
                    self._cycle_last = last
                self.mark_stage("cycle")
                self.emit_event("evo", {"stage": "cycle", **last})
            except Exception as exc:
                print(f"[cycle_loop error] {exc}", flush=True)
                try:
                    self.emit_event("error", {"stage": "cycle_loop",
                                              "error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass
            self._stop_cycle.wait(timeout=self.CYCLE_INTERVAL_S)

    def _paper_wiring(self) -> Dict[str, Any]:
        try:
            summary = self.gmgn.paper_trader.get_summary()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "open_count": summary.get("open_count"),
            "closed_count": len(getattr(self.gmgn.paper_trader, "closed_positions", []) or []),
            "winrate_pct": summary.get("winrate_pct"),
            "auto_bot_enabled": bool(getattr(self.gmgn.paper_trader, "auto_bot_enabled", False)),
        }

    def wiring(self) -> Dict[str, Any]:
        """Estado de TODOS los subsistemas cableados en una sola llamada (solo lectura).

        Una sola fuente para la tira de estado de la UI: base de datos (y qué le
        falta), snapshot, feed real, loop autónomo, ciclo de automejora, paper y ML.
        """
        resolved = snap_mod.resolve_db_path(self.db_path)
        db_path = resolved.get("path") or self.db_path
        schema = snap_mod.schema_report(db_path)
        feed_health = self.feed.health() if self.feed is not None else {"source": None}
        try:
            evo = self.evolution_engine.get_status_summary()
        except Exception:
            evo = {}
        try:
            ml = self.ai.get_status()
        except Exception as exc:
            ml = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        qm = ml.get("quant_model") or {}
        return {
            "ok": True,
            "schema": WIRING_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": {
                "path": db_path,
                "resolved_from": resolved.get("source"),
                "exists": bool(resolved.get("exists")),
                "read_only": True,
                **{k: schema.get(k) for k in (
                    "has_token", "has_paper", "missing_token_columns", "token_fallbacks",
                    "universe_measurable", "rise50_measurable", "paper_measurable")},
            },
            "snapshot": self.snapshot_health(),
            "feed": {
                "source": feed_health.get("source"),
                "calls": feed_health.get("calls"),
                "misses": feed_health.get("misses"),
                "errors": feed_health.get("errors"),
                "last_error": feed_health.get("last_error"),
            },
            "loop": {
                "alive": self.loop_alive(),
                "stages": {k: self.stage_age(k) for k in ("screen", "tick", "shadow", "cycle")},
            },
            "cycle": self.cycle_state(),
            "paper": self._paper_wiring(),
            # ``test_accuracy``/``test_roc_auc`` son fracciones 0..1 (no porcentajes).
            "ml": {
                "provider": ml.get("active_provider"),
                "status": qm.get("status"),
                "split_mode": qm.get("split_mode"),
                "train_samples": qm.get("train_samples"),
                "test_samples": qm.get("test_samples"),
                "test_roc_auc": qm.get("test_roc_auc"),
                "test_accuracy": qm.get("test_accuracy"),
                "trained_at": qm.get("trained_at"),
                "version": qm.get("version"),
            },
            "evolution": {
                "evolutions_count": (evo.get("adaptive_rules") or {}).get("evolutions_count"),
                "shadow": evo.get("shadow_summary"),
            },
        }

    # ── Estado de etapas del loop (para `dashboard.runtime`) ────────────

    def loop_alive(self) -> bool:
        return self._loop_thread is not None and self._loop_thread.is_alive()

    def mark_stage(self, stage: str, ts: Optional[float] = None) -> None:
        """Marca el instante del último ciclo real de una etapa del pipeline."""
        with self._lock:
            self._stage_ts[str(stage)] = float(time.time() if ts is None else ts)

    def stage_timestamps(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._stage_ts)

    def stage_age(self, stage: str) -> Optional[float]:
        with self._lock:
            ts = self._stage_ts.get(str(stage))
        return None if ts is None else round(time.time() - ts, 2)

    def emit_event(self, kind: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.events.emit(kind, data)

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
        """Monitoreo continuo, inferencia ML, enriquecimiento forense, auto-apertura paper y seguimiento de vetos."""
        last_screen_time = 0.0
        last_shadow_time = 0.0
        last_heartbeat = 0.0
        prev_open: set = set()
        prev_closed = 0
        prev_evolutions = 0
        prev_feed_error: Optional[str] = None
        self.events.emit("stage", {"stage": "boot", "loop_alive": True})
        while not self._stop_loop.is_set():
            try:
                now = time.time()
                # 1. Descubrir y enriquecer tokens en vivo cada 10 segundos
                if now - last_screen_time >= 10.0:
                    last_screen_time = now
                    tokens = self.gmgn.screen_tokens_to_buy(enrich_live_dossiers=True)
                    self.mark_stage("screen", now)
                    self.events.emit("stage", {
                        "stage": "screen",
                        "source": self.gmgn.last_screen_source,
                        "tokens": len(tokens),
                        "admitted": sum(1 for t in tokens if getattr(t, "admitted", False)),
                        "vetados": sum(1 for t in tokens if not getattr(t, "admitted", True)),
                    })
                # 2. Actualizar precios en tiempo real y evaluar salidas disciplinadas cada 3 segundos
                self.gmgn.paper_trader.tick_prices()
                self.mark_stage("tick", now)
                # Eventos de apertura/cierre detectados por diferencia de estado real (sin inventar)
                cur_open = set(self.gmgn.paper_trader.open_positions)
                cur_closed = self.gmgn.paper_trader.closed_positions
                for mint in cur_open - prev_open:
                    pos = self.gmgn.paper_trader.open_positions.get(mint)
                    if pos is not None:
                        self.events.emit("trade_open", {
                            "mint": mint, "symbol": getattr(pos, "symbol", None),
                            "entry_mc_usd": getattr(pos, "entry_mc", None),
                            "size_sol": getattr(pos, "size_sol", None),
                        })
                if len(cur_closed) > prev_closed:
                    for pos in cur_closed[prev_closed:]:
                        self.events.emit("trade_close", {
                            "mint": getattr(pos, "mint", None), "symbol": getattr(pos, "symbol", None),
                            "pnl_pct": getattr(pos, "pnl_pct", None), "pnl_usd": getattr(pos, "pnl_usd", None),
                            "exit_reason": getattr(pos, "exit_reason", None),
                        })
                prev_open, prev_closed = cur_open, len(cur_closed)
                # Cambios de reglas adaptativas (post-mortem automático tras un cierre)
                evolutions_now = int(self.evolution_engine.rules.get("evolutions_count") or 0)
                if evolutions_now > prev_evolutions:
                    last_event = self.gmgn.paper_trader.last_evolution_event or {}
                    self.events.emit("evo", {
                        "evolutions_count": evolutions_now,
                        "rule": last_event.get("rule_adjusted") or None,
                        "old": last_event.get("old_val") or None,
                        "new": last_event.get("new_val") or None,
                        "diagnosis": last_event.get("diagnosis") or None,
                    })
                prev_evolutions = evolutions_now
                # Salud del feed real: solo eventos de transición (no spam)
                health = self.feed.health() if self.feed is not None else {}
                feed_error = health.get("last_error")
                if feed_error != prev_feed_error:
                    self.events.emit("feed", {
                        "source": health.get("source"),
                        "calls": health.get("calls"),
                        "misses": health.get("misses"),
                        "errors": health.get("errors"),
                        "last_error": feed_error,
                    })
                    prev_feed_error = feed_error
                # 3. Evaluar contrafactual de tokens vetados ("¿ese trade vetado hubiera perdido o ganado?") cada 15s
                if now - last_shadow_time >= 15.0:
                    last_shadow_time = now
                    self.evolution_engine.tick_shadow_veto_outcomes(feed=self.feed, max_check=5)
                    self.mark_stage("shadow", now)
                # 4. Heartbeat (prueba viva de que el loop corre; el ring buffer descarta los viejos)
                if now - last_heartbeat >= 30.0:
                    last_heartbeat = now
                    self.events.emit("stage", {
                        "stage": "heartbeat",
                        "open_count": len(self.gmgn.paper_trader.open_positions),
                        "closed_count": len(self.gmgn.paper_trader.closed_positions),
                        "auto_bot_enabled": bool(self.gmgn.paper_trader.auto_bot_enabled),
                    })
            except Exception as exc:
                print(f"[autonomous_loop error] {exc}", flush=True)
                try:
                    self.events.emit("error", {"stage": "autonomous_loop", "error": f"{type(exc).__name__}: {exc}"})
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

    @staticmethod
    def _open_default_annotation_db(snapshot_path: str) -> Optional[Any]:
        """AnnotationDB para etiquetado manual/IA y cálculo de precisión de filtros."""
        try:
            from detector.manual_annotation import AnnotationDB
            directory = os.path.dirname(os.path.abspath(snapshot_path))
            os.makedirs(directory, exist_ok=True)
            return AnnotationDB(os.path.join(directory, "annotations.db"))
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

    # ------------------------------------------------- analítica de rendimiento (P2)
    def performance_report(self, period: str = "all") -> Dict[str, Any]:
        """Analítica REAL de las operaciones paper cerradas (solo lectura).

        Agrega la base canónica sin proyectar rentabilidad futura: win rate
        medido, profit factor, expectativa (EV por operación), curva de capital,
        drawdown máximo, histograma y desglose por motivo de salida.
        """
        with self._lock:
            return snap_mod.performance_report(
                self.db_path, period=period, initial_usd=PAPER_INITIAL_USD)

    # ---------------------------------------------------------- pipeline en vivo (P1)
    def pipeline_live(self, limit: int = 30, window_s: Optional[float] = 3600.0) -> Dict[str, Any]:
        """Últimas decisiones completas (E0→E6) + embudo agregado de la ventana."""
        store = self.store
        if store is None or not hasattr(store, "list_decision_traces"):
            return {"ok": True, "available": False, "schema": PIPELINE_SCHEMA,
                    "reason": "store sin soporte de trazas de decisión",
                    "window_s": window_s, "traces": [],
                    "funnel": {"seen": 0, "admitted": 0, "veto": 0, "neutral": 0,
                               "executed": 0, "veto_by_stage": {}}}
        traces = store.list_decision_traces(limit=int(limit), max_age_s=window_s)
        funnel = {"seen": 0, "admitted": 0, "veto": 0, "neutral": 0, "executed": 0,
                  "veto_by_stage": {}}
        out: List[Dict[str, Any]] = []
        for t in traces:
            summ = summarize_pipeline_trace(t)
            out.append({
                "trace_id": t.get("trace_id"), "mint": t.get("mint"),
                "symbol": t.get("symbol"), "ts": t.get("ts"),
                "verdict": summ["verdict"], "veto_stage": summ["veto_stage"],
                "executed": summ["executed"], "stages": t.get("stages") or [],
            })
            funnel["seen"] += 1
            if summ["verdict"] == "VETO":
                funnel["veto"] += 1
                st = summ["veto_stage"] or "desconocido"
                funnel["veto_by_stage"][st] = funnel["veto_by_stage"].get(st, 0) + 1
            elif summ["verdict"] == "NEUTRAL":
                funnel["neutral"] += 1
            else:
                funnel["admitted"] += 1
            if summ["executed"]:
                funnel["executed"] += 1
        return {"ok": True, "available": True, "schema": PIPELINE_SCHEMA,
                "window_s": window_s, "traces": out, "funnel": funnel}

    def pipeline_trace(self, mint: str, limit: int = 5) -> Dict[str, Any]:
        """Historial de decisiones de un mint concreto, con sus etapas completas."""
        mint = str(mint or "").strip()
        store = self.store
        if store is None or not hasattr(store, "get_decision_trace"):
            return {"ok": True, "available": False, "schema": PIPELINE_SCHEMA,
                    "mint": mint, "reason": "store sin soporte de trazas de decisión",
                    "traces": []}
        traces = store.get_decision_trace(mint, limit=int(limit))
        out = []
        for t in traces:
            summ = summarize_pipeline_trace(t)
            out.append({
                "trace_id": t.get("trace_id"), "mint": t.get("mint"),
                "symbol": t.get("symbol"), "ts": t.get("ts"),
                "verdict": summ["verdict"], "veto_stage": summ["veto_stage"],
                "executed": summ["executed"], "stages": t.get("stages") or [],
            })
        return {"ok": True, "available": True, "schema": PIPELINE_SCHEMA,
                "mint": mint, "traces": out}


def _make_handler(state: CockpitState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PumpfunCockpit/1.0"

        def log_message(self, fmt, *args):  # silencio: es una UI local
            return

        def _send_json(self, obj: Dict[str, Any], code: int = 200) -> None:
            try:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        def _send_file(self, path: str) -> None:
            if not os.path.isfile(path):
                self.send_error(404, "not found")
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                body = fh.read()
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        def _read_json_body(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw)
            except Exception:
                return {}

        def _send_sse(self, state: CockpitState, max_seconds: float = 600.0) -> None:
            """Stream SSE (`text/event-stream`) con el MISMO bus de eventos que /api/logs."""
            qs = parse_qs(urlparse(self.path).query)
            try:
                cursor = int((qs.get("since", ["0"]) or ["0"])[0] or 0)
            except ValueError:
                cursor = 0
            if cursor <= 0:
                cursor = state.events.latest_id()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            deadline = time.time() + float(max_seconds)
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while time.time() < deadline and not state._stop_loop.is_set():
                    batch = state.events.wait_for(cursor, timeout=15.0)
                    if not batch:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    cursor = int(batch[-1].get("id") or cursor)
                    self.wfile.write(EventBus.format_sse(batch).encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                return

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?", 1)[0]
            if route in ("/", "/index.html"):
                self._send_file(os.path.join(STATIC_DIR, "index.html"))
            elif route == "/api/state":
                self._send_json(state.state())
            elif route == "/api/filter":
                self._send_json(state.get_filter())
            elif route == "/api/performance":
                qs = parse_qs(urlparse(self.path).query)
                period = (qs.get("period", ["all"]) or ["all"])[0] or "all"
                self._send_json(state.performance_report(period))
            elif route == "/api/wiring":
                self._send_json(state.wiring())
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
                enrich = bool(state._loop_thread is not None and state._loop_thread.is_alive())
                tokens = state.gmgn.screen_tokens_to_buy(enrich_live_dossiers=enrich)
                self._send_json({"ok": True, "source": state.gmgn.last_screen_source,
                                 "tokens": [asdict(t) for t in tokens]})
            elif route == "/api/bot/status":
                state.gmgn.paper_trader.tick_prices()
                bot_summary, dashboard = compose_bot_dashboard(state)
                self._send_json({
                    "ok": True,
                    "dashboard": dashboard,
                    "provenance": compose_provenance(dashboard),
                    **bot_summary,
                })
            elif route == "/api/logs":
                qs = parse_qs(urlparse(self.path).query)
                since_id = int((qs.get("since", ["0"]) or ["0"])[0] or 0)
                limit = int((qs.get("limit", ["200"]) or ["200"])[0] or 200)
                events = state.events.since(since_id, limit=max(1, min(limit, 500)))
                self._send_json({
                    "ok": True,
                    "latest_id": state.events.latest_id(),
                    "count": len(events),
                    "events": events,
                    "stats": state.events.stats(),
                })
            elif route == "/api/events":
                self._send_sse(state)
            elif route == "/api/evolution/status":
                self._send_json({"ok": True, **state.evolution_engine.get_status_summary()})
            elif route == "/api/pipeline/live":
                qs = parse_qs(urlparse(self.path).query)
                try:
                    limit = int((qs.get("limit") or ["30"])[0])
                except (TypeError, ValueError):
                    limit = 30
                try:
                    window_s: Optional[float] = float((qs.get("window_s") or ["3600"])[0])
                except (TypeError, ValueError):
                    window_s = 3600.0
                self._send_json(state.pipeline_live(limit=limit, window_s=window_s))
            elif route.startswith("/api/pipeline/trace/"):
                mint = unquote(route[len("/api/pipeline/trace/"):])
                qs = parse_qs(urlparse(self.path).query)
                try:
                    limit = int((qs.get("limit") or ["5"])[0])
                except (TypeError, ValueError):
                    limit = 5
                self._send_json(state.pipeline_trace(mint, limit=limit))
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
            elif route == "/api/ai/openrouter/models":
                models = state.ai.fetch_openrouter_models()
                self._send_json({"ok": True, "models": models})
            elif route == "/api/ai/context":
                # El copiloto IA consume el MISMO bloque canónico `dashboard` que
                # /api/bot/status: paper_wallet/evolution reales o ausentes —
                # jamás defaults fabricados (ley anti-falacia §1.5).
                _, dashboard = compose_bot_dashboard(state)
                current_state = {**state.state(), "dashboard": dashboard}
                self._send_json({"ok": True, "context": state.ai.get_ai_context(current_state)})
            elif route == "/api/ai/schema":
                self._send_json({"ok": True, "schemas": state.ai.get_ai_schemas()})
            elif route == "/api/system/telemetry":
                import psutil
                try:
                    cpu_pct = psutil.cpu_percent(interval=None)
                    mem = psutil.virtual_memory()
                    self._send_json({
                        "ok": True,
                        "cpu_percent": cpu_pct,
                        "ram_used_mb": round((mem.total - mem.available) / (1024 * 1024), 1),
                        "ram_total_mb": round(mem.total / (1024 * 1024), 1),
                        "ram_percent": mem.percent,
                    })
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)})
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
            elif route == "/api/dossiers":
                dossiers = (
                    state.store.list_recent_dossiers(limit=25)
                    if state.store and hasattr(state.store, "list_recent_dossiers")
                    else []
                )
                self._send_json({"ok": True, "dossiers": dossiers})
            elif route == "/api/annotations":
                reviews = state.annotation_db.get_reviewed_signals()[:30] if state.annotation_db else []
                eff = state.annotation_db.get_filter_effectiveness() if state.annotation_db else []
                self._send_json({"ok": True, "reviews": reviews, "effectiveness": eff})
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
            elif route == "/api/evolution/run":
                try:
                    params = self._read_json_body()
                    overrides = params.get("price_overrides")
                    state.evolution_engine.tick_shadow_veto_outcomes(
                        feed=state.feed, price_overrides=overrides, max_check=12
                    )
                    self._send_json({"ok": True, **state.evolution_engine.get_status_summary()})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/gmgn/audit":
                try:
                    params = self._read_json_body()
                    addr = params.get("address", "")
                    chain = params.get("chain", "sol")
                    res = state.gmgn.audit_token(addr, chain=chain)
                    self._send_json({"ok": True, "audit": res})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/dossier/inspect":
                try:
                    params = self._read_json_body()
                    mint = str(params.get("mint") or params.get("address") or "").strip()
                    chain = params.get("chain")
                    dev_stats = params.get("dev_stats")
                    if not mint:
                        self._send_json({"ok": False, "error": "mint/address requerido"}, 400)
                        return
                    dossier = state.gmgn.dossier_scanner.scan_token_live(
                        mint, chain=chain, dev_stats=dev_stats
                    )
                    traj = (
                        state.store.get_token_trajectory(mint)
                        if state.store and hasattr(state.store, "get_token_trajectory")
                        else []
                    )
                    self._send_json({"ok": True, "dossier": dossier.to_dict(), "trajectory": traj})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
            elif route == "/api/annotations":
                try:
                    import hashlib
                    import time as _time
                    from detector.manual_annotation import (
                        FilterAnnotation,
                        FilterVerdict,
                        SignalReview,
                        SignalVerdict,
                    )
                    params = self._read_json_body()
                    if not state.annotation_db:
                        self._send_json({"ok": False, "error": "AnnotationDB no disponible"}, 500)
                        return
                    mint = str(params.get("mint") or params.get("token_id") or "unknown")
                    branch = str(params.get("branch") or "GMGN_DOSSIER")
                    admitted = bool(params.get("admitted", False))
                    v_str = str(params.get("verdict") or "veto").lower()
                    verdict_map = {
                        "buy": SignalVerdict.BUY,
                        "veto": SignalVerdict.VETO,
                        "review_needed": SignalVerdict.REVIEW_NEEDED,
                        "false_alarm": SignalVerdict.FALSE_ALARM,
                    }
                    g_verdict = verdict_map.get(v_str, SignalVerdict.VETO)
                    f_name = str(params.get("filter_name") or "BUNDLER_AND_CHIP_AUDIT")
                    f_verdict_str = str(params.get("filter_verdict") or ("pass" if g_verdict == SignalVerdict.BUY else "fail")).lower()
                    f_map = {
                        "pass": FilterVerdict.PASS,
                        "fail": FilterVerdict.FAIL,
                        "false_positive": FilterVerdict.FALSE_POSITIVE,
                        "unsure": FilterVerdict.UNSURE,
                    }
                    now_ms = int(_time.time() * 1000)
                    reviewer = str(params.get("reviewer") or "cockpit_operator")
                    sig_id = hashlib.sha256(f"{mint}:{now_ms}".encode()).hexdigest()[:16]
                    f_ann = FilterAnnotation(
                        filter_name=f_name,
                        filter_value=params.get("filter_value", 0),
                        threshold=params.get("threshold", 0),
                        verdict=f_map.get(f_verdict_str, FilterVerdict.PASS),
                        notes=str(params.get("notes") or ""),
                        reviewer=reviewer,
                        timestamp_ms=now_ms,
                    )
                    review = SignalReview(
                        signal_id=sig_id,
                        token_id=mint,
                        branch=branch,
                        admitted=admitted,
                        features_json=json.dumps(params.get("features") or {}, ensure_ascii=False),
                        metrics_json=json.dumps(params.get("metrics") or {}, ensure_ascii=False),
                        global_verdict=g_verdict,
                        filter_annotations=[f_ann],
                        reviewer=reviewer,
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                        session_id="cockpit_live",
                    )
                    state.annotation_db.save_review(review)
                    self._send_json({
                        "ok": True,
                        "signal_id": sig_id,
                        "reviews": state.annotation_db.get_reviewed_signals()[:20],
                        "effectiveness": state.annotation_db.get_filter_effectiveness(),
                    })
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
                    ctx = dict(params.get("context") or {})
                    ctx.setdefault("evolution_status", state.evolution_engine.get_status_summary())
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
    # En Windows ``SO_REUSEADDR`` permite que un SEGUNDO proceso secuestre el
    # puerto ya ocupado: dos servidores escuchan a la vez y las peticiones del
    # navegador caen en uno u otro (ERR_EMPTY_RESPONSE / ERR_CONNECTION_RESET).
    # Se desactiva ahí para que el segundo bind falle con OSError y el lanzador
    # pueda reutilizar el cockpit vivo en vez de duplicarlo. En POSIX se
    # mantiene para no pelear con sockets en TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:  # type: ignore[override]
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        super().handle_error(request, client_address)


def make_server(state: CockpitState, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    return _FastHTTPServer((host, port), _make_handler(state))


def probe_cockpit(host: str = "127.0.0.1", port: int = 8787,
                  timeout_s: float = 2.0) -> Optional[Dict[str, Any]]:
    """¿Hay ya un cockpit VIVO en host:port? Devuelve su ``/api/state`` o ``None``.

    Se usa desde el lanzador para REUTILIZAR el servidor existente en lugar de
    abrir un segundo listener sobre el mismo puerto. Fail-closed: si el puerto no
    responde o responde algo que no es el cockpit, devuelve ``None``.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/api/state"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pumpscope-cockpit-probe/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                return None
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # Firma mínima del cockpit: ``/api/state`` siempre trae estas claves.
    if isinstance(data, dict) and "ok" in data and "universe" in data:
        return data
    return None


def serve_forever(state: CockpitState, host: str = "127.0.0.1", port: int = 8787) -> None:
    srv = make_server(state, host, port)
    srv.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PumpScope Cockpit Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default 8000)")
    parser.add_argument("--db", default=snap_mod.DEFAULT_DB, help="Database path")
    parser.add_argument("--live-store", default=None, help="LiveStore path")
    args = parser.parse_args()

    runtime_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
    snapshot_path = os.path.join(runtime_dir, "snapshot.json")
    if probe_cockpit(args.host, args.port) is not None:
        print(f"[cockpit] ya hay un cockpit vivo en http://{args.host}:{args.port}/; "
              f"se reutiliza ese servidor (no se abre un segundo listener).", flush=True)
        raise SystemExit(0)
    # Cableado automático: DB resuelta sola, snapshot auto-reparado, loops en marcha.
    state = CockpitState(snapshot_path, args.db, args.live_store, auto_loop=True)
    repair = state.ensure_snapshot()
    print(f"[cockpit] base: {state.db_path}", flush=True)
    print(f"[cockpit] snapshot: {repair.get('state')} (rebuilt={repair.get('rebuilt')})", flush=True)
    print(f"[cockpit] servidor en http://{args.host}:{args.port} con bucle autónomo "
          f"+ ciclo de automejora en segundo plano.", flush=True)
    serve_forever(state, host=args.host, port=args.port)

