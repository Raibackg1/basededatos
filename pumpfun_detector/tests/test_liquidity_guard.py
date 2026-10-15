"""
Tests de LiquidityGuard (veto de entrada por liquidez / slippage de salida).

Verifica: modelo de slippage decreciente, veto fail-closed ante liquidez
desconocida, umbrales, y determinismo.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.liquidity_guard import LiquidityGuard


class TestLiquidityGuard(unittest.TestCase):
    def setUp(self):
        self.guard = LiquidityGuard(min_liquidity_sol=15.0, max_exit_slippage_pct=35.0)

    def test_unknown_liquidity_blocks(self):
        """Fail-closed: sin saber la liquidez, no se entra."""
        for bad in (None, 0.0, -5.0, float("nan")):
            v = self.guard.evaluate(bad)
            self.assertFalse(v.allowed)
            self.assertEqual(v.reason, "LIQUIDITY_UNKNOWN")

    def test_low_liquidity_blocks(self):
        v = self.guard.evaluate(8.0)
        self.assertFalse(v.allowed)
        self.assertTrue(v.reason.startswith("LIQUIDITY_BELOW_MIN"))

    def test_sufficient_liquidity_allows(self):
        v = self.guard.evaluate(60.0)
        self.assertTrue(v.allowed)
        self.assertEqual(v.reason, "OK")

    def test_slippage_monotonically_decreases(self):
        prev = None
        for liq in (1, 5, 15, 30, 60, 200, 1000):
            s = self.guard.model_exit_slippage_pct(liq)
            if prev is not None:
                self.assertLessEqual(s, prev)
            prev = s

    def test_slippage_bounded_reasonable(self):
        """El slippage modelado queda en un rango plausible (~3% a ~33%)."""
        self.assertLess(self.guard.model_exit_slippage_pct(1e6), 5.0)
        self.assertGreater(self.guard.model_exit_slippage_pct(0.1), 25.0)

    def test_boundary_liquidity_at_min(self):
        """En el umbral exacto se permite (>=)."""
        v = self.guard.evaluate(15.0)
        self.assertTrue(v.allowed)

    def test_config_and_determinism(self):
        c = self.guard.config()
        self.assertEqual(c["min_liquidity_sol"], 15.0)
        a = self.guard.evaluate(40.0).as_dict()
        b = self.guard.evaluate(40.0).as_dict()
        self.assertEqual(a, b)

    def test_negative_thresholds_clamped(self):
        g = LiquidityGuard(min_liquidity_sol=-10.0, max_exit_slippage_pct=-1.0)
        self.assertEqual(g.config()["min_liquidity_sol"], 0.0)
        self.assertEqual(g.config()["max_exit_slippage_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
