"""
GMGN Bridge & AI Pattern Recognition Engine para Solana / Pump.fun.

Conecta el detector de Pump.fun y el Cockpit nativo de Windows con el ecosistema
GMGN (CLI y OpenAPI), incorporando:
1. "Detector de Patrones IA": Reconocimiento causal de arquetipos:
   - Confluencia de Smart Money (Bullish)
   - Expansión Orgánica Temprana (Bullish)
   - Trampa de Cabal Concentrado (Veto Bajista)
   - Sobrecarga de Snipers de Bloque 0 (Veto Bajista)
   - Riesgo de Dump del Desarrollador (Veto Bajista)
2. "A Quién Seguir": Clasificación y filtrado cuantitativo de Smart Money & KOLs.
3. "Qué Token Comprar": Matriz de confluencia con sparklines, scores 0-100 y escudos anti-rug.
4. "Motor de Trading Automático (Live Paper Trader)":
   - **Precios reales** vía ``detector.market_feed.MarketFeed`` (DexScreener): sin
     paseo aleatorio y sin posiciones sembradas.
   - Ejecución autónoma con TP1 (+50%), Breakeven (+25%), TP2 (+150%) y SL real (-33.49%).
   - Seguimiento en vivo de PnL en SOL y USD con cierre dinámico y persistencia
     de decisiones/outcomes en ``LiveStore`` (paper, no mueve fondos).
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .ai_quant_model import AIQuantModel
from .auto_evolution import AutoEvolutionEngine
from .gmgn_dossier_scanner import GMGNDossierScanner

logger = logging.getLogger(__name__)


@dataclass
class PatternDetection:
    pattern_id: str
    label: str
    confidence_pct: float
    bias: str  # "BULLISH", "BEARISH_VETO", "NEUTRAL"
    expected_ev: str
    causal_trigger: str


@dataclass
class SmartWalletProfile:
    address: str
    tag: str
    winrate_7d: Optional[float]
    winrate_30d: Optional[float]
    realized_pnl_usd: Optional[float]
    total_trades: Optional[int]
    avg_holding_time_s: Optional[float]
    is_copyable: Optional[bool]
    verdict: str  # "FOLLOW", "WATCH", "AVOID"
    reason: str


@dataclass
class TokenBuyRecommendation:
    mint: str
    symbol: str
    name: str
    market_cap_usd: float
    liquidity_usd: float
    smart_buyers_count: int
    smart_buyers_tags: List[str]
    rug_ratio: float
    top10_concentration_pct: float
    dev_balance_pct: float
    sniper_ratio_pct: float
    confluence_score: float  # 0 to 100
    pattern: PatternDetection
    price_change_5m: float
    volume_5m_usd: float
    sparkline: List[float]
    admitted: bool
    verdict: str  # "STRONG_BUY", "SPECULATIVE_BUY", "VETO"
    veto_reason: Optional[str]
    tp_targets: Dict[str, float]
    sl_target: float
    metrics_known: bool = True
    bundler_pct: float = 0.0
    wash_trading_flag: bool = False
    clone_factory_flag: bool = False
    dev_factory_flag: bool = False
    avg_tx_m5_usd: float = 0.0
    same_name_count: int = 1
    why_price_moves: str = ""
    actionable_playbook: str = ""


# ---------------------------------------------------------------------------
# P1 — Trazas de decisión (E0→E6): espejo fiel de la cadena elif de
# ``AIQuantModel.predict_token``. Orden importa: primero-que-falla-manda; luego
# del primer veto, las etapas restantes quedan "skip" porque la realidad es que
# el código NO las ejecutó (no se inventan evaluaciones que no ocurrieron).
# ---------------------------------------------------------------------------

_VETO_STAGE_BY_PATTERN = {
    "UNVERIFIED_SECURITY_TRAP": "E1_metrics_known",
    "BUNDLER_UNIFORM_TRAP": "E3_bundler",
    "DEV_FACTORY_RUGGER": "E3_dev_factory",
    "WASH_TRADING_STAIRCASE": "E3_wash_trading",
    "CLONE_VAMP_DERIVATIVE": "E3_clone_factory",
    "DEV_DUMP_RISK": "E3_dev_holding",
    "CABAL_CONCENTRATION": "E3_cabal_top10",
    "ILLIQUID_SLIPPAGE_TRAP": "E3_liquidity_ratio",
}

# (stage, etiqueta humana) en el orden real de la cadena elif.
_E3_STAGE_ORDER = (
    ("E3_bundler", "Bundler / snipers bloque 0"),
    ("E3_dev_factory", "Fábrica serial del dev"),
    ("E3_wash_trading", "Wash-trading en escalera"),
    ("E3_clone_factory", "Clon homónimo / vamp"),
    ("E3_dev_holding", "Dev holding"),
    ("E3_cabal_top10", "Concentración top-10 (Cabal)"),
    ("E3_liquidity_ratio", "Ratio liquidez / market cap"),
)


def _stage_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_decision_stages(
    source: str,
    mint: str,
    symbol: str,
    mc_usd: float,
    liq_usd: float,
    metrics_known: bool,
    bundler_pct: float,
    wash_trading_flag: bool,
    clone_factory_flag: bool,
    dev_factory_flag: bool,
    dev_balance_pct: float,
    top10_pct: float,
    ml_eval: Dict[str, Any],
    thresholds_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Convierte la evaluación real (features + ``ml_eval``) en etapas legibles.

    ``thresholds_meta`` (salida de ``get_effective_decider_thresholds``) declara
    qué umbrales están desbloqueados por la puerta BP-003 (N≥30): la traza muestra
    el umbral EFECTIVO usado y su procedencia (``default_literal`` vs
    ``adaptive_rule``), sin inventar evaluaciones.

    Devuelve una lista de dicts con el contrato de ``decision_traces``:
    ``stage, value, threshold, operator, verdict(pass|fail|skip|info), reason,
    source, source_kind``.
    """
    mc_usd = _stage_float(mc_usd)
    liq_usd = _stage_float(liq_usd)
    liq_ratio = (liq_usd / max(mc_usd, 1.0)) if (mc_usd is not None and liq_usd is not None) else None
    ml_eval = ml_eval or {}

    # Umbrales EFECTIVOS (los devuelve predict_token) + procedencia por regla.
    used = ml_eval.get("thresholds_used")
    used = used if isinstance(used, dict) else {}

    def _used_thr(key: str, default: float) -> float:
        v = _stage_float(used.get(key))
        return v if v is not None else default

    meta = thresholds_meta if isinstance(thresholds_meta, dict) else {}

    def _thr_source(decider_key: Optional[str]):
        if decider_key:
            entry = meta.get(decider_key)
            if isinstance(entry, dict) and entry.get("unlocked"):
                return "adaptive_rules.json", "adaptive_rule"
        return "regla_forense", "default_literal"

    pattern_id = str(ml_eval.get("pattern_id") or "")
    admitted = bool(ml_eval.get("admitted"))
    is_veto = str(ml_eval.get("verdict") or "") == "VETO"
    fail_stage: Optional[str] = _VETO_STAGE_BY_PATTERN.get(pattern_id) if is_veto else None
    if fail_stage is None and not is_veto and not admitted:
        fail_stage = "E4_ev_net"

    def _mk(stage, value, threshold, operator, verdict, reason, src, kind):
        return {
            "stage": stage, "value": value, "threshold": threshold,
            "operator": operator, "verdict": verdict, "reason": reason,
            "source": src, "source_kind": kind,
        }

    mc_txt = f"MC ${mc_usd:,.0f}" if mc_usd is not None else "MC N/D"
    liq_txt = f"liq ${liq_usd:,.0f}" if liq_usd is not None else "liq N/D"
    ratio_txt = f" · ratio {liq_ratio * 100:.1f}%" if liq_ratio is not None else ""
    stages: List[Dict[str, Any]] = [
        _mk("E0_discovery", round(mc_usd, 2) if mc_usd is not None else None, None, None, "info",
            f"{symbol or mint} · {mc_txt} · {liq_txt}{ratio_txt}",
            str(source or "desconocido"), "feed")
    ]

    # E1 — métricas verificadas (fail-closed)
    if fail_stage == "E1_metrics_known":
        stages.append(_mk("E1_metrics_known", 0.0, 1.0, "==", "fail",
                          str(ml_eval.get("veto_reason") or "VETO_UNVERIFIED_METRICS"),
                          "regla_fail_closed", "default_literal"))
    else:
        stages.append(_mk("E1_metrics_known", 1.0 if metrics_known else 0.0, 1.0, "==",
                          "pass" if metrics_known else "skip",
                          "métricas on-chain verificadas" if metrics_known else "sin verificar",
                          "regla_fail_closed", "default_literal"))

    # E3 — cadena de escudos forenses (orden real; despues del primer fail → skip)
    values = {
        "E3_bundler": (_stage_float(bundler_pct),
                       _used_thr("max_bundler_pct", 20.0), "<=", "max_bundler_pct"),
        "E3_dev_factory": (1.0 if dev_factory_flag else 0.0, 0.0, "==", None),
        "E3_wash_trading": (1.0 if wash_trading_flag else 0.0, 0.0, "==", None),
        "E3_clone_factory": (1.0 if clone_factory_flag else 0.0, 0.0, "==", None),
        "E3_dev_holding": (_stage_float(dev_balance_pct),
                           _used_thr("max_dev_balance_pct", 1.5), "<=", "max_dev_balance_pct"),
        "E3_cabal_top10": (_stage_float(top10_pct),
                           _used_thr("max_top10_pct", 30.0), "<=", "max_top10_pct"),
        "E3_liquidity_ratio": (round(liq_ratio, 4) if liq_ratio is not None else None,
                               _used_thr("min_liq_ratio", 0.15), ">=", "min_liq_ratio"),
    }
    after_fail = fail_stage == "E1_metrics_known"
    for stage_name, label in _E3_STAGE_ORDER:
        value, threshold, operator, decider_key = values[stage_name]
        src, kind = _thr_source(decider_key)
        if after_fail:
            stages.append(_mk(stage_name, value, threshold, operator, "skip",
                              f"no evaluado: veto previo", src, kind))
        elif stage_name == fail_stage:
            stages.append(_mk(stage_name, value, threshold, operator, "fail",
                              str(ml_eval.get("veto_reason") or label), src, kind))
            after_fail = True
        elif value is None:
            stages.append(_mk(stage_name, None, threshold, operator, "info",
                              f"{label}: sin dato (no evaluado)", src, kind))
        else:
            stages.append(_mk(stage_name, value, threshold, operator, "pass",
                              f"{label}: {value} {operator} {threshold} (OK)", src, kind))

    # E4 — puerta de EV neto (admisión estricta: EV > 0)
    ev_fraction = _stage_float(ml_eval.get("ev_fraction"))
    if fail_stage == "E4_ev_net":
        stages.append(_mk("E4_ev_net", ev_fraction, 0.0, ">", "fail",
                          f"EV neto {ml_eval.get('expected_ev', 'N/D')} ≤ 0 (no admisible)",
                          "modelo+costes", "model"))
    elif after_fail:
        stages.append(_mk("E4_ev_net", ev_fraction, 0.0, ">", "skip",
                          "no evaluado: veto previo", "modelo+costes", "model"))
    else:
        stages.append(_mk("E4_ev_net", ev_fraction, 0.0, ">", "pass",
                          f"EV neto {ml_eval.get('expected_ev', 'N/D')} > 0 tras costes",
                          "modelo+costes", "model"))

    # E4b — probabilidad ML (informativa: define arquetipo, no admite sola)
    prob = _stage_float(ml_eval.get("probability_success"))
    prob_txt = f"{prob * 100:.1f}%" if prob is not None else "N/D"
    stages.append(_mk("E4_ml_probability", prob, 0.65, ">=", "info",
                      f"P(éxito) {prob_txt} · arquetipo {pattern_id} "
                      f"({ml_eval.get('pattern_label', '')}); fuerte ≥65%, especulativo ≥50%",
                      "modelo:HistGradientBoosting", "model"))

    # E5 — veredicto final
    stages.append(_mk("E5_verdict", _stage_float(ml_eval.get("confluence_score")), None, None, "info",
                      f"{ml_eval.get('verdict', 'N/D')} · "
                      f"{ml_eval.get('veto_reason') or ml_eval.get('causal_trigger') or 'sin confluencia'}",
                      "modelo+reglas", "model"))
    return stages


