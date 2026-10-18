"""Tests end-to-end del recalibrador de umbrales (P1).

Verifican el loop de ganancia real medible:
    decisiones + outcomes resueltos ──► ThresholdRecalibrator
        ──► barrido por expectancy neta ──► subclase recalibrada

Sin tocar nunca la clase de producción MasterSnipeSignal.
"""
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from detector.live_store import LiveStore
from detector.master_signal import MasterSnipeSignal
from detector.metrics_registry import claimed_wr, claimed_pf
from detector.recalibrator import ThresholdRecalibrator, RecalibrationComparator


def _dec(admitted=True, branch="TEST"):
    """Decisión ligera con el contrato que consume LiveStore.record_decision."""
    return types.SimpleNamespace(
        admitted=admitted, branch=branch, confidence=0.9,
        expected_winrate=claimed_wr("test_recalib_wr"), expected_profit_factor=claimed_pf("test_recalib_pf"), veto_reason=None,
    )


class TestThresholdRecalibrator(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = LiveStore(self.db)
        self._seed()

    def tearDown(self):
        try:
            self.store._con.close()
        except Exception:
            pass
        if os.path.exists(self.db):
            os.remove(self.db)

    def _seed(self):
        """20 trades resueltos: 8 'altos' ganadores (mc>=6000) + 12 'bajos'
        perdedores (mc en [4600,5150]). El umbral de market cap que maximiza
        PnL es 6000 (aisla a todos los ganadores), interior al grid de
        candidatos [3000..8000], no un extremo.
        """
        # buenos: mc 6000..6700, pnl +2
        for i in range(8):
            mint = f"hi_{i:02d}"
            mc = 6000.0 + i * 100.0
            did = self.store.record_decision(
                mint, {"mc_at_entry_est": mc, "data_origin": "live"}, _dec())
            self.store.record_outcome(did, pnl_pct=5.0, pnl_sol=2.0,
                                      exit_reason="TP")
        # malos: mc 4600..5150, pnl -1
        for i in range(12):
            mint = f"lo_{i:02d}"
            mc = 4600.0 + i * 50.0
            did = self.store.record_decision(
                mint, {"mc_at_entry_est": mc, "data_origin": "live"}, _dec())
            self.store.record_outcome(did, pnl_pct=-2.0, pnl_sol=-1.0,
                                      exit_reason="SL")

    def test_load_resolved_reconstructs_features_and_pnl(self):
        rec = ThresholdRecalibrator(self.store, origin="live", min_n=3)
        df = rec.load_resolved()
        self.assertEqual(len(df), 20)
        self.assertIn("mc_at_entry_est", df.columns)
        # PnL agregado: 8*2 - 12*1 = +4
        self.assertAlmostEqual(float(df["_pnl_sol"].sum()), 4.0, places=6)

    def test_calibrate_threshold_finds_interior_optimum(self):
        rec = ThresholdRecalibrator(self.store, origin="live", min_n=3)
        best = rec.calibrate_threshold("MIN_MARKET_CAP_USD")
        self.assertIsNotNone(best)
        # Umbral 6000 aísla a los 8 ganadores -> +16 (máximo), no en el borde.
        self.assertEqual(best["candidate"], 6000.0)
        self.assertEqual(best["n"], 8)
        self.assertAlmostEqual(best["total_pnl"], 16.0, places=6)
        self.assertFalse(best["at_boundary"])

    def test_build_recalibrated_signal_returns_subclass_with_override(self):
        rec = ThresholdRecalibrator(self.store, origin="live", min_n=3)
        cls, changes = rec.build_recalibrated_signal()
        # Subclase real, producción intacta.
        self.assertTrue(issubclass(cls, MasterSnipeSignal))
        self.assertEqual(MasterSnipeSignal.MIN_MARKET_CAP_USD, 4500.0)
        # El umbral recalibrado queda aplicado en la subclase.
        self.assertEqual(cls.MIN_MARKET_CAP_USD, 6000.0)
        attr_changed = {c["attr"]: c for c in changes}
        self.assertIn("MIN_MARKET_CAP_USD", attr_changed)
        self.assertAlmostEqual(attr_changed["MIN_MARKET_CAP_USD"]["recalibrated"],
                               6000.0, places=6)

    def test_recalibrated_subclass_behaviourally_vetoes_low_mc(self):
        rec = ThresholdRecalibrator(self.store, origin="live", min_n=3)
        cls, _ = rec.build_recalibrated_signal()
        feats = {"mc_at_entry_est": 5000.0, "n_buys_60s": 30}
        # Producción (umbral 4500): NO veta por dead launch.
        dec_prod = MasterSnipeSignal.evaluate(dict(feats))
        self.assertNotIn("DEAD_LAUNCH", dec_prod.veto_reason or "")
        # Recalibrado (umbral 6000): 5000 < 6000 -> VETO_DEAD_LAUNCH.
        dec_new = cls.evaluate(dict(feats))
        self.assertIn("DEAD_LAUNCH", dec_new.veto_reason or "")

    def test_min_n_guard_prevents_overfit_change(self):
        # Exigiendo más muestra de la disponible, no se recalibra nada.
        rec = ThresholdRecalibrator(self.store, origin="live", min_n=50)
        cls, changes = rec.build_recalibrated_signal()
        self.assertEqual(changes, [])
        self.assertEqual(cls.MIN_MARKET_CAP_USD, 4500.0)

    def test_origin_isolation_ignores_other_provenance(self):
        # Recalibrar sobre 'synthetic' no debe ver los datos 'live'.
        rec = ThresholdRecalibrator(self.store, origin="synthetic", min_n=1)
        df = rec.load_resolved()
        self.assertEqual(len(df), 0)
        self.assertIsNone(rec.calibrate_threshold("MIN_MARKET_CAP_USD"))


class TestRecalibrationComparator(unittest.TestCase):
    """Comparativa producción vs recalibrada sobre outcomes REALES resueltos.

    El seed activa la rama A de producción (firstMarketCap<=4285 y
    firstLiquidity<=1725) para que haya trades realmente admitidos y, por
    tanto, flips auditables cuando la recalibrada sube el piso de market cap.
    """

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = LiveStore(self.db)
        self._seed()
        # min_n bajo para que el barrido acepte el óptimo (6000) con la muestra.
        self.cmp = RecalibrationComparator(self.store, origin="live", min_n=3)

    def tearDown(self):
        try:
            self.store._con.close()
        except Exception:
            pass
        if os.path.exists(self.db):
            os.remove(self.db)

    def _seed(self):
        # 8 ganadores (mc 6000..6700, pnl +2) y 12 perdedores (mc 4600..5150,
        # pnl -1). Todos con firstMarketCap/firstLiquidity de rama A -> la señal
        # de producción los admite; el barrido óptimo de MIN_MARKET_CAP_USD es
        # 6000 (aísla a los 8 ganadores, +16), que es interior al grid.
        for i in range(8):
            mint = f"hi_{i:02d}"
            feats = {"mc_at_entry_est": 6000.0 + i * 100.0,
                     "firstMarketCap": 4000.0, "firstLiquidity": 1500.0,
                     "data_origin": "live"}
            did = self.store.record_decision(mint, feats, _dec())
            self.store.record_outcome(did, pnl_pct=5.0, pnl_sol=2.0, exit_reason="TP")
        for i in range(12):
            mint = f"lo_{i:02d}"
            feats = {"mc_at_entry_est": 4600.0 + i * 50.0,
                     "firstMarketCap": 4000.0, "firstLiquidity": 1500.0,
                     "data_origin": "live"}
            did = self.store.record_decision(mint, feats, _dec())
            self.store.record_outcome(did, pnl_pct=-2.0, pnl_sol=-1.0, exit_reason="SL")

    def test_production_admits_all_branch_a(self):
        # Sin recalibrar, la producción debe admitir los 20 por rama A.
        feats = {"mc_at_entry_est": 5000.0, "firstMarketCap": 4000.0,
                 "firstLiquidity": 1500.0}
        dec = MasterSnipeSignal.evaluate(feats)
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.branch, "MASTER_ALPHA_EXPANSION")

    def test_compare_quantifies_losses_avoided(self):
        res = self.cmp.compare()
        self.assertEqual(res["n_resolved"], 20)
        # baseline = PnL real de lo ejecutado por producción = 8*2 - 12*1 = +4
        self.assertAlmostEqual(res["baseline_pnl_sol"], 4.0, places=6)
        # recalibrada (MIN_MC=6000) solo deja pasar los 8 ganadores -> +16
        self.assertAlmostEqual(res["recalibrated_pnl_sol"], 16.0, places=6)
        self.assertAlmostEqual(res["delta_pnl_sol"], 12.0, places=6)
        # Los 12 perdedores que producción ejecutó y la recalibrada veta.
        self.assertEqual(res["n_loss_avoided"], 12)
        self.assertEqual(res["n_gain_missed"], 0)
        self.assertAlmostEqual(res["losses_avoided_sol"], 12.0, places=6)
        self.assertAlmostEqual(res["gains_missed_sol"], 0.0, places=6)
        # Cada flip documenta el veto de la recalibrada sobre un perdedor real.
        self.assertEqual(len(res["flips"]), 12)
        self.assertTrue(all(f["pnl_sol"] < 0 for f in res["flips"]))
        self.assertTrue(all(f["recal_veto"] and "DEAD_LAUNCH" in f["recal_veto"]
                            for f in res["flips"]))

    def test_compare_empty_without_resolved_trades(self):
        # Un store sin outcomes no tiene nada que comparar (no inventa PnL).
        fd, empty_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = LiveStore(empty_db)
            res = RecalibrationComparator(store, origin="live").compare()
            self.assertEqual(res["n_resolved"], 0)
            self.assertEqual(res["delta_pnl_sol"], 0.0)
            self.assertIn("note", res)
            store._con.close()
        finally:
            if os.path.exists(empty_db):
                os.remove(empty_db)


if __name__ == "__main__":
    unittest.main(verbosity=2)
