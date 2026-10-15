"""TDD: slippage como distribucion condicionada por liquidez (no haircut fijo).

- Sin datos aprendidos: fallback determinista a priors documentados (BP-001 /
  BP-010 escalados por bucket de liquidez).
- Con datos reales ajustados: cuantiles condicionales que se ordenan
  correctamente (menos liquidez -> mas slippage) y sustituyen al prior.
- pocas muestras en un bucket -> ese bucket sigue usando el prior.
- ExecutionCostModel.compute_exit_pnl(slippage_model=...) usa la distribucion;
  sin modelo el comportamiento legacy queda byte-idéntico (regresion).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.costs import ConditionalSlippageModel, ExecutionCostModel


class TestConditionalSlippageModel(unittest.TestCase):

    def test_prior_fallback_without_fit(self):
        m = ConditionalSlippageModel()
        # Bucket 5-15 SOL: prior q75 documentado
        v = m.quantile(10.0, 0.75)
        self.assertGreater(v, 0.20)
        self.assertLess(v, 0.50)
        counts = m.learned_counts()
        self.assertTrue(all(c == 0 for c in counts.values()))

    def test_quantiles_monotonic_in_liquidity_after_fit(self):
        m = ConditionalSlippageModel(min_samples_per_bucket=5)
        low_liq = [(2.0, 0.60), (3.0, 0.55), (1.5, 0.70), (4.0, 0.50), (2.5, 0.65), (3.5, 0.58)]
        mid_liq = [(10.0, 0.35), (8.0, 0.40), (12.0, 0.30), (9.0, 0.38), (11.0, 0.33), (7.0, 0.42)]
        high_liq = [(50.0, 0.10), (60.0, 0.08), (45.0, 0.12), (70.0, 0.07), (55.0, 0.09), (80.0, 0.06)]
        m.fit(low_liq + mid_liq + high_liq)
        q_low = m.quantile(3.0, 0.75)
        q_mid = m.quantile(10.0, 0.75)
        q_high = m.quantile(60.0, 0.75)
        self.assertGreater(q_low, q_mid)
        self.assertGreater(q_mid, q_high)
        # Bucket aprendido NO usa el prior
        self.assertNotAlmostEqual(q_mid, ConditionalSlippageModel().quantile(10.0, 0.75), places=6)

    def test_sparse_bucket_keeps_prior(self):
        m = ConditionalSlippageModel(min_samples_per_bucket=5)
        m.fit([(3.0, 0.9), (2.0, 0.8)])  # 2 muestras < 5 -> prior intacto
        self.assertEqual(m.learned_counts()["5-15 SOL"], 0)
        prior = ConditionalSlippageModel().quantile(10.0, 0.90)
        self.assertAlmostEqual(m.quantile(10.0, 0.90), prior, places=9)

    def test_fit_ignores_invalid_samples(self):
        m = ConditionalSlippageModel()
        info = m.fit([(0.0, 0.3), (-5.0, 0.3), (10.0, -0.1), (10.0, 1.5), (10.0, 0.3),
                      (float("nan"), 0.3), (10.0, float("nan"))])
        self.assertEqual(sum(info.values()), 1)  # solo (10.0, 0.3) valido

    def test_quantile_interpolates_between_knots(self):
        m = ConditionalSlippageModel()
        q50 = m.quantile(10.0, 0.50)
        q90 = m.quantile(10.0, 0.90)
        q75 = m.quantile(10.0, 0.75)
        self.assertTrue(q50 <= q75 <= q90)


class TestExitPnlWithSlippageModel(unittest.TestCase):

    def test_sl_panic_uses_conditional_distribution(self):
        costs = ExecutionCostModel()
        model = ConditionalSlippageModel(min_samples_per_bucket=5)
        model.fit([(10.0, 0.40)] * 6)  # bucket 5-15 aprendido en 0.40
        res = costs.compute_exit_pnl(entry_sol=1.0, nominal_exit_pct=-10.0,
                                     exit_type="sl_panic", liquidity_sol=10.0,
                                     slippage_model=model)
        # slippage_applied = q90 del bucket aprendido (0.40)
        self.assertAlmostEqual(res["slippage_applied"], 0.40, places=6)

    def test_default_path_unchanged_without_model(self):
        costs = ExecutionCostModel()
        legacy = costs.compute_exit_pnl(entry_sol=1.0, nominal_exit_pct=-10.0,
                                        exit_type="sl_panic", liquidity_sol=10.0)
        same = costs.compute_exit_pnl(entry_sol=1.0, nominal_exit_pct=-10.0,
                                      exit_type="sl_panic", liquidity_sol=10.0,
                                      slippage_model=None)
        self.assertEqual(legacy, same)  # byte-identico: regresion protegida
        # y el valor legacy sigue siendo el haircut auditado + liq_penalty
        self.assertAlmostEqual(legacy["slippage_applied"],
                               0.3349 + max(0.0, (15.0 - 10.0) / 15.0 * 0.10), places=6)

    def test_stall_uses_median_not_tail(self):
        costs = ExecutionCostModel()
        model = ConditionalSlippageModel(min_samples_per_bucket=5)
        model.fit([(10.0, 0.20), (10.0, 0.22), (10.0, 0.24), (10.0, 0.26),
                   (10.0, 0.28), (10.0, 0.30)])
        res = costs.compute_exit_pnl(entry_sol=1.0, nominal_exit_pct=-5.0,
                                     exit_type="stall", liquidity_sol=10.0,
                                     slippage_model=model)
        # mediana de 6 muestras = (0.24+0.26)/2 = 0.25 -> cola, no capping a 90
        self.assertAlmostEqual(res["slippage_applied"], 0.25, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
