"""Gate standalone de exportación: solo reglas con evidencia OOS válida salen.

Por que existe
--------------
`export_production_strategy` ya filtra internamente, pero el filtro está
escondido en un loop de ~30 líneas y no es testeable aisladamente. Peor: un
consumidor externo (otro repo, un bot) podría importar las reglas sin pasar
por esa función y quedar con reglas sin OOS en producción.

Que hace
--------
`oos_export_gate(rules)` evalúa CADA regla contra los criterios duros:

1. ``oos_pass`` truthy
2. ``oos_n`` >= min_oos_n (default 10)
3. ``oos_lift`` > min_oos_lift (default 1.0)
4. ``bounds`` presente y no vacío
5. ``data_origin`` en {"historical", "forward_live"}

Devuelve un `ExportGateResult` con:
- ``allowed``: True solo si al menos una regla pasa
- ``passed_rules``: lista de reglas que pasan
- ``rejected``: lista de (rule, reason) para las que no pasan
- ``reasons``: resumen de motivos de rechazo (deduplicados)

Fail-closed: si ``rules`` es vacío o None, ``allowed=False``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


_VALID_ORIGINS = {"historical", "forward_live"}


@dataclass(frozen=True)
class ExportGateResult:
    allowed: bool
    passed_rules: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Tuple[Dict[str, Any], str]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "n_passed": len(self.passed_rules),
            "n_rejected": len(self.rejected),
            "reasons": list(self.reasons),
        }


def _check_rule(
    rule: Dict[str, Any],
    min_oos_n: int,
    min_oos_lift: float,
) -> Optional[str]:
    """Devuelve None si la regla pasa, o el motivo de rechazo."""
    if not isinstance(rule, dict):
        return "no_es_dict"
    if not bool(rule.get("oos_pass", False)):
        return "sin_oos_pass"
    oos_n = int(rule.get("oos_n", 0) or 0)
    if oos_n < min_oos_n:
        return f"oos_n<{min_oos_n}"
    oos_lift = float(rule.get("oos_lift", 0.0) or 0.0)
    if oos_lift <= min_oos_lift:
        return f"oos_lift<={min_oos_lift}"
    bounds = rule.get("bounds")
    if not bounds:
        return "sin_bounds"
    origin = rule.get("data_origin")
    if origin is not None and origin not in _VALID_ORIGINS:
        return f"data_origin_invalida:{origin}"
    return None


def oos_export_gate(
    rules: Optional[List[Dict[str, Any]]],
    min_oos_n: int = 10,
    min_oos_lift: float = 1.0,
) -> ExportGateResult:
    """Evalúa una lista de reglas contra los criterios OOS de exportación.

    Fail-closed: sin reglas o lista vacía -> allowed=False.
    """
    if not rules:
        return ExportGateResult(
            allowed=False, passed_rules=[], rejected=[],
            reasons=["sin_reglas"])

    passed: List[Dict[str, Any]] = []
    rejected: List[Tuple[Dict[str, Any], str]] = []
    reasons: List[str] = []

    for r in rules:
        reason = _check_rule(r, min_oos_n, min_oos_lift)
        if reason is None:
            passed.append(r)
        else:
            rejected.append((r, reason))
            if reason not in reasons:
                reasons.append(reason)

    return ExportGateResult(
        allowed=len(passed) > 0,
        passed_rules=passed,
        rejected=rejected,
        reasons=reasons,
    )
