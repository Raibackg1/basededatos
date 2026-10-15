"""
Tests de ScalingGate (kill-criteria de escalado, informe §8.4).

Verifica que el gate es FAIL-CLOSED: ante métricas ausentes o deficientes,
bloquea el escalado. Cubre cada criterio duro y los opcionales.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.scaling_gate import ScalingGate


def _audit(resolved=120, winrate=0.80, pf=3.5, pnl=12.5):
    return {
        "resolved": resolved, "winrate": winrate,
        "profit_factor": pf, "total_pnl_sol": pnl,
    }


class TestScalingGate(unittest.TestCase):
    def test_all_criteria_met_allows(self):
        v = ScalingGate().evaluate(_audit())
        self.assertTrue(v.allowed)
        self.assertEqual(v.blocked_reasons, ())

    def test_missing_audit_blocks(self):
        v = ScalingGate().evaluate(None)
        self.assertFalse(v.allowed)
        self.assertIn("MISSING_AUDIT_METRICS", v.blocked_reasons)

    def test_missing_key_blocks(self):
        a = _audit(); del a["profit_factor"]
        v = ScalingGate().evaluate(a)
        self.assertFalse(v.allowed)
        self.assertTrue(any("profit_factor" in r for r in v.blocked_reasons))

    def test_insufficient_trades_blocks(self):
        v = ScalingGate(min_forward_trades=100).evaluate(_audit(resolved=45))
        self.assertFalse(v.allowed)
        self.assertTrue(any("INSUFFICIENT_FORWARD_TRADES" in r for r in v.blocked_reasons))

    def test_low_winrate_blocks(self):
        v = ScalingGate(min_winrate=0.65).evaluate(_audit(winrate=0.1255))
        self.assertFalse(v.allowed)
        self.assertTrue(any("WINRATE_BELOW_MIN" in r for r in v.blocked_reasons))

    def test_low_profit_factor_blocks(self):
        v = ScalingGate(min_profit_factor=1.30).evaluate(_audit(pf=0.2687))
        self.assertFalse(v.allowed)
        self.assertTrue(any("PROFIT_FACTOR_BELOW_MIN" in r for r in v.blocked_reasons))

    def test_negative_pnl_blocks(self):
        v = ScalingGate(require_positive_pnl=True).evaluate(_audit(pnl=-79770.96))
        self.assertFalse(v.allowed)
        self.assertTrue(any("NON_POSITIVE_NET_PNL" in r for r in v.blocked_reasons))

    def test_infinite_pf_allowed_when_positive(self):
        v = ScalingGate().evaluate(_audit(pf=float("inf"), pnl=5.0))
        self.assertTrue(v.allowed)

    def test_real_breakeven_data_blocks(self):
        """Reproduzco el estado REAL de la base canónica: debe bloquear."""
        v = ScalingGate().evaluate(
            _audit(resolved=5021, winrate=0.1255, pf=0.2687, pnl=-79770.96))
        self.assertFalse(v.allowed)
        # Falla winrate, profit factor y PnL a la vez.
        self.assertGreaterEqual(len(v.blocked_reasons), 3)

    def test_optional_fill_rate(self):
        g = ScalingGate(min_fill_rate=0.80)
        self.assertTrue(g.evaluate(_audit(), fill_rate=0.85).allowed)
        self.assertFalse(g.evaluate(_audit(), fill_rate=0.50).allowed)

    def test_optional_drawdown(self):
        g = ScalingGate(max_drawdown_pct=20.0)
        self.assertTrue(g.evaluate(_audit(), drawdown_pct=12.0).allowed)
        self.assertFalse(g.evaluate(_audit(), drawdown_pct=35.0).allowed)

    def test_optional_slippage_ratio(self):
        g = ScalingGate(max_slippage_ratio=1.2)
        self.assertTrue(g.evaluate(_audit(), slippage_real_p90=0.30,
                                   slippage_model_p90=0.30).allowed)
        self.assertFalse(g.evaluate(_audit(), slippage_real_p90=0.60,
                                    slippage_model_p90=0.30).allowed)

    def test_slippage_unavailable_blocks_when_configured(self):
        g = ScalingGate(max_slippage_ratio=1.2)
        v = g.evaluate(_audit(), slippage_real_p90=None, slippage_model_p90=0.3)
        self.assertFalse(v.allowed)
        self.assertIn("SLIPPAGE_METRICS_UNAVAILABLE", v.blocked_reasons)

    def test_as_dict_serializable(self):
        d = ScalingGate().evaluate(_audit()).as_dict()
        self.assertIn("allowed", d)
        self.assertIn("criteria", d)
        self.assertTrue(d["allowed"])

    def test_deterministic(self):
        g = ScalingGate()
        a = _audit(pf=0.9)
        self.assertEqual(g.evaluate(a).blocked_reasons, g.evaluate(a).blocked_reasons)

    def test_infinite_winrate_blocks(self):
        """Hardening: winrate=inf es absurdo; el gate fail-closed lo bloquea."""
        v = ScalingGate().evaluate(_audit(winrate=float("inf")))
        self.assertFalse(v.allowed)
        self.assertTrue(any("WINRATE_BELOW_MIN" in r for r in v.blocked_reasons))

    def test_infinite_pnl_blocks(self):
        """Hardening: pnl=inf es absurdo; se bloquea aunque supere el min."""
        v = ScalingGate().evaluate(_audit(pnl=float("inf")))
        self.assertFalse(v.allowed)
        self.assertTrue(any("NON_POSITIVE_NET_PNL" in r for r in v.blocked_reasons))

    def test_negative_infinite_pnl_blocks(self):
        v = ScalingGate().evaluate(_audit(pnl=float("-inf")))
        self.assertFalse(v.allowed)


if __name__ == "__main__":
    unittest.main()
