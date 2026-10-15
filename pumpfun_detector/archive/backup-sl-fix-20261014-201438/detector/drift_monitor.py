"""
DriftMonitor - Detector Estadístico de Concept Drift y Circuit Breaker Automático.

Implementa los algoritmos CUSUM y Page-Hinkley para detectar en tiempo real:
1. Degradación estadística del Winrate forward por debajo del umbral de rentabilidad.
2. Cambio de régimen de mercado (volatilidad tóxica / cluster de pérdidas de cola gruesa).
3. Transición de estados del Circuit Breaker: CLOSED -> OPEN -> HALF_OPEN.
"""
from __future__ import annotations

import math
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any


class CircuitBreakerState(str, Enum):
    CLOSED = "CLOSED"        # Operación normal autorizada
    HALF_OPEN = "HALF_OPEN"  # Modo de prueba con tamaño reducido (probation)
    OPEN = "OPEN"            # Operación suspendida por detección de drift


class DriftMonitor:
    """Monitor de deriva conceptual y protector de capital."""

    def __init__(
        self,
        target_winrate: float = 0.45,
        min_sample: int = 10,
        cusum_threshold: float = 20.0,
        page_hinkley_delta: float = 5.0,
        page_hinkley_lambda: float = 30.0,
        cooldown_seconds: float = 180.0,
        alert_manager: Any = None,
    ):
        self.target_winrate = target_winrate
        self.min_sample = min_sample
        self.cusum_threshold = cusum_threshold
        self.page_hinkley_delta = page_hinkley_delta
        self.page_hinkley_lambda = page_hinkley_lambda
        self.cooldown_seconds = cooldown_seconds

        self.state = CircuitBreakerState.CLOSED
        self.opened_at: Optional[float] = None
        self.consecutive_losses = 0

        # Histórico de operaciones
        self._outcomes: List[Dict[str, Any]] = []

        # Estado CUSUM
        self._cusum_pos = 0.0
        self._cusum_neg = 0.0

        # Estado Page-Hinkley
        self._ph_sum = 0.0
        self._ph_min = 0.0
        self._mean_pnl = 0.0

        # Drift de FEATURES (PSI/KS) opcional: se adjunta con attach_feature_drift
        self._feature_drift = None

        # Hub de alertas opcional (detector/alerts.py)
        self._alerts = alert_manager

    def _emit_alert(self, severity: Any, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self._alerts is None:
            return
        try:
            self._alerts.emit("circuit_breaker", severity, message, payload or {})
        except Exception:
            pass

    def attach_feature_drift(self, feature_monitor) -> None:
        """Adjunta un FeatureDriftMonitor (PSI/KS sobre features de entrada)."""
        self._feature_drift = feature_monitor

    def record_feature_values(self, features: Dict[str, float]) -> List[Dict[str, Any]]:
        """Alimenta el monitor de features; tripa el breaker si hay drift.

        Devuelve las alertas como list[dict] (vacio si no hay monitor adjunto).
        A diferencia del CUSUM de PnL, aqui se detecta el desplazamiento de las
        ENTRADAS antes de acumular perdidas.
        """
        if self._feature_drift is None:
            return []
        alerts = self._feature_drift.update(features or {})
        drifted = [a for a in alerts if a.drifted]
        if drifted and self.state == CircuitBreakerState.CLOSED:
            names = ", ".join(a.feature for a in drifted)
            self._trip(reason=f"Feature drift detectado: {names}")
            from .alerts import AlertSeverity
            self._emit_alert(AlertSeverity.CRITICAL,
                             f"Feature drift detectado: {names}",
                             {"features": [a.feature for a in drifted]})
        return [a.as_dict() for a in alerts]

    def record_trade_outcome(self, pnl_pct: float, is_win: bool) -> None:
        """Registra el resultado de una operación y actualiza las estadísticas de deriva."""
        now = time.time()
        self._outcomes.append({
            "pnl_pct": pnl_pct,
            "is_win": is_win,
            "ts": now,
        })

        if is_win:
            self.consecutive_losses = 0
            if self.state == CircuitBreakerState.HALF_OPEN:
                # Si gana la operación de prueba en HALF_OPEN, restaura a CLOSED
                self.state = CircuitBreakerState.CLOSED
                self.opened_at = None
        else:
            self.consecutive_losses += 1
            if self.state == CircuitBreakerState.HALF_OPEN:
                # Si falla en probation, re-abre inmediatamente
                self.state = CircuitBreakerState.OPEN
                self.opened_at = now

        n = len(self._outcomes)
        if n < self.min_sample:
            return

        # Actualizar media de PnL
        self._mean_pnl = float(sum(o["pnl_pct"] for o in self._outcomes) / n)

        # 1. Algoritmo CUSUM sobre desviación del target winrate
        score = 1.0 if is_win else -1.0
        target_score = 2.0 * self.target_winrate - 1.0
        dev = score - target_score

        self._cusum_neg = max(0.0, self._cusum_neg - dev)

        # 2. Algoritmo Page-Hinkley sobre PnL acumulado
        ph_dev = (pnl_pct - self._mean_pnl) - self.page_hinkley_delta
        self._ph_sum += ph_dev
        if self._ph_sum < self._ph_min:
            self._ph_min = self._ph_sum

        ph_stat = self._ph_sum - self._ph_min

        # Comprobar si se sobrepasan los umbrales de alerta
        drift_detected = (
            self._cusum_neg >= self.cusum_threshold
            or ph_stat >= self.page_hinkley_lambda
            or self.consecutive_losses >= 4
        )

        if drift_detected and self.state == CircuitBreakerState.CLOSED:
            reason = f"Drift detectado: CUSUM={self._cusum_neg:.1f}, ConsecLosses={self.consecutive_losses}"
            self._trip(reason=reason)
            from .alerts import AlertSeverity
            self._emit_alert(AlertSeverity.CRITICAL, reason,
                             {"cusum_neg": self._cusum_neg,
                              "consecutive_losses": self.consecutive_losses})

    def _trip(self, reason: str) -> None:
        """Dispara el Circuit Breaker a estado OPEN."""
        self.state = CircuitBreakerState.OPEN
        self.opened_at = time.time()

    def get_status(self) -> Dict[str, Any]:
        """Consulta el estado del monitor y gestiona transiciones temporales."""
        now = time.time()

        # Si está OPEN y el cooldown venció, transiciona a HALF_OPEN para una prueba
        if self.state == CircuitBreakerState.OPEN and self.opened_at is not None:
            if now - self.opened_at >= self.cooldown_seconds:
                self.state = CircuitBreakerState.HALF_OPEN

        trading_allowed = self.state in (CircuitBreakerState.CLOSED, CircuitBreakerState.HALF_OPEN)
        drift_detected = self.state != CircuitBreakerState.CLOSED

        wins = sum(1 for o in self._outcomes if o["is_win"])
        n = len(self._outcomes)
        wr = wins / n if n else 0.0

        ci_low, ci_high = self.wilson_interval(wins, n) if n else (0.0, 1.0)

        return {
            "state": self.state,
            "trading_allowed": trading_allowed,
            "drift_detected": drift_detected,
            "sample_size": n,
            "winrate": wr,
            "wilson_ci_95": (ci_low, ci_high),
            "consecutive_losses": self.consecutive_losses,
            "mean_pnl_pct": self._mean_pnl,
            "size_multiplier": 1.0 if self.state == CircuitBreakerState.CLOSED else (0.5 if self.state == CircuitBreakerState.HALF_OPEN else 0.0),
        }

    @staticmethod
    def wilson_interval(wins: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
        """Calcula el intervalo de confianza de Wilson para la tasa de acierto."""
        if n == 0:
            return 0.0, 1.0
        z = 1.95996 if confidence == 0.95 else 2.57583
        p = wins / n
        denom = 1.0 + (z**2) / n
        center = (p + (z**2) / (2.0 * n)) / denom
        delta = (z / denom) * math.sqrt((p * (1.0 - p) / n) + (z**2) / (4.0 * (n**2)))
        return max(0.0, center - delta), min(1.0, center + delta)
