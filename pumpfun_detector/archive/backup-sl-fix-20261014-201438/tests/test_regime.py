"""Tests del clasificador de régimen de mercado (detector/regime.py).

Verifica:
- UNKNOWN con menos de min_samples
- EXPANDING con winrate >= umbral_expanding
- CONTRACTING con winrate <= umbral_contracting
- NEUTRAL en la zona intermedia
- suggested_confidence_scale por régimen
- should_suspend_entries solo en CONTRACTING
- Ignora NaN/inf
- Umbral inválido lanza ValueError
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.regime import MarketRegime, RegimeClassifier


class TestRegimeClassifier(unittest.TestCase):

    def _classifier(self, **kw):
        defaults = dict(window=50, min_samples=10,
                        umbral_expanding=0.55, umbral_contracting=0.35)
        defaults.update(kw)
        return RegimeClassifier(**defaults)

    def test_unknown_below_min_samples(self):
        c = self._classifier(min_samples=10)
        for _ in range(9):
            c.record_outcome(pnl_r=0.5)
        snap = c.snapshot()
        self.assertEqual(snap.regime, MarketRegime.UNKNOWN)
        self.assertEqual(snap.n, 9)

    def test_expanding_when_winrate_high(self):
        c = self._classifier(min_samples=10)
        # 12 wins de 12 -> wr=1.0 >= 0.55
        for _ in range(12):
            c.record_outcome(pnl_r=0.5)
        self.assertEqual(c.regime, MarketRegime.EXPANDING)
        self.assertAlmostEqual(c.suggested_confidence_scale, 1.0)

    def test_contracting_when_winrate_low(self):
        c = self._classifier(min_samples=10)
        # 12 losses de 12 -> wr=0.0 <= 0.35
        for _ in range(12):
            c.record_outcome(pnl_r=-0.5)
        self.assertEqual(c.regime, MarketRegime.CONTRACTING)
        self.assertAlmostEqual(c.suggested_confidence_scale, 0.5)
        self.assertTrue(c.should_suspend_entries())

    def test_neutral_between_thresholds(self):
        c = self._classifier(min_samples=10)
        # 6 wins, 6 losses -> wr=0.5 (entre 0.35 y 0.55)
        for _ in range(6):
            c.record_outcome(pnl_r=0.5)
        for _ in range(6):
            c.record_outcome(pnl_r=-0.5)
        self.assertEqual(c.regime, MarketRegime.NEUTRAL)
        self.assertAlmostEqual(c.suggested_confidence_scale, 0.8)
        self.assertFalse(c.should_suspend_entries())

    def test_win_override_takes_precedence(self):
        c = self._classifier(min_samples=10)
        # pnl negativo pero win=True forzado -> cuenta como win
        for _ in range(12):
            c.record_outcome(pnl_r=-0.1, win=True)
        self.assertEqual(c.regime, MarketRegime.EXPANDING)

    def test_nan_inf_ignored(self):
        c = self._classifier(min_samples=3)
        c.record_outcome(pnl_r=float("nan"))
        c.record_outcome(pnl_r=float("inf"))
        c.record_outcome(pnl_r=float("-inf"))
        c.record_outcome(pnl_r="no_numero")
        self.assertEqual(c.n, 0)
        self.assertEqual(c.regime, MarketRegime.UNKNOWN)

    def test_invalid_thresholds_raise(self):
        with self.assertRaises(ValueError):
            RegimeClassifier(umbral_expanding=0.3, umbral_contracting=0.6)
        with self.assertRaises(ValueError):
            RegimeClassifier(umbral_expanding=0.5, umbral_contracting=0.5)

    def test_window_limits_memory(self):
        c = self._classifier(window=5, min_samples=3)
        for i in range(20):
            c.record_outcome(pnl_r=1.0 if i < 15 else -1.0)
        # Solo los ultimos 5 quedan (todos losses)
        self.assertEqual(c.n, 5)
        self.assertEqual(c.regime, MarketRegime.CONTRACTING)

    def test_snapshot_dict_serializable(self):
        c = self._classifier(min_samples=2)
        c.record_outcome(pnl_r=0.5)
        c.record_outcome(pnl_r=-0.2)
        d = c.snapshot().as_dict()
        self.assertIn("regime", d)
        self.assertIsInstance(d["regime"], str)
        self.assertIn("winrate", d)
        self.assertIn("n", d)


if __name__ == "__main__":
    unittest.main()
