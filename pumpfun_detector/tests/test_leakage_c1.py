"""C1 — Fuga de las features de "primeros 60 s" (lookahead desde la creación).

Las métricas ``*_60s`` de ``token_features`` se calculan sobre [creación,
creación+60 s], no hasta la entrada. Con una entrada típica a ~10 s incluyen
~50 s POSTERIORES a la entrada. Deben quedar prohibidas como features de
entrada; el equivalente causal es la familia ``ef_*`` (``entry_features``), que
corta los trades en ``entry_time``.
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.features import (
    ENTRY_SAFE_FEATURES,
    POST_ENTRY_FEATURES,
    FeatureAvailabilityGuard,
)
from detector.master_signal import MasterSnipeSignal

LEAKY_60S = [
    "n_trades_60s", "n_buys_60s", "n_sells_60s", "buy_sell_ratio_60s",
    "unique_buyers_60s", "top5_wallet_share_60s", "buy_vol_sol_60s",
    "avg_buy_sol_60s", "max_buy_sol_60s",
]
CAUSAL_EF = ["ef_n_buys", "ef_n_sells", "ef_unique_buyers", "ef_curve_progress"]


class TestC1Leakage(unittest.TestCase):
    def test_60s_features_are_declared_post_entry(self):
        for c in LEAKY_60S:
            self.assertIn(c, POST_ENTRY_FEATURES, f"{c} debe ser post-entrada")

    def test_60s_features_are_not_entry_safe(self):
        for c in LEAKY_60S:
            self.assertNotIn(c, ENTRY_SAFE_FEATURES, f"{c} no debe ser entry-safe")

    def test_guard_rejects_60s_features(self):
        df = pd.DataFrame({c: [1.0, 2.0] for c in LEAKY_60S})
        with self.assertRaises(ValueError):
            FeatureAvailabilityGuard.validate(df, stage="c1_leakage")

    def test_ef_features_remain_entry_safe(self):
        for c in CAUSAL_EF:
            self.assertIn(c, ENTRY_SAFE_FEATURES, f"{c} debe seguir entry-safe")

    def test_master_signal_uses_causal_ef_fallbacks(self):
        # Offline (tras el purge) solo existen las ef_*: la rama C debe seguir
        # siendo alcanzable SIN depender de las *_60s (que ya no se mergean).
        dec = MasterSnipeSignal.evaluate({
            "mc_at_entry_est": 8000.0,
            "ef_n_buys": 20,
            "ef_n_sells": 5,
            "ef_unique_buyers": 45,
            "ef_curve_progress": 0.30,
        })
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.branch, "MASTER_ALPHA_CONFLUENCE_MOMENTUM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
