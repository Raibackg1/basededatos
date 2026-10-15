"""
ScalingGate — Kill-criteria formal para decidir si ESCALAR tamaño o ir a live.

Motivación (informe §8.4 y auditoría de la base canónica):
    La base real (5,021 posiciones paper) muestra PnL neto -79,771 USD con un
    profit factor de 0.2687. El error más caro NO es de arquitectura: es escalar
    (subir tamaño o pasar a live) una estrategia que todavía pierde en forward.
    Ningún módulo del proyecto lo impedía de forma explícita y fail-closed.

Qué hace:
    Toma las métricas NETAS forward (p.ej. `LiveStore.net_audit()`) y devuelve un
    veredicto binario duro: ¿se permite escalar?, con las razones concretas de
    bloqueo. Si falta cualquier métrica requerida, el veredicto es BLOCK (fail-closed):
    ante la duda, no se escala.

Principios:
    - No mira features ni señales; solo outcome NETO ya resuelto (post-costes).
    - No promete rentabilidad; implementa las "kill criteria" del informe:
      mínimo de trades forward, winrate, profit factor, PnL total positivo,
      y opcionalmente fill-rate, drawdown y slippage realizado vs modelado.
    - Determinista y puro: mismas métricas -> mismo veredicto. Sin I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

#: Claves mínimas que `net_audit()` debe aportar para poder evaluar el escalado.
_REQUIRED_AUDIT_KEYS: Tuple[str, ...] = (
    "resolved", "winrate", "profit_factor", "total_pnl_sol",
)


@dataclass(frozen=True)
class ScalingVerdict:
    """Resultado de evaluar las kill-criteria de escalado."""
    allowed: bool
    blocked_reasons: Tuple[str, ...] = ()
    metrics: Dict[str, Any] = field(default_factory=dict)
    criteria: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blocked_reasons": list(self.blocked_reasons),
            "metrics": dict(self.metrics),
            "criteria": dict(self.criteria),
        }


class ScalingGate:
    """
    Gate fail-closed que aplica las kill-criteria antes de escalar tamaño/live.

    Umbrales por defecto alineados con el informe §8.4:
        - min_forward_trades = 100   (muestra mínima para que las métricas signifiquen algo)
        - min_winrate        = 0.65
        - min_profit_factor  = 1.30
        - require_positive_pnl = True

    Opcionales (None = no se evalúan):
        - min_fill_rate
        - max_drawdown_pct (valor absoluto en %)
        - max_slippage_ratio (slippage_real_p90 / slippage_model_p90)

    Uso:
        gate = ScalingGate()
        audit = store.net_audit(origin="live", admitted_only=True)
        verdict = gate.evaluate(audit, fill_rate=0.83, drawdown_pct=12.0)
        if not verdict.allowed:
            ...  # no escalar; registrar razones
    """

    def __init__(
        self,
        min_forward_trades: int = 100,
        min_winrate: float = 0.65,
        min_profit_factor: float = 1.30,
        require_positive_pnl: bool = True,
        min_fill_rate: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        max_slippage_ratio: Optional[float] = None,
    ) -> None:
        self.min_forward_trades = int(min_forward_trades)
        self.min_winrate = float(min_winrate)
        self.min_profit_factor = float(min_profit_factor)
        self.require_positive_pnl = bool(require_positive_pnl)
        self.min_fill_rate = None if min_fill_rate is None else float(min_fill_rate)
        self.max_drawdown_pct = None if max_drawdown_pct is None else float(max_drawdown_pct)
        self.max_slippage_ratio = None if max_slippage_ratio is None else float(max_slippage_ratio)

    def criteria(self) -> Dict[str, Any]:
        """Umbral efectivo configurado (para auditoría auditable)."""
        return {
            "min_forward_trades": self.min_forward_trades,
            "min_winrate": self.min_winrate,
            "min_profit_factor": self.min_profit_factor,
            "require_positive_pnl": self.require_positive_pnl,
            "min_fill_rate": self.min_fill_rate,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_slippage_ratio": self.max_slippage_ratio,
        }

    def evaluate(
        self,
        audit: Optional[Mapping[str, Any]],
        fill_rate: Optional[float] = None,
        drawdown_pct: Optional[float] = None,
        slippage_real_p90: Optional[float] = None,
        slippage_model_p90: Optional[float] = None,
    ) -> ScalingVerdict:
        """
        Evalúa las kill-criteria sobre métricas NETAS forward ya resueltas.

        Devuelve ScalingVerdict(allowed=False, ...) ante cualquier señal de alarma
        o dato faltante (fail-closed). Un profit_factor `inf` (cero pérdidas) se
        trata como válido únicamente si además hay PnL positivo y muestra suficiente.
        """
        reasons: list[str] = []
        metrics: Dict[str, Any] = {}

        # ---- Fail-closed: métricas requeridas ausentes ----
        if not isinstance(audit, Mapping):
            return ScalingVerdict(
                allowed=False,
                blocked_reasons=("MISSING_AUDIT_METRICS",),
                metrics={},
                criteria=self.criteria(),
            )
        for key in _REQUIRED_AUDIT_KEYS:
            if key not in audit or audit[key] is None:
                return ScalingVerdict(
                    allowed=False,
                    blocked_reasons=(f"MISSING_AUDIT_METRICS:{key}",),
                    metrics=dict(audit),
                    criteria=self.criteria(),
                )

        resolved = _as_int(audit.get("resolved"))
        winrate = _as_float(audit.get("winrate"))
        pf = _as_float(audit.get("profit_factor"))
        total_pnl = _as_float(audit.get("total_pnl_sol"))
        metrics.update({
            "resolved": resolved,
            "winrate": winrate,
            "profit_factor": pf,
            "total_pnl_sol": total_pnl,
        })

        # ---- Criterios duros ----
        if resolved is None or resolved < self.min_forward_trades:
            reasons.append(f"INSUFFICIENT_FORWARD_TRADES:{resolved}<{self.min_forward_trades}")
        # winrate/pnl infinitos son absurdos (no métricas reales); un gate
        # fail-closed los bloquea. profit_factor=inf SI es legitimo (cero perdidas).
        if winrate is None or not _is_finite(winrate) or winrate < self.min_winrate:
            reasons.append(f"WINRATE_BELOW_MIN:{winrate}<{self.min_winrate}")
        if pf is None or pf < self.min_profit_factor:
            reasons.append(f"PROFIT_FACTOR_BELOW_MIN:{pf}<{self.min_profit_factor}")
        if self.require_positive_pnl and (
            total_pnl is None or not _is_finite(total_pnl) or total_pnl <= 0.0
        ):
            reasons.append(f"NON_POSITIVE_NET_PNL:{total_pnl}")

        # ---- Criterios opcionales (solo si se aportan) ----
        if self.min_fill_rate is not None:
            fr = _as_float(fill_rate)
            metrics["fill_rate"] = fr
            if fr is None or fr < self.min_fill_rate:
                reasons.append(f"FILL_RATE_BELOW_MIN:{fr}<{self.min_fill_rate}")

        if self.max_drawdown_pct is not None:
            dd = _as_float(drawdown_pct)
            metrics["drawdown_pct"] = dd
            if dd is None or abs(dd) > self.max_drawdown_pct:
                reasons.append(f"DRAWDOWN_EXCEEDS_MAX:{dd}>{self.max_drawdown_pct}")

        if self.max_slippage_ratio is not None:
            real = _as_float(slippage_real_p90)
            model = _as_float(slippage_model_p90)
            metrics["slippage_real_p90"] = real
            metrics["slippage_model_p90"] = model
            if real is None or model is None or model <= 0.0:
                reasons.append("SLIPPAGE_METRICS_UNAVAILABLE")
            else:
                ratio = real / model
                metrics["slippage_ratio"] = ratio
                if ratio > self.max_slippage_ratio:
                    reasons.append(
                        f"SLIPPAGE_RATIO_ABOVE_MAX:{ratio:.3f}>{self.max_slippage_ratio}"
                    )

        return ScalingVerdict(
            allowed=not reasons,
            blocked_reasons=tuple(reasons),
            metrics=metrics,
            criteria=self.criteria(),
        )


def _as_float(v: Any) -> Optional[float]:
    """Convierte a float; preserva inf/-inf; None si no es convertible."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _is_finite(v: Optional[float]) -> bool:
    """True si v es un float finito (no inf/-inf/None)."""
    return v is not None and v not in (float("inf"), float("-inf")) and v == v


def _as_int(v: Any) -> Optional[int]:
    f = _as_float(v)
    if f is None or f != f or f in (float("inf"), float("-inf")):
        return None
    return int(f)

