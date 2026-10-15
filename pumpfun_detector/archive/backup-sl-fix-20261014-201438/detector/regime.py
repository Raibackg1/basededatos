"""Clasificador de régimen de mercado en streaming (expanding/contracting/neutral).

Por que existe
--------------
Las ramas de `MasterSnipeSignal` se validan contra datos historicos agregados.
Pero el mercado pump.fun alterna ciclos: expansiones donde casi todo sube,
contracciones donde hasta los buenos setups mueren, y zonas neutras. Operar
con el mismo umbral en los tres regimenes es como no tener modelo.

Que hace
--------
`RegimeClassifier` mantiene una ventana rolling de outcomes (wins/losses) y
PnL. Clasifica el estado actual en uno de tres regimenes:

- EXPANDING:  winrate rolling >= umbral_expanding (ej. 0.55)
- CONTRACTING: winrate rolling <= umbral_contracting (ej. 0.35)
- NEUTRAL:    entre ambos

El classifier no veta nada por si mismo; expone `regime` y `suggested_confidence_scale`
para que el caller (live_runner o el propio signal) escale la confianza o
suspenda entradas en CONTRACTING.

Determinista: mismas entradas -> misma clasificacion. Sin dependencias externas.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Deque, Dict, Optional, Tuple


class MarketRegime(str, Enum):
    EXPANDING = "expanding"
    CONTRACTING = "contracting"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"  # sin datos suficientes


@dataclass(frozen=True)
class RegimeSnapshot:
    """Estado clasificado en un instante dado."""
    regime: MarketRegime
    winrate: float
    n: int
    avg_pnl_r: float
    suggested_confidence_scale: float

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["regime"] = self.regime.value
        return d


class RegimeClassifier:
    """Clasificador rolling de régimen basado en winrate y PnL medio.

    Parameters
    ----------
    window: tamaño de la ventana rolling (últimos N outcomes).
    min_samples: mínimo de outcomes en la ventana para emitir un régimen
        distinto de UNKNOWN.
    umbral_expanding: winrate >= este valor => EXPANDING.
    umbral_contracting: winrate <= este valor => CONTRACTING.
    scale_expanding / scale_contracting / scale_neutral: multiplicadores de
        confianza sugeridos por régimen (1.0 = sin escala).
    """

    def __init__(
        self,
        window: int = 50,
        min_samples: int = 10,
        umbral_expanding: float = 0.55,
        umbral_contracting: float = 0.35,
        scale_expanding: float = 1.0,
        scale_contracting: float = 0.5,
        scale_neutral: float = 0.8,
    ):
        if umbral_contracting >= umbral_expanding:
            raise ValueError("umbral_contracting debe ser < umbral_expanding")
        self.window = int(window)
        self.min_samples = int(min_samples)
        self.umbral_expanding = float(umbral_expanding)
        self.umbral_contracting = float(umbral_contracting)
        self.scale_expanding = float(scale_expanding)
        self.scale_contracting = float(scale_contracting)
        self.scale_neutral = float(scale_neutral)
        self._outcomes: Deque[Tuple[float, float]] = deque(maxlen=self.window)

    def record_outcome(self, pnl_r: float, win: Optional[bool] = None) -> None:
        """Registra un outcome cerrado.

        - ``pnl_r``: PnL en R (o fraccion). Si ``win`` es None se infiere pnl_r > 0.
        - Ignora NaN/inf.
        """
        try:
            v = float(pnl_r)
        except (TypeError, ValueError):
            return
        if v != v or v in (float("inf"), float("-inf")):
            return
        w = bool(win) if win is not None else (v > 0.0)
        self._outcomes.append((v, 1.0 if w else 0.0))

    @property
    def n(self) -> int:
        return len(self._outcomes)

    def snapshot(self) -> RegimeSnapshot:
        n = self.n
        if n < self.min_samples:
            return RegimeSnapshot(
                regime=MarketRegime.UNKNOWN, winrate=0.0, n=n,
                avg_pnl_r=0.0, suggested_confidence_scale=1.0)
        pnls = [o[0] for o in self._outcomes]
        wins = [o[1] for o in self._outcomes]
        wr = sum(wins) / n
        avg = sum(pnls) / n
        if wr >= self.umbral_expanding:
            regime, scale = MarketRegime.EXPANDING, self.scale_expanding
        elif wr <= self.umbral_contracting:
            regime, scale = MarketRegime.CONTRACTING, self.scale_contracting
        else:
            regime, scale = MarketRegime.NEUTRAL, self.scale_neutral
        return RegimeSnapshot(
            regime=regime, winrate=round(wr, 6), n=n,
            avg_pnl_r=round(avg, 6), suggested_confidence_scale=scale)

    @property
    def regime(self) -> MarketRegime:
        return self.snapshot().regime

    @property
    def suggested_confidence_scale(self) -> float:
        return self.snapshot().suggested_confidence_scale

    def should_suspend_entries(self) -> bool:
        """True si el régimen actual justifica suspender entradas nuevas."""
        return self.regime == MarketRegime.CONTRACTING
