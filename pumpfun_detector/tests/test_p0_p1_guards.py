import os
import sys
import unittest
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detector.data_integrity import FeatureAvailabilityError, FeatureAvailabilityGuard
from detector.execution_guards import QuoteGate, LatencyBudget, safe_position_size
from detector.temporal_validation import temporal_splits, rule_is_production_ready
from detector.master_signal import MasterSnipeSignal


class TestP0P1Guards(unittest.TestCase):
    def test_forbids_future_features(self):
        with self.assertRaises(FeatureAvailabilityError):
            FeatureAvailabilityGuard.validate({"price": 1, "future_ret_5": 20})

    def test_rejects_unavailable_timestamp(self):
        with self.assertRaises(FeatureAvailabilityError):
            FeatureAvailabilityGuard.validate({"available_at_ms": 2000}, entry_time=1.0)

    def test_filter_frame_drops_outcomes(self):
        got = FeatureAvailabilityGuard.filter_frame(pd.DataFrame({"x": [1], "pnl_pct": [2], "available_at_ms": [1000], "entry_time": [2]}))
        self.assertNotIn("pnl_pct", got.columns)
        self.assertEqual(len(got), 1)

    def test_temporal_splits_are_ordered_with_gap(self):
        df = pd.DataFrame({"t": list(range(20)), "y": list(range(20))})
        s = temporal_splits(df, "t", gap=1)
        self.assertLess(s["train"]["t"].max(), s["validation"]["t"].min())
        self.assertLess(s["validation"]["t"].max(), s["test"]["t"].min())

    def test_rule_requires_structured_oos(self):
        self.assertFalse(rule_is_production_ready({"rule": "x", "support_n": 100}))
        self.assertTrue(rule_is_production_ready({"conditions": [{"feature": "x", "op": "<=", "value": 1}], "support_n": 100, "oos_pass": True, "test_lift_net": 0.2, "data_origin": "historical"}))

    def test_execution_gates_fail_closed(self):
        stale = QuoteGate.evaluate({"quoted_at_ms": 0, "route": "pump", "liquidity_sol": 20}, now_ms=5000)
        self.assertFalse(stale.allowed)
        impact = QuoteGate.evaluate({"quoted_at_ms": 5000, "price_impact_pct": 9, "route": "pump", "liquidity_sol": 20}, now_ms=5000)
        self.assertFalse(impact.allowed)
        self.assertTrue(LatencyBudget.evaluate({"detect": 10, "quote": 20, "confirm": 30}).allowed)
        self.assertFalse(LatencyBudget.evaluate({"detect": 2000}).allowed)

    def test_sizing_can_be_zero(self):
        self.assertEqual(safe_position_size(10, -0.1, 100, 0), 0.0)
        self.assertGreater(safe_position_size(10, 0.1, 10, 0), 0.0)
        self.assertEqual(safe_position_size(10, 0.1, 10, 11), 0.0)

    def test_master_signal_strict_asof_rejects_final_market_cap(self):
        decision = MasterSnipeSignal.evaluate({"market_cap": 9000, "firstMarketCap": 3500,
                                                "firstLiquidity": 1200, "realSolReserves": 20,
                                                "devBalancePct": 0.5, "n_buys_60s": 3}, strict_asof=True)
        self.assertFalse(decision.admitted)
        self.assertIn("WITHOUT_ASOF", decision.veto_reason)


if __name__ == "__main__":
    unittest.main()
