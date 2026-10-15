"""Etiquetas descriptivas on-chain, sin outcomes futuros ni decisiones automáticas."""
from __future__ import annotations
from typing import Any, Dict


def _f(x: Any, default: float = 0.0) -> float:
    try: return float(x)
    except (TypeError, ValueError): return default


def entry_risk_labels(features: Dict[str, Any]) -> Dict[str, Any]:
    """Calcula riesgos disponibles en la entrada; no mira PnL ni ventanas futuras."""
    f = features or {}
    top = _f(f.get("topHolderPct", f.get("top_holder_pct")))
    dev = _f(f.get("devBalancePct", f.get("dev_balance_pct")))
    liq = _f(f.get("firstLiquidity", f.get("first_liquidity", f.get("liquidity_sol"))))
    reserves = _f(f.get("realSolReserves", f.get("sol_reserves")))
    migration = _f(f.get("migrationProgress", f.get("curve_progress", f.get("ef_curve_progress"))))
    if 0 < migration <= 1: migration *= 100
    # C1: preferir las features causales ef_* (cortadas en entry_time); las
    # *_60s quedan solo como fallback del camino live (ventana rolling).
    buys = _f(f.get("ef_n_buys", f.get("n_buys_60s", f.get("buys_m5"))))
    sells = _f(f.get("ef_n_sells", f.get("n_sells_60s", f.get("sells_m5"))))
    volume = buys + sells
    entropy = _f(f.get("holder_entropy", f.get("holder_shannon_entropy")))
    if "holder_balances" in f and entropy <= 0.0:
        from .creator_registry import holder_shannon_entropy
        entropy = holder_shannon_entropy(f["holder_balances"])
    return {
        "risk_creator_concentration": round(min(1.0, max(0.0, (top - 10) / 40)), 6),
        "risk_dev_inventory": round(min(1.0, max(0.0, (dev - 2) / 20)), 6),
        "liquidity_depth_sol": liq,
        "reserves_sol": reserves,
        "migration_progress_pct": migration,
        "flow_buy_sell_ratio": round(buys / max(sells, 1.0), 6),
        "flow_activity": volume,
        "bundle_like_flow_flag": bool(volume >= 8 and buys / max(volume, 1.0) >= 0.9),
        "holder_shannon_entropy": round(entropy, 4),
        "risk_label_version": "p2-entry-v1",
    }
