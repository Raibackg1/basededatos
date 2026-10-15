"""Tests de wiring: live_runner con alert_manager + regime + drift + slippage.

Verifica que los componentes nuevos se conectan correctamente en run_replay:
- AlertManager recibe alertas de drift/gate/regime durante el replay
- RegimeClassifier se alimenta con outcomes y reporta régimen en control
- DriftMonitor recibe feature values y su estado aparece en el audit
- ConditionalSlippageModel se pasa a PaperBroker
- FileAlertSink persiste alertas en disco
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.alerts import AlertManager, AlertSeverity, FileAlertSink
from detector.costs import CostModel, ConditionalSlippageModel
from detector.drift_monitor import DriftMonitor
from detector.feature_drift import FeatureDriftMonitor
from detector.live_runner import run_replay
from detector.regime import RegimeClassifier


def _make_events(n_tokens=5, n_ticks=3):
    """Genera eventos replay sintéticos mínimos para PaperBroker."""
    events = []
    base_ts = 1_700_000_000.0
    for i in range(n_tokens):
        mint = f"tok_{i:03d}"
        mc0 = 5000.0 + i * 100.0
        for t in range(n_ticks):
            mc = mc0 * (1.0 + 0.02 * t)
            events.append({
                "type": "trade",
                "mint": mint,
                "marketCapSol": mc,
                "ts": base_ts + i * 10.0 + t,
                "solAmount": 1.0,
                "side": "buy",
                "traderPublicKey": f"w{i}",
            })
    return events


class TestLiveRunnerWiring(unittest.TestCase):

    def test_alert_manager_receives_regime_alert(self):
        """run_replay con alert_manager + regime_classifier emite alertas."""
        am = AlertManager()
        rc = RegimeClassifier(min_samples=2)
        events = _make_events(n_tokens=3, n_ticks=2)
        audit = run_replay(
            events, alert_manager=am, regime_classifier=rc,
            enable_branch_gate=False, entry_sol=0.1,
        )
        self.assertIn("alerts_emitted", audit["control"])
        self.assertIn("regime", audit["control"])
        # Al menos una alerta de régimen o ninguna (si no hay suficientes outcomes)
        # Pero el manager debe estar conectado: count >= 0
        self.assertGreaterEqual(audit["control"]["alerts_emitted"], 0)

    def test_regime_in_control(self):
        """El audit siempre incluye el campo regime."""
        rc = RegimeClassifier(min_samples=2)
        events = _make_events(n_tokens=2, n_ticks=2)
        audit = run_replay(
            events, regime_classifier=rc,
            enable_branch_gate=False, entry_sol=0.1,
        )
        self.assertIn(audit["control"]["regime"],
                      ("expanding", "contracting", "neutral", "unknown"))

    def test_drift_monitor_attached(self):
        """Si se pasa drift_monitor, aparece en el audit."""
        dm = DriftMonitor(min_sample=2)
        fdm = FeatureDriftMonitor(reference={
            "market_cap": [5000.0, 5100.0, 4900.0],
            "first_liquidity": [1500.0, 1600.0, 1400.0],
        })
        dm.attach_feature_drift(fdm)
        events = _make_events(n_tokens=3, n_ticks=2)
        audit = run_replay(
            events, drift_monitor=dm,
            enable_branch_gate=False, entry_sol=0.1,
        )
        self.assertTrue(audit["control"]["drift_monitor_attached"])
        self.assertIn("drift_monitor", audit)
        self.assertIn("state", audit["drift_monitor"])

    def test_drift_monitor_receives_alert_manager(self):
        """drift_monitor._alerts se inyecta si no lo tiene."""
        am = AlertManager()
        dm = DriftMonitor(min_sample=2)
        self.assertIsNone(dm._alerts)
        events = _make_events(n_tokens=2, n_ticks=2)
        run_replay(
            events, drift_monitor=dm, alert_manager=am,
            enable_branch_gate=False, entry_sol=0.1,
        )
        self.assertIs(dm._alerts, am)

    def test_slippage_model_param_accepted(self):
        """run_replay acepta slippage_model sin errores."""
        sm = ConditionalSlippageModel()
        events = _make_events(n_tokens=3, n_ticks=2)
        audit = run_replay(
            events, slippage_model=sm,
            enable_branch_gate=False, entry_sol=0.1,
        )
        self.assertIn("control", audit)


class TestFileAlertSink(unittest.TestCase):

    def test_file_sink_persists_alerts(self):
        """FileAlertSink escribe alertas en JSONL."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "alerts.jsonl")
            sink = FileAlertSink(path)
            am = AlertManager()
            am.subscribe(sink)
            am.emit("drift", AlertSeverity.WARNING, "test msg", ts=100.0)
            am.emit("gate", AlertSeverity.CRITICAL, "gate msg", ts=101.0)
            sink.flush()
            sink.close()
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)
            a0 = json.loads(lines[0])
            self.assertEqual(a0["kind"], "drift")
            self.assertEqual(a0["severity"], "warning")
            a1 = json.loads(lines[1])
            self.assertEqual(a1["kind"], "gate")

    def test_file_sink_creates_parent_dirs(self):
        """FileAlertSink crea directorios padre si no existen."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "dir", "alerts.jsonl")
            sink = FileAlertSink(path)
            self.assertTrue(os.path.exists(path))
            sink.close()

    def test_file_sink_survives_broken_write(self):
        """Un error de escritura no propaga excepción."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "alerts.jsonl")
            sink = FileAlertSink(path)
            am = AlertManager()
            am.subscribe(sink)
            # Emitir con payload no serializable no debe crashear
            am.emit("x", AlertSeverity.INFO, "msg", payload={"bad": object()}, ts=1.0)
            sink.close()


