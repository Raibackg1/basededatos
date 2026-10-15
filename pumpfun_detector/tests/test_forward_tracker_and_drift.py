"""
Suite de Pruebas TDD para Seguimiento Forward en Tiempo Real y Detección de Drift.

Cubre:
1. PositionTracker: Apertura, actualización con ticks de precio y resolución de salidas
   (TP, Trailing Stop, Breakeven Lock, Stall Timeout a 60s y SL con slippage realista).
2. LiveStore Integration: Persistencia de decisiones y outcomes resueltos en SQLite.
3. DriftMonitor & CircuitBreaker: Algoritmos CUSUM y Page-Hinkley para detección
   de degradación de winrate y desconexión automática del sizing si EV <= 0.
"""
import os
import sys
import tempfile
import time
import unittest
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from detector.costs import ExecutionQuote
from detector.live_store import LiveStore
from detector.forward_tracker import PositionTracker, TrackedPosition
from detector.drift_monitor import DriftMonitor, CircuitBreakerState
from detector.master_signal import MasterSnipeSignal, MasterSignalDecision


class TestPositionTracker(unittest.TestCase):
    """Pruebas del ciclo de vida de posiciones y salidas dinámicas multi-etapa."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_live.db")
        self.store = LiveStore(self.db_path)
        self.tracker = PositionTracker(
            live_store=self.store,
            take_profit_pct=30.0,
            initial_sl_pct=60.0,
            realistic_slippage_pct=33.49,
            breakeven_trigger_pct=15.0,
            breakeven_lock_pct=5.0,
            trailing_stop_pct=10.0,
            stall_window_s=60.0,
            network_fee_sol=0.002,
        )

    def tearDown(self):
        self.store.close()
        self.tmp_dir.cleanup()

    def test_open_position_and_take_profit(self):
        """Posición alcanza TP (+30%) y se cierra con ganancia neta."""
        pos = self.tracker.open_position(
            mint="mint_tp_123",
            entry_price=1.0,
            entry_ts=1000.0,
            position_size_sol=0.5,
            branch="MASTER_ALPHA_FLOW_MOMENTUM",
        )
        self.assertEqual(pos.mint, "mint_tp_123")
        self.assertTrue(pos.is_open)

        # Tick intermedio (+10%)
        res1 = self.tracker.process_tick("mint_tp_123", price=1.10, ts=1010.0)
        self.assertIsNone(res1)
        self.assertTrue(pos.is_open)

        # Tick que alcanza Take Profit (+35% > +30%)
        res2 = self.tracker.process_tick("mint_tp_123", price=1.35, ts=1020.0)
        self.assertIsNotNone(res2)
        self.assertFalse(pos.is_open)
        self.assertEqual(res2["exit_reason"], "TAKE_PROFIT")
        self.assertGreater(res2["pnl_sol"], 0.10)
        self.assertTrue(res2["is_win"])

    def test_trailing_stop_lock_profit(self):
        """Posición sube a +25%, retrocede más de 10% del pico y ejecuta Trailing Stop."""
        self.tracker.open_position(
            mint="mint_trail_456",
            entry_price=1.0,
            entry_ts=1000.0,
            position_size_sol=0.5,
            branch="MASTER_ALPHA_SOCIAL_BUYERS_CONFLUENCE",
        )
        # Sube a 1.25 (+25% pico)
        self.tracker.process_tick("mint_trail_456", price=1.25, ts=1015.0)
        # Cae a 1.11 (caída de ~11.2% desde 1.25 -> detonador de trailing 10%)
        res = self.tracker.process_tick("mint_trail_456", price=1.11, ts=1030.0)
        self.assertIsNotNone(res)
        self.assertEqual(res["exit_reason"], "TRAILING_STOP")
        self.assertGreater(res["pnl_sol"], 0.0)  # Cierra con ganancia asegurada
        self.assertTrue(res["is_win"])

    def test_breakeven_lock(self):
        """Posición sube a +18% (activa BE), cae a 1.03 (debajo de lock +5%) y sale con BE_LOCK."""
        self.tracker.open_position(
            mint="mint_be_789",
            entry_price=1.0,
            entry_ts=1000.0,
            position_size_sol=0.5,
            branch="MASTER_ALPHA_FLOW_MOMENTUM",
        )
        # Sube a +18% (activa BE trigger de +15%)
        self.tracker.process_tick("mint_be_789", price=1.18, ts=1020.0)
        # Cae a 1.03 (debajo de lock +5%)
        res = self.tracker.process_tick("mint_be_789", price=1.03, ts=1040.0)
        self.assertIsNotNone(res)
        self.assertIn("BREAKEVEN", res["exit_reason"])
        self.assertGreaterEqual(res["nominal_pnl_pct"], 0.0)

    def test_stall_exit_at_60s(self):
        """Posición no arranca en 60s (precio <= entrada) y detona STALL_EXIT con pérdida reducida."""
        self.tracker.open_position(
            mint="mint_stall_001",
            entry_price=1.0,
            entry_ts=1000.0,
            position_size_sol=0.5,
            branch="MASTER_ALPHA_EXPANSION",
        )
        # Tick a 30s con precio 0.99 (espera activa)
        r1 = self.tracker.process_tick("mint_stall_001", price=0.99, ts=1030.0)
        self.assertIsNone(r1)

        # Tick a 65s con precio 0.98 <= entrada -> STALL_EXIT
        r2 = self.tracker.process_tick("mint_stall_001", price=0.98, ts=1065.0)
        self.assertIsNotNone(r2)
        self.assertEqual(r2["exit_reason"], "STALL_EXIT")
        # El slippage de stall es menor que un rug total
        self.assertFalse(r2["is_win"])

    def test_stop_loss_with_realistic_haircut(self):
        """Posición colapsa a -65% y modela slippage severo (-33.49%)."""
        self.tracker.open_position(
            mint="mint_sl_002",
            entry_price=1.0,
            entry_ts=1000.0,
            position_size_sol=0.5,
            branch="MASTER_ALPHA_CONFLUENCE_MOMENTUM",
        )
        res = self.tracker.process_tick("mint_sl_002", price=0.35, ts=1020.0)
        self.assertIsNotNone(res)
        self.assertEqual(res["exit_reason"], "STOP_LOSS")
        self.assertLess(res["pnl_pct"], -60.0)
        self.assertFalse(res["is_win"])


class TestDriftMonitor(unittest.TestCase):
    """Pruebas del monitor de concept drift CUSUM/Page-Hinkley y Circuit Breaker."""

    def test_drift_monitor_normal_operation(self):
        dm = DriftMonitor(target_winrate=0.45, min_sample=10)
        # Serie sana de trades (50% ganadores)
        for i in range(20):
            pnl = 20.0 if i % 2 == 0 else -25.0
            dm.record_trade_outcome(pnl_pct=pnl, is_win=(pnl > 0))

        status = dm.get_status()
        self.assertEqual(status["state"], CircuitBreakerState.CLOSED)
        self.assertTrue(status["trading_allowed"])
        self.assertFalse(status["drift_detected"])

    def test_drift_monitor_trips_on_consecutive_losses(self):
        dm = DriftMonitor(target_winrate=0.45, min_sample=5, cusum_threshold=15.0)
        # Racha de 8 pérdidas severas que destruyen el EV
        for _ in range(8):
            dm.record_trade_outcome(pnl_pct=-45.0, is_win=False)

        status = dm.get_status()
        self.assertIn(status["state"], (CircuitBreakerState.OPEN, CircuitBreakerState.HALF_OPEN))
        self.assertFalse(status["trading_allowed"])
        self.assertTrue(status["drift_detected"])

    def test_wilson_confidence_interval(self):
        dm = DriftMonitor(target_winrate=0.45)
        # 10 victorias de 20 trades
        ci_low, ci_high = dm.wilson_interval(wins=10, n=20, confidence=0.95)
        self.assertGreater(ci_low, 0.25)
        self.assertLess(ci_high, 0.75)


class TestLiveDetectorDaemonReplay(unittest.TestCase):
    """Pruebas del daemon ejecutor autónomo en modo replay determinista."""

    def test_daemon_replay_end_to_end(self):
        from scripts.run_live_detector import LiveDetectorDaemon

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = os.path.join(tmp_dir, "replay_decisions.db")
            daemon = LiveDetectorDaemon(db_path=db_file, dry_run=True)

            # Inyectar métricas de holders simuladas para que pase los filtros
            daemon.portal_adapter.holders_fetcher = lambda mint: {
                "devBalancePct": 0.5,
                "topHolderPct": 25.0,
                "holderCount": 45,
                "n_socials": 1,
                "netBuyers1h": 20,
            }

            events = [
                # 1. Trade inicial que define firstMarketCap ($6,000)
                {
                    "mint": "tok_replay_abc",
                    "txType": "buy",
                    "solAmount": 1.0,
                    "tokensAmount": 50000.0,
                    "marketCapSol": 40.0,
                    "vSolInCurve": 35.0,
                    "vTokensInCurve": 1e9,
                    "pool": "pump",
                    "traderPublicKey": "trader_1",
                    "signature": "sig_1",
                    "timestamp": 1000.0,
                },
                # 2. Ráfaga de compras para activar Branch D (Flow Momentum)
                {
                    "mint": "tok_replay_abc",
                    "txType": "buy",
                    "solAmount": 2.0,
                    "tokensAmount": 80000.0,
                    "marketCapSol": 45.0,
                    "vSolInCurve": 37.0,
                    "vTokensInCurve": 1e9,
                    "pool": "pump",
                    "traderPublicKey": "trader_2",
                    "signature": "sig_2",
                    "timestamp": 1010.0,
                },
                # 3. Subida parabólica que detona Take Profit (+40% > +30%)
                {
                    "mint": "tok_replay_abc",
                    "txType": "buy",
                    "solAmount": 5.0,
                    "tokensAmount": 150000.0,
                    "marketCapSol": 70.0,
                    "vSolInCurve": 42.0,
                    "vTokensInCurve": 1e9,
                    "pool": "pump",
                    "traderPublicKey": "trader_3",
                    "signature": "sig_3",
                    "timestamp": 1025.0,
                },
            ]

            try:
                summary = daemon.run_replay(events)
                self.assertGreater(summary["events_processed"], 0)
                self.assertGreater(summary["decisions_evaluated"], 0)
                self.assertIn("winrate_pct", summary)
                self.assertIn("circuit_breaker_state", summary)
            finally:
                daemon.store.close()

    def test_branch_a_microstructure_confirmation_guard(self):
        """Verifica que Rama A vete tokens zombis (0 compras) pero admita con confirmación mínima."""
        # 1. Token zombi (0 compras, 1 holder) -> Veto / No admitido por Rama A
        zombie_token = {
            "marketCap": 5500.0,
            "firstMarketCap": 4100.0,
            "firstLiquidity": 1200.0,
            "devBalancePct": 0.5,
            "migrationProgress": 10.0,
            "realSolReserves": 25.0,
            "buysM5": 0,
            "holderCount": 1,
        }
        dec_zombie = MasterSnipeSignal.evaluate(zombie_token)
        self.assertFalse(dec_zombie.admitted)
        self.assertNotEqual(dec_zombie.branch, "MASTER_ALPHA_EXPANSION")

        # 2. Token confirmado con compradores mínimos (buysM5 >= 2 o holderCount >= 2) -> Admitido
        confirmed_token = {
            "marketCap": 5500.0,
            "firstMarketCap": 4100.0,
            "firstLiquidity": 1200.0,
            "devBalancePct": 0.5,
            "migrationProgress": 10.0,
            "realSolReserves": 25.0,
            "buysM5": 3,
            "holderCount": 4,
        }
        dec_confirmed = MasterSnipeSignal.evaluate(confirmed_token)
        self.assertTrue(dec_confirmed.admitted)
        self.assertEqual(dec_confirmed.branch, "MASTER_ALPHA_EXPANSION")

        # 3. Token sintético sin datos de microestructura -> Admitido por compatibilidad
        synthetic_token = {
            "marketCap": 5500.0,
            "firstMarketCap": 4100.0,
            "firstLiquidity": 1200.0,
            "devBalancePct": 0.5,
            "migrationProgress": 10.0,
            "realSolReserves": 25.0,
        }
        dec_synth = MasterSnipeSignal.evaluate(synthetic_token)
        self.assertTrue(dec_synth.admitted)
        self.assertEqual(dec_synth.branch, "MASTER_ALPHA_EXPANSION")


if __name__ == "__main__":
    unittest.main()