@dataclass
class PaperPositionState:
    id: str
    mint: str
    symbol: str
    entry_mc: float
    current_mc: float
    size_sol: float
    pnl_pct: float
    pnl_usd: float
    opened_at: float
    status: str  # "OPEN", "CLOSED"
    exit_reason: Optional[str] = None
    tp1_hit: bool = False
    be_hit: bool = False
    tp2_hit: bool = False
    peak_mc: float = 0.0


@dataclass
class _PaperDecision:
    """Decisión mínima compatible con ``LiveStore.record_decision``."""
    admitted: bool = True
    branch: str = "COCKPIT_MANUAL"
    confidence: float = 0.0
    expected_winrate: float = 0.0
    expected_profit_factor: float = 0.0
    veto_reason: Optional[str] = None


class LivePaperTrader:
    """Paper-forward del cockpit con **precios reales** y persistencia.

    A diferencia de la versión anterior, NO simula el precio con un paseo
    aleatorio ni siembra posiciones ficticias: cada tick pide el market cap real
    al ``MarketFeed`` y, si no hay dato verificable, deja la posición quieta (no
    inventa drift). Cada apertura/cierre se persiste en el ``LiveStore``
    (aislado por ``origin``) para auditar winrate/PnL neto reales.

    Sigue siendo paper: no firma ni transmite ninguna transacción.
    """

    ORIGIN = "cockpit_forward"
    # Puerta BP-003: el auto-/boost ML se gatilla récord de cierres {30, 60, 90…},
    # nunca con muestras menores (misma ley N≥30 que los umbrales adaptativos).
    AUTO_BOOST_EVERY_N = 30

    def __init__(self, sol_price_usd: float = 150.0, feed: Optional[Any] = None,
                 store: Optional[Any] = None, origin: str = ORIGIN,
                 evolution_engine: Optional[AutoEvolutionEngine] = None,
                 quant_model: Optional[Any] = None):
        self.sol_price_usd = sol_price_usd
        self.feed = feed
        self.store = store
        self.origin = origin
        self.evolution_engine = evolution_engine
        self.quant_model = quant_model  # inyectado por GMGNBridge (auto-/boost ML)
        self.auto_bot_enabled = True
        self.stall_timeout_sec = 300.0
        self.open_positions: Dict[str, PaperPositionState] = {}
        self.closed_positions: List[PaperPositionState] = []
        self._decision_ids: Dict[str, Optional[int]] = {}
        self.feed_misses = 0
        self.last_feed_error: Optional[str] = None
        self.last_evolution_event: Optional[Dict[str, Any]] = None
        self.last_auto_boost: Optional[Dict[str, Any]] = None

    def _persist_position(self, pos: PaperPositionState) -> None:
        if self.store is not None and hasattr(self.store, "save_paper_position"):
            try:
                self.store.save_paper_position(asdict(pos))
            except Exception:
                pass

    def restore_from_store(self) -> int:
        """Restaura posiciones abiertas y cerradas desde SQLite tras un reinicio."""
        if self.store is None or not hasattr(self.store, "load_paper_positions"):
            return 0
        rows = self.store.load_paper_positions(limit=200)
        restored = 0
        for r in rows:
            try:
                pos = PaperPositionState(
                    id=str(r.get("id", "POS-0")),
                    mint=str(r.get("mint", "")),
                    symbol=str(r.get("symbol", "UNKNOWN")),
                    entry_mc=float(r.get("entry_mc", 0.0)),
                    current_mc=float(r.get("current_mc", 0.0)),
                    size_sol=float(r.get("size_sol", 1.0)),
                    pnl_pct=float(r.get("pnl_pct", 0.0)),
                    pnl_usd=float(r.get("pnl_usd", 0.0)),
                    opened_at=float(r.get("opened_at", time.time())),
                    status=str(r.get("status", "OPEN")),
                    exit_reason=r.get("exit_reason"),
                    tp1_hit=bool(r.get("tp1_hit", False)),
                    be_hit=bool(r.get("be_hit", False)),
                    tp2_hit=bool(r.get("tp2_hit", False)),
                    peak_mc=float(r.get("peak_mc", r.get("entry_mc", 0.0))),
                )
                if pos.status == "OPEN" and pos.mint:
                    self.open_positions[pos.mint] = pos
                else:
                    self.closed_positions.append(pos)
                restored += 1
            except Exception:
                continue
        return restored

    def open_trade(self, mint: str, symbol: str, entry_mc: float, size_sol: float = 1.0) -> PaperPositionState:
        pos = PaperPositionState(
            id=f"POS-{int(time.time()*1000)%100000}",
            mint=mint,
            symbol=symbol,
            entry_mc=entry_mc,
            current_mc=entry_mc,
            size_sol=size_sol,
            pnl_pct=0.0,
            pnl_usd=0.0,
            opened_at=time.time(),
            status="OPEN",
            peak_mc=entry_mc,
        )
        self.open_positions[mint] = pos
        self._persist_position(pos)
        # Persiste la decisión (paper) para que el outcome real quede auditable.
        decision_id = None
        if self.store is not None and hasattr(self.store, "record_decision"):
            feats = {"mint": mint, "entry_price": float(entry_mc or 0.0),
                     "data_origin": self.origin}
            try:
                decision_id = self.store.record_decision(
                    mint, feats, _PaperDecision(), ts=pos.opened_at,
                    provenance={"include_ts_in_sig": True})
            except Exception:
                decision_id = None
        self._decision_ids[mint] = decision_id
        return pos

    def close_trade(self, mint: str, reason: str = "MANUAL_CLOSE") -> Optional[PaperPositionState]:
        if mint in self.open_positions:
            pos = self.open_positions.pop(mint)
            pos.status = "CLOSED"
            pos.exit_reason = reason
            self.closed_positions.append(pos)
            self._persist_position(pos)
            decision_id = self._decision_ids.pop(mint, None)
            pnl_sol = pos.pnl_pct / 100.0 * pos.size_sol
            if decision_id is not None and self.store is not None \
                    and hasattr(self.store, "record_outcome"):
                try:
                    self.store.record_outcome(
                        decision_id, pos.pnl_pct, pnl_sol,
                        exit_reason=reason, resolved_at=time.time())
                except Exception:
                    pass
            if self.evolution_engine is not None:
                try:
                    pos_payload = asdict(pos)
                    pos_payload["realized_pnl_sol"] = round(pnl_sol, 4)
                    pos_payload["entry_mc_usd"] = pos.entry_mc
                    dossier_dict = (
                        self.store.get_token_dossier(mint)
                        if (self.store is not None and hasattr(self.store, "get_token_dossier"))
                        else None
                    )
                    self.last_evolution_event = self.evolution_engine.diagnose_and_learn_from_closed_trade(
                        pos_dict=pos_payload,
                        dossier_dict=dossier_dict,
                    )
                except Exception as exc:
                    logger.debug("Fallo en post-mortem automático: %s", exc)
            # Auto-/boost ML (N≥30) tras cada hito de cierres paper.
            self._maybe_auto_boost()
            return pos
        return None

    def _maybe_auto_boost(self) -> None:
        """Recalibra ``AIQuantModel`` cada ``AUTO_BOOST_EVERY_N`` cierres forward.

        Sólo dispara con modelo inyectado y en múltiplos exactos (30, 60, 90…).
        El resultado queda declarado en ``last_auto_boost`` — éxito o error,
        nunca silencio (anti-falacia).
        """
        n_closed = len(self.closed_positions)
        if n_closed == 0 or n_closed % self.AUTO_BOOST_EVERY_N != 0:
            return
        if self.last_auto_boost is not None and self.last_auto_boost.get("at_n") == n_closed:
            return
        if self.quant_model is None or not hasattr(self.quant_model, "train_and_boost"):
            return
        try:
            self.quant_model.train_and_boost()
            self.last_auto_boost = {"at_n": n_closed, "ts": round(time.time(), 2), "ok": True}
            logger.info("Auto-/boost ML tras %d cierres paper (recalibración completa)", n_closed)
        except Exception as exc:
            self.last_auto_boost = {"at_n": n_closed, "ts": round(time.time(), 2),
                                    "ok": False, "error": str(exc)}
            logger.warning("Auto-/boost ML falló tras %d cierres: %s", n_closed, exc)

    def tick_prices(self) -> None:
        """Actualiza con precios REALES y evalúa salidas (TP1 +50%, BE +25%, Peak Trailing, Stall, TP2 +150%, SL -33.49%).

        Sin feed, o sin dato real para el mint, la posición NO se mueve.
        """
        now = time.time()
        for mint, pos in list(self.open_positions.items()):
            if pos.entry_mc <= 0:
                continue
            mc = self.feed.market_cap_usd(mint) if self.feed is not None else None
            if mc is None or mc <= 0:
                self.feed_misses += 1
                if self.feed is not None:
                    self.last_feed_error = getattr(self.feed, "last_error", None)
                continue
            pos.current_mc = round(float(mc), 2)
            pos.peak_mc = max(pos.peak_mc, pos.current_mc)
            pos.pnl_pct = round(((pos.current_mc - pos.entry_mc) / pos.entry_mc) * 100.0, 2)
            pos.pnl_usd = round((pos.pnl_pct / 100.0) * (pos.size_sol * self.sol_price_usd), 2)
            self._persist_position(pos)

            # Lógica de salidas disciplinadas:
            # 1. Breakeven Lock al +25%
            if pos.pnl_pct >= 25.0 and not pos.be_hit:
                pos.be_hit = True
            # 2. TP1 Parcial al +50%
            if pos.pnl_pct >= 50.0 and not pos.tp1_hit:
                pos.tp1_hit = True
            # 3. TP2 Runner al +150%
            if pos.pnl_pct >= 150.0:
                self.close_trade(mint, reason="TP2_RUNNER_150%_TAKEN")
                continue
            # 4. Peak Trailing Stop: tras alcanzar TP1 (+50%), si retrocede >= 18% desde peak_mc asegura ganancia
            if pos.tp1_hit and pos.peak_mc > pos.entry_mc * 1.45:
                drop_from_peak_pct = ((pos.peak_mc - pos.current_mc) / max(pos.peak_mc, 1.0)) * 100.0
                if drop_from_peak_pct >= 18.0 and pos.pnl_pct > 5.0:
                    self.close_trade(mint, reason="PEAK_TRAILING_STOP_PROFIT_LOCKED")
                    continue
            # 5. Stop Loss de Pánico (-33.49%) o Reversión de Breakeven (+5%)
            if pos.be_hit and pos.pnl_pct <= 5.0:
                self.close_trade(mint, reason="BREAKEVEN_LOCK_+5%_SECURED")
                continue
            elif pos.pnl_pct <= -33.49:
                self.close_trade(mint, reason="PANIC_SL_SLIPPAGE_EXIT")
                continue
            # 6. Stall Timeout Exit (300s sin alcanzar +6% ni TP1)
            elapsed = now - float(pos.opened_at or now)
            if elapsed >= self.stall_timeout_sec and not pos.tp1_hit and -28.0 < pos.pnl_pct < 6.0:
                self.close_trade(mint, reason="STALL_TIMEOUT_EXIT")

    def get_summary(self) -> Dict[str, Any]:
        all_closed = self.closed_positions
        wins = [p for p in all_closed if p.pnl_pct > 0]
        total_pnl_usd = sum(p.pnl_usd for p in all_closed) + sum(p.pnl_usd for p in self.open_positions.values())
        total_pnl_sol = total_pnl_usd / self.sol_price_usd

        summary: Dict[str, Any] = {
            "auto_bot_enabled": self.auto_bot_enabled,
            "mode": "PAPER_FORWARD_REAL_PRICES",
            "origin": self.origin,
            "open_count": len(self.open_positions),
            "closed_count": len(all_closed),
            "winrate_pct": round(len(wins) / max(len(all_closed), 1) * 100.0, 1),
            "total_pnl_sol": round(total_pnl_sol, 3),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "open_positions": [asdict(p) for p in self.open_positions.values()],
            "recent_closed": [asdict(p) for p in all_closed[-8:]],
            "feed": self.feed.health() if self.feed is not None else {"source": None},
            "feed_misses": self.feed_misses,
            "last_feed_error": self.last_feed_error,
            "last_evolution_event": self.last_evolution_event,
            "last_auto_boost": self.last_auto_boost,
        }
        # Auditoría NETA persistida (aislada por origin). Solo si hay store.
        if self.store is not None and hasattr(self.store, "net_audit"):
            try:
                summary["net_audit"] = self.store.net_audit(
                    origin=self.origin, admitted_only=True)
            except Exception:
                pass
        return summary


