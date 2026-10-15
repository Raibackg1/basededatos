"""
PaperBroker - Puente que cierra el loop de "ganancia real medible".

Conecta la decisión de la Señal Maestra con la persistencia y el cost model:

    MasterSnipeSignal.evaluate(feats)
        -> si admitida: abre posición paper (persistida en LiveStore)
        -> el precio toca TP / SL / timeout
        -> cierra con PnL NETO real vía ExecutionCostModel.compute_exit_pnl
        -> persiste el outcome (record_outcome)
        -> net_audit()  => ganancia neta medible, aislada por procedencia.

Diseño defensivo (duck-typing): se adapta en tiempo de ejecución a la interfaz
real de `cost_model` y `live_store` sin acoplarse a firmas frágiles. No toca
código existente: es un módulo puente nuevo y autocontenido.

Honestidad: es paper-trading simulado. El PnL usa el cost model real (fees,
price impact, slippage), pero los precios son los que se le inyectan. Mide la
ecuabilidad del motor de ejecución, no el mercado en vivo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .momentum_exit import EXIT_MOMENTUM_STALL, MomentumStallExit

# Tipos de salida aceptados por ExecutionCostModel.compute_exit_pnl.
_EXIT_TP = "tp"
_EXIT_SL = "sl_panic"
_EXIT_STALL = "stall"


@dataclass
class PaperPosition:
    """Posición paper abierta, vinculada a su decisión persistida."""
    mint: str
    entry_sol: float
    entry_ts: float
    entry_price: float
    decision_id: Optional[int]
    branch: str
    confidence: float
    liquidity_sol: float
    take_profit_pct: float
    stop_loss_pct: float
    timeout_s: float
    features: Dict[str, Any] = field(default_factory=dict)
    peak_price: float = 0.0
    trough_price: float = 0.0
    ticks_seen: int = 0


@dataclass
class ClosedTrade:
    """Resultado del cierre de una posición con PnL neto real."""
    mint: str
    decision_id: Optional[int]
    exit_type: str
    nominal_exit_pct: float
    net_pnl_pct: float
    net_pnl_sol: float
    hold_s: float
    branch: str

class PaperBroker:
    """
    Motor de paper-trading que cierra posiciones contra el cost model real y
    persiste cada decisión + outcome en LiveStore, habilitando net_audit().

    Opcionalmente consume ``store.branch_exit_policy()``: si una rama tiene
    >= min_labeled trades etiquetados con MFE/MAE, las posiciones de esa rama
    usan TP/SL adaptativos (capture_frac * mediana(MFE), -|mediana(MAE)|) en
    vez de los umbrales estáticos del constructor. Se refresca automáticamente
    cada ``policy_refresh_interval`` cierres para incorporar nueva evidencia.

    Uso:
        broker = PaperBroker(live_store=store, cost_model=costs)
        did = broker.on_signal(feats, decision, ts)        # abre si admitida
        broker.on_price(mint, price, ts)                   # cierra en TP/SL/timeout
        audit = broker.audit(origin="live")                # ganancia neta medible
    """

    def __init__(
        self,
        live_store: Any,
        cost_model: Any,
        entry_sol: float = 1.0,
        take_profit_pct: float = 0.30,
        stop_loss_pct: float = -0.35,
        timeout_s: float = 300.0,
        default_liquidity_sol: float = 20.0,
        max_open: int = 50,
        slippage_model: Any = None,
        use_branch_exit_policy: bool = True,
        policy_min_labeled: int = 5,
        policy_capture_frac: float = 0.5,
        policy_refresh_interval: int = 20,
        momentum_exit: Optional[MomentumStallExit] = None,
    ) -> None:
        self.store = live_store
        self.costs = cost_model
        self.entry_sol = float(entry_sol)
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.timeout_s = float(timeout_s)
        self.default_liquidity_sol = float(default_liquidity_sol)
        self.max_open = int(max_open)
        self.slippage_model = slippage_model
        self.use_branch_exit_policy = bool(use_branch_exit_policy)
        self.policy_min_labeled = int(policy_min_labeled)
        self.policy_capture_frac = float(policy_capture_frac)
        self.policy_refresh_interval = int(policy_refresh_interval)
        # Guarda anti-TIMEOUT (opt-in): None por defecto para no alterar producción.
        self.momentum_exit = momentum_exit
        self._branch_policies: Dict[str, Dict[str, Any]] = {}
        self._closes_since_policy_refresh = 0
        self._open: Dict[str, PaperPosition] = {}
        self._closed: List[ClosedTrade] = []

    def _refresh_branch_policies(self) -> None:
        """Carga/actualiza las policies de salida por rama desde MFE/MAE reales."""
        if not self.use_branch_exit_policy or self.store is None \
                or not hasattr(self.store, "branch_exit_policy"):
            return
        try:
            rows = self.store.branch_exit_policy(
                origin="live", admitted_only=True,
                capture_frac=self.policy_capture_frac,
                min_labeled=self.policy_min_labeled)
        except Exception:
            return
        self._branch_policies = {
            r["branch"]: r for r in rows if r.get("sufficient")
        }
        self._closes_since_policy_refresh = 0

    def _exit_policy_for_branch(self, branch: str) -> Optional[Dict[str, Any]]:
        """Devuelve la policy adaptativa de la rama si existe y es suficiente."""
        if not self.use_branch_exit_policy or not branch:
            return None
        if not self._branch_policies and self.store is not None:
            self._refresh_branch_policies()
        return self._branch_policies.get(branch)

    def on_signal(
        self,
        feats: Dict[str, Any],
        decision: Any,
        ts: Optional[float] = None,
    ) -> Optional[int]:
        """Si la decisión es admitida, abre posición paper y la persiste.
        Devuelve el decision_id o None."""
        ts = time.time() if ts is None else float(ts)
        if not bool(getattr(decision, "admitted", False)):
            return None

        mint = str(feats.get("mint") or feats.get("token_id") or feats.get("symbol") or "")
        if not mint or mint in self._open or len(self._open) >= self.max_open:
            return None

        entry_price = self._num(
            feats.get("entry_price") or feats.get("price") or feats.get("p_first"), 0.0)
        liquidity = self._num(
            feats.get("realSolReserves") or feats.get("real_sol_reserves")
            or feats.get("liquidity_sol"), self.default_liquidity_sol)

        # TP/SL adaptativo por rama si hay evidencia suficiente (MFE/MAE reales).
        tp, sl = self.take_profit_pct, self.stop_loss_pct
        branch = str(getattr(decision, "branch", ""))
        pol = self._exit_policy_for_branch(branch)
        if pol is not None:
            tp = float(pol.get("suggested_take_profit_pct", tp))
            sl = float(pol.get("suggested_stop_pct", sl))

        decision_id = None
        if self.store is not None and hasattr(self.store, "record_decision"):
            try:
                decision_id = self.store.record_decision(mint, feats, decision, ts)
            except TypeError:
                decision_id = self.store.record_decision(mint, feats, decision)

        self._open[mint] = PaperPosition(
            mint=mint, entry_sol=self.entry_sol, entry_ts=ts,
            entry_price=entry_price if entry_price > 0 else 0.0,
            decision_id=decision_id, branch=branch,
            confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            liquidity_sol=liquidity, take_profit_pct=tp,
            stop_loss_pct=sl, timeout_s=self.timeout_s,
            features=dict(feats), peak_price=entry_price if entry_price > 0 else 0.0,
            trough_price=entry_price if entry_price > 0 else 0.0)
        return decision_id


    def _num(self, val: Any, default: float) -> float:
        """Convierte a float finito o devuelve el default (blindaje ante NaN/None)."""
        try:
            f = float(val)
        except (TypeError, ValueError):
            return default
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f

    def on_price(
        self,
        mint: str,
        price: float,
        ts: Optional[float] = None,
    ) -> Optional[ClosedTrade]:
        """Alimenta un precio. Cierra la posición si toca TP / SL / timeout.

        Devuelve el ClosedTrade si cerró, o None si sigue abierta.
        """
        ts = time.time() if ts is None else float(ts)
        mint = str(mint)
        pos = self._open.get(mint)
        if pos is None or pos.entry_price <= 0:
            return None

        price = self._num(price, pos.entry_price)
        if price > pos.peak_price:
            pos.peak_price = price
        if pos.trough_price <= 0.0 or price < pos.trough_price:
            pos.trough_price = price

        pct = (price - pos.entry_price) / pos.entry_price * 100.0  # compute_exit_pnl espera %
        pos.ticks_seen += 1
        # Guarda anti-TIMEOUT (opt-in): cierra pronto posiciones SIN momentum.
        # Se evalúa ANTES de TP/SL/timeout porque ataca el sangrado lento de
        # trades que nunca superan la entrada (mediana de drawup = 0% en la base real).
        if self.momentum_exit is not None:
            _md = self.momentum_exit.evaluate(
                entry_price=pos.entry_price,
                peak_price=pos.peak_price,
                held_s=ts - pos.entry_ts,
                ticks_seen=pos.ticks_seen,
            )
            if _md.exit_now:
                return self._close(pos, EXIT_MOMENTUM_STALL, pct, ts)

        # Orden de prioridad: TP, SL, timeout (salida por estancamiento).
        tp_thr = self._threshold_to_pct(pos.take_profit_pct)
        sl_thr = self._threshold_to_pct(pos.stop_loss_pct)
        # Orden de prioridad: TP, SL, timeout.
        if pct >= tp_thr:
            return self._close(pos, _EXIT_TP, pct, ts)
        if pct <= sl_thr:
            return self._close(pos, _EXIT_SL, pct, ts)
        if (ts - pos.entry_ts) >= pos.timeout_s:
            return self._close(pos, _EXIT_STALL, pct, ts)
        return None

    @staticmethod
    def _threshold_to_pct(t: float) -> float:
        """Normaliza un umbral a unidades de PORCENTAJE.

        Si |t| <= 1 se interpreta como fraccion (0.30 -> 30.0 %); si ya es grande
        se asume que viene en % y se deja intacto. Blindaje ante doble escala:
        sin esto, comparar pct(en %) contra 0.30 dispararia el TP con +0.3 % y
        arruinaria la rentabilidad real.
        """
        return t * 100.0 if abs(t) <= 1.0 else t


    def _close(
        self,
        pos: PaperPosition,
        exit_type: str,
        nominal_pct: float,
        ts: float,
    ) -> ClosedTrade:
        """Cierra contra el cost model real, persiste el outcome y registra."""
        net_pct, net_sol = self._net_exit(pos, exit_type, nominal_pct)

        # MFE/MAE reales de la trayectoria observada (para branch_exit_policy)
        mfe_pct = mae_pct = None
        if pos.entry_price > 0:
            if pos.peak_price > 0:
                mfe_pct = (pos.peak_price - pos.entry_price) / pos.entry_price * 100.0
            if pos.trough_price > 0:
                mae_pct = (pos.trough_price - pos.entry_price) / pos.entry_price * 100.0

        # Persistir el outcome (duck-typed: con o sin MFE/MAE / resolved_at).
        if pos.decision_id is not None and self.store is not None \
                and hasattr(self.store, "record_outcome"):
            try:
                self.store.record_outcome(
                    pos.decision_id, net_pct, net_sol, exit_reason=exit_type,
                    resolved_at=ts, mfe_pct=mfe_pct, mae_pct=mae_pct)
            except TypeError:
                try:
                    self.store.record_outcome(
                        pos.decision_id, net_pct, net_sol, exit_type, ts)
                except TypeError:
                    self.store.record_outcome(
                        pos.decision_id, net_pct, net_sol, exit_type)

        self._open.pop(pos.mint, None)
        trade = ClosedTrade(
            mint=pos.mint, decision_id=pos.decision_id, exit_type=exit_type,
            nominal_exit_pct=nominal_pct, net_pnl_pct=net_pct,
            net_pnl_sol=net_sol, hold_s=max(0.0, ts - pos.entry_ts),
            branch=pos.branch,
        )
        self._closed.append(trade)

        # Refresca policies de salida por rama cada N cierres para incorporar
        # nueva evidencia (MFE/MAE recientes) sin consultar en cada tick.
        if self.use_branch_exit_policy:
            self._closes_since_policy_refresh += 1
            if self._closes_since_policy_refresh >= self.policy_refresh_interval:
                self._refresh_branch_policies()

        return trade

    def _net_exit(
        self,
        pos: PaperPosition,
        exit_type: str,
        nominal_pct: float,
    ) -> tuple[float, float]:
        """PnL neto real vía cost_model.compute_exit_pnl, con fallback nominal."""
        # momentum_stall se costea como "stall" (mismo haircut empírico de
        # estancamiento), pero conserva su etiqueta propia en el registro.
        cost_exit_type = _EXIT_STALL if exit_type == EXIT_MOMENTUM_STALL else exit_type
        if self.costs is not None and hasattr(self.costs, "compute_exit_pnl"):
            if self.slippage_model is not None:
                try:
                    res = self.costs.compute_exit_pnl(
                        entry_sol=pos.entry_sol,
                        nominal_exit_pct=nominal_pct,
                        exit_type=cost_exit_type,
                        liquidity_sol=pos.liquidity_sol,
                        slippage_model=self.slippage_model,
                    )
                except TypeError:
                    res = None  # cost_model sin soporte de kwarg -> camino legacy
                if isinstance(res, dict):
                    net_pct = self._num(res.get("net_pnl_pct"), nominal_pct)
                    net_sol = self._num(
                        res.get("net_pnl_sol"), nominal_pct * pos.entry_sol)
                    return net_pct, net_sol
            try:
                res = self.costs.compute_exit_pnl(
                    entry_sol=pos.entry_sol,
                    nominal_exit_pct=nominal_pct,
                    exit_type=cost_exit_type,
                    liquidity_sol=pos.liquidity_sol,
                )
            except TypeError:
                res = self.costs.compute_exit_pnl(
                    pos.entry_sol, nominal_pct, cost_exit_type, pos.liquidity_sol)
            if isinstance(res, dict):
                net_pct = self._num(res.get("net_pnl_pct"), nominal_pct)
                net_sol = self._num(
                    res.get("net_pnl_sol"), nominal_pct * pos.entry_sol)
                return net_pct, net_sol
        # Sin cost model: PnL nominal (documentado, no incluye fricción).
        return nominal_pct, nominal_pct * pos.entry_sol

    def audit(self, origin: str = "live", admitted_only: bool = True) -> Dict[str, Any]:
        """Ganancia neta medible desde el store persistido (aislada por origen)."""
        if self.store is not None and hasattr(self.store, "net_audit"):
            try:
                return self.store.net_audit(origin=origin, admitted_only=admitted_only)
            except TypeError:
                return self.store.net_audit(origin, admitted_only)
        # Sin store: resumen en memoria (solo de esta sesión).
        n = len(self._closed)
        if n == 0:
            return {"origin": origin, "n_closed": 0, "net_pnl_sol": 0.0,
                    "net_pnl_pct": 0.0, "winrate": 0.0}
        net_sol = sum(t.net_pnl_sol for t in self._closed)
        wins = sum(1 for t in self._closed if t.net_pnl_sol > 0)
        return {
            "origin": origin, "n_closed": n,
            "net_pnl_sol": net_sol,
            "net_pnl_pct": net_sol / max(n * self.entry_sol, 1e-9),
            "winrate": wins / n,
            "note": "resumen en memoria (sin LiveStore persistente)",
        }

    @property
    def open_count(self) -> int:
        return len(self._open)

    @property
    def closed_trades(self) -> List[ClosedTrade]:
        return list(self._closed)

