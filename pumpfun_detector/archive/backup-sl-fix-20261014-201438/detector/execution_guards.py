"""Gates fail-closed para ejecución y control de riesgo."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    metrics: Dict[str, Any] = None


class QuoteGate:
    @staticmethod
    def evaluate(quote: Dict[str, Any], now_ms: float, max_age_ms: float = 3000.0,
                 max_impact_pct: float = 5.0, min_depth_sol: float = 0.0,
                 allowed_routes: Iterable[str] = ()) -> GateDecision:
        q = quote or {}
        age = float(now_ms) - float(q.get("quoted_at_ms", now_ms))
        impact = float(q.get("price_impact_pct", q.get("priceImpactPct", 0.0)) or 0.0)
        depth = float(q.get("liquidity_sol", q.get("depth_sol", 0.0)) or 0.0)
        route = str(q.get("route", ""))
        metrics = {"quote_age_ms": age, "price_impact_pct": impact, "depth_sol": depth, "route": route}
        if not q.get("is_executable", True):
            return GateDecision(False, "QUOTE_NOT_EXECUTABLE", metrics)
        if age > max_age_ms:
            return GateDecision(False, "QUOTE_STALE", metrics)
        if impact > max_impact_pct:
            return GateDecision(False, "PRICE_IMPACT_TOO_HIGH", metrics)
        if depth < min_depth_sol:
            return GateDecision(False, "INSUFFICIENT_DEPTH", metrics)
        allowed = set(allowed_routes)
        if allowed and route not in allowed:
            return GateDecision(False, "ROUTE_NOT_ALLOWED", metrics)
        return GateDecision(True, "OK", metrics)


class LatencyBudget:
    @staticmethod
    def evaluate(stages: Dict[str, float], max_total_ms: float = 1500.0) -> GateDecision:
        vals = {str(k): float(v) for k, v in (stages or {}).items() if v is not None}
        total = sum(vals.values())
        metrics = {"total_ms": total, "stages": vals}
        if not vals or any(v < 0 for v in vals.values()):
            return GateDecision(False, "INVALID_LATENCY_TRACE", metrics)
        if total > max_total_ms:
            return GateDecision(False, "LATENCY_BUDGET_EXCEEDED", metrics)
        return GateDecision(True, "OK", metrics)


def safe_position_size(portfolio_sol: float, expected_net_ev: float, liquidity_sol: float,
                       drawdown_pct: float, max_position_sol: float = 0.1,
                       risk_fraction: float = 0.10, max_drawdown_pct: float = 10.0,
                       liquidity_fraction: float = 0.05) -> float:
    vals = [portfolio_sol, expected_net_ev, liquidity_sol, drawdown_pct]
    if any(v is None for v in vals) or portfolio_sol <= 0 or expected_net_ev <= 0:
        return 0.0
    if drawdown_pct >= max_drawdown_pct or liquidity_sol <= 0:
        return 0.0
    target = portfolio_sol * max(0.0, min(risk_fraction, 1.0))
    cap_by_liquidity = liquidity_sol * max(0.0, liquidity_fraction)
    return round(max(0.0, min(max_position_sol, target, cap_by_liquidity)), 9)
