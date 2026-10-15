import os
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.meta_labeler import (
    ConformalMetaLabeler,
    ConformalPredictionResult,
    fit_conformal_meta_labeler,
    _safe_features,
)
from detector.master_signal import MasterSnipeSignal, MasterSignalDecision


class ConformalMetaLabelerTests(unittest.TestCase):
    def test_safe_features_blocks_forbidden_and_future_columns(self):
        df = pd.DataFrame({
            "market_cap": [1000, 2000],
            "future_ret_5": [0.1, 0.2],
            "pnl_net": [10.0, -5.0],
            "exit_ts": [1234, 1235],
            "mfe_pct": [20.0, 30.0],
            "mae_pct": [-5.0, -10.0],
            "win_flag": [1, 0],
            "sol_reserves": [20.0, 25.0],
            "label": [1, 0],
        })
        safe = _safe_features(df, "label")
        self.assertIn("market_cap", safe)
        self.assertIn("sol_reserves", safe)
        self.assertNotIn("future_ret_5", safe)
        self.assertNotIn("pnl_net", safe)
        self.assertNotIn("exit_ts", safe)
        self.assertNotIn("mfe_pct", safe)
        self.assertNotIn("mae_pct", safe)
        self.assertNotIn("win_flag", safe)
        self.assertNotIn("label", safe)

    def test_fit_and_predict_conformal_meta_labeler(self):
        np.random.seed(42)
        n = 100
        # Crear un dataset sintético separable e independiente del tiempo
        x1 = np.random.uniform(10, 100, n)
        x2 = np.random.normal(20, 5, n)
        y = (x2 > 20).astype(int)

        df = pd.DataFrame({
            "created_at": range(n),
            "feature1": x1,
            "feature2": x2,
            "target": y,
        })

        res = fit_conformal_meta_labeler(
            df,
            label="target",
            time_col="created_at",
            alpha=0.20,
            train_frac=0.60,
            calib_frac=0.20,
            min_samples=40,
        )

        self.assertEqual(res["status"], "calibrated")
        self.assertIsNotNone(res["conformal_model"])
        self.assertIn("q_hat", res)
        self.assertGreaterEqual(res["q_hat"], 0.0)

        model = res["conformal_model"]
        # Predicción en un caso claramente positivo (x2 alto)
        high_val = {"feature1": 50.0, "feature2": 35.0}
        pred_high = model.predict_one(high_val)
        self.assertIsInstance(pred_high, ConformalPredictionResult)
        self.assertIn(1, pred_high.prediction_set)

        # Predicción en un caso claramente negativo (x2 bajo)
        low_val = {"feature1": 50.0, "feature2": 5.0}
        pred_low = model.predict_one(low_val)
        self.assertFalse(pred_low.admitted)

    def test_disabled_branch_veto(self):
        # Probar que MASTER_ALPHA_ORGANIC_RETENTION se veta por DISABLED_BRANCHES
        features = {
            "firstMarketCap": 5000.0,
            "topHolderPct": 60.0,
            "devBalancePct": 0.5,
            "holderCount": 5,
            "market_cap": 8000.0,
            "realSolReserves": 25.0,
            "migrationProgress": 20.0,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.branch, "VETO")
        self.assertIn("VETO_BRANCH_DISABLED", dec.veto_reason)

    def test_master_signal_with_conformal_abstention(self):
        # Dummy model que siempre predice incertidumbre {0, 1}
        class DummyUncertainModel:
            def predict_proba(self, X):
                return np.array([[0.5, 0.5]])

        uncertain_conformal = ConformalMetaLabeler(
            model=DummyUncertainModel(),
            features=["fmc"],
            q_hat=0.60,  # 1 - 0.5 = 0.5 <= 0.60, 0.5 <= 0.60 => {0, 1}
            alpha=0.20,
        )

        # Token candidato a Rama A
        feat_a = {
            "firstMarketCap": 3500.0,
            "firstLiquidity": 1200.0,
            "market_cap": 6000.0,
            "realSolReserves": 20.0,
            "devBalancePct": 0.5,
            "migrationProgress": 15.0,
            "buysM5": 5,
            "holderCount": 10,
        }

        # Sin meta-labeler: admitido
        dec_normal = MasterSnipeSignal.evaluate(feat_a)
        self.assertTrue(dec_normal.admitted)
        self.assertEqual(dec_normal.branch, "MASTER_ALPHA_EXPANSION")

        # Con conformal meta-labeler incierto: VETO por abstención
        dec_conformal = MasterSnipeSignal.evaluate(feat_a, meta_labeler=uncertain_conformal)
        self.assertFalse(dec_conformal.admitted)
        self.assertEqual(dec_conformal.branch, "VETO")
        self.assertIn("CONFORMAL_ABSTENTION_UNCERTAINTY", dec_conformal.veto_reason)


if __name__ == "__main__":
    unittest.main()
