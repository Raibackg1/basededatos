"""Tests del cockpit: snapshot honesto + servidor local (sin red externa)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cockpit import snapshot as snap
from cockpit.server import CockpitState, make_server


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
    # 5 tokens que pasan AMBOS filtros (incluida la condición futura MC>=4500)
    for i in range(5):
        con.execute("INSERT INTO Token VALUES(?,?,?,?,?)",
                    (f"WIN{i}", 3000.0, 1000.0, 6000.0, "2026-01-01" if i < 4 else None))
    # 5 tokens que pasan SOLO el filtro de entrada (MC final < 4500)
    for i in range(5):
        con.execute("INSERT INTO Token VALUES(?,?,?,?,?)",
                    (f"LOSE{i}", 3000.0, 1000.0, 2000.0, "2026-01-01" if i == 0 else None))
    # PaperPosition: WIN0..WIN2 ganadores; LOSE0 perdedor
    for m in ("WIN0", "WIN1", "WIN2"):
        con.execute("INSERT INTO PaperPosition VALUES(?,?,?)", (m, 50.0, "CLOSED"))
    con.execute("INSERT INTO PaperPosition VALUES(?,?,?)", ("LOSE0", -40.0, "CLOSED"))
    con.execute("INSERT INTO PaperPosition VALUES(?,?,?)", ("OPENX", 5.0, "OPEN"))
    con.commit()
    con.close()


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        _make_db(self.db)

    def test_build_snapshot_basic(self):
        s = snap.build_snapshot(self.db, sample=100)
        self.assertTrue(s["ok"], s.get("error"))
        u = s["universe"]
        self.assertEqual(u["n_tokens"], 10)
        self.assertEqual(u["n_paper_positions"], 4)   # OPEN excluida
        self.assertEqual(u["rise50_pct"], 50.0)       # 5 de 10
        self.assertEqual(u["paper_wr_pct"], 75.0)     # 3 de 4

    def test_bias_segments_show_the_collapse(self):
        s = snap.build_snapshot(self.db, sample=100)
        with_future, without_future, total = s["bias"]
        self.assertEqual(with_future["n_tokens"], 5)
        self.assertEqual(with_future["hit50_pct"], 80.0)
        self.assertEqual(without_future["n_tokens"], 10)
        self.assertEqual(without_future["hit50_pct"], 50.0)
        self.assertEqual(total["n_tokens"], 10)
        # El filtro "con futuro" infla el +50%: la trampa queda visible.
        self.assertGreater(with_future["hit50_pct"], without_future["hit50_pct"])

    def test_points_shape(self):
        s = snap.build_snapshot(self.db, sample=100)
        self.assertEqual(len(s["points"]), 10)
        for p in s["points"]:
            self.assertEqual(len(p), 6)
            self.assertIn(p[3], (0, 1))   # hit50
            self.assertIn(p[4], (0, 1))   # has_paper

    def test_missing_db_reports_error(self):
        s = snap.build_snapshot(os.path.join(self.tmp, "nope.db"))
        self.assertFalse(s["ok"])
        self.assertIn("no encontrada", s["error"])

    def test_point_meta_included(self):
        s = snap.build_snapshot(self.db, sample=100)
        self.assertIn("point_meta", s)
        self.assertEqual(len(s["point_meta"]), 10)
        self.assertEqual(s["point_meta"][0]["mint"], "WIN0")

    def test_evaluate_custom_filter_causal(self):
        res = snap.evaluate_custom_filter(self.db, fmc_min=0, fmc_max=4000, fliq_min=0, fliq_max=2000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["n_matched"], 10)
        self.assertFalse(res["has_lookahead"])
        self.assertEqual(res["hit50_count"], 5)
        self.assertEqual(res["paper_wr_pct"], 75.0)

    def test_evaluate_custom_filter_invalid_ranges(self):
        res = snap.evaluate_custom_filter(self.db, fmc_min=5000, fmc_max=1000)
        self.assertFalse(res["ok"])
        self.assertIn("fmc_min", res["error"])

        res2 = snap.evaluate_custom_filter(self.db, fliq_min=3000, fliq_max=1000)
        self.assertFalse(res2["ok"])
        self.assertIn("fliq_min", res2["error"])

    def test_evaluate_custom_filter_quant_metrics(self):
        res = snap.evaluate_custom_filter(self.db, fmc_min=0, fmc_max=4000, fliq_min=0, fliq_max=2000)
        self.assertTrue(res["ok"])
        self.assertIn("hit50_ci95", res)
        self.assertEqual(len(res["hit50_ci95"]), 2)
        self.assertIn("kelly_pct", res)
        self.assertIn("edge_vs_universe", res)
        self.assertIn("delta_hit50_pct", res["edge_vs_universe"])


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        _make_db(self.db)
        self.snap_path = os.path.join(self.tmp, "snap.json")
        self.state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        self.state.rebuild()
        self.srv = make_server(self.state, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_api_state_returns_snapshot(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/state", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["universe"]["n_tokens"], 10)

    def test_index_is_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as r:
            self.assertEqual(r.status, 200)
            html = r.read().decode("utf-8")
        self.assertIn("Pumpfun", html)
        self.assertIn("canvas", html)

    def test_api_evaluate_endpoint(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/evaluate",
            data=json.dumps({"fmc_max": 4000, "fliq_max": 2000}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["n_matched"], 10)

    def test_api_evaluate_defensive_handling(self):
        # Envío con fmc_min > fmc_max debe responder con status 400 y ok: False
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/evaluate",
            data=json.dumps({"fmc_min": 6000, "fmc_max": 2000}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
                self.assertFalse(data["ok"])
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            err_data = json.loads(exc.read().decode("utf-8"))
            self.assertFalse(err_data["ok"])

    def test_api_filter_save_and_get(self):
        payload = {"fmc_max": 3500.0, "fliq_max": 1500.0, "use_lookahead": False}
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/filter",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
        self.assertTrue(data["ok"])

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/filter", timeout=10) as r:
            self.assertEqual(r.status, 200)
            loaded = json.loads(r.read().decode("utf-8"))
        self.assertEqual(loaded["fmc_max"], 3500.0)

    def test_api_gmgn_endpoints(self):
        # 1. /api/gmgn/status
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/gmgn/status", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["skills_count"], 17)

        # 2. /api/gmgn/wallets (A quién seguir)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/gmgn/wallets", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertGreater(len(data["wallets"]), 0)
            # Verifica que clasifique y distinga FOLLOW de AVOID
            verdicts = {w["verdict"] for w in data["wallets"]}
            self.assertIn("FOLLOW", verdicts)
            self.assertIn("AVOID", verdicts)

        # 3. /api/gmgn/tokens (Qué token comprar)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/gmgn/tokens", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertGreater(len(data["tokens"]), 0)
            first = data["tokens"][0]
            self.assertIn("confluence_score", first)
            self.assertIn("tp_targets", first)
            self.assertIn("sl_target", first)

        # 4. /api/gmgn/audit
        req_audit = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/gmgn/audit",
            data=json.dumps({"address": "SoL1111111111111111111111111111111111111112"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_audit, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("rug_ratio", data["audit"])

        # 5. /api/gmgn/paper_trade
        req_trade = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/gmgn/paper_trade",
            data=json.dumps({"mint": "SoL1111111111111111111111111111111111111112", "market_cap_usd": 10000.0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_trade, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["expected_slippage_pct"], -33.49)
            self.assertEqual(data["tp1_target"], 15000.0)

        # 6. /api/bot/status
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/bot/status", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("auto_bot_enabled", data)
            self.assertIn("open_positions", data)

        # 7. /api/patterns/summary
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/patterns/summary", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertGreater(len(data["patterns"]), 0)

        # 8. /api/bot/toggle
        req_toggle = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/bot/toggle",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_toggle, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("auto_bot_enabled", data)

        # 9. /api/bot/snipe and close
        req_snipe = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/bot/snipe",
            data=json.dumps({"mint": "TEST_MINT_123", "symbol": "TEST", "market_cap_usd": 12000.0, "size_sol": 1.0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_snipe, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["position"]["mint"], "TEST_MINT_123")

        req_close = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/bot/close",
            data=json.dumps({"mint": "TEST_MINT_123", "reason": "TEST_EXIT"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_close, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["position"]["exit_reason"], "TEST_EXIT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