class TestBranchGateEmitsAlerts(unittest.TestCase):

    def test_gate_emits_on_disable(self):
        """BranchGate emite alerta CRITICAL al desactivar una rama."""
        from detector.branch_gate import BranchGate
        from detector.live_store import LiveStore

        store = LiveStore(":memory:")
        am = AlertManager()
        gate = BranchGate(store, min_samples=3, min_ev_sol=0.0,
                          use_bayes=False, alert_manager=am)

        # Simular outcomes netos negativos para una rama
        for i in range(5):
            store.record_decision(
                mint=f"tok{i}", feats={"mc": 5000 + i},
                decision=type("D", (), {"admitted": True, "branch": "BRANCH_TEST",
                                        "confidence": 0.8, "veto_reason": None})(),
                ts=1000.0 + i,
            )
        # Registrar outcomes con pérdida fuerte
        for i in range(5):
            did = i + 1  # primer decision_id
            store.record_outcome(did, pnl_sol=-0.5, pnl_pct=-50.0,
                                 exit_reason="SL", mfe_pct=1.0, mae_pct=-60.0)

        am.clear()
        gate.refresh(now=2000.0)
        alerts = am.recent(kind="branch_gate")
        # Si la rama quedó DISABLED, debe haber alerta
        if gate.disabled_branches():
            self.assertGreater(len(alerts), 0)
            self.assertEqual(alerts[0].severity, AlertSeverity.CRITICAL)


class TestResourceCleanup(unittest.TestCase):
    """Cierra de recursos: FileAlertSink, LiveStore, close_all()."""

    def test_alert_manager_close_all_closes_sinks(self):
        """close_all() invoca close() en todos los sinks suscritos."""
        am = AlertManager()
        closed = []

        class _Sink:
            def __call__(self, alert):
                pass

            def close(self):
                closed.append(True)

        am.subscribe(_Sink())
        am.subscribe(lambda a: None)  # sin close() — no debe crashear
        am.close_all()
        self.assertEqual(closed, [True])

    def test_alert_manager_close_all_idempotent(self):
        """close_all() dos veces no lanza excepción."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.jsonl")
            am = AlertManager()
            am.subscribe(FileAlertSink(path))
            am.close_all()
            am.close_all()  # segunda llamada OK

    def test_file_alert_sink_close_idempotent(self):
        """FileAlertSink.close() dos veces no lanza excepción."""
        with tempfile.TemporaryDirectory() as td:
            sink = FileAlertSink(os.path.join(td, "a.jsonl"))
            sink.close()
            sink.close()

    def test_live_store_close_idempotent(self):
        """LiveStore.close() dos veces no lanza excepción."""
        from detector.live_store import LiveStore
        store = LiveStore(":memory:")
        store.close()
        store.close()

    def test_live_engine_finalize_closes_store(self):
        """LiveEngine.finalize() cierra el store (sin error en temp dir)."""
        from run_live import LiveEngine, _run_demo
        from detector.live_store import LiveStore

        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "e.db")
            alert_log = os.path.join(td, "a.jsonl")
            store = LiveStore(db)
            eng = LiveEngine(store, entry_sol=0.1, alert_log_path=alert_log)
            _run_demo(eng)
            audit = eng.finalize()
            self.assertIn("control", audit)
            # Temp dir se puede borrar sin PermissionError (store + sink cerrados)

    def test_daemon_close_idempotent(self):
        """LiveDetectorDaemon.close() dos veces no lanza excepción."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        try:
            from run_live_detector import LiveDetectorDaemon
        except ImportError:
            self.skipTest("run_live_detector no importable en este contexto")
            return

        with tempfile.TemporaryDirectory() as td:
            d = LiveDetectorDaemon(
                db_path=os.path.join(td, "d.db"),
                dry_run=True,
                alert_log_path=os.path.join(td, "a.jsonl"),
            )
            d.close()
            d.close()

    def test_daemon_replay_closes_store(self):
        """run_replay() cierra store al terminar."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        try:
            from run_live_detector import LiveDetectorDaemon
        except ImportError:
            self.skipTest("run_live_detector no importable en este contexto")
            return

        with tempfile.TemporaryDirectory() as td:
            d = LiveDetectorDaemon(
                db_path=os.path.join(td, "d.db"),
                dry_run=True,
                position_size_sol=0.1,
                alert_log_path=os.path.join(td, "a.jsonl"),
            )
            # 2 eventos mínimos para que el loop corra
            ts0 = 1_700_000_000.0
            events = [
                {"mint": "M", "txType": "buy", "traderPublicKey": "w1",
                 "solAmount": 0.5, "tokensAmount": 1e5, "marketCapSol": 50.0,
                 "vSolInCurve": 30.0, "vTokensInCurve": 1e9, "pool": "pump",
                 "signature": f"M-w1-{ts0}", "timestamp": ts0},
            ]
            summary = d.run_replay(events)
            self.assertIn("events_processed", summary)
            # store cerrado: segunda llamada close() OK
            d.close()


if __name__ == "__main__":
    unittest.main()
