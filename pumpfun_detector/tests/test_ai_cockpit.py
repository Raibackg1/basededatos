"""
Tests unitarios y de integración para el motor de IA, cuantificación y servidor del Cockpit.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cockpit.server import CockpitState, make_server
from detector.ai_engine import AIEngine
from detector.ai_quant_model import AIQuantModel
from detector.browser_agent import BrowserAgent


def _make_test_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE Token(mint TEXT PRIMARY KEY, firstMarketCap REAL,
                           firstLiquidity REAL, marketCap REAL, rise50At TEXT,
                           volume24h REAL, holderCount INTEGER, devBalancePct REAL,
                           top10HoldersPct REAL, symbol TEXT, name TEXT, firstSeenAt TEXT,
                           bundlerHoldingPct REAL, changeM5 REAL, liquidity REAL);
        CREATE TABLE PaperPosition(mint TEXT, pnlPct REAL, status TEXT);
        CREATE TABLE WalletProfile(id TEXT PRIMARY KEY, label TEXT, solBalance REAL,
                                   closedTrades INTEGER, wins INTEGER, txsScanned INTEGER);
    """)
    # 15 tokens ganadores
    for i in range(15):
        con.execute(
            "INSERT INTO Token VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"WIN{i}", 3000.0, 1000.0, 6000.0, "2026-01-01" if i < 12 else None,
             5000.0, 15, 0.2, 18.0, f"SYM_W{i}", f"Winner {i}", f"2026-01-0{i+1}", 5.0, 12.0, 1000.0),
        )
    # 15 tokens perdedores
    for i in range(15):
        con.execute(
            "INSERT INTO Token VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"LOSE{i}", 3000.0, 1000.0, 2000.0, "2026-01-01" if i == 0 else None,
             100.0, 2, 8.5, 65.0, f"SYM_L{i}", f"Loser {i}", f"2026-01-0{i+1}", 25.0, -35.0, 1000.0),
        )
    # Wallets
    con.execute("INSERT INTO WalletProfile VALUES(?,?,?,?,?,?)",
                ("WALLET_TOP", "Ballena Alpha", 150.0, 20, 15, 100))
    con.execute("INSERT INTO WalletProfile VALUES(?,?,?,?,?,?)",
                ("WALLET_BLEED", "Trader C", 1.5, 30, 5, 50))
    con.commit()
    con.close()


class TestAIQuantModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test_tokens.db")
        _make_test_db(self.db)
        self.ckpt = os.path.join(self.tmp, "model.pkl")
        self.model = AIQuantModel(checkpoint_path=self.ckpt, db_path=self.db)

    def test_model_training_and_boost(self):
        meta = self.model.train_and_boost()
        self.assertIn(meta["status"], ("OPTIMAL_BOOSTED", "INSUFFICIENT_DATA"))
        self.assertIn("features", meta)

    def test_predict_token_bullish_structure(self):
        pred = self.model.predict_token(
            first_mc=3500.0,
            first_liq=2500.0,
            volume_24h=12000.0,
            holder_count=15,
            dev_balance_pct=0.0,
            top10_pct=14.0,
        )
        self.assertIn("confluence_score", pred)
        self.assertIn("expected_ev", pred)
        self.assertIn("verdict", pred)
        self.assertGreaterEqual(pred["confluence_score"], 0.0)
        self.assertLessEqual(pred["confluence_score"], 100.0)

    def test_predict_token_veto_rules(self):
        # Dev dumping > 1.5%
        pred_dev = self.model.predict_token(
            first_mc=5000.0,
            first_liq=1500.0,
            dev_balance_pct=6.5,
            top10_pct=20.0,
        )
        self.assertEqual(pred_dev["verdict"], "VETO")
        self.assertIn("Dev holding excesivo", pred_dev["veto_reason"])

        # Cabal > 30%
        pred_cabal = self.model.predict_token(
            first_mc=5000.0,
            first_liq=1500.0,
            dev_balance_pct=0.0,
            top10_pct=45.0,
        )
        self.assertEqual(pred_cabal["verdict"], "VETO")
        self.assertIn("Cabal", pred_cabal["veto_reason"])


class TestAIEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp, "ai_config.json")
        self.engine = AIEngine(config_path=self.cfg_path)

    def test_status_structure(self):
        st = self.engine.get_status()
        self.assertTrue(st["ok"])
        self.assertIn("providers", st)
        self.assertIn("quant_model", st)
        self.assertIn("browser_agent", st)
        self.assertGreater(st["goals_count"], 0)

    def test_command_boost(self):
        res = self.engine.process_chat("/boost")
        self.assertTrue(res["ok"])
        self.assertEqual(res["command"], "boost")
        self.assertIn("Recalibrado", res["response"])

    def test_command_boost_deep_and_fast(self):
        res_deep = self.engine.process_chat("/boost deep")
        self.assertTrue(res_deep["ok"])
        self.assertEqual(res_deep["data"]["hyperparameters"]["max_iter"], 160)

        res_fast = self.engine.process_chat("/boost fast lr=0.05")
        self.assertTrue(res_fast["ok"])
        self.assertEqual(res_fast["data"]["hyperparameters"]["max_iter"], 60)
        self.assertEqual(res_fast["data"]["hyperparameters"]["learning_rate"], 0.05)

    def test_command_goal(self):
        # List goals
        res = self.engine.process_chat("/goal")
        self.assertTrue(res["ok"])
        self.assertEqual(res["command"], "goal")
        self.assertIn("Tablero de Objetivos", res["response"])

        # Add goal
        res_add = self.engine.process_chat("/goal add Mantener Profit Factor > 1.35")
        self.assertTrue(res_add["ok"])
        self.assertIn("Mantener Profit Factor", res_add["response"])

        # Eval goals autonomously
        res_eval = self.engine.process_chat("/goal eval", context={"winrate_pct": 65.0, "open_count": 2})
        self.assertTrue(res_eval["ok"])
        self.assertIn("Evaluación Autónoma", res_eval["response"])

        # Remove goal
        res_del = self.engine.process_chat("/goal remove GOAL-01")
        self.assertTrue(res_del["ok"])
        self.assertIn("eliminado", res_del["response"])

    def test_intelligent_local_reasoning(self):
        # Asegurar evaluación local determinista sin depender de cuotas de red remotas
        orig_gemini = self.engine._call_gemini
        orig_groq = self.engine._call_groq
        orig_anthropic = self.engine._call_anthropic
        orig_ollama = self.engine._call_ollama
        self.engine._call_gemini = lambda *a, **kw: None
        self.engine._call_groq = lambda *a, **kw: None
        self.engine._call_anthropic = lambda *a, **kw: None
        self.engine._call_ollama = lambda *a, **kw: None
        try:
            # Pregunta sobre token
            res_token = self.engine.process_chat("Analiza el token MOCA")
            self.assertTrue(res_token["ok"])
            self.assertIn("moca", res_token["response"].lower())
            self.assertIn("Confluencia ML", res_token["response"])

            # Pregunta sobre regla de slippage
            res_slip = self.engine.process_chat("Explica la regla de slippage de pánico")
            self.assertTrue(res_slip["ok"])
            self.assertIn("-33.49%", res_slip["response"])
            self.assertIn("BP-001", res_slip["response"])

            # Pregunta sobre reglas de cabal
            res_cabal = self.engine.process_chat("¿Por qué vetas si el cabal supera 30%?")
            self.assertTrue(res_cabal["ok"])
            self.assertIn("30.0%", res_cabal["response"])
        finally:
            self.engine._call_gemini = orig_gemini
            self.engine._call_groq = orig_groq
            self.engine._call_anthropic = orig_anthropic
            self.engine._call_ollama = orig_ollama

    def test_fallback_chat_works(self):
        res = self.engine.process_chat("Hola, copiloto")
        self.assertTrue(res["ok"])
        self.assertEqual(res["command"], "chat")
        self.assertGreater(len(res["response"]), 10)


class TestServerAIEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t_ai.db")
        _make_test_db(self.db)
        self.snap_path = os.path.join(self.tmp, "snap.json")
        self.state = CockpitState(self.snap_path, self.db, demo_mode=True)
        self.state.rebuild()
        self.srv = make_server(self.state, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_api_ai_status(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ai/status", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("providers", data)

    def test_api_ai_goals(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ai/goals", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertGreater(len(data["goals"]), 0)

    def test_api_ai_chat_and_boost(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ai/boost",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["command"], "boost")

    def test_api_ai_config(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ai/config",
            data=json.dumps({"provider": "gemini", "model_gemini": "gemini-2.5-flash"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["config"]["provider"], "gemini")

    def test_api_ai_audit_endpoint(self):
        token_payload = {
            "mint": "TEST_MINT_AUDIT",
            "symbol": "AUDIT_COIN",
            "name": "Audit Coin",
            "market_cap_usd": 12000.0,
            "liquidity_usd": 3500.0,
            "dev_balance_pct": 0.5,
            "top10_concentration_pct": 18.0,
            "rug_ratio": 0.1,
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ai/audit",
            data=json.dumps(token_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("ml_evaluation", data)
            self.assertIn("ai_narrative", data)

    def test_api_ai_test_provider(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ai/test",
            data=json.dumps({"provider": "local"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("latency_ms", data)

    def test_api_ai_goal_eval(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ai/goal/eval",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertGreater(len(data["goals"]), 0)

    def test_api_ai_ollama_models(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ai/ollama/models", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIsInstance(data["models"], list)

    def test_api_screenshot_placeholder_svg(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ai/browser/screenshot", timeout=10) as r:
            self.assertEqual(r.status, 200)
            raw = r.read()
            self.assertTrue(b"<svg" in raw or raw.startswith(b"\x89PNG") or r.headers.get("Content-Type", "").startswith("image/"))

    def test_api_favicon_ico(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/favicon.ico", timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers.get("Content-Type"), "image/svg+xml")


if __name__ == "__main__":
    unittest.main(verbosity=2)
