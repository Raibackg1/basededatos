"""Tests del gate de promoción del barrido causal (OOS honesto).

Fijan la lección medida en la base real: una winrate alta NO basta. Las mejores
reglas causales encontradas tenían WR OOS 36.8% pero EV OOS −16.8% y PF 0.405:
más aciertos, pero perdiendo dinero. El gate debe rechazarlas.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.causal_sweep import select_promoted, wilson_ci


def _rule(n, ci_low, ev, wr=50.0):
    return {"rule": "x", "oos_n": n, "oos_ci_low": ci_low, "oos_ev": ev, "oos_wr": wr}


class TestWilsonCI(unittest.TestCase):
    def test_interval_brackets_proportion(self):
        lo, hi = wilson_ci(30, 100)
        self.assertLess(lo, 0.30)
        self.assertGreater(hi, 0.30)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)

    def test_zero_n_is_degenerate(self):
        self.assertEqual(wilson_ci(0, 0), (0.0, 0.0))

    def test_more_samples_tighten_interval(self):
        lo_small, hi_small = wilson_ci(30, 100)
        lo_big, hi_big = wilson_ci(300, 1000)
        self.assertLess(hi_big - lo_big, hi_small - lo_small)


class TestPromotionGate(unittest.TestCase):
    BASE = 19.4  # baseline OOS WR observado

    def test_high_winrate_but_negative_ev_is_rejected(self):
        # El caso real: WR alto (36.8%) pero EV negativo -> NO es edge.
        self.assertEqual(
            select_promoted([_rule(n=38, ci_low=23.38, ev=-16.77, wr=36.84)],
                            min_oos_n=30, baseline_oos_wr=self.BASE),
            [])

    def test_small_sample_is_rejected(self):
        self.assertEqual(
            select_promoted([_rule(n=7, ci_low=40.0, ev=25.0)],
                            min_oos_n=30, baseline_oos_wr=self.BASE),
            [])

    def test_ci_not_above_baseline_is_rejected(self):
        self.assertEqual(
            select_promoted([_rule(n=200, ci_low=18.0, ev=10.0)],
                            min_oos_n=30, baseline_oos_wr=self.BASE),
            [])

    def test_good_rule_is_promoted(self):
        good = _rule(n=200, ci_low=28.0, ev=12.5)
        self.assertEqual(
            select_promoted([good], min_oos_n=30, baseline_oos_wr=self.BASE),
            [good])


if __name__ == "__main__":
    unittest.main(verbosity=2)
