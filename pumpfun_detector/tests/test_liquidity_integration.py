"""
Tests de integración de LiquidityGuard en MasterSnipeSignal.evaluate.

Verifica que el veto de liquidez, cuando se pasa como colaborador opt-in,
rechaza entradas de baja liquidez con VETO_LIQUIDITY_SLIPPAGE, y que SIN el
guard el comportamiento de la señal queda idéntico (opt-in real).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.master_signal import MasterSnipeSignal
from detector.liquidity_guard import LiquidityGuard


def _feats(reserves):
    """Features mínimas que pasan todos los vetos base hasta llegar al de reservas."""
    return {
        "market_cap_at_entry": 20000.0,   # > 4500, < 50000
        "firstMarketCap": 3000.0,          # < 4285 (Rama A)
        "firstLiquidity": 1000.0,
        "realSolReserves": reserves,       # clave para el guard
        "devBalancePct": 0.5,              # < 1.5
        "migrationProgress": 20.0,         # < 45
        "topHolderPct": 40.0,              # < 85
        "holderCount": 100,                # > 40
        "buysM5": 30,                      # > 10
        "sellsM5": 5,                      # < 50
    }


class TestLiquidityGuardIntegration(unittest.TestCase):
    def test_high_liquidity_admits_without_guard(self):
        d = MasterSnipeSignal.evaluate(_feats(50.0))
        self.assertTrue(d.admitted)

    def test_high_liquidity_admits_with_guard(self):
        d = MasterSnipeSignal.evaluate(_feats(50.0), liquidity_guard=LiquidityGuard())
        self.assertTrue(d.admitted)

    def test_low_liquidity_vetoed_with_guard(self):
        # 8 SOL < min 15: el guard debe vetar aunque pase el veto base de 12 SOL.
        d = MasterSnipeSignal.evaluate(_feats(13.0), liquidity_guard=LiquidityGuard())
        self.assertFalse(d.admitted)
        self.assertIn("VETO_LIQUIDITY_SLIPPAGE", d.veto_reason)

    def test_without_guard_low_liquidity_not_vetoed_by_it(self):
        # Opt-in real: sin guard, 13 SOL pasa (el veto base es <12).
        d = MasterSnipeSignal.evaluate(_feats(13.0))
        self.assertTrue(d.admitted)

    def test_guard_metrics_recorded(self):
        d = MasterSnipeSignal.evaluate(_feats(50.0), liquidity_guard=LiquidityGuard())
        self.assertIn("liquidity_exit_slippage_pct", d.metrics)

    def test_missing_liquidity_vetoed_fail_closed(self):
        """Sin campo de liquidez, el guard veta (no deja pasar el default 30)."""
        feats = _feats(50.0)
        del feats["realSolReserves"]
        d = MasterSnipeSignal.evaluate(feats, liquidity_guard=LiquidityGuard())
        self.assertFalse(d.admitted)
        self.assertIn("VETO_LIQUIDITY_SLIPPAGE", d.veto_reason)
        self.assertIn("LIQUIDITY_UNKNOWN", d.veto_reason)

    def test_missing_liquidity_without_guard_unchanged(self):
        """Sin guard, la falta de liquidez no altera el comportamiento previo."""
        feats = _feats(50.0)
        del feats["realSolReserves"]
        d = MasterSnipeSignal.evaluate(feats)
        self.assertTrue(d.admitted)


if __name__ == "__main__":
    unittest.main()
