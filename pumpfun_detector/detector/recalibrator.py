"""
Recalibrador de umbrales de la Señal Maestra a partir de outcomes REALES.

Cierra el loop de "ganancia real medible": ``LiveStore`` persiste cada decisión
con su snapshot de features entrada-seguro y, cuando el trade se cierra, une el
PnL real. Este módulo:

1. ``load_resolved``: lee las decisiones con outcome unido (JOIN) y reconstruye
   un DataFrame plano con las features + el PnL real, **solo** de una procedencia
   (por defecto ``"live"``) para que el demo sintético jamás contamine la
   recalibración.
2. ``calibrate_threshold``: para un umbral de veto/admisión, barre candidatos y
   mide la *expectancy neta* (PnL agregado) de los trades que pasarían el filtro.
   Devuelve el umbral óptimo observado con su muestra (n) y métricas.
3. ``build_recalibrated_signal``: construye una **subclase** de
   ``MasterSnipeSignal`` con los umbrales que tienen muestra suficiente
   (``n >= min_n``); los demás mantienen el valor de producción auditado.

Filosofía anti-overfitting: un umbral solo se recalibra si hay evidencia real
suficiente; en caso contrario se conserva el valor de producción. Nunca se
modifica la clase en producción — solo se deriva.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .master_signal import MasterSnipeSignal
from .live_store import LiveStore


# Mapeo umbral-atributo-de-clase -> key de feature en el snapshot entrada-seguro.
# Cada entrada: (atributo, feature_key, dirección)
#   dirección "le": el veto rechaza cuando feature > umbral  (p.ej. SELLS_M5)
#   dirección "ge": el veto rechaza cuando feature < umbral  (p.ej. MC muerta)
_THRESHOLD_FEATURES: List[Tuple[str, str, str]] = [
    ("MIN_MARKET_CAP_USD", "mc_at_entry_est", "ge"),
    ("MAX_MARKET_CAP_USD", "mc_at_entry_est", "le"),
    ("MAX_SELLS_M5", "n_sells_60s", "le"),
    ("MIN_BUYS_M5", "n_buys_60s", "ge"),
    ("MIN_HOLDERS", "unique_buyers_60s", "ge"),
    ("MAX_TOP_HOLDER_PCT", "top_holder_pct", "le"),
    ("MAX_MIGRATION_PROGRESS_PCT", "ef_curve_progress", "le"),
    ("MIN_SOL_RESERVES", "realSolReserves", "ge"),
]

# Candidatos de barrido por feature (mismo orden de granularidad).
_CANDIDATES: Dict[str, List[float]] = {
    "mc_at_entry_est": [3000.0, 3500.0, 4000.0, 4500.0, 5000.0, 6000.0, 8000.0],
    "n_sells_60s": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
    "n_buys_60s": [5.0, 8.0, 10.0, 12.0, 15.0, 20.0],
    "unique_buyers_60s": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
    "top_holder_pct": [60.0, 70.0, 80.0, 85.0, 90.0],
    "ef_curve_progress": [0.30, 0.35, 0.40, 0.45, 0.50],
    "realSolReserves": [8.0, 10.0, 12.0, 15.0, 20.0],
}

# Derivados de _THRESHOLD_FEATURES para lookup O(1) en el barrido.
_ATTR_TO_FEATURE: Dict[str, Tuple[str, str]] = {
    attr: (feat_key, direction) for attr, feat_key, direction in _THRESHOLD_FEATURES
}

# Factor de escala feature->atributo. La mayoría opera 1:1. Excepción honesta:
# ``ef_curve_progress`` se mide en [0..1] pero ``MAX_MIGRATION_PROGRESS_PCT``
# opera en [0..100] (igual que ``evaluate()`` hace ``mig *= 100.0``). Sin este
# factor el barrido produciría umbrales de saturación 100x demasiado pequeños.
_ATTR_SCALE: Dict[str, float] = {
    "ef_curve_progress": 100.0,
}



def _as_float(v: Any) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else np.nan
    except (ValueError, TypeError):
        return np.nan


class ThresholdRecalibrator:
    """Re-estima umbrales de ``MasterSnipeSignal`` con outcomes reales.

    Parámetros
    ----------
    store : LiveStore
        Almacén con decisiones + outcomes resueltos.
    origin : str
        Procedencia a auditar (P0). Por defecto ``"live"``: los datos del demo
        (``"synthetic"``) nunca participan de la recalibración.
    min_n : int
        Muestra mínima de trades que pasarían el filtro para aceptar un umbral
        recalibrado. Por debajo, se conserva el valor de producción.
    """

    def __init__(self, store: LiveStore, origin: str = "live", min_n: int = 20):
        self.store = store
        self.origin = origin
        self.min_n = int(min_n)

    # ------------------------------------------------------------------ #
    # Carga de datos reales resueltos
    # ------------------------------------------------------------------ #
    def load_resolved(self, admitted_only: bool = False) -> pd.DataFrame:
        """DataFrame con features + PnL real de las decisiones resueltas.

        Reconstruye las features desde ``features_json`` y las une con el PnL de
        ``live_outcomes``. Solo incluye filas con outcome unido (PnL conocido).
        """
        q = """
            SELECT d.id AS decision_id, d.mint AS mint, d.data_origin AS data_origin,
                   d.admitted AS admitted, d.branch AS branch,
                   d.features_json AS features_json,
                   o.pnl_sol AS pnl_sol, o.pnl_pct AS pnl_pct,
                   o.exit_reason AS exit_reason
            FROM live_decisions d
            JOIN live_outcomes  o ON o.decision_id = d.id
            WHERE 1 = 1
        """
        params: List[Any] = []
        if self.origin is not None:
            q += " AND d.data_origin = ?"
            params.append(str(self.origin))
        if admitted_only:
            q += " AND d.admitted = 1"
        rows = self.store._con.execute(q, params).fetchall()

        records: List[Dict[str, Any]] = []
        for r in rows:
            try:
                feats = json.loads(r["features_json"] or "{}")
            except (ValueError, TypeError):
                feats = {}
            rec: Dict[str, Any] = dict(feats) if isinstance(feats, dict) else {}
            rec["_decision_id"] = int(r["decision_id"])
            rec["_mint"] = r["mint"]
            rec["_admitted"] = int(r["admitted"])
            rec["_branch"] = r["branch"]
            rec["_pnl_sol"] = _as_float(r["pnl_sol"])
            if not np.isfinite(rec["_pnl_sol"]):
                rec["_pnl_sol"] = _as_float(r["pnl_pct"])
            rec["_exit_reason"] = r["exit_reason"]
            records.append(rec)

        return pd.DataFrame.from_records(records)

    # ------------------------------------------------------------------ #
    # Barrido de un umbral y construcción de la subclase recalibrada
    # ------------------------------------------------------------------ #
    def calibrate_threshold(self, attr_name: str) -> Optional[Dict[str, Any]]:
        """Barre candidatos de un umbral y devuelve el de mayor PnL neto real.

        Objetivo = *expectancy neta agregada*: de todos los trades resueltos que
        pasarían el veto con un umbral dado, maximizar la suma de PnL real, sujeto
        a ``n >= min_n``. Los NaN de la feature se excluyen (conservador: el veto
        de producción los rechazaría). Si el óptimo cae en un extremo de los
        candidatos se marca ``at_boundary=True`` para no sobre-confiar en él.

        Nota honesta: en producción solo los trades ADMITIDOS tienen outcome real
        (los vetados no se ejecutaron). Este barrido re-optimiza umbrales sobre
        los perdedores admitidos; descubrir "fuimos demasiado estrictos y perdimos
        ganadores" exigiría paper-trading de los vetados.
        """
        spec = next((t for t in _THRESHOLD_FEATURES if t[0] == attr_name), None)
        if spec is None:
            return None
        _, feat_key, direction = spec
        self._ensure_df()
        if feat_key not in self.df.columns:
            return None

        feats = pd.to_numeric(self.df[feat_key], errors="coerce")
        pnl = self.df["_pnl_sol"]
        valid = feats.notna() & pnl.notna()
        feats, pnl = feats[valid], pnl[valid]
        if not len(feats):
            return None

        candidates = _CANDIDATES.get(feat_key, [])
        scale = _ATTR_SCALE.get(feat_key, 1.0)
        best: Optional[Dict[str, Any]] = None
        last = len(candidates) - 1
        for i, c in enumerate(candidates):
            mask = (feats <= c) if direction == "le" else (feats >= c)
            n = int(mask.sum())
            if n < self.min_n:
                continue
            sub = pnl[mask]
            total = float(sub.sum())
            info = {
                "attr": attr_name, "feature": feat_key, "direction": direction,
                "candidate": c, "attr_value": c * scale, "n": n,
                "total_pnl": total, "mean_pnl": float(sub.mean()),
                "winrate": float((sub > 0).sum() / n),
                "at_boundary": i in (0, last),
            }
            if best is None or total > best["total_pnl"]:
                best = info
        return best

    def calibrate_all(self) -> Dict[str, Any]:
        """Barre todos los umbrales mapeados y devuelve el informe por atributo."""
        self._ensure_df()
        return {attr: self.calibrate_threshold(attr) for attr, _, _ in _THRESHOLD_FEATURES}

    def build_recalibrated_signal(self) -> Tuple[type, List[Dict[str, Any]]]:
        """Construye una SUBCLASE de ``MasterSnipeSignal`` con umbrales recalibrados.

        Solo se sobreescribe un umbral si el barrido tiene muestra suficiente
        (``n >= min_n``) y supone un cambio real respecto al valor de producción.
        Los demás atributos se heredan intactos. La clase de producción nunca se
        modifica. Devuelve ``(subclase, cambios)`` donde ``cambios`` es la lista
        auditable de cada ajuste con su evidencia (n, PnL, winrate, at_boundary).
        """
        report = self.calibrate_all()
        overrides: Dict[str, float] = {}
        changes: List[Dict[str, Any]] = []
        for attr, info in report.items():
            if not info or info["n"] < self.min_n:
                continue
            feat_key = _ATTR_TO_FEATURE[attr][0]
            new_val = info["candidate"] * _ATTR_SCALE.get(feat_key, 1.0)
            prod_val = float(getattr(MasterSnipeSignal, attr))
            if abs(new_val - prod_val) < 1e-9:
                continue
            overrides[attr] = new_val
            changes.append({
                "attr": attr, "production": prod_val, "recalibrated": new_val,
                "n": info["n"], "total_pnl": info["total_pnl"],
                "mean_pnl": info["mean_pnl"], "winrate": info["winrate"],
                "at_boundary": info["at_boundary"],
            })

        cls = type("RecalibratedMasterSignal", (MasterSnipeSignal,), dict(overrides))
        cls.recalibration_report = report
        cls.recalibration_changes = changes
        cls.recalibration_min_n = self.min_n
        cls.recalibration_origin = self.origin
        cls.recalibration_n_resolved = int(len(self.df))
        return cls, changes

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    def _ensure_df(self) -> None:
        if getattr(self, "df", None) is None:
            self.df = self.load_resolved()


# --------------------------------------------------------------------------- #
# Comparativa de expectancy: producción vs recalibrada sobre outcomes REALES
# --------------------------------------------------------------------------- #
class RecalibrationComparator:
    """Compara la expectancy neta de la señal de producción vs la recalibrada.

    Diseño honesto sobre su alcance:

    - Solo los trades **admitidos** tienen outcome real en ``LiveStore`` (los
      vetados no se ejecutaron). Por tanto la comparativa mide el efecto de los
      umbrales recalibrados **sobre el set realmente ejecutado**: si la recalibrada
      habría rechazado un trade ejecutado que resultó ganador es una "ganancia
      perdida" (miss); si habría rechazado uno perdedor es una "pérdida evitada".
    - Lo que NO puede ver (y se declara como limitación, no se oculta): trades
      que producción vetó y la recalibrada habría admitido, porque esos no tienen
      PnL real salvo paper-trading de los vetados.
    - ``baseline_pnl`` = PnL neto real agregado de lo ejecutado por producción.
      ``recalibrated_pnl`` = PnL de ese mismo set filtrado por la señal recalibrada.
      ``delta`` = recalibrado - baseline (expectancy incremental observada).
    """

    def __init__(self, store: LiveStore, origin: str = "live", min_n: int = 20):
        self.recalibrator = ThresholdRecalibrator(store, origin=origin, min_n=min_n)
        self.origin = origin

    def compare(self) -> Dict[str, Any]:
        rec = self.recalibrator
        recal_cls, changes = rec.build_recalibrated_signal()
        # Solo trades con outcome real (admitidos y ejecutados por producción).
        df = rec.load_resolved(admitted_only=True)
        n_resolved = int(len(df))

        result: Dict[str, Any] = {
            "origin": self.origin,
            "n_resolved": n_resolved,
            "n_changes": len(changes),
            "changes": changes,
            "baseline_pnl_sol": 0.0,
            "recalibrated_pnl_sol": 0.0,
            "delta_pnl_sol": 0.0,
            "losses_avoided_sol": 0.0,
            "gains_missed_sol": 0.0,
            "n_loss_avoided": 0,
            "n_gain_missed": 0,
            "flips": [],
        }
        if n_resolved == 0:
            result["note"] = "sin outcomes resueltos: nada que comparar todavía"
            return result

        baseline = 0.0
        recalibrated = 0.0
        avoided = 0.0
        missed = 0.0
        n_avoided = 0
        n_missed = 0
        flips: List[Dict[str, Any]] = []

        for row in df.to_dict("records"):
            pnl = _as_float(row.get("_pnl_sol"))
            if not np.isfinite(pnl):
                continue
            feats = {k: v for k, v in row.items() if not k.startswith("_")}
            d_prod = MasterSnipeSignal.evaluate(feats)
            d_rec = recal_cls.evaluate(feats)
            # Baseline: producción ejecutó este trade (es historial admitido).
            baseline += pnl
            if d_rec.admitted:
                recalibrated += pnl
            if d_prod.admitted and not d_rec.admitted:
                # La recalibrada habría rechazado un trade que sí se ejecutó.
                if pnl < 0:
                    avoided += -pnl  # pérdida evitada
                    n_avoided += 1
                elif pnl > 0:
                    missed += pnl   # ganancia perdida
                    n_missed += 1
                flips.append({
                    "mint": row.get("_mint"), "branch": row.get("_branch"),
                    "prod_branch": d_prod.branch, "recal_branch": d_rec.branch,
                    "pnl_sol": pnl,
                    "recal_veto": d_rec.veto_reason,
                })

        result.update({
            "baseline_pnl_sol": float(baseline),
            "recalibrated_pnl_sol": float(recalibrated),
            "delta_pnl_sol": float(recalibrated - baseline),
            "losses_avoided_sol": float(avoided),
            "gains_missed_sol": float(missed),
            "n_loss_avoided": int(n_avoided),
            "n_gain_missed": int(n_missed),
            "flips": flips,
        })
        return result

