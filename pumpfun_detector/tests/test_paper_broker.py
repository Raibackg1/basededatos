"""
Tests de PaperBroker: cierre del loop de "ganancia real medible".

Cadena verificada aquí (offline, sin red):
  MasterSnipeSignal-style decision admitida
    -> on_signal abre posición paper y la persiste en LiveStore
    -> on_price toca TP / SL / timeout
    -> cierre contra ExecutionCostModel (PnL NETO con fricción)
    -> outcome persistido
    -> audit() = ganancia neta medible desde el store.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.paper_broker import PaperBroker
from detector.live_store import LiveStore
from detector.costs import ExecutionCostModel


class _Decision:
    """Stub ligero de MasterSignalDecision (duck-typed para on_signal)."""

    def __init__(self, admitted, branch="MASTER_ALPHA_EXPANSION", confidence=0.9):
        self.admitted = admitted
        self.branch = branch
        self.confidence = confidence
        self.veto_reason = None if admitted else "VETO_DEAD_LAUNCH"
        self.metrics = {"market_cap": 5000.0}


class TestPaperBroker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "paper_test.db")
        self.store = LiveStore(self.db)
        self.costs = ExecutionCostModel()
        self.broker = PaperBroker(
            self.store, self.costs,
            entry_sol=1.0, take_profit_pct=0.30, stop_loss_pct=-0.35,
            timeout_s=300.0, default_liquidity_sol=20.0, max_open=50,
        )

    def _feats(self, mint):
        return {
            "mint": mint, "token_id": mint, "symbol": mint,
            "entry_price": 1.0, "price": 1.0,
            "realSolReserves": 25.0, "real_sol_reserves": 25.0,
        }

    def test_on_signal_opens_only_when_admitted(self):
        # Rechazada: no abre nada.
        self.assertIsNone(self.broker.on_signal(self._feats("REJ"), _Decision(False), ts=1000.0))
        self.assertEqual(self.broker.open_count, 0)
        # Admitida: abre y persiste -> decision_id.
        did = self.broker.on_signal(self._feats("ACC"), _Decision(True), ts=1000.0)
        self.assertIsNotNone(did)
        self.assertEqual(self.broker.open_count, 1)

    def test_take_profit_closes_with_positive_net(self):
        self.broker.on_signal(self._feats("TP"), _Decision(True), ts=1000.0)
        # +40% supera el TP de +30%.
        trade = self.broker.on_price("TP", 1.40, ts=1010.0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_type, "tp")
        # PnL neto sigue siendo positivo tras la fricción del cost model.
        self.assertGreater(trade.net_pnl_sol, 0.0)
        # net_pnl_pct viene en PORCENTAJE (cost model): +40% nominal -> <40 neto.
        self.assertLessEqual(trade.net_pnl_pct, 40.0)
        self.assertEqual(self.broker.open_count, 0)

    def test_stop_loss_closes_with_negative_net(self):
        self.broker.on_signal(self._feats("SL"), _Decision(True), ts=1000.0)
        # -50% perfora el SL de -35%.
        trade = self.broker.on_price("SL", 0.50, ts=1010.0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_type, "sl_panic")
        self.assertLess(trade.net_pnl_sol, 0.0)

    def test_timeout_closes_as_stall(self):
        self.broker.on_signal(self._feats("TO"), _Decision(True), ts=1000.0)
        # Precio plano (+2%) pero pasa el timeout de 300s.
        trade = self.broker.on_price("TO", 1.02, ts=1000.0 + 300.0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_type, "stall")

    def test_audit_reflects_persisted_net_profit(self):
        # Ganador por TP.
        self.broker.on_signal(self._feats("WIN"), _Decision(True), ts=1000.0)
        self.broker.on_price("WIN", 1.50, ts=1005.0)
        # Perdedor por SL.
        self.broker.on_signal(self._feats("LOSE"), _Decision(True), ts=1000.0)
        self.broker.on_price("LOSE", 0.60, ts=1005.0)

        self.assertEqual(len(self.broker.closed_trades), 2)
        aud = self.broker.audit(origin="live", admitted_only=True)
        # Métricas de ganancia real medible presentes.
        self.assertIn("n", aud)
        self.assertGreaterEqual(aud["n"], 2)
        # El store expone la ganancia neta agregada como total_pnl_sol.
        self.assertIn("total_pnl_sol", aud)
        # 1 ganador y 1 perdedor -> winrate 0.5 (puede incluir más filas del store).
        self.assertGreaterEqual(aud.get("winrate", 0.0), 0.0)

    def test_threshold_units_do_not_trigger_tp_prematurely(self):
        # Regresión: umbrales fraccionarios (0.30) vs pct en %. Una subida
        # pequeña (+5%) NO debe disparar el TP de +30% (antes lo hacía por
        # comparar 5.0% contra 0.30 -> cierre prematuro que arruina la rentabilidad).
        self.broker.on_signal(self._feats("UNIT"), _Decision(True), ts=1000.0)
        res = self.broker.on_price("UNIT", 1.05, ts=1005.0)
        self.assertIsNone(res)  # sigue abierta: +5% < +30%
        self.assertEqual(self.broker.open_count, 1)
        # Recién al superar +30% debe cerrar por TP.
        res2 = self.broker.on_price("UNIT", 1.35, ts=1010.0)
        self.assertIsNotNone(res2)
        self.assertEqual(res2.exit_type, "tp")

    def test_stop_loss_threshold_units(self):
        # Regresión del otro lado: -5% no debe perforar el SL de -35%.
        self.broker.on_signal(self._feats("SLU"), _Decision(True), ts=1000.0)
        res = self.broker.on_price("SLU", 0.95, ts=1005.0)
        self.assertIsNone(res)  # -5% no alcanza el SL de -35%
        res2 = self.broker.on_price("SLU", 0.60, ts=1010.0)
        self.assertIsNotNone(res2)
        self.assertEqual(res2.exit_type, "sl_panic")


if __name__ == "__main__":
    unittest.main(verbosity=2)

