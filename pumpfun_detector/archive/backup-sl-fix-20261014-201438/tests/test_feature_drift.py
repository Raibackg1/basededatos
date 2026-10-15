"""TDD: drift de FEATURES con PSI y KS (complementa CUSUM sobre PnL).

- PSI identico entre distribuciones -> bajo; distribucion desplazada -> alto.
- KS sobre samples identicos -> p alto; desplazados -> p ~ 0.
- FeatureDriftMonitor en streaming: alerta solo con min_samples y sobre
  features presentes en la referencia (no inventa features nuevas).
- Integracion con DriftMonitor: el trip del circuit breaker ocurre por drift
  de features, no solo por racha de perdidas.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.feature_drift import FeatureDriftMonitor, ks_2sample, psi
from detector.drift_monitor import DriftMonitor, CircuitBreakerState


def _norm(rng, loc, scale, n):
    return rng.normal(loc, scale, n)


class TestPsiKs(unittest.TestCase):

    def test_psi_same_distribution_is_small(self):
        rng = np.random.default_rng(7)
        ref = _norm(rng, 0.0, 1.0, 4000)
        same = _norm(rng, 0.0, 1.0, 4000)
        self.assertLess(psi(ref, same), 0.10)

    def test_psi_shifted_distribution_is_large(self):
        rng = np.random.default_rng(7)
        ref = _norm(rng, 0.0, 1.0, 4000)
        shifted = _norm(rng, 4.0, 1.0, 4000)
        self.assertGreater(psi(ref, shifted), 0.25)

    def test_ks_same_distribution_not_significant(self):
        rng = np.random.default_rng(11)
        ref = _norm(rng, 0.0, 1.0, 3000)
        same = _norm(rng, 0.0, 1.0, 3000)
        r = ks_2sample(ref, same)
        self.assertGreater(r["p_value"], 0.01)

    def test_ks_shifted_distribution_significant(self):
        rng = np.random.default_rng(11)
        ref = _norm(rng, 0.0, 1.0, 3000)
        shifted = _norm(rng, 3.0, 1.0, 3000)
        r = ks_2sample(ref, shifted)
        self.assertLess(r["p_value"], 0.01)
        self.assertGreater(r["statistic"], 0.3)


class TestFeatureDriftMonitor(unittest.TestCase):

    def _ref(self, n=600):
        rng = np.random.default_rng(3)
        return {
            "liquidity_sol": list(_norm(rng, 20.0, 4.0, n)),
            "vpin": list(np.clip(_norm(rng, 0.25, 0.06, n), 0.0, 1.0)),
        }

    def test_no_drift_no_alert(self):
        rng = np.random.default_rng(4)
        mon = FeatureDriftMonitor(self._ref(), min_samples=50)
        for v in _norm(rng, 20.0, 4.0, 120):
            alerts = mon.update({"liquidity_sol": v})
        drifted = [a for a in alerts if a.drifted]
        self.assertEqual(drifted, [])

    def test_drifted_feature_raises_alert(self):
        rng = np.random.default_rng(4)
        mon = FeatureDriftMonitor(self._ref(), min_samples=50)
        alerts = []
        for v in _norm(rng, 90.0, 10.0, 120):  # liquidez se dispara -> drift
            alerts = mon.update({"liquidity_sol": v})
        drifted = [a for a in alerts if a.drifted]
        self.assertTrue(drifted)
        self.assertEqual(drifted[0].feature, "liquidity_sol")
        self.assertGreater(drifted[0].psi, 0.25)
        self.assertLess(drifted[0].ks_p_value, 0.01)

    def test_min_samples_blocks_early_alerts(self):
        mon = FeatureDriftMonitor(self._ref(), min_samples=50)
        alerts = []
        for v in [500.0] * 10:  # 10 muestras bastantes
            alerts = mon.update({"liquidity_sol": v})
        self.assertEqual([a for a in alerts if a.drifted], [])

    def test_unknown_feature_ignored(self):
        mon = FeatureDriftMonitor(self._ref(), min_samples=10)
        for i in range(30):
            alerts = mon.update({"feature_inventada": float(i)})
        self.assertEqual(alerts, [])


class TestDriftMonitorIntegration(unittest.TestCase):

    def test_feature_drift_trips_circuit_breaker(self):
        rng = np.random.default_rng(9)
        ref = {"liquidity_sol": list(_norm(rng, 20.0, 3.0, 600))}
        dm = DriftMonitor(min_sample=5)
        dm.attach_feature_drift(FeatureDriftMonitor(ref, min_samples=50))
        # Racha de wins: NO deberia tripear por CUSUM
        for i in range(60):
            dm.record_trade_outcome(pnl_pct=1.0, is_win=True)
        self.assertEqual(dm.state, CircuitBreakerState.CLOSED)
        # Feature driftado -> debe tripear aunque todo era verde
        alerts = []
        for v in _norm(rng, 120.0, 15.0, 80):
            alerts = dm.record_feature_values({"liquidity_sol": v})
        self.assertTrue(any(a.get("drifted") for a in alerts))
        self.assertEqual(dm.state, CircuitBreakerState.OPEN)

    def test_without_attachment_record_feature_values_is_noop(self):
        dm = DriftMonitor()
        self.assertEqual(dm.record_feature_values({"x": 1.0}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
