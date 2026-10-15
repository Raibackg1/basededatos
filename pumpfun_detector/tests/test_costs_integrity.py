"""C5 / C6 / C7 — Integridad del modelo de salidas y de costes.

- C5: empate TP/SL conservador y fill del SL al precio OBSERVADO (no al nivel ideal).
- C6: una sola fuente de fees (antes 0.008 / 0.004 / 5.64% plano en 3 módulos).
- C7: ningún slippage hardcodeado presentado como medición.
"""
import inspect
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector import advanced_engine as ae
from detector.costs import (
    DEFAULT_COSTS,
    PLATFORM_FEE_ROUNDTRIP_FRAC,
    ROUNDTRIP_TX_FEE_SOL,
)
from detector.labels import _tpsl_outcome


class TestC5ExitFills(unittest.TestCase):
    def test_sl_fills_at_observed_price_not_idealized_level(self):
        # La vela salta el SL: el precio pasa de 1.0 a 0.5 (-50%) cuando el SL
        # nominal es -30%. El fill real es 0.5, NO el nivel ideal 0.70.
        path = (np.array([0.0, 1.0, 2.0]), np.array([1.0, 1.1, 0.5]))
        res, _t, p_exit = _tpsl_outcome(path, 0.0, 1.0, 0.5, 0.3, 100.0)
        self.assertEqual(res, "loss")
        self.assertAlmostEqual(p_exit, 0.5, places=6)

    def test_tp_still_fills_at_level(self):
        # TP es una orden límite: se llena en el nivel, no por encima.
        path = (np.array([0.0, 1.0]), np.array([1.0, 2.0]))
        res, _t, p_exit = _tpsl_outcome(path, 0.0, 1.0, 0.5, 0.3, 100.0)
        self.assertEqual(res, "win")
        self.assertAlmostEqual(p_exit, 1.5, places=6)

    def test_timeout_when_neither_hit(self):
        path = (np.array([0.0, 1.0]), np.array([1.0, 1.05]))
        res, _t, _p = _tpsl_outcome(path, 0.0, 1.0, 0.5, 0.3, 100.0)
        self.assertEqual(res, "timeout")


class TestC6UnifiedCosts(unittest.TestCase):
    def test_single_roundtrip_fee_constant(self):
        # 2 * (0.002 priority + 0.002 network) = 0.008 SOL
        self.assertAlmostEqual(ROUNDTRIP_TX_FEE_SOL, 0.008, places=9)
        self.assertAlmostEqual(DEFAULT_COSTS.roundtrip_tx_fee_sol(), 0.008, places=9)

    def test_advanced_engine_uses_the_same_constant(self):
        # Antes advanced_engine usaba 0.004 (solo priority x2): la mitad.
        self.assertAlmostEqual(ae.REAL_TX_FEE_SOL, ROUNDTRIP_TX_FEE_SOL, places=9)

    def test_platform_fee_is_a_single_field(self):
        self.assertAlmostEqual(PLATFORM_FEE_ROUNDTRIP_FRAC, 0.035, places=9)

    def test_offline_simulation_charges_more_than_tx_fees(self):
        # El simulador offline debe cobrar red + plataforma (no solo red).
        path = (np.array([0.0, 1.0]), np.array([1.0, 1.6]))
        res = ae.realistic_tpsl_outcome(path, 0.0, 1.0, 0.5, 0.3, 100.0,
                                        realistic=True, position_size_sol=0.5)
        self.assertEqual(res["result"], "win")
        self.assertGreater(res["fee_sol"], ROUNDTRIP_TX_FEE_SOL)
        self.assertAlmostEqual(res["fee_sol"],
                               ROUNDTRIP_TX_FEE_SOL + 0.5 * PLATFORM_FEE_ROUNDTRIP_FRAC,
                               places=9)


class TestC7NoHardcodedSlippage(unittest.TestCase):
    def test_loss_learner_has_no_literal_5645(self):
        import detector.loss_learner as ll
        src = inspect.getsource(ll)
        self.assertNotIn("56.45", src)

    def test_pattern_identifier_has_no_literal_56(self):
        import detector.pattern_identifier as pi
        src = inspect.getsource(pi)
        self.assertNotIn("-56%", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
