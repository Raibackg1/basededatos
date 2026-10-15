"""Drift estadistico de FEATURES con PSI y test KS de dos muestras.

Por que existe
--------------
`DriftMonitor` vigila el OUTCOME (CUSUM/Page-Hinkley sobre winrate y PnL).
Eso detecta el problema cuando ya perdio dinero. Este modulo vigila las
ENTRADAS: si la distribucion de una feature (liquidez, vpin, mc...) se
desplaza respecto de la referencia de entrenamiento/produccion, el modelo
dejo de operar sobre el mismo problema. Deteccion temprana -> corte antes
del drawdown.

Algoritmos (dependencia: scipy, ya presente via scikit-learn del repo):
- PSI (Population Stability Index): bins cuantiles de la referencia,
  PSI = sum((a_i - e_i) * ln(a_i / e_i)). Umbral tipico 0.25 (shift material).
- KS 2-samples: statistic y p-value; drift si p < ks_alpha.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, List, Optional, Sequence

import numpy as np
from scipy import stats


def psi(expected: Sequence[float], actual: Sequence[float], bins: int = 10,
        eps: float = 1e-6) -> float:
    """Population Stability Index entre referencia (expected) y muestra actual."""
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    e = e[np.isfinite(e)]
    a = a[np.isfinite(a)]
    if len(e) < 10 or len(a) < 10:
        return 0.0
    qs = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(e, qs))
    if edges.size < 3:
        # Feature casi constante: PSI indefinido/irrelevante -> 0
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e_hist, _ = np.histogram(e, bins=edges)
    a_hist, _ = np.histogram(a, bins=edges)
    e_frac = e_hist / max(1, e_hist.sum())
    a_frac = a_hist / max(1, a_hist.sum())
    e_frac = np.clip(e_frac, eps, None)
    a_frac = np.clip(a_frac, eps, None)
    return float(np.sum((a_frac - e_frac) * np.log(a_frac / e_frac)))


def ks_2sample(reference: Sequence[float], sample: Sequence[float]) -> Dict[str, float]:
    """KS de dos muestras: statistic y p_value (scipy, determinista)."""
    r = np.asarray(reference, dtype=float)
    s = np.asarray(sample, dtype=float)
    r = r[np.isfinite(r)]
    s = s[np.isfinite(s)]
    if len(r) < 2 or len(s) < 2:
        return {"statistic": 0.0, "p_value": 1.0}
    res = stats.ks_2samp(r, s)
    return {"statistic": float(res.statistic), "p_value": float(res.pvalue)}


@dataclass(frozen=True)
class DriftAlert:
    """Alerta de drift para una feature."""
    feature: str
    psi: float
    ks_statistic: float
    ks_p_value: float
    drifted: bool
    n: int
    reason: str

    def as_dict(self) -> Dict:
        return asdict(self)


class FeatureDriftMonitor:
    """Monitor en streaming: actualiza buffers y evalua PSI/KS por feature.

    - Solo evalua features presentes en la referencia (no inventa senales).
    - No alerta hasta alcanzar ``min_samples`` muestras actuales.
    - ``update`` devuelve el estado actual de TODAS las features referenciadas
      que tengan muestras (drifted=True cuando cruza algun umbral).
    """

    def __init__(
        self,
        reference: Dict[str, Sequence[float]],
        psi_threshold: float = 0.25,
        ks_alpha: float = 0.01,
        min_samples: int = 30,
        window: int = 256,
    ):
        self.psi_threshold = float(psi_threshold)
        self.ks_alpha = float(ks_alpha)
        self.min_samples = int(min_samples)
        self._reference: Dict[str, np.ndarray] = {}
        for name, vals in (reference or {}).items():
            arr = np.asarray(list(vals), dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size >= 10:
                self._reference[str(name)] = arr
        maxlen = max(int(window), self.min_samples)
        self._buffers: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=maxlen))

    @property
    def reference_features(self) -> List[str]:
        return sorted(self._reference.keys())

    def update(self, features: Dict[str, float]) -> List[DriftAlert]:
        """Incorpora UNA observacion (feature -> valor) y devuelve alertas."""
        for name, value in (features or {}).items():
            key = str(name)
            if key not in self._reference:
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if v == v:  # no-NaN
                self._buffers[key].append(v)
        return self.evaluate()

    def update_batch(self, rows: Sequence[Dict[str, float]]) -> List[DriftAlert]:
        alerts: List[DriftAlert] = []
        for row in rows or []:
            alerts = self.update(row)
        return alerts

    def evaluate(self) -> List[DriftAlert]:
        out: List[DriftAlert] = []
        for name, ref in self._reference.items():
            buf = self._buffers.get(name)
            if not buf or len(buf) < self.min_samples:
                continue
            cur = np.asarray(buf, dtype=float)
            p = psi(ref, cur)
            ks = ks_2sample(ref, cur)
            drifted = bool(p >= self.psi_threshold or ks["p_value"] < self.ks_alpha)
            reasons = []
            if p >= self.psi_threshold:
                reasons.append(f"PSI={p:.3f}>={self.psi_threshold:.2f}")
            if ks["p_value"] < self.ks_alpha:
                reasons.append(f"KS p={ks['p_value']:.2e}<{self.ks_alpha:.2f}")
            out.append(DriftAlert(
                feature=name, psi=round(p, 6),
                ks_statistic=round(ks["statistic"], 6),
                ks_p_value=ks["p_value"], drifted=drifted,
                n=len(buf), reason="; ".join(reasons) if reasons else "estable"))
        return out
