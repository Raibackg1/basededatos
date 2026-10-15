"""Tests: branch_exit_policy como consumidor real en PaperBroker.

Verifica que PaperBroker usa TP/SL adaptativos por rama cuando hay
suficientes trades etiquetados con MFE/MAE reales, y que se refresca
periódicamente para incorporar nueva evidencia.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.live_store import LiveStore
from detector.paper_broker import PaperBroker
from detector.costs import CostModel


class _FakeDecision:
    def __init__(self, branch="BRANCH_TEST", admitted=True, confidence=0.8):
        self.admitted = admitted
        self.branch = branch
        self.confidence = confidence
        self.expected_winrate = 0.5
        self.expected_profit_factor = 1.0
        self.veto_reason = None


def _seed_branch_outcomes(store, branch="BRANCH_TEST", n=6,
                          mfe_values=None, mae_values=None):
    """Inserta n decisions + outcomes con MFE/MAE conocidos para una rama."""
    if mfe_values is None:
        mfe_values = [30.0, 40.0, 50.0, 45.0, 35.0, 55.0]
    if mae_values is None:
        mae_values = [-20.0, -25.0, -30.0, -15.0, -22.0, -28.0]
    for i in range(n):
        dec = _FakeDecision(branch=branch)
        did = store.record_decision(
            mint=f"tok{i}", feats={"mc": 5000 + i}, decision=dec, ts=1000.0 + i)
        store.record_outcome(
            did, pnl_pct=5.0, pnl_sol=0.05, exit_reason="tp",
            mfe_pct=mfe_values[i % len(mfe_values)],
            mae_pct=mae_values[i % len(mae_values)])


class TestBranchExitPolicyWiring(unittest.TestCase):

    def test_policy_loaded_when_sufficient(self):
        """Con suficientes MFE/MAE, on_signal usa TP/SL de la policy."""
        store = LiveStore(":memory:")
        _seed_branch_outcomes(store, n=6)
        broker = PaperBroker(store, CostModel(), use_branch_exit_policy=True,
                             policy_min_labeled=5)
        dec = _FakeDecision(branch="BRANCH_TEST")
        did = broker.on_signal(
            {"mint": "newtok", "entry_price": 100.0}, dec, ts=2000.0)
        self.assertIsNotNone(did)
        pos = broker._open["newtok"]
        # Mediana MFE de [30,35,40,45,50,55] = 42.5; capture 0.5 -> 21.25
        # branch_exit_policy redondea a 1 decimal -> 21.2
        # Mediana MAE de [-15,-20,-22,-25,-28,-30] = -23.5; SL = -23.5%
        self.assertAlmostEqual(pos.take_profit_pct, 21.2, places=1)
        self.assertAlmostEqual(pos.stop_loss_pct, -23.5, places=1)

    def test_policy_not_applied_when_insufficient(self):
        """Sin datos suficientes, usa TP/SL estáticos del constructor."""
        store = LiveStore(":memory:")
        # Solo 2 outcomes -> insuficiente para min_labeled=5
        _seed_branch_outcomes(store, n=2)
        broker = PaperBroker(store, CostModel(), use_branch_exit_policy=True,
                             policy_min_labeled=5)
        dec = _FakeDecision(branch="BRANCH_TEST")
        did = broker.on_signal(
            {"mint": "newtok", "entry_price": 100.0}, dec, ts=2000.0)
        self.assertIsNotNone(did)
        pos = broker._open["newtok"]
        self.assertAlmostEqual(pos.take_profit_pct, 0.30, places=6)
        self.assertAlmostEqual(pos.stop_loss_pct, -0.35, places=6)

    def test_policy_disabled(self):
        """use_branch_exit_policy=False ignora la policy."""
        store = LiveStore(":memory:")
        _seed_branch_outcomes(store, n=6)
        broker = PaperBroker(store, CostModel(), use_branch_exit_policy=False)
        dec = _FakeDecision(branch="BRANCH_TEST")
        broker.on_signal({"mint": "newtok", "entry_price": 100.0}, dec, ts=2000.0)
        pos = broker._open["newtok"]
        self.assertAlmostEqual(pos.take_profit_pct, 0.30, places=6)

    def test_policy_refreshes_after_interval(self):
        """Tras policy_refresh_interval cierres, se recargan las policies."""
        store = LiveStore(":memory:")
        _seed_branch_outcomes(store, n=6)
        broker = PaperBroker(store, CostModel(), use_branch_exit_policy=True,
                             policy_min_labeled=5, policy_refresh_interval=3)
        # Abrir y cerrar 3 posiciones para disparar el refresh
        for i in range(4):
            mint = f"tokx{i}"
            dec = _FakeDecision(branch="BRANCH_TEST")
            broker.on_signal({"mint": mint, "entry_price": 100.0}, dec,
                             ts=2000.0 + i)
            # Precio con profit -> cierra por TP
            broker.on_price(mint, 200.0, 2001.0 + i)
        # policies deben haberse refrescado al menos una vez
        self.assertGreater(broker._closes_since_policy_refresh, 0)
        # Y la policy de BRANCH_TEST debe estar cacheada
        self.assertIn("BRANCH_TEST", broker._branch_policies)

    def test_adaptive_tp_actually_triggers_earlier(self):
        """TP adaptativo cierra antes que el estático si la evidencia lo indica."""
        store = LiveStore(":memory:")
        # MFE bajo (~15%) -> TP adaptativo ~7.5%, mucho menor que 30% estático
        mfe = [14.0, 16.0, 15.0, 13.0, 17.0, 15.5]
        mae = [-10.0, -12.0, -11.0, -9.0, -13.0, -10.5]
        _seed_branch_outcomes(store, n=6, mfe_values=mfe, mae_values=mae)
        broker = PaperBroker(store, CostModel(), use_branch_exit_policy=True,
                             policy_min_labeled=5)
        dec = _FakeDecision(branch="BRANCH_TEST")
        broker.on_signal({"mint": "tokadapt", "entry_price": 100.0}, dec, ts=2000.0)
        # Precio +10%: con TP adaptativo (~7.5%) ya cierra; con 30% no.
        broker.on_price("tokadapt", 110.0, 2005.0)
        self.assertEqual(len(broker._closed), 1)
        self.assertEqual(broker._closed[0].exit_type, "tp")

    def test_no_store_no_crash(self):
        """Sin store, no intenta cargar policies y no crashea."""
        broker = PaperBroker(None, CostModel(), use_branch_exit_policy=True)
        dec = _FakeDecision(branch="BRANCH_TEST")
        broker.on_signal({"mint": "tok", "entry_price": 100.0}, dec, ts=1.0)
        # Sin store no hay decision_id, pero la posición abre con TP/SL estáticos.
        self.assertIn("tok", broker._open)
        pos = broker._open["tok"]
        self.assertAlmostEqual(pos.take_profit_pct, 0.30, places=6)


if __name__ == "__main__":
    unittest.main()
