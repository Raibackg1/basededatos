"""Tests del hub de alertas (detector/alerts.py).

Verifica:
- emit devuelve Alert y lo registra en buffer
- subscribe recibe cada alerta
- dedup por ventana temporal (mismo kind+message no se re-emite)
- dedup expira tras la ventana
- recent() filtra por kind y severidad
- sink que lanza excepción no tumbe el pipeline
- clear() limpia buffer y dedup
- fail-closed: sin reglas en export_gate
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.alerts import AlertManager, AlertSeverity
from detector.export_gate import oos_export_gate


class TestAlertManager(unittest.TestCase):

    def test_emit_returns_alert_and_buffers(self):
        m = AlertManager()
        a = m.emit("feature_drift", AlertSeverity.WARNING,
                   "PSI alto en liquidity_sol", ts=100.0)
        self.assertIsNotNone(a)
        self.assertEqual(a.kind, "feature_drift")
        self.assertEqual(a.severity, AlertSeverity.WARNING)
        self.assertEqual(m.count, 1)

    def test_subscribe_receives_alert(self):
        m = AlertManager()
        received = []
        m.subscribe(received.append)
        m.emit("branch_gate", AlertSeverity.CRITICAL, "BRANCH_X DISABLED", ts=100.0)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].kind, "branch_gate")

    def test_dedup_within_window(self):
        m = AlertManager(dedup_window_s=60.0)
        a1 = m.emit("drift", AlertSeverity.WARNING, "mismo mensaje", ts=100.0)
        a2 = m.emit("drift", AlertSeverity.WARNING, "mismo mensaje", ts=130.0)
        self.assertIsNotNone(a1)
        self.assertIsNone(a2, "dedup debe suprimir dentro de la ventana")
        self.assertEqual(m.count, 1)

    def test_dedup_expires_after_window(self):
        m = AlertManager(dedup_window_s=60.0)
        a1 = m.emit("drift", AlertSeverity.WARNING, "msg", ts=100.0)
        a2 = m.emit("drift", AlertSeverity.WARNING, "msg", ts=170.0)
        self.assertIsNotNone(a1)
        self.assertIsNotNone(a2, "tras expirar la ventana debe emitir de nuevo")
        self.assertEqual(m.count, 2)

    def test_different_messages_not_deduped(self):
        m = AlertManager(dedup_window_s=60.0)
        m.emit("drift", AlertSeverity.WARNING, "msg A", ts=100.0)
        m.emit("drift", AlertSeverity.WARNING, "msg B", ts=101.0)
        self.assertEqual(m.count, 2)

    def test_recent_filters_by_kind(self):
        m = AlertManager()
        m.emit("drift", AlertSeverity.WARNING, "a", ts=1.0)
        m.emit("gate", AlertSeverity.INFO, "b", ts=2.0)
        m.emit("drift", AlertSeverity.CRITICAL, "c", ts=3.0)
        drift = m.recent(kind="drift")
        self.assertEqual(len(drift), 2)
        self.assertTrue(all(a.kind == "drift" for a in drift))

    def test_recent_filters_by_min_severity(self):
        m = AlertManager()
        m.emit("x", AlertSeverity.INFO, "info", ts=1.0)
        m.emit("x", AlertSeverity.WARNING, "warn", ts=2.0)
        m.emit("x", AlertSeverity.CRITICAL, "crit", ts=3.0)
        crit = m.recent(min_severity=AlertSeverity.CRITICAL)
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0].message, "crit")
        warn_plus = m.recent(min_severity=AlertSeverity.WARNING)
        self.assertEqual(len(warn_plus), 2)

    def test_broken_sink_does_not_crash(self):
        m = AlertManager()
        good = []
        def bad_sink(alert):
            raise RuntimeError("sink roto")
        m.subscribe(bad_sink)
        m.subscribe(good.append)
        a = m.emit("drift", AlertSeverity.INFO, "ok", ts=1.0)
        self.assertIsNotNone(a)
        self.assertEqual(len(good), 1)

    def test_clear_resets_everything(self):
        m = AlertManager(dedup_window_s=60.0)
        m.emit("drift", AlertSeverity.WARNING, "msg", ts=100.0)
        m.clear()
        self.assertEqual(m.count, 0)
        # Tras clear, el dedup tambien se limpia: puede volver a emitir
        a = m.emit("drift", AlertSeverity.WARNING, "msg", ts=110.0)
        self.assertIsNotNone(a)

    def test_non_callable_sink_raises(self):
        m = AlertManager()
        with self.assertRaises(TypeError):
            m.subscribe("no_es_callable")

    def test_alert_as_dict(self):
        m = AlertManager()
        a = m.emit("k", AlertSeverity.INFO, "m", payload={"x": 1}, ts=5.0)
        d = a.as_dict()
        self.assertEqual(d["severity"], "info")
        self.assertEqual(d["payload"], {"x": 1})


class TestExportGate(unittest.TestCase):

    def _rule(self, **kw):
        base = {
            "rule": "mc<=4285",
            "conditions": [{"feature": "mc", "op": "<=", "value": 4285}],
            "support_n": 100,
            "oos_pass": True,
            "oos_n": 25,
            "oos_lift": 1.3,
            "bounds": [[4285, None]],
            "data_origin": "historical",
        }
        base.update(kw)
        return base

    def test_fail_closed_empty_rules(self):
        r = oos_export_gate([])
        self.assertFalse(r.allowed)
        self.assertIn("sin_reglas", r.reasons)

    def test_fail_closed_none(self):
        r = oos_export_gate(None)
        self.assertFalse(r.allowed)

    def test_passing_rule_allowed(self):
        r = oos_export_gate([self._rule()])
        self.assertTrue(r.allowed)
        self.assertEqual(len(r.passed_rules), 1)
        self.assertEqual(r.rejected, [])

    def test_reject_no_oos_pass(self):
        r = oos_export_gate([self._rule(oos_pass=False)])
        self.assertFalse(r.allowed)
        self.assertIn("sin_oos_pass", r.reasons)

    def test_reject_low_oos_n(self):
        r = oos_export_gate([self._rule(oos_n=5)])
        self.assertFalse(r.allowed)
        self.assertTrue(any("oos_n" in reason for reason in r.reasons))

    def test_reject_low_oos_lift(self):
        r = oos_export_gate([self._rule(oos_lift=0.8)])
        self.assertFalse(r.allowed)
        self.assertTrue(any("oos_lift" in reason for reason in r.reasons))

    def test_reject_no_bounds(self):
        r = oos_export_gate([self._rule(bounds=[])])
        self.assertFalse(r.allowed)
        self.assertIn("sin_bounds", r.reasons)

    def test_reject_invalid_origin(self):
        r = oos_export_gate([self._rule(data_origin="synthetic")])
        self.assertFalse(r.allowed)
        self.assertTrue(any("data_origin" in reason for reason in r.reasons))

    def test_mixed_rules_pass_only_valid(self):
        good = self._rule()
        bad = self._rule(oos_pass=False)
        r = oos_export_gate([good, bad])
        self.assertTrue(r.allowed)
        self.assertEqual(len(r.passed_rules), 1)
        self.assertEqual(len(r.rejected), 1)

    def test_custom_thresholds(self):
        r = oos_export_gate([self._rule(oos_n=5, oos_lift=1.2)],
                            min_oos_n=3, min_oos_lift=1.1)
        self.assertTrue(r.allowed)

    def test_as_dict(self):
        r = oos_export_gate([self._rule()])
        d = r.as_dict()
        self.assertTrue(d["allowed"])
        self.assertEqual(d["n_passed"], 1)


if __name__ == "__main__":
    unittest.main()
