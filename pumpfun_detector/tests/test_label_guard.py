"""Tests del guard anti-fuga de etiquetas (label causality guard).

Reproduce el caso real que produjo el falso "96.8% de winrate": una etiqueta
derivada de `rise50At` (desenlace del ciclo de vida del token) debe fallar.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.label_guard import (
    FutureLeakageError,
    assert_causal_label,
    assert_no_future_features,
    is_derived_outcome,
    is_lifecycle_future,
    validate_causal_label,
)


class TestLifecycleDetection(unittest.TestCase):
    def test_lifecycle_future_columns_are_flagged(self):
        for col in ("rise50At", "rise100At", "peakPriceUsd", "peakAt", "pumpAthMcap",
                    "deadAt", "graduatedAt", "graduated", "complete", "status",
                    "reason", "drawup_pct", "minutesInTrade"):
            self.assertTrue(is_lifecycle_future(col), col)

    def test_entry_time_columns_are_not_lifecycle(self):
        for col in ("firstMarketCap", "firstLiquidity", "market_cap", "realSolReserves",
                    "buysM5", "sellsM5", "devBalancePct", "holderCount", "entry_price"):
            self.assertFalse(is_lifecycle_future(col), col)

    def test_derived_outcome_columns(self):
        for col in ("pnlPct", "pnl_sol", "exitPrice", "exitTs", "mfe_pct", "mae_pct",
                    "win_real", "fut_ret_5m", "future_ret_5m", "tpsl_timeout", "hold_s"):
            self.assertTrue(is_derived_outcome(col), col)
        self.assertFalse(is_derived_outcome("entry_price"))


class TestCausalLabel(unittest.TestCase):
    def test_rise50_label_is_rejected(self):
        # EL CASO REAL: etiqueta derivada de rise50At.
        with self.assertRaises(FutureLeakageError):
            assert_causal_label("hit_50", derived_from=["rise50At"])

    def test_label_named_after_lifecycle_column_is_rejected(self):
        with self.assertRaises(FutureLeakageError):
            assert_causal_label("rise50At")

    def test_trade_outcome_label_is_allowed(self):
        # Un label SÍ puede derivar del resultado del propio trade.
        assert_causal_label("win_tp_sl", derived_from=["entry_price", "exit_price"])
        assert_causal_label("win_real_net", derived_from=["pnl_sol"])

    def test_validate_returns_diagnostic(self):
        v = validate_causal_label("hit_50", derived_from=["rise50At", "peakPriceUsd"])
        self.assertFalse(v["ok"])
        self.assertIn("rise50at", [c.lower() for c in v["offending"]])
        ok = validate_causal_label("win_tp_sl", derived_from=["exit_price"])
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["offending"], [])


class TestFutureFeatures(unittest.TestCase):
    def test_features_with_future_columns_are_rejected(self):
        with self.assertRaises(FutureLeakageError):
            assert_no_future_features(["market_cap", "rise50At", "pnl_sol"])

    def test_clean_features_pass(self):
        assert_no_future_features(
            ["market_cap", "firstMarketCap", "firstLiquidity", "buysM5", "realSolReserves"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
