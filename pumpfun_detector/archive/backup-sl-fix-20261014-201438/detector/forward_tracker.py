"""
PositionTracker - Seguimiento y Resolución de Posiciones en Tiempo Real (P1/Goal).

Orquesta el ciclo de vida de posiciones abiertas por ExecutionAdapter contra ticks
en vivo de PumpPortal / RPC, aplicando salidas multi-etapa dinámicas:
- Take-Profit (+30%)
- Breakeven Trigger (+15%) con Breakeven Lock (+5%)
- Trailing Stop Dinámico (10% tras consolidar)
- Stall Timeout (60s si precio <= entrada)
- Stop Loss con modelado de slippage realista (-33.49%)

Escribe atómicamente los resultados en LiveStore (live_outcomes) para auditoría forward.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from .costs import ExecutionCostModel, DEFAULT_COSTS
from .live_store import LiveStore


@dataclass
class TrackedPosition:
    """Estado en memoria de una posición abierta."""
    mint: str
    entry_price: float
    entry_ts: float
    position_size_sol: float
    branch: str
    decision_id: Optional[int] = None
    peak_price: float = field(init=False)
    be_activated: bool = False
    is_open: bool = True
    ticks_count: int = 0
    last_ts: float = field(init=False)
    last_price: float = field(init=False)

    def __post_init__(self):
        self.peak_price = self.entry_price
        self.last_ts = self.entry_ts
        self.last_price = self.entry_price


class PositionTracker:
    """Gestor de ciclo de vida de posiciones forward con salidas dinámicas multi-etapa."""

    def __init__(
        self,
        live_store: Optional[LiveStore] = None,
        cost_model: Optional[ExecutionCostModel] = None,
        take_profit_pct: float = 30.0,
        initial_sl_pct: float = 60.0,
        realistic_slippage_pct: float = 33.49,
        breakeven_trigger_pct: float = 15.0,
        breakeven_lock_pct: float = 5.0,
        trailing_stop_pct: float = 10.0,
        stall_window_s: float = 60.0,
        network_fee_sol: float = 0.002,
    ):
        self.live_store = live_store
        self.cost_model = cost_model or DEFAULT_COSTS
        self.take_profit_pct = take_profit_pct
        self.initial_sl_pct = initial_sl_pct
        self.realistic_slippage_pct = realistic_slippage_pct
        self.breakeven_trigger_pct = breakeven_trigger_pct
        self.breakeven_lock_pct = breakeven_lock_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.stall_window_s = stall_window_s
        self.network_fee_sol = network_fee_sol

        self._positions: Dict[str, TrackedPosition] = {}
        self._closed_records: List[Dict[str, Any]] = []

    def open_position(
        self,
        mint: str,
        entry_price: float,
        entry_ts: Optional[float] = None,
        position_size_sol: float = 0.5,
        branch: str = "MASTER_ALPHA_EXPANSION",
        decision_id: Optional[int] = None,
    ) -> TrackedPosition:
        """Abre una nueva posición en seguimiento."""
        ts = entry_ts if entry_ts is not None else time.time()
        pos = TrackedPosition(
            mint=mint,
            entry_price=entry_price,
            entry_ts=ts,
            position_size_sol=position_size_sol,
            branch=branch,
            decision_id=decision_id,
        )
        self._positions[mint] = pos
        return pos

    def get_position(self, mint: str) -> Optional[TrackedPosition]:
        """Obtiene la posición activa para un mint."""
        return self._positions.get(mint)

    def process_tick(self, mint: str, price: float, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Procesa una actualización de precio para un token en seguimiento.
        Retorna None si la posición sigue abierta, o dict con el outcome si cerró.
        """
        pos = self._positions.get(mint)
        if pos is None or not pos.is_open:
            return None

        now = ts if ts is not None else time.time()
        pos.last_ts = now
        pos.last_price = price
        pos.ticks_count += 1

        ep = pos.entry_price
        if ep <= 0.0:
            return None

        # Actualizar precio máximo alcanzado
        if price > pos.peak_price:
            pos.peak_price = price

        elapsed_s = now - pos.entry_ts
        nominal_pct = (price / ep - 1.0) * 100.0
        peak_pct = (pos.peak_price / ep - 1.0) * 100.0

        # Activar Breakeven Lock si se superó el trigger
        if peak_pct >= self.breakeven_trigger_pct:
            pos.be_activated = True

        exit_reason = None
        exit_type = "nominal"

        # 1. Take Profit
        if nominal_pct >= self.take_profit_pct:
            exit_reason = "TAKE_PROFIT"
            exit_type = "tp"

        # 2. Breakeven Lock
        elif pos.be_activated and nominal_pct <= self.breakeven_lock_pct:
            exit_reason = "BREAKEVEN_LOCK"
            exit_type = "breakeven"

        # 3. Trailing Stop
        elif peak_pct >= self.breakeven_trigger_pct and (pos.peak_price - price) / pos.peak_price * 100.0 >= self.trailing_stop_pct:
            exit_reason = "TRAILING_STOP"
            exit_type = "trailing"

        # 4. Stall Exit a los 60s
        elif elapsed_s >= self.stall_window_s and price <= ep:
            exit_reason = "STALL_EXIT"
            exit_type = "stall"

        # 5. Stop Loss nominal
        elif nominal_pct <= -self.initial_sl_pct:
            exit_reason = "STOP_LOSS"
            exit_type = "sl_panic"

        if exit_reason is None:
            return None

        # Cerrar posición y calcular costes reales
        pos.is_open = False
        outcome = self._compute_exit_outcome(pos, price, now, nominal_pct, exit_reason, exit_type)
        self._closed_records.append(outcome)

        # Persistir en LiveStore si está conectado
        if self.live_store is not None and pos.decision_id is not None:
            try:
                self.live_store.record_outcome(
                    decision_id=pos.decision_id,
                    pnl_pct=outcome["pnl_pct"],
                    pnl_sol=outcome["pnl_sol"],
                    exit_reason=exit_reason,
                    resolved_at=now,
                )
            except Exception:
                pass

        return outcome

    def _compute_exit_outcome(
        self,
        pos: TrackedPosition,
        exit_price: float,
        exit_ts: float,
        nominal_pct: float,
        exit_reason: str,
        exit_type: str,
    ) -> Dict[str, Any]:
        """Calcula el PnL neto con slippage y comisiones de red."""
        # Cálculo con modelo de costes unificado
        cost_res = self.cost_model.compute_exit_pnl(
            entry_sol=pos.position_size_sol,
            nominal_exit_pct=nominal_pct,
            exit_type=exit_type,
            liquidity_sol=30.0,
        )

        net_pnl_pct = cost_res["net_pnl_pct"]
        net_pnl_sol = cost_res["net_pnl_sol"]
        is_win = net_pnl_sol > 0.0

        return {
            "mint": pos.mint,
            "decision_id": pos.decision_id,
            "branch": pos.branch,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "entry_ts": pos.entry_ts,
            "exit_ts": exit_ts,
            "hold_seconds": exit_ts - pos.entry_ts,
            "nominal_pnl_pct": nominal_pct,
            "pnl_pct": net_pnl_pct,
            "pnl_sol": net_pnl_sol,
            "is_win": is_win,
            "exit_reason": exit_reason,
            "exit_type": exit_type,
            "fees_sol": cost_res["total_fees_sol"],
            "slippage_applied": cost_res.get("slippage_applied", 0.0),
        }

    def get_open_positions(self) -> List[TrackedPosition]:
        """Retorna todas las posiciones que siguen abiertas."""
        return [p for p in self._positions.values() if p.is_open]

    def get_stats(self) -> Dict[str, Any]:
        """Estadísticas acumuladas de las posiciones cerradas."""
        if not self._closed_records:
            return {
                "n": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "total_pnl_sol": 0.0, "profit_factor": 0.0,
                "open_positions": len(self.get_open_positions()),
            }

        wins = [r for r in self._closed_records if r["is_win"]]
        losses = [r for r in self._closed_records if not r["is_win"]]
        n = len(self._closed_records)
        wr = len(wins) / n

        tot_pnl_sol = sum(r["pnl_sol"] for r in self._closed_records)
        sum_g = sum(r["pnl_sol"] for r in wins)
        sum_l = sum(abs(r["pnl_sol"]) for r in losses)
        pf = sum_g / sum_l if sum_l > 0 else (10.0 if sum_g > 0 else 0.0)

        return {
            "n": n,
            "wins": len(wins),
            "losses": len(losses),
            "winrate": wr,
            "total_pnl_sol": tot_pnl_sol,
            "profit_factor": pf,
            "open_positions": len(self.get_open_positions()),
        }
