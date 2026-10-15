"""
LiquidityGuard — Veto de entrada por COSTO de slippage de salida.

Motivación (auditoría honesta sobre 1,775 trades con liquidez conocida):
    El cost model real calcula en salida de pánico:
        realized_loss_frac = min(0.98, |nominal_sl|/100 + slippage)
    y `slippage` escala inversamente con la liquidez. Por eso una SL nominal de
    -30% se materializa en ~-55% real: la brecha ES slippage.

    Un veto de entrada que descarta tokens con liquidez tan baja que el slippage
    de salida es destructivo reduce pérdidas. IMPORTANTE (medido, no asumido):
    sobre los datos reales el efecto aislado (solo liquidez conocida) es
    +4,265.59 USD de PnL y +0.73 pp de winrate (ver
    outputs/fix-stoploss-liquidity-guard.md), y la señal NO es monótona (tokens
    de liquidez alta TAMBIÉN pierden). Ver scratch/measure_sl_guard.py. Esto no
    arregla por sí solo el -70k del SL; es un amortiguador de costo, no una cura.

Qué hace:
    Dada la liquidez al entrar (realSolReserves), decide si un trade es admisible
    según (a) un mínimo de liquidez y (b) el slippage de salida modelado. Es
    DETERMINISTA y conoce solo la liquidez de entrada (sin lookahead): en producción
    realSolReserves se obtiene del token en el momento de decidir.

Integración:
    Se usa como veto ANTES de abrir posición:
        guard = LiquidityGuard(min_liquidity_sol=15.0)
        v = guard.evaluate(real_sol_reserves=feats.get("realSolReserves"))
        if not v.allowed: ...  # no entrar
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LiquidityVerdict:
    """Resultado del veto de liquidez/slippage para una entrada."""
    allowed: bool
    reason: str
    liquidity_sol: Optional[float]
    modeled_exit_slippage_pct: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "liquidity_sol": self.liquidity_sol,
            "modeled_exit_slippage_pct": self.modeled_exit_slippage_pct,
        }


class LiquidityGuard:
    """
    Veto fail-closed de entrada por liquidez y slippage de salida modelado.

    Umbrales por defecto (derivados del barrido honesto en datos reales):
        - min_liquidity_sol = 15.0   (por debajo de 15 SOL el PF histórico es <0.4)
        - max_exit_slippage_pct = 35.0  (slippage de cola q90 destructivo si es mayor)

    Modelo de slippage: aproxima el slippage de salida de pánico con una curva
    decreciente en la liquidez (menor liquidez -> mayor slippage). No es un fits
    fino: es una cota conservadora coherente con `ConditionalSlippageModel`
    (priors ~-33% en baja liquidez). Se puede sustituir por un modelo empírico.

    Fail-closed: liquidez desconocida (None o <=0) -> RECHAZA (no se entra sin
    saber el costo de salida). Esto es intencional: en producción no se debe
    operar un token sin conocer sus reservas.
    """

    def __init__(
        self,
        min_liquidity_sol: float = 15.0,
        max_exit_slippage_pct: float = 35.0,
    ) -> None:
        self.min_liquidity_sol = max(0.0, float(min_liquidity_sol))
        self.max_exit_slippage_pct = max(0.0, float(max_exit_slippage_pct))

    def model_exit_slippage_pct(self, liquidity_sol: float) -> float:
        """
        Slippage de salida de pánico modelado (%), decreciente en la liquidez.

        Curva conservadora: se aproxima a ~-33% en liquidez muy baja y decae
        asintóticamente hacia ~-3% en liquidez muy alta. Determinista, sin I/O.
        """
        liq = max(1e-6, float(liquidity_sol))
        # slippage = 3 + 30 * (L0/(L0+liq)) con L0=12 -> ~33% en liq~0, ~3% en liq->inf.
        return 3.0 + 30.0 * (12.0 / (12.0 + liq))

    def evaluate(self, real_sol_reserves: Optional[float]) -> LiquidityVerdict:
        """Evalúa si se permite entrar dado el liquidity SOL de entrada."""
        liq = self._to_float(real_sol_reserves)
        if liq is None or liq <= 0.0:
            return LiquidityVerdict(
                allowed=False, reason="LIQUIDITY_UNKNOWN",
                liquidity_sol=liq, modeled_exit_slippage_pct=None,
            )
        slip = self.model_exit_slippage_pct(liq)
        if liq < self.min_liquidity_sol:
            return LiquidityVerdict(
                allowed=False, reason=f"LIQUIDITY_BELOW_MIN:{liq:.2f}<{self.min_liquidity_sol}",
                liquidity_sol=liq, modeled_exit_slippage_pct=slip,
            )
        if slip > self.max_exit_slippage_pct:
            return LiquidityVerdict(
                allowed=False, reason=f"EXIT_SLIPPAGE_TOO_HIGH:{slip:.1f}%>{self.max_exit_slippage_pct}%",
                liquidity_sol=liq, modeled_exit_slippage_pct=slip,
            )
        return LiquidityVerdict(
            allowed=True, reason="OK",
            liquidity_sol=liq, modeled_exit_slippage_pct=slip,
        )

    def config(self) -> Dict[str, Any]:
        return {
            "min_liquidity_sol": self.min_liquidity_sol,
            "max_exit_slippage_pct": self.max_exit_slippage_pct,
        }

    @staticmethod
    def _to_float(v: Any) -> Optional[float]:
        if v is None or isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
