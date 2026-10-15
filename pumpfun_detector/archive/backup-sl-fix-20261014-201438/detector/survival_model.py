"""Modelo de supervivencia ante Rug Pulls (Hazard Model) y recalibración bayesiana adaptativa (Bloques A / B / I).

1. RugHazardModel:
   Modela el riesgo instantáneo (hazard rate) lambda(t | X) de que un token sea liquidado o sufra dump
   por el desarrollador antes de completar la ventana de toma de beneficios (t = 60s).
   Estima la probabilidad de supervivencia S(t | X) = exp(-Lambda(t | X)).
   Si S(60s) < 0.60 -> VETO_HAZARD_RUG_RISK.

2. BayesianBranchTracker:
   Modelo conjugado Beta-Binomial para estimar la distribución posterior del Winrate de cada rama:
   theta ~ Beta(alpha_0 + Wins, beta_0 + Losses).
   Permite calcular P(theta > p_breakeven | D) y desactivar ramas de forma adaptativa.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class SurvivalEstimate:
    survival_prob_60s: float
    hazard_rate: float
    is_safe: bool
    veto_reason: Optional[str]
    risk_factors: Dict[str, float]


class RugHazardModel:
    """
    Modelo de tiempo hasta el fallo (Hazard Survival Analysis).
    Sustituye umbrales estáticos por una función continua de riesgo de liquidación.
    """

    def __init__(
        self,
        base_hazard: float = 0.005,  # Tasa base por segundo (~30% a 60s sin factores)
        min_survival_threshold: float = 0.60,
    ):
        self.base_hazard = base_hazard
        self.min_survival_threshold = min_survival_threshold

    def evaluate(self, features: Dict[str, Any], horizon_seconds: float = 60.0) -> SurvivalEstimate:
        """
        Evalúa el vector de características en t=0 y proyecta la probabilidad de supervivencia
        durante el horizonte de salida (por defecto 60 segundos).
        """
        f = features or {}
        dev_b = float(f.get("devBalancePct", f.get("dev_balance_pct", 0.0)) or 0.0)
        vpin = float(f.get("ef_vpin_30s", f.get("vpin", 0.0)) or 0.0)
        sells = float(f.get("sellsM5", f.get("sells_m5", f.get("n_sells_60s", 0.0))) or 0.0)
        buys = float(f.get("buysM5", f.get("buys_m5", f.get("n_buys_60s", 1.0))) or 1.0)
        reserves = float(f.get("realSolReserves", f.get("sol_reserves", 30.0)) or 30.0)
        mc = float(f.get("market_cap_at_entry", f.get("market_cap", f.get("marketCap", 5000.0))) or 5000.0)

        sell_pressure_ratio = sells / max(buys, 1.0)

        # Coeficientes de riesgo empíricos (obtenidos del análisis forense de 4,366 pérdidas)
        # 1. Dev Dump: cada 1% de supply en manos del dev incrementa el log-hazard en +0.85
        dev_risk = max(0.0, dev_b) * 0.85
        # 2. VPIN tóxico: flujo informado eleva el hazard
        vpin_risk = max(0.0, vpin - 0.30) * 2.50
        # 3. Ratio de ventas agresivas:
        flow_risk = max(0.0, sell_pressure_ratio - 1.0) * 0.40
        # 4. Amortiguador de reservas: reservas altas (> 25 SOL) reducen el hazard
        reserves_buffer = max(0.0, (reserves - 12.0) / 30.0) * 0.50
        # 5. Micro-cap trap: capitalizaciones < $5k aumentan el riesgo exponencialmente
        mc_risk = max(0.0, (5000.0 - mc) / 2500.0) * 0.60 if mc < 5000.0 else 0.0

        # Exponente de riesgo proporcional de Cox: exp(beta * X)
        log_relative_hazard = dev_risk + vpin_risk + flow_risk + mc_risk - reserves_buffer
        relative_hazard = math.exp(min(4.0, max(-2.0, log_relative_hazard)))

        # Hazard instantáneo
        h_t = self.base_hazard * relative_hazard

        # Función de supervivencia acumulada: S(t) = exp(- h_t * t)
        survival_prob = math.exp(-h_t * float(horizon_seconds))
        survival_prob = round(max(0.0, min(1.0, survival_prob)), 4)

        is_safe = (survival_prob >= self.min_survival_threshold)
        veto_reason = None if is_safe else f"VETO_HAZARD_RUG_RISK: S({horizon_seconds:.0f}s)={survival_prob:.1%} < {self.min_survival_threshold:.0%}"

        return SurvivalEstimate(
            survival_prob_60s=survival_prob,
            hazard_rate=round(h_t, 6),
            is_safe=is_safe,
            veto_reason=veto_reason,
            risk_factors={
                "dev_risk": round(dev_risk, 4),
                "vpin_risk": round(vpin_risk, 4),
                "flow_risk": round(flow_risk, 4),
                "reserves_buffer": round(reserves_buffer, 4),
                "mc_risk": round(mc_risk, 4),
            },
        )


class BayesianBranchTracker:
    """
    Rastreador bayesiano adaptativo para estimar la probabilidad a posteriori
    del Winrate de cada rama (Modelo Beta-Binomial Conjugado).
    """

    def __init__(
        self,
        prior_alpha: float = 2.0,
        prior_beta: float = 2.0,
        breakeven_winrate: float = 0.40,
        min_samples: int = 10,
    ):
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self.breakeven_winrate = breakeven_winrate
        self.min_samples = min_samples

        # Estado por rama: branch -> (alpha, beta)
        self._states: Dict[str, Tuple[float, float]] = {}

    def record_outcome(self, branch: str, is_win: bool) -> Tuple[float, float]:
        """Actualiza la distribución posterior tras observar un trade."""
        a, b = self._states.get(branch, (self.prior_alpha, self.prior_beta))
        if is_win:
            a += 1.0
        else:
            b += 1.0
        self._states[branch] = (a, b)
        return (a, b)

    def get_posterior_mean(self, branch: str) -> float:
        """E[theta | D] = alpha / (alpha + beta)."""
        a, b = self._states.get(branch, (self.prior_alpha, self.prior_beta))
        return float(a / (a + b))

    def get_posterior_prob_above_breakeven(self, branch: str) -> float:
        """
        Calcula aproximadamente P(theta > breakeven | D) usando la aproximación normal
        de la distribución Beta posterior.
        """
        a, b = self._states.get(branch, (self.prior_alpha, self.prior_beta))
        total = a + b
        n_obs = total - (self.prior_alpha + self.prior_beta)
        if n_obs < self.min_samples:
            return 0.50  # Prior neutro hasta tener muestra

        mean = a / total
        var = (a * b) / (total * total * (total + 1.0))
        std = math.sqrt(var) if var > 0 else 1e-6

        # Normal CDF aproximada
        z = (mean - self.breakeven_winrate) / std
        # CDF de z
        cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        return round(float(cdf), 4)

    def should_deactivate_branch(self, branch: str, min_confidence: float = 0.15) -> bool:
        """
        Retorna True si la probabilidad a posteriori de superar el breakeven es < 15%
        tras haber observado al menos min_samples trades.
        """
        a, b = self._states.get(branch, (self.prior_alpha, self.prior_beta))
        n_obs = (a + b) - (self.prior_alpha + self.prior_beta)
        if n_obs < self.min_samples:
            return False
        prob_profitable = self.get_posterior_prob_above_breakeven(branch)
        return prob_profitable < min_confidence
