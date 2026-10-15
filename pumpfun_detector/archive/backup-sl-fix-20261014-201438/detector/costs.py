"""Modelo unificado de costes y ejecución realista (P0 / P1 / BP-001 / BP-010).

Fuentes auditadas y validadas:
- memecoin66 costs.ts: pump.fun 1.25% + PumpPortal 0.5% + priority = 2.85%/lado,
  5.64% ida+vuelta sobre posición de 0.1 SOL.
- hjh LESSONS L3: roundtrip -2.5% a -4% en 0.05 SOL.
- pumpfun_detector audit: fee 0.002 SOL + slippage SL -33.49% (BP-001) + STALL -28.93% (BP-010).
- Jupiter Swap API: quote condicional con price impact y rutas.
- Hummingbot: ejecución con comisiones explícitas de red y plataforma.

Regla innegociable: ningún hallazgo ni regla se promueve sin PnL NETO auditado.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple
import math
import numpy as np


# ---------------------------------------------------------------------------
# Slippage como DISTRIBUCION condicionada por liquidez (no haircut fijo)
# ---------------------------------------------------------------------------
#: Buckets de liquidez en SOL alineados con live_store.LIQ_BUCKETS_SOL
_SLIP_BUCKETS: Tuple[Tuple[float, float, str], ...] = (
    (0.0, 5.0, "<5 SOL"),
    (5.0, 15.0, "5-15 SOL"),
    (15.0, 30.0, "15-30 SOL"),
    (30.0, float("inf"), ">=30 SOL"),
)

#: Priors documentados: escalados desde los haircuts empiricos auditados
#: BP-001 (sl_panic -33.49% medio) y BP-010 (stall -28.93%), ajustados por
#: bucket de liquidez con la misma monotonia que el liq_penalty legacy
#: (menos liquidez -> mas slippage). Solo se usan hasta que hay muestras
#: reales suficientes por bucket (min_samples_per_bucket).
_SLIP_PRIORS: Dict[str, Dict[float, float]] = {
    "<5 SOL":    {0.50: 0.35, 0.75: 0.44, 0.90: 0.58},
    "5-15 SOL":  {0.50: 0.28, 0.75: 0.335, 0.90: 0.44},
    "15-30 SOL": {0.50: 0.20, 0.75: 0.27, 0.90: 0.36},
    ">=30 SOL":  {0.50: 0.14, 0.75: 0.20, 0.90: 0.28},
}


class ConditionalSlippageModel:
    """Distribucion de slippage condicionada por bucket de liquidez.

    - ``fit(samples)`` ajusta cuantiles empiricos por bucket desde fills
      reales observados: pares (liquidity_sol, realized_slippage_frac).
    - ``quantile(liquidity_sol, q)`` devuelve el cuantil q estimado: usa el
      cuantil empirico del bucket si tiene >= min_samples_per_bucket muestras;
      si no, cae al prior documentado (BP-001/BP-010 escalado).
    - Sin ``fit`` el modelo es 100% determinista: priors fijos.
    """

    BUCKET_LABELS = tuple(b[2] for b in _SLIP_BUCKETS)

    def __init__(self, min_samples_per_bucket: int = 5,
                 priors: Optional[Dict[str, Dict[float, float]]] = None):
        self.min_samples_per_bucket = int(min_samples_per_bucket)
        self._priors = priors if priors is not None else _SLIP_PRIORS
        self._samples: Dict[str, list] = {label: [] for label in self.BUCKET_LABELS}

    @staticmethod
    def _bucket_of(liquidity_sol: float) -> str:
        liq = float(liquidity_sol)
        for lo, hi, label in _SLIP_BUCKETS:
            if lo <= liq < hi:
                return label
        return ">=30 SOL"

    def fit(self, samples: Sequence[Tuple[float, float]]) -> Dict[str, int]:
        """Ajusta cuantiles empiricos. Ignora muestras invalidas.

        Validez: liquidity_sol > 0 finita y slippage en [0, 1).
        Devuelve cuantas muestras validas aporto cada bucket.
        """
        added = {label: 0 for label in self.BUCKET_LABELS}
        for liq_raw, slip_raw in samples or []:
            try:
                liq = float(liq_raw)
                slip = float(slip_raw)
            except (TypeError, ValueError):
                continue
            if not (liq == liq and slip == slip):  # NaN
                continue
            if liq <= 0.0 or not (0.0 <= slip < 1.0):
                continue
            label = self._bucket_of(liq)
            self._samples[label].append(slip)
            added[label] += 1
        return added

    def learned_counts(self) -> Dict[str, int]:
        return {label: len(vals) for label, vals in self._samples.items()}

    def quantile(self, liquidity_sol: float, q: float) -> float:
        """Cuantil q de slippage para una liquidez dada (empirico o prior)."""
        label = self._bucket_of(liquidity_sol)
        learned = self._samples[label]
        if len(learned) >= self.min_samples_per_bucket:
            return float(np.quantile(np.asarray(learned, dtype=float), float(q)))
        return self._prior_quantile(label, q)

    @staticmethod
    def _prior_quantile(label: str, q: float) -> float:
        """Interpola/extrapola plano sobre los knots {0.50, 0.75, 0.90}."""
        knots = _SLIP_PRIORS[label]
        qs = sorted(knots.keys())
        qq = max(qs[0], min(qs[-1], float(q)))
        for a, b in zip(qs, qs[1:]):
            if a <= qq <= b:
                frac = (qq - a) / (b - a) if b > a else 0.0
                return float(knots[a] + frac * (knots[b] - knots[a]))
        return float(knots[qs[-1]])


@dataclass(frozen=True)
class ExecutionQuote:
    """Cotización detallada de ejecución con price impact y comisiones on-chain."""
    in_amount_sol: float
    out_amount_tokens: float
    spot_price_sol: float
    effective_price_sol: float
    price_impact_pct: float
    priority_fee_sol: float = 0.002
    network_fee_sol: float = 0.002
    route: str = "pump_curve"
    is_executable: bool = True
    rejection_reason: Optional[str] = None


@dataclass(frozen=True)
class CostModel:
    """Costes por operación en fracción (0.0285 = 2.85% por lado)."""
    fee_per_side_frac: float = 0.0285
    roundtrip_frac: float = 0.0564
    priority_fee_sol: float = 0.002
    network_fee_sol: float = 0.002
    sl_panic_haircut: float = 0.3349
    stall_haircut: float = 0.2893

    def net_pnl_pct(self, gross_pct: float) -> float:
        """PnL neto tras costes de ida+vuelta."""
        return float(gross_pct) - self.roundtrip_frac * 100.0

    def gross_needed_for_net(self, net_target_pct: float) -> float:
        return float(net_target_pct) + self.roundtrip_frac * 100.0

    def breakeven_winrate(self, avg_win_net: float, avg_loss_net: float) -> float:
        """p* = -L / (W - L) con medias netas. Retorna NaN si degenerado."""
        w, loss = float(avg_win_net), float(avg_loss_net)
        denom = w - loss
        if denom <= 0 or loss >= 0:
            return float("nan")
        return -loss / denom


@dataclass(frozen=True)
class ExecutionCostModel(CostModel):
    """
    Modelo Unificado de Costes y Ejecución Realista.
    Reemplaza haircuts fijos por distribuciones condicionales a tamaño, liquidez y ruta.
    """

    def calculate_quote(
        self,
        in_sol: float,
        liquidity_sol: float,
        spot_price_sol: float,
        route: str = "pump_curve",
        max_price_impact_pct: float = 5.0,
        priority_fee_sol: Optional[float] = None,
        network_fee_sol: Optional[float] = None,
    ) -> ExecutionQuote:
        """
        Calcula el impacto en precio y precio efectivo sobre la bonding curve o pool.
        Falla cerrado si el price impact supera el umbral máximo de seguridad.
        """
        p_fee = self.priority_fee_sol if priority_fee_sol is None else priority_fee_sol
        n_fee = self.network_fee_sol if network_fee_sol is None else network_fee_sol

        if in_sol <= 0 or liquidity_sol <= 0 or spot_price_sol <= 0:
            return ExecutionQuote(
                in_amount_sol=in_sol,
                out_amount_tokens=0.0,
                spot_price_sol=max(0.0, spot_price_sol),
                effective_price_sol=max(0.0, spot_price_sol),
                price_impact_pct=100.0,
                priority_fee_sol=p_fee,
                network_fee_sol=n_fee,
                route=route,
                is_executable=False,
                rejection_reason="INVALID_INPUT_METRICS: in_sol o liquidez <= 0",
            )

        # Mecánica CPMM / Bonding curve x * y = k: impacto = in_sol / (liquidity_sol + in_sol)
        impact_frac = float(in_sol) / float(liquidity_sol + in_sol)
        impact_pct = impact_frac * 100.0

        if impact_pct > max_price_impact_pct:
            return ExecutionQuote(
                in_amount_sol=in_sol,
                out_amount_tokens=0.0,
                spot_price_sol=spot_price_sol,
                effective_price_sol=spot_price_sol * (1.0 + impact_frac),
                price_impact_pct=impact_pct,
                priority_fee_sol=p_fee,
                network_fee_sol=n_fee,
                route=route,
                is_executable=False,
                rejection_reason=f"EXCESSIVE_PRICE_IMPACT: {impact_pct:.2f}% > {max_price_impact_pct:.2f}%",
            )

        eff_price = spot_price_sol * (1.0 + impact_frac)
        out_tokens = (in_sol / eff_price) if eff_price > 0 else 0.0

        return ExecutionQuote(
            in_amount_sol=in_sol,
            out_amount_tokens=out_tokens,
            spot_price_sol=spot_price_sol,
            effective_price_sol=eff_price,
            price_impact_pct=impact_pct,
            priority_fee_sol=p_fee,
            network_fee_sol=n_fee,
            route=route,
            is_executable=True,
            rejection_reason=None,
        )

    def compute_exit_pnl(
        self,
        entry_sol: float,
        nominal_exit_pct: float,
        exit_type: str = "tp",
        liquidity_sol: float = 30.0,
        priority_fee_sol: Optional[float] = None,
        network_fee_sol: Optional[float] = None,
        slippage_model: Optional["ConditionalSlippageModel"] = None,
    ) -> dict:
        """
        Calcula el PnL neto en SOL y % aplicando comisiones de plataforma,
        priority fees (ida y vuelta) y haircuts empíricos condicionales (BP-001 y BP-010).

        Si se pasa ``slippage_model`` (ConditionalSlippageModel), el slippage de
        salidas de pánico/estancamiento sale de la distribución condicionada
        por liquidez (cola q90 para sl_panic, mediana q50 para stall) en lugar
        del haircut fijo. Sin modelo, el comportamiento legacy queda intacto.
        """
        p_fee = self.priority_fee_sol if priority_fee_sol is None else priority_fee_sol
        n_fee = self.network_fee_sol if network_fee_sol is None else network_fee_sol
        total_tx_fees_sol = 2.0 * (p_fee + n_fee)  # Ida y vuelta

        # Plataforma: 1.25% pump.fun + 0.5% agregador = 1.75% por lado (3.5% total)
        platform_fee_frac = 0.035

        slippage_applied = 0.0
        if exit_type == "sl_panic":
            if slippage_model is not None:
                # Distribución condicionada: cola (q90) del bucket de liquidez
                slippage_applied = slippage_model.quantile(liquidity_sol, 0.90)
            else:
                # BP-001: Slippage medio empírico auditado de -33.49%, condicionado por liquidez
                liq_penalty = max(0.0, (15.0 - liquidity_sol) / 15.0 * 0.10) if liquidity_sol < 15.0 else 0.0
                slippage_applied = self.sl_panic_haircut + liq_penalty
            # Realized loss = -(nominal_sl + slippage), acotado a -98%
            realized_loss_frac = min(0.98, abs(nominal_exit_pct) / 100.0 + slippage_applied)
            gross_pnl_pct = -realized_loss_frac * 100.0
        elif exit_type == "stall":
            if slippage_model is not None:
                # Distribución condicionada: mediana (q50) del bucket de liquidez
                slippage_applied = slippage_model.quantile(liquidity_sol, 0.50)
            else:
                # BP-010: Latencia de muestreo en salida STALL arroja pérdida real de -28.93%
                slippage_applied = self.stall_haircut
            realized_loss_frac = min(0.98, max(abs(nominal_exit_pct) / 100.0, slippage_applied))
            gross_pnl_pct = -realized_loss_frac * 100.0
        elif exit_type == "breakeven":
            gross_pnl_pct = 0.0
        else:  # TP o salida normal
            gross_pnl_pct = float(nominal_exit_pct)

        gross_exit_sol = max(0.0, entry_sol * (1.0 + gross_pnl_pct / 100.0))
        platform_fee_sol = (entry_sol + gross_exit_sol) * (platform_fee_frac / 2.0)
        total_fees_sol = platform_fee_sol + total_tx_fees_sol

        net_exit_sol = gross_exit_sol - total_fees_sol
        net_pnl_sol = net_exit_sol - entry_sol
        net_pnl_pct = (net_pnl_sol / entry_sol) * 100.0 if entry_sol > 0 else 0.0

        return {
            "net_pnl_pct": float(net_pnl_pct),
            "net_pnl_sol": float(net_pnl_sol),
            "gross_pnl_pct": float(gross_pnl_pct),
            "total_fees_sol": float(total_fees_sol),
            "slippage_applied": float(slippage_applied),
            "exit_type": exit_type,
        }

    def estimate_dynamic_priority_fee(
        self, congestion_ratio: float = 0.0, base_fee_sol: float = 0.002
    ) -> float:
        """
        Escala la priority fee según la congestión on-chain de Solana (Bloques C / E).
        congestion_ratio en [0.0 .. 1.0]. A congestión alta (1.0), escala hasta 0.008 SOL.
        """
        c = max(0.0, min(1.0, float(congestion_ratio or 0.0)))
        return round(base_fee_sol * (1.0 + 3.0 * c), 6)

    def compute_no_fill_penalty_ev(
        self,
        expected_net_ev_pct: float,
        price_impact_pct: float = 0.0,
        congestion_ratio: float = 0.0,
    ) -> float:
        """
        Penaliza el EV neto esperado considerando la probabilidad de no-fill o reversión (Bloque A).
        P(no-fill) = min(0.60, 0.02 + 0.04 * impact + 0.25 * congestion).
        """
        impact = max(0.0, float(price_impact_pct or 0.0))
        c = max(0.0, min(1.0, float(congestion_ratio or 0.0)))
        p_nofill = min(0.60, 0.02 + 0.04 * impact + 0.25 * c)
        return float(expected_net_ev_pct) * (1.0 - p_nofill)


DEFAULT_COSTS = ExecutionCostModel()
