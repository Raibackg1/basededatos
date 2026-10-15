"""Meta-labeling conservador con Conformal Prediction e incertidumbre calibrada (P0/P1/P2).

Implementa:
1. Conformal Prediction inductivo (split conformal) con garantía matemática de cobertura (1 - alpha).
2. Conjuntos de predicción conformal: {1} (admitido), {0} (rechazado), {0, 1} (abstención por ambigüedad).
3. Abstención explícita: si la incertidumbre es alta o p < 1 - q_hat, el trade NO se ejecuta.
4. Prevención estricta de fuga temporal: bloqueo de columnas con prefijos prohibidos (FORBIDDEN).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import numpy as np
import pandas as pd

FORBIDDEN = ("future_", "pnl_", "exit_", "mfe_", "mae_", "win_")


def _safe_features(df: pd.DataFrame, label: str) -> List[str]:
    out = []
    for c in df.columns:
        if c == label or any(str(c).lower().startswith(p) for p in FORBIDDEN):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            out.append(c)
    return out


@dataclass(frozen=True)
class ConformalPredictionResult:
    """Resultado de predicción conformal para una operación candidata."""
    admitted: bool
    should_abstain: bool
    prediction_set: Set[int]
    prob_success: float
    q_hat: float
    confidence_level: float
    reason: Optional[str] = None


class ConformalMetaLabeler:
    """Meta-clasificador con garantía conformal y política de abstención explícita."""

    def __init__(
        self,
        model: Any,
        features: List[str],
        q_hat: float,
        alpha: float = 0.05,
        min_win_prob: float = 0.50,
    ):
        self.model = model
        self.features = list(features)
        self.q_hat = float(q_hat)
        self.alpha = float(alpha)
        self.confidence_level = 1.0 - self.alpha
        self.min_win_prob = float(min_win_prob)

    def predict_one(self, feat_dict: Dict[str, Any]) -> ConformalPredictionResult:
        """Evalúa un único vector de características en O(1) tiempo de inferencia."""
        row_vals = []
        for col in self.features:
            v = feat_dict.get(col, 0.0)
            try:
                f = float(v)
                row_vals.append(0.0 if (np.isnan(f) or np.isinf(f)) else f)
            except (ValueError, TypeError):
                row_vals.append(0.0)

        x_df = pd.DataFrame([row_vals], columns=self.features)
        prob_1 = float(self.model.predict_proba(x_df)[0, 1])

        # Inductive conformal prediction set
        # Score de no conformidad: s_1 = 1 - prob_1
        in_set_1 = (1.0 - prob_1) <= self.q_hat
        in_set_0 = prob_1 <= self.q_hat

        pred_set: Set[int] = set()
        if in_set_0:
            pred_set.add(0)
        if in_set_1:
            pred_set.add(1)

        # Política de abstención:
        # Si pred_set es {0, 1} -> Incertidumbre máxima -> Abstención
        # Si pred_set es {0} -> Rechazo con alta confianza -> Veto
        # Si pred_set es {1} -> Admisión con cobertura garantizada
        # Si pred_set es {} (conjunto vacío atípico) -> Abstención por outlier
        if pred_set == {1} and prob_1 >= self.min_win_prob:
            return ConformalPredictionResult(
                admitted=True,
                should_abstain=False,
                prediction_set=pred_set,
                prob_success=prob_1,
                q_hat=self.q_hat,
                confidence_level=self.confidence_level,
                reason=None,
            )
        elif pred_set == {0, 1}:
            return ConformalPredictionResult(
                admitted=False,
                should_abstain=True,
                prediction_set=pred_set,
                prob_success=prob_1,
                q_hat=self.q_hat,
                confidence_level=self.confidence_level,
                reason="CONFORMAL_ABSTENTION_UNCERTAINTY: Conjunto ambiguo {0, 1}",
            )
        elif pred_set == {0}:
            return ConformalPredictionResult(
                admitted=False,
                should_abstain=False,
                prediction_set=pred_set,
                prob_success=prob_1,
                q_hat=self.q_hat,
                confidence_level=self.confidence_level,
                reason="CONFORMAL_VETO_PREDICTED_LOSS: Conjunto predicho {0}",
            )
        else:
            return ConformalPredictionResult(
                admitted=False,
                should_abstain=True,
                prediction_set=pred_set,
                prob_success=prob_1,
                q_hat=self.q_hat,
                confidence_level=self.confidence_level,
                reason="CONFORMAL_ABSTENTION_OUTLIER: Conjunto nulo",
            )


def fit_conformal_meta_labeler(
    df: pd.DataFrame,
    label: str,
    time_col: str = "created_at",
    alpha: float = 0.05,
    train_frac: float = 0.60,
    calib_frac: float = 0.20,
    min_samples: int = 40,
) -> Dict[str, Any]:
    """
    Entrena y calibra un meta-clasificador conformal con partición temporal de 3 bloques:
    Bloque 1 (Train): Entrena estimador probabilístico.
    Bloque 2 (Calibración): Calcula cuantiles empíricos de no-conformidad.
    Bloque 3 (Test OOS): Evaluación no contaminada.
    """
    result: Dict[str, Any] = {
        "status": "candidate_only",
        "production_ready": False,
        "conformal_model": None,
        "reason": None,
    }

    if label not in df or len(df) < min_samples:
        result["reason"] = f"insufficient_samples_{len(df)}_lt_{min_samples}"
        return result

    xcols = _safe_features(df, label)
    if not xcols:
        result["reason"] = "no_safe_numeric_features"
        return result

    work = df.sort_values(time_col, kind="mergesort") if time_col in df else df.copy()
    n = len(work)
    n_train = int(n * train_frac)
    n_calib = int(n * calib_frac)
    n_test = n - n_train - n_calib

    if n_train < 15 or n_calib < 10 or n_test < 10:
        result["reason"] = "split_blocks_too_small"
        return result

    train_data = work.iloc[:n_train]
    calib_data = work.iloc[n_train:n_train + n_calib]
    test_data = work.iloc[n_train + n_calib:]

    if train_data[label].nunique() < 2:
        result["reason"] = "class_imbalance_zero_variance"
        return result

    if (calib_data[label] == 1).sum() == 0:
        result["reason"] = "no_positive_samples_in_calibration"
        return result

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score, brier_score_loss

        pipe = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=500, class_weight="balanced")
        )
        pipe.fit(train_data[xcols], train_data[label].astype(int))

        # Calibración sobre Bloque 2
        calib_probs = pipe.predict_proba(calib_data[xcols])[:, 1]
        calib_y = calib_data[label].astype(int).values
        # Non-conformity score for true positive class
        scores_pos = 1.0 - calib_probs[calib_y == 1]
        if len(scores_pos) == 0:
            result["reason"] = "no_positive_samples_in_calibration"
            return result

        q_hat = float(np.quantile(scores_pos, min(1.0, 1.0 - alpha)))

        conformal_clf = ConformalMetaLabeler(
            model=pipe,
            features=xcols,
            q_hat=q_hat,
            alpha=alpha,
        )

        # Evaluación OOS sobre Bloque 3
        test_probs = pipe.predict_proba(test_data[xcols])[:, 1]
        test_y = test_data[label].astype(int).values
        
        preds = [conformal_clf.predict_one(row) for _, row in test_data[xcols].iterrows()]
        adm_mask = [p.admitted for p in preds]
        abstain_mask = [p.should_abstain for p in preds]

        n_admitted = sum(adm_mask)
        admitted_success_rate = float(test_y[adm_mask].mean()) if n_admitted > 0 else 0.0
        baseline_success_rate = float(test_y.mean())

        result.update({
            "status": "calibrated",
            "production_ready": True if n_admitted >= 10 and admitted_success_rate > baseline_success_rate else False,
            "conformal_model": conformal_clf,
            "q_hat": q_hat,
            "alpha": alpha,
            "features": xcols,
            "n_train": n_train,
            "n_calib": n_calib,
            "n_test": n_test,
            "test_auc": float(roc_auc_score(test_y, test_probs)) if len(np.unique(test_y)) > 1 else None,
            "test_brier": float(brier_score_loss(test_y, test_probs)),
            "baseline_success_rate": baseline_success_rate,
            "admitted_success_rate": admitted_success_rate,
            "n_admitted_oos": n_admitted,
            "n_abstained_oos": sum(abstain_mask),
        })

    except Exception as exc:
        result["reason"] = f"conformal_fit_error:{type(exc).__name__}:{str(exc)}"

    return result


def fit_candidate(
    df: pd.DataFrame,
    label: str,
    time_col: str = "created_at",
    purge_gap: int = 5,
    min_train: int = 30,
    min_test: int = 10,
) -> Dict[str, Any]:
    """Devuelve métricas y modelo candidato tradicional (compatibilidad hacia atrás)."""
    result: Dict[str, Any] = {"status": "candidate_only", "production_ready": False, "reason": None}
    if label not in df or len(df) < min_train + min_test:
        result["reason"] = "insufficient_rows"
        return result
    xcols = _safe_features(df, label)
    if not xcols:
        result["reason"] = "no_safe_numeric_features"
        return result
    work = df.sort_values(time_col, kind="mergesort") if time_col in df else df.copy()
    split = min_train
    test_start = split + max(0, purge_gap)
    train, test = work.iloc[:split], work.iloc[test_start:]
    if len(test) < min_test or train[label].nunique() < 2:
        result["reason"] = "invalid_temporal_split"
        return result
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score, brier_score_loss
        from sklearn.pipeline import make_pipeline
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler

        model = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=500, class_weight="balanced")
        )
        model.fit(train[xcols], train[label].astype(int))
        prob = model.predict_proba(test[xcols])[:, 1]
        result.update({
            "features": xcols,
            "n_train": len(train),
            "n_test": len(test),
            "oos_auc": float(roc_auc_score(test[label], prob)) if test[label].nunique() > 1 else None,
            "oos_brier": float(brier_score_loss(test[label], prob)),
            "model": model,
        })
    except Exception as exc:
        result["reason"] = f"fit_error:{type(exc).__name__}"
    return result
