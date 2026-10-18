"""P1 — Trazas de decisión del pipeline (E0 descubre → E6 ejecuta).

Congela el contrato de "la decisión de pensamiento filtrado en vivo":

- ``LiveStore``: tabla ``decision_traces`` (append agrupado por ``trace_id``,
  consulta por mint, poda por retención).
- ``gmgn_bridge.build_decision_stages()``: función pura que convierte la
  evaluación real (``ml_eval`` + features) en la secuencia de etapas
  ``pass|fail|skip|info`` — espejo fiel del orden ``elif`` de
  ``AIQuantModel.predict_token`` (luego del primer veto, las etapas restantes
  se marcan ``skip``: la realidad es que no se ejecutaron).
- Endpoints ``/api/pipeline/live`` y ``/api/pipeline/trace/{mint}``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
sys.path.insert(0, _PKG)

from detector.gmgn_bridge import build_decision_stages  # noqa: E402
from detector.live_store import LiveStore  # noqa: E402
from cockpit.server import CockpitState, make_server  # noqa: E402


class _NullFeed:
    """Feed sin red para tests: nunca devuelve precio ni candidatos."""

    last_error = None

    def market_cap_usd(self, mint):
        return None

    def token_snapshot(self, mint):
        return None

    def discover_new_tokens(self, limit=10):
        return []

    def health(self):
        return {"source": "null", "calls": 0, "misses": 0, "errors": 0}


def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE Token(mint TEXT PRIMARY KEY, firstMarketCap REAL,
                           firstLiquidity REAL, marketCap REAL, rise50At TEXT);
        CREATE TABLE PaperPosition(mint TEXT, pnlPct REAL, status TEXT);
    """)
    con.commit()
    con.close()


def _stage(stage, verdict, value=None, threshold=None, operator=None,
           reason="", source="rule_default", source_kind="default_literal"):
    return {
        "stage": stage, "value": value, "threshold": threshold, "operator": operator,
        "verdict": verdict, "reason": reason, "source": source, "source_kind": source_kind,
    }


def _ml_veto_bundler():
    return {
        "probability_success": 0.71, "confluence_score": 20.0, "expected_ev": "+1.0%",
        "ev_fraction": 0.01, "admitted": False, "verdict": "VETO",
        "veto_reason": "VETO_BUNDLER_CLUSTER: Bundler empaquetó 23.2% del supply (>= 20%)",
        "pattern_id": "BUNDLER_UNIFORM_TRAP", "pattern_label": "Trampa de Bundler",
        "bias": "BEARISH_VETO", "causal_trigger": "bloque 0", "metrics_known": True,
    }


def _features(**over):
    base = dict(source="dexscreener", mint="MINT1", symbol="TOK1", mc_usd=25000.0,
                liq_usd=6000.0, metrics_known=True, bundler_pct=0.0,
                wash_trading_flag=False, clone_factory_flag=False,
                dev_factory_flag=False, dev_balance_pct=0.5, top10_pct=18.0)
    base.update(over)
    return base


