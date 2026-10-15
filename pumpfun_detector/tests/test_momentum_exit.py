"""
Tests de MomentumStallExit (guarda anti-TIMEOUT) y su integración en PaperBroker.

Verifica:
  - La guarda solo decretó salida tras vivir la ventana, con ticks suficientes
    y sin momentum (drawup bajo). Fail-safe ante datos degenerados.
  - PaperBroker con momentum_exit ON cierra trades sin momentum como
    "momentum_stall"; OFF no altera el comportamiento existente.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.momentum_exit import MomentumStallExit, EXIT_MOMENTUM_STALL
from detector.paper_broker import PaperBroker
from detector.live_store import LiveStore
from detector.costs import ExecutionCostModel


class _Decision:
    def __init__(self, admitted=True, branch="BRANCH_A", confidence=0.9):
        self.admitted = admitted
        self.branch = branch
        self.confidence = confidence
        self.veto_reason = None
        self.metrics = {}


class TestMomentumStallExit(unittest.TestCase):
    def setUp(self):
        self.gate = MomentumStallExit(stall_window_s=90.0, min_drawup_pct=1.0, min_ticks=3)

    def test_no_momentum_after_window_exits(self):
        d = self.gate.evaluate(entry_price=1.0, peak_price=1.003, held_s=95.0, ticks_seen=8)
        self.assertTrue(d.exit_now)
        self.assertEqual(d.reason, "MOMENTUM_STALL")

    def test_window_not_elapsed_never_exits(self):
        d = self.gate.evaluate(entry_price=1.0, peak_price=1.0, held_s=30.0, ticks_seen=10)
        self.assertFalse(d.exit_now)
        self.assertEqual(d.reason, "WINDOW_NOT_ELAPSED")

    def test_insufficient_ticks_never_exits(self):
        d = self.gate.evaluate(entry_price=1.0, peak_price=1.0, held_s=200.0, ticks_seen=1)
        self.assertFalse(d.exit_now)
        self.assertEqual(d.reason, "INSUFFICIENT_TICKS")

    def test_with_momentum_never_exits(self):
        d = self.gate.evaluate(entry_price=1.0, peak_price=1.05, held_s=200.0, ticks_seen=10)
        self.assertFalse(d.exit_now)
        self.assertEqual(d.reason, "HAS_MOMENTUM")

    def test_exactly_at_threshold_has_momentum(self):
        # drawup == min_drawup_pct -> se considera vivo (>=), no sale.
        d = self.gate.evaluate(entry_price=1.0, peak_price=1.01, held_s=100.0, ticks_seen=5)
        self.assertFalse(d.exit_now)
        self.assertEqual(d.reason, "HAS_MOMENTUM")

    def test_fail_safe_on_invalid_entry(self):
        d = self.gate.evaluate(entry_price=0.0, peak_price=0.0, held_s=200.0, ticks_seen=9)
        self.assertFalse(d.exit_now)
        self.assertEqual(d.reason, "INVALID_INPUT")

    def test_fail_safe_on_nan(self):
        d = self.gate.evaluate(entry_price=float("nan"), peak_price=1.0,
                               held_s=200.0, ticks_seen=9)
        self.assertFalse(d.exit_now)
        self.assertEqual(d.reason, "INVALID_INPUT")

    def test_config_roundtrip(self):
        c = self.gate.config()
        self.assertEqual(c["stall_window_s"], 90.0)
        self.assertEqual(c["min_drawup_pct"], 1.0)
        self.assertEqual(c["min_ticks"], 3)

    def test_negative_thresholds_clamped_to_safe(self):
        """Hardening: umbrales negativos no deben convertir la guarda en cierre masivo."""
        g = MomentumStallExit(stall_window_s=-50.0, min_drawup_pct=-10.0, min_ticks=0)
        self.assertEqual(g.config()["stall_window_s"], 0.0)
        self.assertEqual(g.config()["min_drawup_pct"], 0.0)
        self.assertEqual(g.config()["min_ticks"], 1)


class TestMomentumExitInBroker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = LiveStore(os.path.join(self.tmp, "m.db"))
        self.costs = ExecutionCostModel()

    def tearDown(self):
        self.store.close()

    def _feats(self, mint="ABC", price=1.0):
        return {"mint": mint, "entry_price": price, "realSolReserves": 30.0}

    def test_broker_off_is_default(self):
        b = PaperBroker(live_store=self.store, cost_model=self.costs)
        self.assertIsNone(b.momentum_exit)

    def test_dead_trade_closed_as_momentum_stall(self):
        gate = MomentumStallExit(stall_window_s=50.0, min_drawup_pct=1.0, min_ticks=3)
        b = PaperBroker(live_store=self.store, cost_model=self.costs,
                        timeout_s=300.0, momentum_exit=gate)
        b.on_signal(self._feats(), _Decision(), ts=0.0)
        # Precio plano (sin momentum). Tras superar ventana con ticks, debe cerrar.
        closed = None
        ts = 10.0
        for _ in range(8):  # 8 ticks * 10s = 80s > 50s
            closed = b.on_price("ABC", 1.0, ts)
            ts += 10.0
            if closed is not None:
                break
        self.assertIsNotNone(closed)
        self.assertEqual(closed.exit_type, EXIT_MOMENTUM_STALL)

    def test_live_trade_not_closed_by_momentum(self):
        gate = MomentumStallExit(stall_window_s=50.0, min_drawup_pct=1.0, min_ticks=3)
        b = PaperBroker(live_store=self.store, cost_model=self.costs,
                        timeout_s=300.0, momentum_exit=gate)
        b.on_signal(self._feats(), _Decision(), ts=0.0)
        closed = None
        ts = 10.0
        for _ in range(8):
            closed = b.on_price("ABC", 1.20, ts)  # +20%: momentum sano
            ts += 10.0
            if closed is not None:
                break
        # Puede cerrar por TP (alcanza +30%? no, +20%) — pero NUNCA por momentum_stall.
        if closed is not None:
            self.assertNotEqual(closed.exit_type, EXIT_MOMENTUM_STALL)

    def test_momentum_stall_costs_like_stall_not_tp(self):
        """momentum_stall debe costearse con haircut de stall, no como ganancia."""
        gate = MomentumStallExit(stall_window_s=10.0, min_drawup_pct=1.0, min_ticks=2)
        b = PaperBroker(live_store=self.store, cost_model=self.costs,
                        timeout_s=1000.0, momentum_exit=gate)
        b.on_signal(self._feats(), _Decision(), ts=0.0)
        closed = b.on_price("ABC", 1.0, 15.0) or b.on_price("ABC", 1.0, 30.0)
        self.assertIsNotNone(closed)
        self.assertEqual(closed.exit_type, EXIT_MOMENTUM_STALL)
        # PnL neto debe ser NEGATIVO (haircut + fees), no positivo como un TP.
        self.assertLess(closed.net_pnl_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