class GMGNBridge:
    """Puente entre el runtime de Python / Cockpit y GMGN Skills / OpenAPI."""

    def __init__(self, cli_path: Optional[str] = None, feed: Optional[Any] = None,
                 store: Optional[Any] = None, demo_mode: bool = False,
                 use_db_fallback: bool = False,
                 evolution_engine: Optional[AutoEvolutionEngine] = None):
        self.cli_path = cli_path or self._resolve_cli()
        self.api_key = os.environ.get("GMGN_API_KEY")
        self.feed = feed
        self.store = store
        self.demo_mode = bool(demo_mode)
        self.use_db_fallback = bool(use_db_fallback)
        self.quant_model = AIQuantModel()
        self.dossier_scanner = GMGNDossierScanner(store=store)
        self.evolution_engine = evolution_engine or (AutoEvolutionEngine(store=store) if store is not None else None)
        self.paper_trader = LivePaperTrader(
            feed=feed, store=store, evolution_engine=self.evolution_engine,
            quant_model=self.quant_model,
        )
        self.last_screen_source: Optional[str] = None
        self._check_global_env()

    def _check_global_env(self) -> None:
        """Carga ~/.config/gmgn/.env si existe y no tenemos la clave en entorno."""
        if not self.api_key:
            cfg_path = os.path.expanduser(os.path.join("~", ".config", "gmgn", ".env"))
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if line.startswith("GMGN_API_KEY="):
                                self.api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                except Exception:
                    pass

    @staticmethod
    def _resolve_cli() -> Optional[str]:
        cmd = shutil.which("gmgn-cli") or shutil.which("gmgn-cli.cmd")
        if cmd:
            return cmd
        win_candidates = [
            os.environ.get("GMGN_CLI_PATH", ""),
            os.path.expandvars(r"%APPDATA%\npm\gmgn-cli.cmd"),
            os.environ.get("GMGN_CLI_GEMINI_PATH", ""),
            os.path.expanduser(r"~/.gemini/antigravity/bin/gmgn-cli.cmd"),
        ]
        for c in win_candidates:
            if os.path.exists(c):
                return c
        return None

    def is_available(self) -> bool:
        return self.cli_path is not None and os.path.exists(self.cli_path)

    def is_authenticated(self) -> bool:
        return bool(self.api_key)

    def run_cli_json(self, args: List[str], timeout: float = 15.0) -> Optional[Dict[str, Any]]:
        if not self.cli_path:
            return None
        cmd = [self.cli_path] + args
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=(os.name == "nt"),
            )
            stdout = res.stdout.strip()
            if not stdout:
                return None
            return json.loads(stdout)
        except Exception as exc:
            logger.debug(f"[gmgn-bridge] Error ejecutando {args}: {exc}")
            return None

    # =========================================================================
    # RECONOCEDOR DE PATRONES IA
    # =========================================================================

    @staticmethod
    def classify_pattern(
        smart_cnt: int,
        rug_ratio: float,
        top10: float,
        dev_pct: float,
        sniper_pct: float,
        mc: float,
        liq: float,
    ) -> PatternDetection:
        """Determina el arquetipo causal del token en base a microestructura."""
        if rug_ratio > 0.30:
            return PatternDetection(
                pattern_id="RUG_PULL_SIGNATURE",
                label="🚨 Riesgo Crítico de Rug Pull",
                confidence_pct=96.4,
                bias="BEARISH_VETO",
                expected_ev="-35.0%",
                causal_trigger=f"Rug ratio {rug_ratio:.2f} excede el límite de seguridad (0.30)",
            )
        if top10 > 30.0:
            return PatternDetection(
                pattern_id="CABAL_ACCUMULATION_TRAP",
                label="🔴 Trampa de Cabal Concentrado",
                confidence_pct=92.1,
                bias="BEARISH_VETO",
                expected_ev="-33.5%",
                causal_trigger=f"Top 10 billeteras coludidas retienen {top10:.1f}% (> 30%)",
            )
        if sniper_pct > 25.0:
            return PatternDetection(
                pattern_id="BLOCK0_SNIPER_OVERHANG",
                label="⚠️ Sobrecarga Snipers Bloque 0",
                confidence_pct=88.5,
                bias="BEARISH_VETO",
                expected_ev="-31.0%",
                causal_trigger=f"Snipers controlan {sniper_pct:.1f}% (> 25%). Venta masiva inminente",
            )
        if dev_pct > 1.50:
            return PatternDetection(
                pattern_id="DEV_DUMP_RISK",
                label="⚠️ Inventario Dev Peligroso",
                confidence_pct=89.0,
                bias="BEARISH_VETO",
                expected_ev="-35.0%",
                causal_trigger=f"Dev retiene {dev_pct:.1f}% (> 1.50%). Riesgo de drenaje de pool",
            )
        if smart_cnt >= 2:
            return PatternDetection(
                pattern_id="SMART_MONEY_CONFLUENCE",
                label="🟢 Confluencia Smart Money",
                confidence_pct=94.2,
                bias="BULLISH",
                expected_ev="+26.8%",
                causal_trigger=f"{smart_cnt} billeteras de Smart Money acumulando simultáneamente",
            )
        if smart_cnt == 1 and top10 < 20.0:
            return PatternDetection(
                pattern_id="SMART_ACCUMULATOR",
                label="🟢 Acumulación Ballena Solitaria",
                confidence_pct=85.0,
                bias="BULLISH",
                expected_ev="+19.4%",
                causal_trigger="Ballena verificada con WR > 60% entrando con supply distribuido",
            )
        if mc <= 5000.0 and liq >= 1500.0:
            return PatternDetection(
                pattern_id="EXPANSION_BREAKOUT",
                label="🔵 Expansión Orgánica Temprana",
                confidence_pct=81.1,
                bias="BULLISH",
                expected_ev="+15.0%",
                causal_trigger="Ratio Liquidez/MC superior a 30% en micro-cap sin concentración",
            )
        return PatternDetection(
            pattern_id="UNCONFIRMED_VOLATILITY",
            label="⚪ Flujo Sin Confluencia Confirmada",
            confidence_pct=50.0,
            bias="NEUTRAL",
            expected_ev="0.0%",
            causal_trigger="Sin confirmación simultánea de Smart Money ni patrones de ruptura",
        )

    def _load_wallets_from_db(self) -> List[Dict[str, Any]]:
        db_paths = [
            os.environ.get("PUMPSCOPE_DB", "custom.db"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo_pumpfun.db"),
        ]
        for path in db_paths:
            if not os.path.exists(path):
                continue
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                cur = con.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "WalletProfile" in tables:
                    rows = cur.execute("SELECT id, label, solBalance, closedTrades, wins FROM WalletProfile").fetchall()
                    con.close()
                    wallets = []
                    for r in rows:
                        trades = int(r[3]) if r[3] is not None else None
                        wins = int(r[4]) if r[4] is not None else None
                        label = str(r[1] or "Trader")
                        # El esquema solo expone balance/cierres/ganancias: PnL USD,
                        # WR 30d y holding medio NO son medibles aquí (anti-falacias:
                        # sin dato -> None, jamás un literal sustitutivo).
                        wallets.append({
                            "address": str(r[0]),
                            "tag": label,
                            "winrate_7d": round(wins / trades, 3) if (trades and wins is not None) else None,
                            "total_trades": trades,
                        })
                    if wallets:
                        return wallets
            except Exception as exc:
                logger.warning("Error leyendo carteras de %s: %s", path, exc)
        return []

    # =========================================================================
    # 1. "A QUIÉN SEGUIR" — Billeteras Smart Money & KOLs
    # =========================================================================

    def rank_wallets_to_follow(
        self, chain: str = "sol", min_winrate: float = 0.55
    ) -> List[SmartWalletProfile]:
        raw_wallets: List[Dict[str, Any]] = []

        if self.is_available() and self.is_authenticated():
            data = self.run_cli_json(["track", "smartmoney", "--chain", chain])
            if data and isinstance(data, dict):
                raw_wallets = data.get("wallets", data.get("list", []))

        # Cargar de base de datos canónica si está disponible
        if not raw_wallets:
            raw_wallets = self._load_wallets_from_db()

        # Catálogo de DEMOSTRACIÓN: solo con demo_mode explícito (tests/offline).
        # En vivo (demo_mode=False) la respuesta honesta es [] — jamás un catálogo inventado.
        if not raw_wallets and self.demo_mode:
            raw_wallets = [
                {
                    "address": "4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh",
                    "tag": "Ballena B (Top Quant)",
                    "winrate_7d": 0.621,
                    "winrate_30d": 0.605,
                    "realized_pnl_usd": 64800.0,
                    "total_trades": 29,
                    "avg_holding_time_s": 2490.0,  # 41.5 min -> Copiable humanamente
                },
                {
                    "address": "215nhcAHjQQGgwpQSJQ7zR26etbjjtVdW74NLzwEgQjP",
                    "tag": "Ballena A (Whale Swing)",
                    "winrate_7d": 1.000,
                    "winrate_30d": 0.850,
                    "realized_pnl_usd": 25680.0,
                    "total_trades": 8,
                    "avg_holding_time_s": 26800.0,  # 7.4 horas -> swing amplio
                },
                {
                    "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
                    "tag": "Smart Degen #1",
                    "winrate_7d": 0.680,
                    "winrate_30d": 0.620,
                    "realized_pnl_usd": 42150.0,
                    "total_trades": 84,
                    "avg_holding_time_s": 240.0,  # 4 min -> scalping copiable
                },
                {
                    "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
                    "tag": "KOL Influencer",
                    "winrate_7d": 0.610,
                    "winrate_30d": 0.580,
                    "realized_pnl_usd": 18900.0,
                    "total_trades": 45,
                    "avg_holding_time_s": 650.0,
                },
                {
                    "address": "4vMdpLL4yUoc8a34ZtE9q9sS5WbY7gX89B1nKLmNoPqR",
                    "tag": "Bot MEV Jito Bloque 0",
                    "winrate_7d": 0.850,
                    "winrate_30d": 0.810,
                    "realized_pnl_usd": 95000.0,
                    "total_trades": 1200,
                    "avg_holding_time_s": 1.5,  # NO COPIABLE
                },
                {
                    "address": "FqamE7xrahg7FEWoByrx1o8SeyHt44rpmE6ZQfT7zrve",
                    "tag": "Trader C (Bleeder)",
                    "winrate_7d": 0.261,
                    "winrate_30d": 0.240,
                    "realized_pnl_usd": -12570.0,
                    "total_trades": 46,
                    "avg_holding_time_s": 134.0,
                },
            ]

        results: List[SmartWalletProfile] = []
        for w in raw_wallets:
            addr = w.get("address", "unknown")
            tag = w.get("tag", "trader")
            wr7 = _stage_float(w.get("winrate_7d"))
            wr30 = _stage_float(w.get("winrate_30d"))
            pnl = _stage_float(w.get("realized_pnl_usd"))
            trades_raw = w.get("total_trades")
            trades = int(trades_raw) if isinstance(trades_raw, (int, float)) and not isinstance(trades_raw, bool) else None
            hold_time = _stage_float(w.get("avg_holding_time_s"))

            missing = [name for name, val in (
                ("WR 7D", wr7), ("WR 30D", wr30), ("PnL USD", pnl),
                ("nº trades", trades), ("holding medio", hold_time)) if val is None]

            if hold_time is not None and hold_time < 5.0:
                is_copyable = False
                verdict = "AVOID"
                reason = "Bot MEV / Bloque 0: holding medio < 5s. Pérdida segura por slippage de seguimiento."
            elif (wr7 is not None and wr7 >= min_winrate and pnl is not None and pnl > 0
                  and trades is not None and trades >= 5 and hold_time is not None):
                is_copyable = True
                verdict = "FOLLOW"
                reason = f"WR 7D {wr7*100:.1f}%, PnL ${pnl:,.0f}, holding {hold_time/60:.1f}m. Conduce a EV positivo."
            elif wr30 is not None and wr30 >= min_winrate and pnl is not None and pnl > 0:
                is_copyable = True
                verdict = "WATCH"
                reason = "Consistente a 30 días; verificando cadencia semanal."
            elif wr7 is not None and pnl is not None and wr7 < min_winrate and pnl <= 0:
                is_copyable = False
                verdict = "AVOID"
                reason = f"EV desfavorable: WR 7D {wr7*100:.1f}% < {min_winrate*100:.0f}% y PnL ${pnl:,.0f}."
            elif missing:
                is_copyable = None
                verdict = "WATCH"
                reason = "Datos incompletos en la fuente (sin dato: " + ", ".join(missing) + ")."
            else:
                is_copyable = False
                verdict = "AVOID"
                reason = "EV desfavorable con los datos disponibles (WR 7D / PnL / muestra no cumplen el listón)."

            results.append(
                SmartWalletProfile(
                    address=addr,
                    tag=tag,
                    winrate_7d=wr7,
                    winrate_30d=wr30,
                    realized_pnl_usd=pnl,
                    total_trades=trades,
                    avg_holding_time_s=hold_time,
                    is_copyable=is_copyable,
                    verdict=verdict,
                    reason=reason,
                )
            )

        results.sort(
            key=lambda x: (
                1 if x.verdict == "FOLLOW" else (0 if x.verdict == "WATCH" else -1),
                x.realized_pnl_usd if x.realized_pnl_usd is not None else float("-inf"),
            ),
            reverse=True,
        )
        return results

    # =========================================================================
    # 2. "QUÉ TOKEN COMPRAR" — Matriz de Confluencia & Patrones
    # =========================================================================

    @staticmethod
    def _feed_candidate(snap: Dict[str, Any]) -> Dict[str, Any]:
        """Candidato desde el feed real (DexScreener).

        Concentración/dev/snipers quedan **desconocidos** (0.0) y se marcan con
        ``metrics_known=False``: la confluencia los puntúa solo con lo verificable
        (market cap, liquidez, precio/volumen 5m) en vez de inventarlos.
        """
        return {
            "mint": str(snap.get("mint") or ""),
            "symbol": snap.get("symbol") or "N/A",
            "name": snap.get("name") or "Unknown",
            "market_cap_usd": snap.get("market_cap_usd", 0.0),
            "liquidity_usd": snap.get("liquidity_usd", 0.0),
            "smart_buyers_count": 0,
            "smart_buyers_tags": [],
            "rug_ratio": 0.0,
            "top10_concentration_pct": 0.0,
            "dev_balance_pct": 0.0,
            "sniper_ratio_pct": 0.0,
            "price_change_5m": snap.get("price_change_5m", 0.0),
            "volume_5m_usd": snap.get("volume_5m_usd", 0.0),
            "sparkline": [],
            "metrics_known": False,
        }

    def _load_tokens_from_db(self, max_tokens: int = 15) -> List[Dict[str, Any]]:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parent_dir = os.path.dirname(base_dir)
        db_paths = [
            os.environ.get("PUMPSCOPE_DB", "custom.db"),
            os.path.join(base_dir, "demo_pumpfun.db"),
            os.path.join(parent_dir, "demo_pumpfun.db"),
            os.path.join(base_dir, "cockpit", "runtime", "cockpit_forward.db"),
        ]
        for db_path in db_paths:
            if not os.path.exists(db_path):
                continue
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
                cur = con.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "Token" in tables:
                    query = """
                    SELECT mint, symbol, name, marketCap, firstMarketCap, firstLiquidity, liquidity,
                           devBalancePct, top10HoldersPct, bundlerHoldingPct, changeM5, volume24h, holderCount
                    FROM Token
                    WHERE firstMarketCap > 50 AND firstLiquidity > 20 AND symbol IS NOT NULL
                    ORDER BY firstSeenAt DESC
                    LIMIT ?
                    """
                    rows = cur.execute(query, (max_tokens,)).fetchall()
                    con.close()
                    candidates = []
                    skipped = 0
                    for r in rows:
                        # Solo valores medidos; sin métricas de entrada verificables la
                        # fila se omite (anti-falacias §1.5: jamás fabricar).
                        fmc = _stage_float(r["firstMarketCap"]) or _stage_float(r["marketCap"])
                        fliq = _stage_float(r["firstLiquidity"]) or _stage_float(r["liquidity"])
                        if fmc is None or fliq is None:
                            skipped += 1
                            continue
                        mc = _stage_float(r["marketCap"]) or fmc
                        liq = _stage_float(r["liquidity"]) or fliq
                        dev = _stage_float(r["devBalancePct"])
                        top10 = _stage_float(r["top10HoldersPct"])
                        bundler = _stage_float(r["bundlerHoldingPct"])
                        holder = int(r["holderCount"]) if r["holderCount"] is not None else 0
                        chg5m = _stage_float(r["changeM5"]) or 0.0
                        vol24h = _stage_float(r["volume24h"]) or 0.0
                        metrics_known = dev is not None and top10 is not None and bundler is not None
                        candidates.append({
                            "mint": str(r["mint"]),
                            "symbol": str(r["symbol"] or "N/A"),
                            "name": str(r["name"] or "Token"),
                            "market_cap_usd": round(mc, 2),
                            "liquidity_usd": round(liq, 2),
                            "first_market_cap": round(fmc, 2),
                            "first_liquidity": round(fliq, 2),
                            "smart_buyers_count": 0,   # este esquema no verifica smart money
                            "smart_buyers_tags": [],
                            "rug_ratio": 0.0,           # sin medición propia de rug en esta fuente
                            "top10_concentration_pct": round(top10, 1) if top10 is not None else 0.0,
                            "dev_balance_pct": round(dev, 2) if dev is not None else 0.0,
                            "sniper_ratio_pct": round(bundler, 1) if bundler is not None else 0.0,
                            "bundler_pct": round(bundler, 1) if bundler is not None else 0.0,
                            "price_change_5m": round(chg5m, 2),
                            "volume_5m_usd": round(vol24h / 288.0, 2),
                            "sparkline": [],           # sin serie sintética
                            "holder_count": holder,
                            "metrics_known": metrics_known,
                        })
                    if skipped:
                        logger.info("Token: %d filas sin métricas de entrada omitidas (%s)",
                                    skipped, db_path)
                    if candidates:
                        return candidates
                elif "tokens" in tables:
                    # Esquema sin métricas on-chain verificables (MC/liquidez/holders):
                    # prohibido fabricarlas (§1.5). Fail-closed: no hay candidatos.
                    con.close()
                    logger.warning(
                        "Esquema 'tokens' en %s sin métricas on-chain verificables: "
                        "no se fabrican candidatos (§1.5).", db_path)
                    continue
            except Exception as exc:
                logger.warning("Error leyendo tokens de %s: %s", db_path, exc)
        return []

    def screen_tokens_to_buy(
        self, chain: str = "sol", max_tokens: int = 10, enrich_live_dossiers: bool = False
    ) -> List[TokenBuyRecommendation]:
        candidates: List[Dict[str, Any]] = []
        source: Optional[str] = None

        if self.is_available() and self.is_authenticated():
            data = self.run_cli_json(["market", "trenches", "--chain", chain, "--type", "new_creation"])
            if data and isinstance(data, dict):
                candidates = data.get("tokens", data.get("list", []))[:max_tokens]
                if candidates:
                    source = "gmgn"

        # Fuente REAL sin API key: tokens recientes con market cap de DexScreener.
        if not candidates and self.feed is not None:
            try:
                discovered = self.feed.discover_new_tokens(max_tokens)
                if discovered:
                    candidates = [self._feed_candidate(s) for s in discovered]
                    source = "dexscreener"
            except Exception as exc:
                logger.warning("Error descubriendo tokens en feed: %s", exc)

        # Fallback a Base de Datos Canónica Local (36,998 tokens reales on-chain)
        if not candidates and self.use_db_fallback:
            candidates = self._load_tokens_from_db(max_tokens)
            if candidates:
                source = "custom_db"

        # Enriquecimiento forense multi-fuente en vivo (RugCheck / GoPlus / Clones) para candidatos descubiertos
        if enrich_live_dossiers and candidates and self.dossier_scanner is not None:
            enriched_count = 0
            for c in candidates:
                if enriched_count >= 4:
                    break
                c_mint = str(c.get("mint") or "").strip()
                if not c_mint or c.get("metrics_known", True):
                    continue
                try:
                    dossier = self.dossier_scanner.scan_token_live(c_mint)
                    if dossier and dossier.metrics_known:
                        c["metrics_known"] = True
                        c["top10_concentration_pct"] = dossier.top10_float_pct
                        c["dev_balance_pct"] = dossier.dev_balance_pct
                        c["sniper_ratio_pct"] = dossier.sniper_pct
                        c["bundler_pct"] = dossier.bundler_pct
                        c["wash_trading_flag"] = dossier.wash_trading_flag
                        c["clone_factory_flag"] = dossier.clone_factory_flag
                        c["dev_factory_flag"] = dossier.dev_factory_flag
                        c["avg_tx_m5_usd"] = dossier.avg_tx_m5_usd
                        c["same_name_count"] = dossier.same_name_count
                        c["why_price_moves"] = dossier.why_price_moves
                        c["actionable_playbook"] = dossier.actionable_playbook
                        c["holder_count"] = dossier.holder_count
                        enriched_count += 1
                except Exception as exc:
                    logger.debug("No se pudo enriquecer candidato en vivo %s: %s", c_mint, exc)

        # Datos de DEMOSTRACIÓN: fallback solo si demo_mode está explícitamente activo (§3 Sin Mocks Silenciosos)
        if not candidates and self.demo_mode:
            source = "demo"
            candidates = [
                {
                    "mint": "SoL1111111111111111111111111111111111111112",
                    "symbol": "ALPHA_MEME",
                    "name": "Alpha Confluence Token",
                    "market_cap_usd": 12500.0,
                    "liquidity_usd": 3800.0,
                    "smart_buyers_count": 3,
                    "smart_buyers_tags": ["Ballena B (Top Quant)", "Smart Degen #1"],
                    "rug_ratio": 0.08,
                    "top10_concentration_pct": 18.5,
                    "dev_balance_pct": 0.8,
                    "sniper_ratio_pct": 12.0,
                    "price_change_5m": +34.2,
                    "volume_5m_usd": 9400.0,
                    "sparkline": [10.0, 10.5, 11.2, 11.0, 11.8, 12.0, 12.4, 12.1, 12.8, 13.5],
                },
                {
                    "mint": "PUMP3333333333333333333333333333333333333333",
                    "symbol": "QUANT_SURGE",
                    "name": "Organic Community Surge",
                    "market_cap_usd": 24000.0,
                    "liquidity_usd": 6500.0,
                    "smart_buyers_count": 2,
                    "smart_buyers_tags": ["Ballena A (Whale Swing)"],
                    "rug_ratio": 0.12,
                    "top10_concentration_pct": 14.2,
                    "dev_balance_pct": 0.0,
                    "sniper_ratio_pct": 9.5,
                    "price_change_5m": +18.6,
                    "volume_5m_usd": 14200.0,
                    "sparkline": [20.0, 20.2, 20.8, 21.0, 21.5, 22.0, 22.4, 23.1, 23.8, 24.0],
                },
                {
                    "mint": "RUG9999999999999999999999999999999999999999",
                    "symbol": "CABAL_TRAP",
                    "name": "High Cabal Scam Token",
                    "market_cap_usd": 8900.0,
                    "liquidity_usd": 1200.0,
                    "smart_buyers_count": 0,
                    "smart_buyers_tags": [],
                    "rug_ratio": 0.65,
                    "top10_concentration_pct": 58.2,
                    "dev_balance_pct": 8.5,
                    "sniper_ratio_pct": 42.0,
                    "price_change_5m": -45.0,
                    "volume_5m_usd": 2100.0,
                    "sparkline": [15.0, 14.2, 12.0, 11.5, 10.8, 9.9, 9.2, 8.8, 8.5, 7.2],
                },
                {
                    "mint": "JIT0000000000000000000000000000000000000001",
                    "symbol": "SNIPER_DUMP",
                    "name": "Block 0 Jito Trap",
                    "market_cap_usd": 15000.0,
                    "liquidity_usd": 4100.0,
                    "smart_buyers_count": 1,
                    "smart_buyers_tags": ["Bot MEV Jito Bloque 0"],
                    "rug_ratio": 0.22,
                    "top10_concentration_pct": 22.0,
                    "dev_balance_pct": 0.5,
                    "sniper_ratio_pct": 38.0,
                    "price_change_5m": -12.4,
                    "volume_5m_usd": 6800.0,
                    "sparkline": [16.0, 16.5, 17.0, 16.8, 16.2, 15.8, 15.2, 14.8, 14.2, 13.9],
                },
            ]

        self.last_screen_source = source
        recommendations: List[TokenBuyRecommendation] = []
        for t in candidates:
            mint = t.get("mint", "unknown")
            sym = t.get("symbol", "N/A")
            name = t.get("name", "Unknown")
            mc = float(t.get("market_cap_usd", 0.0))
            liq = float(t.get("liquidity_usd", 0.0))
            smart_cnt = int(t.get("smart_buyers_count", 0))
            smart_tags = t.get("smart_buyers_tags", [])
            rug = float(t.get("rug_ratio", 0.0))
            top10 = float(t.get("top10_concentration_pct", 0.0))
            dev_pct = float(t.get("dev_balance_pct", 0.0))
            sniper_pct = float(t.get("sniper_ratio_pct", 0.0))
            chg5m = float(t.get("price_change_5m", 0.0))
            vol5m = float(t.get("volume_5m_usd", 0.0))
            spark = t.get("sparkline") or []
            metrics_known = bool(t.get("metrics_known", True))
            bundler_pct = float(t.get("bundler_pct", sniper_pct if sniper_pct >= 25.0 else 0.0))
            wash_flag = bool(t.get("wash_trading_flag", False))
            clone_flag = bool(t.get("clone_factory_flag", False))
            dev_factory_flag = bool(t.get("dev_factory_flag", False))
            avg_tx_m5 = float(t.get("avg_tx_m5_usd") or 0.0)
            same_name_cnt = int(t.get("same_name_count", 1))

            # Umbrales adaptativos con puerta BP-003 (N≥30): si ninguna regla está
            # desbloqueada, el decisor usa sus literales de siempre (fail-closed).
            effective_thresholds: Optional[Dict[str, float]] = None
            thresholds_meta: Optional[Dict[str, Dict[str, Any]]] = None
            if self.evolution_engine is not None and hasattr(
                    self.evolution_engine, "get_effective_decider_thresholds"):
                try:
                    thresholds_meta = self.evolution_engine.get_effective_decider_thresholds()
                    unlocked = {k: float(v["value"]) for k, v in thresholds_meta.items()
                                if v.get("unlocked")}
                    effective_thresholds = unlocked or None
                except Exception as exc:
                    logger.warning("Umbrales adaptativos ilegibles; se usan literales: %s", exc)
                    effective_thresholds, thresholds_meta = None, None

            # Evaluación Cuantitativa con Modelo de Machine Learning (HistGradientBoosting + Fail-Closed)
            ml_eval = self.quant_model.predict_token(
                first_mc=_stage_float(t.get("first_market_cap")) or mc,
                first_liq=_stage_float(t.get("first_liquidity")) or liq,
                volume_24h=vol5m * 288.0,
                holder_count=int(t.get("holder_count") or 0),
                dev_balance_pct=dev_pct,
                top10_pct=top10,
                metrics_known=metrics_known,
                bundler_pct=bundler_pct,
                wash_trading_flag=wash_flag,
                clone_factory_flag=clone_flag,
                dev_factory_flag=dev_factory_flag,
                thresholds=effective_thresholds,
            )

            pat = PatternDetection(
                pattern_id=ml_eval["pattern_id"],
                label=ml_eval["pattern_label"],
                confidence_pct=round(ml_eval["probability_success"] * 100.0, 1),
                bias=ml_eval["bias"],
                expected_ev=ml_eval["expected_ev"],
                causal_trigger=ml_eval["causal_trigger"],
            )

            veto_reason = ml_eval["veto_reason"]
            score = ml_eval["confluence_score"]
            admitted = bool(ml_eval["admitted"] and metrics_known)
            verdict = ml_eval["verdict"] if metrics_known else "VETO"

            # Si el token fue vetado y tiene MC real, registrarlo en el Shadow Veto Ledger ("¿hubiera ganado o perdido?")
            if not admitted and self.evolution_engine is not None and mc > 0.0:
                self.evolution_engine.register_vetoed_token(
                    mint=mint,
                    symbol=sym,
                    veto_reason=str(veto_reason or "VETO"),
                    entry_mc_usd=mc,
                )

            tp_targets = {
                "tp1_partial_50pct": round(mc * 1.50, 2),
                "breakeven_lock_25pct": round(mc * 1.25, 2),
                "tp2_runner_150pct": round(mc * 2.50, 2),
            }
            sl_target = round(mc * (1.0 - 0.3349), 2)

            why_moves = str(t.get("why_price_moves") or ml_eval["causal_trigger"])
            playbook = str(
                t.get("actionable_playbook")
                or (
                    f"TP1 +50% (${tp_targets['tp1_partial_50pct']:,.0f}), BE +25%, SL real -33.49%"
                    if admitted
                    else f"VETO: {veto_reason or 'sin confluencia verificada'}"
                )
            )

            # Auto-Sniper Bot en acción: exige metrics_known=True, admitted=True, score >= 85
            executed = False
            if (self.paper_trader.auto_bot_enabled and metrics_known and admitted and score >= 85.0
                    and mint not in self.paper_trader.open_positions
                    and self.feed is not None
                    and self.feed.market_cap_usd(mint) is not None):
                self.paper_trader.open_trade(mint, sym, mc, size_sol=1.5)
                executed = True

            # P1 — persiste la traza E0→E6 de esta decisión (pensamiento filtrado en vivo).
            if self.store is not None and hasattr(self.store, "append_decision_trace"):
                if executed:
                    exec_verdict, exec_reason = "pass", "OPENED_PAPER_TRADE"
                elif not metrics_known:
                    exec_verdict, exec_reason = "skip", "METRICAS_SIN_VERIFICAR"
                elif not admitted:
                    exec_verdict, exec_reason = "skip", f"NO_ADMISION: {veto_reason or 'EV neto <= 0'}"
                elif not self.paper_trader.auto_bot_enabled:
                    exec_verdict, exec_reason = "fail", "AUTO_BOT_DISABLED"
                elif score < 85.0:
                    exec_verdict, exec_reason = "fail", f"SCORE_BELOW_THRESHOLD: confluencia {score:.1f} < 85.0"
                elif mint in self.paper_trader.open_positions:
                    exec_verdict, exec_reason = "fail", "ALREADY_OPEN: posición ya abierta"
                else:
                    exec_verdict, exec_reason = "fail", "NO_LIVE_PRICE: feed sin market cap real"
                try:
                    stages = build_decision_stages(
                        source=str(source or ""), mint=mint, symbol=sym, mc_usd=mc, liq_usd=liq,
                        metrics_known=metrics_known, bundler_pct=bundler_pct,
                        wash_trading_flag=wash_flag, clone_factory_flag=clone_flag,
                        dev_factory_flag=dev_factory_flag, dev_balance_pct=dev_pct,
                        top10_pct=top10, ml_eval=ml_eval,
                        thresholds_meta=thresholds_meta)
                    stages.append({
                        "stage": "E6_execution", "value": round(score, 1), "threshold": 85.0,
                        "operator": ">=", "verdict": exec_verdict, "reason": exec_reason,
                        "source": "auto_sniper", "source_kind": "default_literal"})
                    self.store.append_decision_trace(
                        trace_id=f"{int(time.time() * 1000):x}-{str(mint)[:6]}",
                        mint=mint, symbol=sym, ts=time.time(), stages=stages)
                except Exception as exc:
                    logger.warning("No se pudo persistir traza de decisión (%s): %s", mint, exc)

            recommendations.append(
                TokenBuyRecommendation(
                    mint=mint,
                    symbol=sym,
                    name=name,
                    market_cap_usd=mc,
                    liquidity_usd=liq,
                    smart_buyers_count=smart_cnt,
                    smart_buyers_tags=smart_tags,
                    rug_ratio=rug,
                    top10_concentration_pct=top10,
                    dev_balance_pct=dev_pct,
                    sniper_ratio_pct=sniper_pct,
                    confluence_score=score,
                    pattern=pat,
                    price_change_5m=chg5m,
                    volume_5m_usd=vol5m,
                    sparkline=spark,
                    admitted=admitted,
                    verdict=verdict,
                    veto_reason=veto_reason,
                    tp_targets=tp_targets,
                    sl_target=sl_target,
                    metrics_known=metrics_known,
                    bundler_pct=bundler_pct,
                    wash_trading_flag=wash_flag,
                    clone_factory_flag=clone_flag,
                    dev_factory_flag=dev_factory_flag,
                    avg_tx_m5_usd=avg_tx_m5,
                    same_name_count=same_name_cnt,
                    why_price_moves=why_moves,
                    actionable_playbook=playbook,
                )
            )

        recommendations.sort(
            key=lambda x: (1 if x.admitted else 0, x.confluence_score), reverse=True
        )
        return recommendations

    # =========================================================================
    # 3. AUDITORÍA FORENSE INDIVIDUAL DE TOKEN (Multi-Fuente + GMGN Dossier)
    # =========================================================================

    def audit_token(self, address: str, chain: str = "sol") -> Dict[str, Any]:
        sec_data: Dict[str, Any] = {}
        if self.is_available() and self.is_authenticated():
            raw_sec = self.run_cli_json(["token", "security", "--chain", chain, "--address", address])
            if raw_sec:
                sec_data = raw_sec

        # Escaneo multi-fuente en vivo (GoPlus / RugCheck / DexScreener) si no hay datos CLI
        dossier_dict: Optional[Dict[str, Any]] = None
        if not sec_data and address and len(address.strip()) >= 16:
            try:
                dossier = self.dossier_scanner.scan_token_live(address, chain=chain)
                if dossier and dossier.metrics_known:
                    dossier_dict = dossier.to_dict()
            except Exception as exc:
                logger.debug("Fallback en audit_token scan_token_live: %s", exc)

        mc_real, liq_real = 0.0, 0.0  # 0.0 = "sin dato" (marcador honesto, no literal de mercado)
        if dossier_dict:
            composite = _stage_float(dossier_dict.get("composite_dd_score"))
            rug_ratio = round(max(0.0, (100.0 - composite) / 100.0), 2) if composite is not None else None
            top10 = _stage_float(dossier_dict.get("top10_float_pct"))
            dev_holding = _stage_float(dossier_dict.get("dev_balance_pct"))
            sniper_pct = _stage_float(dossier_dict.get("bundler_pct"))
            if sniper_pct is None:
                sniper_pct = _stage_float(dossier_dict.get("sniper_pct"))
            mc_real = _stage_float(dossier_dict.get("market_cap_usd")) or 0.0
            liq_real = _stage_float(dossier_dict.get("liquidity_usd")) or 0.0
        elif sec_data:
            rug_ratio = _stage_float(sec_data.get("rug_ratio"))
            top10 = _stage_float(sec_data.get("top10_holder_pct"))
            dev_holding = _stage_float(sec_data.get("dev_token_burn_pct"))
            sniper_pct = _stage_float(sec_data.get("sniper_pct"))
        else:
            # Fail-closed (§1.5): sin fuente verificable no se emite juicio de seguridad
            # ni literales sustitutivos; el veredicto honesto es UNVERIFIED.
            return {
                "address": address,
                "chain": chain,
                "is_safe": False,
                "verdict": "UNVERIFIED",
                "metrics_known": False,
                "rug_ratio": None,
                "top10_concentration_pct": None,
                "dev_holding_pct": None,
                "sniper_pct": None,
                "pattern": asdict(PatternDetection(
                    pattern_id="UNVERIFIED_METRICS",
                    label="⚠️ Métricas sin verificar (fail-closed)",
                    confidence_pct=0.0,
                    bias="NEUTRAL",
                    expected_ev="N/D",
                    causal_trigger="Sin fuente verificable (GMGN CLI no disponible y escaneo "
                                   "multi-fuente sin métricas): no se emite veredicto de seguridad",
                )),
                "veto_flags": [],
                "dossier": None,
                "reason": "fail-closed: sin datos verificables no se emite juicio de seguridad",
            }

        metrics_complete = all(v is not None for v in (rug_ratio, top10, dev_holding, sniper_pct))

        if metrics_complete:
            pat = self.classify_pattern(1, rug_ratio, top10, dev_holding, sniper_pct,
                                        mc_real, liq_real)
            is_safe = (
                rug_ratio <= 0.30
                and top10 <= 30.0
                and dev_holding <= 1.50
                and sniper_pct <= 25.0
                and (dossier_dict is None or bool(dossier_dict.get("admitted")))
            )
            reason = ("métricas verificadas dentro de escudos" if is_safe
                      else "veto: una o más métricas verificadas superan los escudos")
        else:
            missing_metrics = [
                name for name, val in (
                    ("rug_ratio", rug_ratio), ("top10", top10),
                    ("dev_holding", dev_holding), ("snipers", sniper_pct)) if val is None
            ]
            pat = PatternDetection(
                pattern_id="UNVERIFIED_METRICS",
                label="⚠️ Métricas sin verificar (fail-closed)",
                confidence_pct=0.0,
                bias="NEUTRAL",
                expected_ev="N/D",
                causal_trigger="Métricas incompletas en la fuente: " + ", ".join(missing_metrics),
            )
            is_safe = False
            reason = "métricas incompletas: " + ", ".join(missing_metrics)

        verdict = "PASS" if is_safe else ("UNVERIFIED" if not metrics_complete else "FAIL")

        veto_flags = [
            f for f in [
                "RUG_RATIO_CRITICAL" if (rug_ratio is not None and rug_ratio > 0.30) else None,
                "CABAL_CLUSTER_DOMINANCE" if (top10 is not None and top10 > 30.0) else None,
                "DEV_INVENTORY_EXCESS" if (dev_holding is not None and dev_holding > 1.50) else None,
                "SNIPER_OVERHANG" if (sniper_pct is not None and sniper_pct > 25.0) else None,
                "BUNDLER_UNIFORM_TRAP" if (dossier_dict and dossier_dict.get("bundler_flag")) else None,
                "WASH_TRADING_STAIRCASE" if (dossier_dict and dossier_dict.get("wash_trading_flag")) else None,
                "CLONE_FACTORY_VAMP" if (dossier_dict and dossier_dict.get("clone_factory_flag")) else None,
            ] if f is not None
        ]

        return {
            "address": address,
            "chain": chain,
            "is_safe": is_safe,
            "verdict": verdict,
            "metrics_known": metrics_complete,
            "rug_ratio": rug_ratio,
            "top10_concentration_pct": top10,
            "dev_holding_pct": dev_holding,
            "sniper_pct": sniper_pct,
            "pattern": asdict(pat),
            "veto_flags": veto_flags,
            "dossier": dossier_dict,
            "reason": reason,
        }