class TestDecisionTraceStore(unittest.TestCase):
    def test_append_and_list_grouped_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LiveStore(os.path.join(tmp, "p.db"))
            store.append_decision_trace(
                trace_id="t1", mint="M1", symbol="S1", ts=100.0,
                stages=[_stage("E0_discovery", "info"), _stage("E1_metrics_known", "pass")])
            store.append_decision_trace(
                trace_id="t2", mint="M2", symbol="S2", ts=200.0,
                stages=[_stage("E0_discovery", "info"), _stage("E1_metrics_known", "pass"),
                        _stage("E3_bundler", "fail", value=23.2, threshold=20.0, operator="<=")])

            traces = store.list_decision_traces(limit=10)
            self.assertEqual(len(traces), 2)
            self.assertEqual(traces[0]["mint"], "M2")  # más nuevo primero
            self.assertEqual(traces[1]["mint"], "M1")
            self.assertEqual([s["stage"] for s in traces[0]["stages"]],
                             ["E0_discovery", "E1_metrics_known", "E3_bundler"])
            self.assertEqual(traces[0]["stages"][2]["verdict"], "fail")
            self.assertAlmostEqual(traces[0]["stages"][2]["value"], 23.2, places=2)
            store.close()

    def test_get_decision_trace_filters_by_mint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LiveStore(os.path.join(tmp, "p.db"))
            store.append_decision_trace(trace_id="a", mint="MA", symbol="A", ts=1.0,
                                        stages=[_stage("E0_discovery", "info")])
            store.append_decision_trace(trace_id="b", mint="MB", symbol="B", ts=2.0,
                                        stages=[_stage("E0_discovery", "info")])
            store.append_decision_trace(trace_id="c", mint="MA", symbol="A", ts=3.0,
                                        stages=[_stage("E0_discovery", "info")])

            traces = store.get_decision_trace("MA", limit=5)
            self.assertEqual([t["trace_id"] for t in traces], ["c", "a"])
            store.close()

    def test_prune_keeps_newest_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LiveStore(os.path.join(tmp, "p.db"))
            for i in range(25):
                store.append_decision_trace(
                    trace_id=f"t{i}", mint=f"M{i}", symbol="S", ts=float(i),
                    stages=[_stage("E0_discovery", "info")])
            store.prune_decision_traces(max_rows=10)
            traces = store.list_decision_traces(limit=100)
            self.assertEqual(len(traces), 10)
            self.assertEqual(traces[0]["mint"], "M24")
            store.close()


class TestBuildDecisionStages(unittest.TestCase):
    def test_veto_bundler_fails_then_skips(self):
        stages = build_decision_stages(**_features(bundler_pct=23.2), ml_eval=_ml_veto_bundler())
        by_stage = {s["stage"]: s for s in stages}

        self.assertEqual(by_stage["E3_bundler"]["verdict"], "fail")
        self.assertAlmostEqual(by_stage["E3_bundler"]["value"], 23.2, places=2)
        self.assertEqual(by_stage["E3_bundler"]["threshold"], 20.0)
        self.assertEqual(by_stage["E3_bundler"]["operator"], "<=")
        # Después del primer veto (orden elif real) las demás etapas NO se ejecutan.
        self.assertEqual(by_stage["E3_dev_factory"]["verdict"], "skip")
        self.assertEqual(by_stage["E3_liquidity_ratio"]["verdict"], "skip")
        self.assertEqual(by_stage["E4_ev_net"]["verdict"], "skip")
        # El veredicto final conserva el motivo textual.
        self.assertIn("VETO_BUNDLER_CLUSTER", by_stage["E5_verdict"]["reason"])
        self.assertEqual(stages[0]["stage"], "E0_discovery")

    def test_unverified_metrics_fails_at_e1(self):
        ml = dict(_ml_veto_bundler())
        ml.update({"pattern_id": "UNVERIFIED_SECURITY_TRAP", "admitted": False,
                   "veto_reason": "VETO_UNVERIFIED_METRICS: ..."})
        stages = build_decision_stages(**_features(metrics_known=False), ml_eval=ml)
        by_stage = {s["stage"]: s for s in stages}

        self.assertEqual(by_stage["E1_metrics_known"]["verdict"], "fail")
        self.assertEqual(by_stage["E3_bundler"]["verdict"], "skip")

    def test_admitted_passes_all_and_e4(self):
        ml = {
            "probability_success": 0.81, "confluence_score": 81.0, "expected_ev": "+32.5%",
            "ev_fraction": 0.325, "admitted": True, "verdict": "STRONG_BUY",
            "veto_reason": None, "pattern_id": "HIGH_CONFLUENCE_BREAKOUT",
            "pattern_label": "Ruptura", "bias": "BULLISH",
            "causal_trigger": "alta confluencia", "metrics_known": True,
        }
        stages = build_decision_stages(**_features(), ml_eval=ml)
        verdicts = [s["verdict"] for s in stages if s["stage"].startswith("E3")]

        self.assertNotIn("fail", verdicts)
        by_stage = {s["stage"]: s for s in stages}
        self.assertEqual(by_stage["E4_ev_net"]["verdict"], "pass")
        self.assertAlmostEqual(by_stage["E4_ev_net"]["value"], 0.325, places=3)
        self.assertIn("STRONG_BUY", by_stage["E5_verdict"]["reason"])

    def test_neutral_when_ev_not_positive(self):
        ml = {
            "probability_success": 0.55, "confluence_score": 55.0, "expected_ev": "-1.5%",
            "ev_fraction": -0.015, "admitted": False, "verdict": "NEUTRAL",
            "veto_reason": None, "pattern_id": "UNCONFIRMED_VOLATILITY",
            "pattern_label": "Volatilidad", "bias": "NEUTRAL",
            "causal_trigger": "estándar", "metrics_known": True,
        }
        stages = build_decision_stages(**_features(), ml_eval=ml)
        by_stage = {s["stage"]: s for s in stages}

        self.assertEqual(by_stage["E1_metrics_known"]["verdict"], "pass")
        self.assertEqual(by_stage["E3_bundler"]["verdict"], "pass")
        self.assertEqual(by_stage["E4_ev_net"]["verdict"], "fail")
        self.assertIn("NEUTRAL", by_stage["E5_verdict"]["reason"])


class TestPipelineEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        _make_db(self.db)
        self.snap_path = os.path.join(self.tmp, "snap.json")
        self.state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        self.srv = make_server(self.state, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.state._stop_loop.set()
        self.srv.shutdown()
        self.srv.server_close()
        try:
            self.state.store.close()
        except Exception:
            pass

    def _get_json(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            self.assertEqual(r.status, 200)
            return json.loads(r.read().decode("utf-8"))

    def _seed(self):
        import time
        now = time.time()
        self.state.store.append_decision_trace(
            trace_id="pipe1", mint="MINT_VETO", symbol="BAD", ts=now,
            stages=[_stage("E0_discovery", "info", source="dexscreener", source_kind="feed"),
                    _stage("E1_metrics_known", "pass"),
                    _stage("E3_bundler", "fail", value=23.2, threshold=20.0, operator="<=",
                           reason="Bundler 23.2% > 20%"),
                    _stage("E5_verdict", "info", reason="VETO · VETO_BUNDLER_CLUSTER")])
        self.state.store.append_decision_trace(
            trace_id="pipe2", mint="MINT_OK", symbol="GOOD", ts=now + 0.01,
            stages=[_stage("E0_discovery", "info", source="dexscreener", source_kind="feed"),
                    _stage("E1_metrics_known", "pass"),
                    _stage("E4_ev_net", "pass", value=0.3, threshold=0.0, operator=">"),
                    _stage("E6_execution", "pass", value=91.0, threshold=85.0, operator=">=",
                           reason="OPENED_PAPER_TRADE"),
                    _stage("E5_verdict", "info", reason="STRONG_BUY")])

    def test_pipeline_live_returns_traces_and_funnel(self):
        self._seed()
        data = self._get_json("/api/pipeline/live")
        self.assertTrue(data["ok"])
        self.assertEqual(data["schema"], "pipeline/1")
        self.assertEqual(len(data["traces"]), 2)
        funnel = data["funnel"]
        self.assertEqual(funnel["seen"], 2)
        self.assertEqual(funnel["admitted"], 1)
        self.assertEqual(funnel["executed"], 1)
        self.assertEqual(funnel["veto_by_stage"].get("E3_bundler"), 1)

    def test_pipeline_trace_by_mint(self):
        self._seed()
        data = self._get_json("/api/pipeline/trace/MINT_VETO")
        self.assertTrue(data["ok"])
        self.assertEqual(data["mint"], "MINT_VETO")
        self.assertEqual(len(data["traces"]), 1)
        stages = data["traces"][0]["stages"]
        self.assertEqual(stages[2]["stage"], "E3_bundler")
        self.assertEqual(stages[2]["verdict"], "fail")

        empty = self._get_json("/api/pipeline/trace/NO_EXISTE")
        self.assertEqual(empty["traces"], [])


if __name__ == "__main__":
    unittest.main()
