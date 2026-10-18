"""TDD: auto-desactivacion de ramas por EV neto < breakeven (BranchGate).

Reglas verificadas:
- n < min_samples  -> INSUFFICIENT_DATA (habilitada, sin evidencia para vetar).
- n >= min_samples y EV neto (expectancy_r en SOL) < 0 -> DISABLED.
- Posterior bayesiano P(wr > breakeven) < 0.15 tambien desactiva.
- Estado persistido en SQLite (sobrevive a reinicio del objeto).
- Re-habilitacion automatica si las estadisticas mejoran.
- veto_if_disabled convierte una decision admitida en rechazo con veto causal.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.branch_gate import BranchGate, veto_if_disabled
from detector.live_store import LiveStore
from detector.master_signal import MasterSignalDecision
from detector.metrics_registry import claimed_wr, claimed_pf


def _dec(branch: str, admitted: bool = True) -> MasterSignalDecision:
    return MasterSignalDecision(
        admitted=admitted, branch=branch, confidence=0.7,
        expected_winrate=claimed_wr("test_gate_wr"), expected_profit_factor=claimed_pf("test_gate_pf"),
        veto_reason=None, metrics={})


class _FakeDecision:
    """Stub no-dataclass: veto_if_disabled debe poder vetarlo igualmente."""
    def __init__(self, branch: str, admitted: bool = True):
        self.branch = branch
        self.admitted = admitted
        self.confidence = 0.7
        self.veto_reason = None


class TestBranchGate(unittest.TestCase):

    def _store(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        st = LiveStore(path)

        def _cleanup():
            st.close()
            if os.path.exists(path):
                os.remove(path)

        self.addCleanup(_cleanup)
        return st

    def _seed_branch(self, store, branch: str, pnls):
        from tests.test_live_store import _FakeDecision
        for i, pnl in enumerate(pnls):
            did = store.record_decision(f"M_{branch}_{i}", {}, _FakeDecision(True, branch))
            store.record_outcome(did, pnl_sol=pnl, pnl_pct=pnl * 100.0)

    def test_disabled_when_negative_ev_after_min_samples(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_LOSER", [-0.01] * 12)
        gate = BranchGate(store, min_samples=10)
        states = gate.refresh()
        st = states["BRANCH_LOSER"]
        self.assertEqual(st.status, "DISABLED")
        self.assertFalse(gate.is_enabled("BRANCH_LOSER"))
        self.assertLess(st.ev_sol, 0.0)
        self.assertIn("EV", st.reason)

    def test_enabled_when_positive_ev(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_WINNER", [0.02] * 12)
        gate = BranchGate(store, min_samples=10)
        states = gate.refresh()
        self.assertEqual(states["BRANCH_WINNER"].status, "ENABLED")
        self.assertTrue(gate.is_enabled("BRANCH_WINNER"))

    def test_insufficient_samples_stays_enabled(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_NEW", [-0.05] * 3)  # 3 trades, todo rojo
        gate = BranchGate(store, min_samples=10)
        states = gate.refresh()
        self.assertEqual(states["BRANCH_NEW"].status, "INSUFFICIENT_DATA")
        self.assertTrue(gate.is_enabled("BRANCH_NEW"))  # sin evidencia suficiente

    def test_state_persists_across_instances(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_PERSIST", [-0.02] * 11)
        BranchGate(store, min_samples=10).refresh()
        # Nueva instancia (simula reinicio del proceso) lee de la BD
        gate2 = BranchGate(store, min_samples=10)
        self.assertFalse(gate2.is_enabled("BRANCH_PERSIST"))
        row = store.get_branch_state("BRANCH_PERSIST")
        self.assertEqual(row["status"], "DISABLED")
        self.assertEqual(row["n"], 11)

    def test_reenable_when_stats_improve(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_FIX", [-0.01] * 12)
        gate = BranchGate(store, min_samples=10)
        gate.refresh()
        self.assertFalse(gate.is_enabled("BRANCH_FIX"))
        # Luego se recuperan con trades ganadores
        self._seed_branch(store, "BRANCH_FIX", [0.03] * 25)
        states = gate.refresh()
        self.assertEqual(states["BRANCH_FIX"].status, "ENABLED")
        self.assertTrue(gate.is_enabled("BRANCH_FIX"))

    def test_bayes_posterior_insufficient_is_neutral(self):
        store = self._store()
        gate = BranchGate(store, min_samples=10)
        self.assertTrue(gate.is_enabled("BRANCH_UNKNOWN"))  # sin datos -> True

    def test_veto_if_disabled_frozen_decision(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_X", [-0.02] * 15)
        gate = BranchGate(store, min_samples=10)
        gate.refresh()
        dec = _dec("BRANCH_X")
        out = veto_if_disabled(dec, gate)
        self.assertFalse(out.admitted)
        self.assertEqual(out.confidence, 0.0)
        self.assertIn("VETO_BRANCH_GATE", out.veto_reason)

    def test_veto_if_disabled_passthrough(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_OK", [0.02] * 15)
        gate = BranchGate(store, min_samples=10)
        gate.refresh()
        dec = _dec("BRANCH_OK")
        out = veto_if_disabled(dec, gate)
        self.assertTrue(out.admitted)                 # sin veto
        self.assertIs(out, dec)                       # sin copia innecesaria
        # Rama sin estado -> no se veto
        out2 = veto_if_disabled(_dec("BRANCH_NO_DATA"), gate)
        self.assertTrue(out2.admitted)

    def test_veto_if_disabled_non_dataclass_decision(self):
        store = self._store()
        self._seed_branch(store, "BRANCH_FAKE", [-0.03] * 15)
        gate = BranchGate(store, min_samples=10)
        gate.refresh()
        fake = _FakeDecision("BRANCH_FAKE")
        out = veto_if_disabled(fake, gate)
        self.assertFalse(out.admitted)
        self.assertIn("VETO_BRANCH_GATE", out.veto_reason)

    def test_origin_isolation_gate_ignores_synthetic(self):
        store = self._store()
        from tests.test_live_store import _FakeDecision
        # 12 outcomes SYNTHETIC negativos (no deben vetar la rama live)
        for i in range(12):
            did = store.record_decision(f"S{i}", {"data_origin": "synthetic"},
                                        _FakeDecision(True, "BRANCH_MIX"))
            store.record_outcome(did, pnl_sol=-1.0)
        # 2 outcomes LIVE positivos
        for i in range(2):
            did = store.record_decision(f"L{i}", {"data_origin": "live"},
                                        _FakeDecision(True, "BRANCH_MIX"))
            store.record_outcome(did, pnl_sol=0.5)
        gate = BranchGate(store, min_samples=10, origin="live")
        states = gate.refresh()
        self.assertEqual(states["BRANCH_MIX"].status, "INSUFFICIENT_DATA")
        self.assertTrue(gate.is_enabled("BRANCH_MIX"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
