"""Tests del almacén de decisiones en vivo (detector/live_store.py).

Cubren el loop de ganancia real medible:
- Escritura idempotente de decisiones (no duplica por re-evaluar el token).
- Unión de outcomes reales (PnL) a decisiones.
- Auditoría neta sobre outcomes resueltos (nunca sobre la etiqueta sintética).
- Aislamiento por procedencia (P0): 'live' jamás se mezcla con 'synthetic'.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.live_store import LiveStore, _decision_sig


class _FakeDecision:
    """Stub de MasterSignalDecision para no acoplar el test al motor."""
    def __init__(self, admitted, branch, confidence=0.8,
                 expected_winrate=0.6, expected_profit_factor=2.0,
                 veto_reason=None):
        self.admitted = admitted
        self.branch = branch
        self.confidence = confidence
        self.expected_winrate = expected_winrate
        self.expected_profit_factor = expected_profit_factor
        self.veto_reason = veto_reason


class TestLiveStore(unittest.TestCase):

    def _store(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)  # que lo cree LiveStore con su esquema
        st = LiveStore(path)

        def _cleanup():
            st.close()  # en Windows hay que cerrar la conexión antes de borrar
            if os.path.exists(path):
                os.remove(path)

        self.addCleanup(_cleanup)
        return st

    def test_decision_sig_is_stable_and_distinct(self):
        a = _decision_sig("MINT1", "BRANCH_A", True)
        b = _decision_sig("MINT1", "BRANCH_A", True)
        c = _decision_sig("MINT1", "BRANCH_A", False)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_record_decision_is_idempotent(self):
        st = self._store()
        feats = {"mc_at_entry_est": 8000.0, "ef_curve_progress": 0.3}
        dec = _FakeDecision(True, "MASTER_ALPHA_CONFLUENCE_MOMENTUM")
        id1 = st.record_decision("MINTX", feats, dec, ts=1000.0)
        id2 = st.record_decision("MINTX", feats, dec, ts=2000.0)  # mismo estado
        self.assertEqual(id1, id2)               # no duplica
        self.assertEqual(st.count_decisions(), 1)

    def test_record_distinct_branches_creates_rows(self):
        st = self._store()
        st.record_decision("MINTY", {}, _FakeDecision(True, "BRANCH_A"), ts=1.0)
        st.record_decision("MINTY", {}, _FakeDecision(True, "BRANCH_B"), ts=2.0)
        self.assertEqual(st.count_decisions(), 2)

    def test_outcome_resolution_and_net_audit(self):
        st = self._store()
        # 3 decisiones admitidas, 2 ganan y 1 pierde
        outcomes = [("A", 50.0), ("B", 30.0), ("C", -25.0)]
        for mint, pnl in outcomes:
            did = st.record_decision(mint, {}, _FakeDecision(True, "BRANCH_A"))
            st.record_outcome(did, pnl_pct=pnl, pnl_sol=pnl / 100.0)
        aud = st.net_audit(origin="live")
        self.assertEqual(aud["n"], 3)
        self.assertAlmostEqual(aud["winrate"], 2 / 3, places=5)
        # PF = (0.5+0.3) / 0.25 = 3.2
        self.assertAlmostEqual(aud["profit_factor"], 3.2, places=5)
        self.assertGreater(aud["total_pnl_sol"], 0.0)

    def test_net_audit_ignores_unresolved_decisions(self):
        st = self._store()
        st.record_decision("A", {}, _FakeDecision(True, "BRANCH_A"))  # sin outcome
        aud = st.net_audit(origin="live")
        self.assertEqual(aud["n"], 0)  # no resuelto -> no cuenta en la métrica
        self.assertEqual(aud["winrate"], 0.0)

    def test_provenance_isolation_live_vs_synthetic(self):
        st = self._store()
        # decisión 'live' con outcome positivo
        d1 = st.record_decision("LIVE1", {"data_origin": "live"},
                                _FakeDecision(True, "BRANCH_A"))
        st.record_outcome(d1, pnl_sol=1.0)
        # decisión 'synthetic' con outcome negativo (no debe contaminar 'live')
        d2 = st.record_decision("SYN1", {"data_origin": "synthetic"},
                                _FakeDecision(True, "BRANCH_A"))
        st.record_outcome(d2, pnl_sol=-5.0)
        live = st.net_audit(origin="live")
        syn = st.net_audit(origin="synthetic")
        self.assertEqual(live["n"], 1)
        self.assertGreater(live["total_pnl_sol"], 0.0)   # +1.0 limpio
        self.assertEqual(syn["n"], 1)
        self.assertLess(syn["total_pnl_sol"], 0.0)       # -5.0 aislado

    def test_resolve_latest_attaches_to_admitted_decision(self):
        st = self._store()
        st.record_decision("Z", {}, _FakeDecision(False, "VETO"), ts=1.0)
        st.record_decision("Z", {}, _FakeDecision(True, "BRANCH_A"), ts=2.0)
        did = st.resolve_latest("Z", pnl_sol=0.4, admitted_only=True)
        self.assertIsNotNone(did)
        aud = st.net_audit(origin="live")
        self.assertEqual(aud["n"], 1)

    def test_net_audit_empty_returns_zeros_not_crash(self):
        st = self._store()
        aud = st.net_audit(origin="live")
        self.assertEqual(aud["n"], 0)
        self.assertEqual(aud["profit_factor"], 0.0)


class TestNetAuditGroups(unittest.TestCase):
    """EV neto por rama, bucket de liquidez y franja horaria (P2)."""

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

    def _add(self, st, branch, feats, pnl_sol, ts):
        did = st.record_decision(f"M{ts}{branch}", feats, _FakeDecision(True, branch), ts=ts)
        st.record_outcome(did, pnl_sol=pnl_sol, pnl_pct=pnl_sol * 100.0)

    def test_group_by_branch_matches_branch_audit(self):
        st = self._store()
        self._add(st, "BR_A", {}, 0.5, 1000.0)
        self._add(st, "BR_A", {}, -0.2, 1001.0)
        self._add(st, "BR_B", {}, 1.0, 1002.0)
        grouped = st.net_audit_by("branch")
        by_branch = st.net_audit_by_branch()
        gm = {g["value"]: g for g in grouped}
        for b in by_branch:
            self.assertIn(b["branch"], gm)
            self.assertEqual(gm[b["branch"]]["n"], b["n"])
            self.assertAlmostEqual(gm[b["branch"]]["total_pnl_sol"], b["total_pnl_sol"], places=9)

    def test_group_by_liquidity_buckets(self):
        st = self._store()
        # liquidez baja (3 SOL) con perdida, liquidez alta (40 SOL) con ganancia
        self._add(st, "BR_A", {"real_sol_reserves": 3.0}, -0.4, 2000.0)
        self._add(st, "BR_B", {"real_sol_reserves": 40.0}, 0.8, 2001.0)
        self._add(st, "BR_C", {}, 0.1, 2002.0)  # sin liquidez -> unknown
        grouped = {g["value"]: g for g in st.net_audit_by("liquidity")}
        self.assertIn("<5 SOL", grouped)
        self.assertIn("30-100 SOL", grouped)
        self.assertIn("unknown", grouped)
        self.assertEqual(grouped["<5 SOL"]["n"], 1)
        self.assertLess(grouped["<5 SOL"]["total_pnl_sol"], 0.0)
        self.assertGreater(grouped["30-100 SOL"]["total_pnl_sol"], 0.0)
        self.assertEqual(grouped["unknown"]["n"], 1)

    def test_group_by_liquidity_accepts_reserve_aliases(self):
        st = self._store()
        self._add(st, "BR_A", {"liquidity_sol": 10.0}, 0.2, 3000.0)
        self._add(st, "BR_B", {"sol_reserves": 20.0}, 0.2, 3001.0)
        grouped = {g["value"]: g for g in st.net_audit_by("liquidity")}
        self.assertEqual(grouped["5-15 SOL"]["n"], 1)
        self.assertEqual(grouped["15-30 SOL"]["n"], 1)

    def test_group_by_hour_franjas_utc(self):
        st = self._store()
        # 2026-01-01 05:00 UTC -> franja 04-08 ; 13:00 UTC -> 12-16
        ts_am = 1767243600.0  # 2026-01-01T05:00Z
        ts_pm = 1767272400.0  # 2026-01-01T13:00Z
        self._add(st, "BR_A", {}, -0.3, ts_am)
        self._add(st, "BR_B", {}, 0.7, ts_pm)
        grouped = {g["value"]: g for g in st.net_audit_by("hour")}
        self.assertIn("04-08", grouped)
        self.assertIn("12-16", grouped)
        self.assertEqual(grouped["04-08"]["n"], 1)
        self.assertLess(grouped["04-08"]["total_pnl_sol"], 0.0)
        self.assertGreater(grouped["12-16"]["total_pnl_sol"], 0.0)

    def test_group_empty_store_returns_empty_list(self):
        st = self._store()
        self.assertEqual(st.net_audit_by("liquidity"), [])
        self.assertEqual(st.net_audit_by("hour"), [])

    def test_group_invalid_raises(self):
        st = self._store()
        with self.assertRaises(ValueError):
            st.net_audit_by("wat")


class TestBranchExitPolicy(unittest.TestCase):
    """Policy de salida por rama derivada de MFE/MAE reales registrados."""

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

    def test_outcome_stores_mfe_mae(self):
        st = self._store()
        did = st.record_decision("M1", {}, _FakeDecision(True, "BR_A"))
        st.record_outcome(did, pnl_sol=0.1, pnl_pct=10.0, mfe_pct=48.0, mae_pct=-12.0)
        row = st.get_outcome(did)
        self.assertAlmostEqual(row["mfe_pct"], 48.0)
        self.assertAlmostEqual(row["mae_pct"], -12.0)

    def test_branch_exit_policy_medians_and_suggestions(self):
        st = self._store()
        data = [
            ("A1", 40.0, -10.0), ("A2", 60.0, -20.0), ("A3", 80.0, -30.0),
        ]
        for mint, mfe, mae in data:
            did = st.record_decision(mint, {}, _FakeDecision(True, "BR_A"))
            st.record_outcome(did, pnl_sol=0.1, pnl_pct=10.0, mfe_pct=mfe, mae_pct=mae)
        # Sin mfe/mae etiquetado -> no aparece
        did = st.record_decision("B1", {}, _FakeDecision(True, "BR_B"))
        st.record_outcome(did, pnl_sol=0.1, pnl_pct=10.0)
        pol = st.branch_exit_policy()
        polm = {p["branch"]: p for p in pol}
        self.assertIn("BR_A", polm)
        self.assertNotIn("BR_B", polm)
        a = polm["BR_A"]
        self.assertEqual(a["n_labeled"], 3)
        self.assertAlmostEqual(a["median_mfe_pct"], 60.0)
        self.assertAlmostEqual(a["median_mae_pct"], -20.0)
        # captura conservadora: TP = 50% de la MFE mediana, SL = -|MAE mediana|
        self.assertAlmostEqual(a["suggested_take_profit_pct"], 30.0)
        self.assertAlmostEqual(a["suggested_stop_pct"], -20.0)
        self.assertTrue(a["sufficient"])  # n >= 3

    def test_branch_exit_policy_flags_insufficient_samples(self):
        st = self._store()
        did = st.record_decision("A1", {}, _FakeDecision(True, "BR_A"))
        st.record_outcome(did, pnl_sol=0.0, pnl_pct=0.0, mfe_pct=25.0, mae_pct=-5.0)
        pol = st.branch_exit_policy(min_labeled=3)
        self.assertEqual(len(pol), 1)
        self.assertFalse(pol[0]["sufficient"])

    def test_branch_exit_policy_origin_isolation(self):
        st = self._store()
        for i in range(3):
            did = st.record_decision(f"S{i}", {"data_origin": "synthetic"},
                                     _FakeDecision(True, "BR_A"))
            st.record_outcome(did, pnl_sol=-1.0, pnl_pct=-100.0, mfe_pct=5.0, mae_pct=-99.0)
        for i in range(3):
            did = st.record_decision(f"L{i}", {"data_origin": "live"},
                                     _FakeDecision(True, "BR_A"))
            st.record_outcome(did, pnl_sol=1.0, pnl_pct=100.0, mfe_pct=90.0, mae_pct=-3.0)
        pol_live = st.branch_exit_policy(origin="live")
        self.assertEqual(pol_live[0]["n_labeled"], 3)
        self.assertAlmostEqual(pol_live[0]["median_mfe_pct"], 90.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
