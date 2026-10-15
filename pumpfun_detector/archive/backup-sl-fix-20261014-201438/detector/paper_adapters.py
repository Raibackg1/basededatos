"""Adaptadores de paper/replay: deliberadamente no existe submitter real."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List
from .execution import OrderResult

@dataclass
class ReplayOrderSubmitter:
    """Simula fills locales y registra trazas; no firma ni transmite transacciones."""
    fills: List[Dict[str, Any]] = field(default_factory=list)
    live_enabled: bool = False

    def submit_order(self, quote: Any) -> OrderResult:
        if self.live_enabled: raise RuntimeError("P2 fail-closed: live submitter no permitido")
        in_sol = float(getattr(quote, "in_sol", 0.0) or 0.0)
        out_tokens = float(getattr(quote, "out_tokens", 0.0) or 0.0)
        rec = {"mode": "paper", "in_sol": in_sol, "out_tokens": out_tokens, "route": getattr(quote, "route", None)}
        self.fills.append(rec)
        return OrderResult(True, f"PAPER-{len(self.fills):06d}", in_sol, out_tokens, float(getattr(quote, "fees_sol", 0.0) or 0.0))

@dataclass
class PaperAdapter:
    """Orquesta candidates sin permitir acceso a claves o red de ejecución."""
    submitter: ReplayOrderSubmitter = field(default_factory=ReplayOrderSubmitter)
    results: List[Dict[str, Any]] = field(default_factory=list)
    def record(self, result: Dict[str, Any]) -> Dict[str, Any]:
        safe = dict(result); safe["mode"] = "paper"; safe["live_enabled"] = False
        self.results.append(safe); return safe
