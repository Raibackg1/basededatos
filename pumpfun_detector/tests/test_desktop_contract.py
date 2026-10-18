"""Contrato de escritorio (P0.5): dashboard canónico, bus de eventos y exe WinForms.

Estos tests son el candado anti-regresión del contrato P0 entre:

- ``cockpit/server.py``  -> bloque ``dashboard`` de ``/api/bot/status``.
- ``cockpit/events.py``  -> ``/api/logs`` + ``/api/events`` (SSE).
- ``scripts/PumpfunCockpitLauncher.cs`` -> el exe de escritorio.

Regla dura: si el exe lee una clave, esa clave DEBE existir en el payload; y el
exe NO puede leer claves fuera del contrato (se detecta por regex sobre el C#).
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
_REPO = os.path.dirname(_PKG)
sys.path.insert(0, _PKG)

from cockpit.events import EVENT_KINDS, EventBus  # noqa: E402
from cockpit.server import (  # noqa: E402
    DASHBOARD_SCHEMA,
    PAPER_INITIAL_USD,
    CockpitState,
    compose_dashboard,
    compose_provenance,
    make_server,
)

_CS_PATH = os.path.join(_REPO, "scripts", "PumpfunCockpitLauncher.cs")


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
    con.execute("INSERT INTO Token VALUES(?,?,?,?,?)", ("T1", 3000.0, 1000.0, 6000.0, "2026-01-01"))
    con.commit()
    con.close()


class TestComposeDashboard(unittest.TestCase):
    """Función pura: el contrato del bloque `dashboard` (P0.1)."""

    def test_empty_summary_is_not_a_metric(self):
        """Sin datos NO se pinta 10,000/0.91/85.0: todo viaja como None."""
        d = compose_dashboard(None, loop_alive=False)
        self.assertEqual(d["schema"], DASHBOARD_SCHEMA)
        w = d["paper_wallet"]
        self.assertEqual(w["initial_usd"], PAPER_INITIAL_USD)
        self.assertIsNone(w["equity_usd"])
        self.assertIsNone(w["pnl_usd"])
        self.assertIsNone(w["pnl_sol"])
        self.assertIsNone(w["closed_count"])
        self.assertIsNone(w["exposure_usd"])
        self.assertIsNone(w["exposure_pct"])
        self.assertIsNone(w["sol_price_source"])
        self.assertEqual(d["open_positions"], [])
        self.assertEqual(d["shadow_vetos"], [])
        mk = d["metrics_known"]
        self.assertFalse(mk["pnl"])
        self.assertFalse(mk["evolution"])
        self.assertFalse(mk["feed"])
        self.assertFalse(mk["loop"])
        self.assertFalse(mk["exposure"])
        # Sin loop vivo y sin edad de feed medible, el dato es STALE.
        self.assertTrue(d["runtime"]["stale"])

    def test_with_pnl_equity_is_initial_plus_realized(self):
        d = compose_dashboard({"total_pnl_usd": 123.45, "total_pnl_sol": 0.5,
                               "closed_count": 7, "open_count": 0}, loop_alive=True)
        w = d["paper_wallet"]
        self.assertEqual(w["equity_usd"], round(PAPER_INITIAL_USD + 123.45, 2))
        self.assertEqual(w["closed_count"], 7)
        self.assertEqual(w["open_count"], 0)
        self.assertTrue(d["metrics_known"]["pnl"])

    def test_exposure_requires_declared_sol_price(self):
        summary = {
            "total_pnl_usd": 0.0,
            "open_count": 1,
            "open_positions": [{"mint": "m1", "symbol": "S1", "entry_mc": 1000.0,
                                "current_mc": 1100.0, "size_sol": 2.0, "pnl_pct": 10.0,
                                "pnl_usd": 30.0, "tp1_hit": False, "be_hit": False}],
        }
        without = compose_dashboard(summary, sol_price_usd=None, loop_alive=True)
        self.assertIsNone(without["paper_wallet"]["exposure_usd"])
        self.assertFalse(without["metrics_known"]["exposure"])

        with_price = compose_dashboard(summary, sol_price_usd=150.0,
                                       sol_price_source="assumed_default", loop_alive=True)
        self.assertEqual(with_price["paper_wallet"]["exposure_usd"], 300.0)
        self.assertEqual(with_price["paper_wallet"]["exposure_pct"], 3.0)
        self.assertEqual(with_price["paper_wallet"]["sol_price_source"], "assumed_default")
        self.assertFalse(with_price["metrics_known"]["sol_price_live"])

        live = compose_dashboard(summary, sol_price_usd=150.0, sol_price_source="live")
        self.assertTrue(live["metrics_known"]["sol_price_live"])

    def test_open_positions_are_normalized_not_raw(self):
        summary = {
            "total_pnl_usd": 0.0,
            "open_positions": [{"mint": "m1", "symbol": "S1", "entry_mc": float("nan"),
                                "current_mc": 1100.0, "size_sol": 1.0, "pnl_pct": 5.0,
                                "pnl_usd": 1.0, "tp1_hit": True, "be_hit": True}],
        }
        d = compose_dashboard(summary, loop_alive=True)
        self.assertEqual(len(d["open_positions"]), 1)
        pos = d["open_positions"][0]
        # NaN no es un dato: se publica como None (nunca como número "pintable").
        self.assertIsNone(pos["entry_mc"])
        self.assertEqual(pos["current_mc"], 1100.0)
        self.assertTrue(pos["tp1_hit"])
        self.assertTrue(pos["be_hit"])

    def test_shadow_vetos_passthrough_and_last_rule_change(self):
        summary = {
            "total_pnl_usd": 0.0,
            "evolution": {
                "shadow_summary": {"true_saves": 3, "false_vetos": 1,
                                   "avoided_loss_sum_pct": 42.5},
                "adaptive_rules": {"evolutions_count": 2},
                "shadow_vetos": [{"mint": "v1", "symbol": "V1", "verdict": "TRUE_SAVE",
                                  "entry_mc_usd": 900.0, "current_mc_usd": 400.0,
                                  "counterfactual_pnl_pct": -55.5, "veto_reason": "dev rugger"}],
                "evolution_events": [
                    {"rule_adjusted": "old_rule", "old_val": 1, "new_val": 2, "ts": 1.0},
                    {"rule_adjusted": "min_liquidity_usd", "old_val": 8000, "new_val": 12000,
                     "ts": 2.0},
                ],
            },
        }
        d = compose_dashboard(summary, loop_alive=True)
        ev = d["evolution"]
        self.assertEqual(ev["true_saves"], 3)
        self.assertEqual(ev["false_vetos"], 1)
        self.assertEqual(ev["avoided_loss_pct"], 42.5)
        self.assertEqual(ev["evolutions_count"], 2)
        self.assertEqual(ev["last_rule_change"]["rule"], "min_liquidity_usd")
        self.assertEqual(ev["last_rule_change"]["new"], 12000)
        self.assertEqual(d["shadow_vetos"][0]["verdict"], "TRUE_SAVE")
        self.assertTrue(d["metrics_known"]["evolution"])

    def test_stale_semantics(self):
        # Feed medido hace 5s => fresco, aunque el loop esté caído.
        fresh = compose_dashboard({}, feed_age_s=5.0, loop_alive=False)
        self.assertFalse(fresh["runtime"]["stale"])
        # Feed medido hace 300s => STALE.
        old = compose_dashboard({}, feed_age_s=300.0, loop_alive=True)
        self.assertTrue(old["runtime"]["stale"])
        # Sin medición de edad: depende de la prueba viva del loop.
        self.assertFalse(compose_dashboard({}, loop_alive=True)["runtime"]["stale"])
        self.assertTrue(compose_dashboard({}, loop_alive=False)["runtime"]["stale"])

    def test_stage_ages_are_computed(self):
        now = 1000.0
        d = compose_dashboard({}, last_stage={"screen": 970.0, "tick": 999.0},
                              now=now, loop_alive=True)
        rt = d["runtime"]
        self.assertAlmostEqual(rt["stage_age_s"]["screen"], 30.0, places=2)
        self.assertEqual(rt["last_stage"]["tick"], 999.0)

    def test_golden_key_contract(self):
        """Conjuntos EXACTOS de claves: cualquier cambio rompe el test a propósito."""
        d = compose_dashboard({}, loop_alive=True)
        self.assertEqual(set(d.keys()), {
            "schema", "ts", "paper_wallet", "evolution", "runtime",
            "metrics_known", "open_positions", "shadow_vetos",
        })
        self.assertEqual(set(d["paper_wallet"].keys()), {
            "initial_usd", "equity_usd", "pnl_usd", "pnl_sol", "open_count",
            "closed_count", "exposure_usd", "exposure_pct",
            "sol_price_usd", "sol_price_source",
        })
        self.assertEqual(set(d["evolution"].keys()), {
            "true_saves", "false_vetos", "avoided_loss_pct", "evolutions_count",
            "last_rule_change",
        })
        self.assertEqual(set(d["runtime"].keys()), {
            "feed_source", "feed_age_s", "stale", "loop_alive", "last_stage",
            "stage_age_s", "feed_misses", "last_feed_error",
        })
        self.assertEqual(set(d["metrics_known"].keys()), {
            "pnl", "evolution", "feed", "loop", "exposure", "sol_price_live",
        })

    def test_provenance(self):
        d = compose_dashboard({}, feed_age_s=12.5, loop_alive=True)
        d["runtime"]["feed_source"] = "dexscreener"
        p = compose_provenance(d)
        self.assertEqual(set(p.keys()), {"source", "ts", "age_s", "stale"})
        self.assertEqual(p["source"], "dexscreener")
        self.assertEqual(p["age_s"], 12.5)
        self.assertFalse(p["stale"])
        self.assertEqual(p["ts"], d["ts"])


class TestEventBus(unittest.TestCase):
    """Ring buffer + JSONL + SSE (P0.3)."""

    def test_emit_since_and_stats(self):
        bus = EventBus(ring_size=5)
        bus.emit("stage", {"stage": "screen", "tokens": 3})
        e2 = bus.emit("trade_open", {"symbol": "S1"})
        self.assertEqual(e2["id"], 2)
        self.assertEqual(bus.latest_id(), 2)
        self.assertEqual([e["id"] for e in bus.since(1)], [2])
        self.assertEqual(len(bus.since(0, limit=1)), 1)
        stats = bus.stats()
        self.assertEqual(stats["emitted_total"], 2)
        self.assertEqual(stats["by_kind"], {"stage": 1, "trade_open": 1})
        self.assertEqual(stats["ring_size"], 5)

    def test_ring_is_bounded(self):
        bus = EventBus(ring_size=3)
        for i in range(10):
            bus.emit("stage", {"stage": "tick", "i": i})
        self.assertEqual(bus.stats()["buffered"], 3)
        self.assertEqual([e["id"] for e in bus.since(0)], [8, 9, 10])

    def test_unknown_kind_is_a_bug(self):
        bus = EventBus(ring_size=3)
        with self.assertRaises(ValueError):
            bus.emit("inventado", {})
        self.assertIn("stage", EVENT_KINDS)

    def test_wait_for_wakes_on_new_event(self):
        bus = EventBus(ring_size=10)
        got = []

        def _reader():
            got.extend(bus.wait_for(0, timeout=5.0))

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        time.sleep(0.1)
        bus.emit("evo", {"rule": "min_liquidity_usd"})
        t.join(timeout=5.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "evo")

    def test_jsonl_persistence_and_reload(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "events.jsonl")
        bus = EventBus(ring_size=10, jsonl_path=path)
        bus.emit("stage", {"stage": "boot"})
        bus.emit("error", {"stage": "loop", "error": "boom"})
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual([l["kind"] for l in lines], ["stage", "error"])

        reborn = EventBus(ring_size=10, jsonl_path=path)
        self.assertEqual(reborn.latest_id(), 2)
        self.assertEqual([e["kind"] for e in reborn.since(0)], ["stage", "error"])
        # Los ids no se reutilizan tras recargar.
        self.assertEqual(reborn.emit("stage", {})["id"], 3)

    def test_sse_format(self):
        bus = EventBus(ring_size=5)
        bus.emit("trade_close", {"symbol": "S1", "pnl_pct": -33.49})
        chunk = EventBus.format_sse(bus.since(0))
        self.assertIn("event: trade_close", chunk)
        self.assertIn("id: 1", chunk)
        self.assertIn('"pnl_pct": -33.49', chunk)
        self.assertTrue(chunk.endswith("\n\n"))


class TestExeSourceContract(unittest.TestCase):
    """El exe C# solo puede leer claves declaradas en este contrato."""

    # Claves del payload que el exe tiene permitido consultar (contrato cerrado).
    ALLOWED_KEYS = frozenset({
        # dashboard (raíz y sub-bloques)
        "dashboard", "paper_wallet", "evolution", "runtime",
        "open_positions", "shadow_vetos", "metrics_known",
        # paper_wallet
        "equity_usd", "pnl_usd", "pnl_sol", "open_count", "closed_count",
        "exposure_pct", "sol_price_source",
        # evolution
        "true_saves", "false_vetos", "avoided_loss_pct", "evolutions_count",
        "last_rule_change", "rule", "old", "new",
        # runtime
        "feed_source", "feed_age_s", "stale", "loop_alive",
        # posiciones abiertas
        "symbol", "mint", "entry_mc", "current_mc", "pnl_pct", "tp1_hit",
        "be_hit", "status",
        # shadow vetos
        "entry_mc_usd", "current_mc_usd", "counterfactual_pnl_pct", "verdict",
        "veto_reason",
        # /api/gmgn/tokens (TokenBuyRecommendation)
        "tokens", "market_cap_usd", "liquidity_usd", "admitted",
        # /api/logs y eventos
        "events", "latest_id", "id", "kind", "data",
        # payload de eventos (SummarizeEvent)
        "stage", "source", "vetados", "size_sol", "exit_reason", "calls",
        "errors", "last_error", "error", "auto_bot_enabled",
        # respuesta del chat IA
        "reply",
        # /api/pipeline/live & /api/pipeline/trace
        "traces", "funnel", "seen", "veto", "neutral", "executed", "veto_stage",
        "stages", "operator", "threshold", "source_kind", "value", "reason",
        # /api/dossier/inspect
        "dossier", "estimated_bundler_pct", "wash_trading_score", "dev_holding_pct",
        "same_name_count_24h", "dev_rug_count",
        # control & respuestas generales
        "ok", "position",
    })

    # Claves cuya lectura es obligatoria: si el exe deja de leerlas, algo se rompió.
    REQUIRED_READS = frozenset({
        "dashboard", "paper_wallet", "evolution", "runtime", "open_positions",
        "shadow_vetos", "equity_usd", "pnl_usd", "pnl_sol", "closed_count",
        "open_count", "exposure_pct", "true_saves", "false_vetos",
        "avoided_loss_pct", "evolutions_count", "last_rule_change",
        "feed_source", "feed_age_s", "stale", "loop_alive", "entry_mc",
        "current_mc", "tp1_hit", "be_hit", "entry_mc_usd", "current_mc_usd",
        "counterfactual_pnl_pct", "verdict", "veto_reason", "events",
        "latest_id", "id", "kind", "data",
    })

    KEY_CALL_RE = re.compile(
        r'Get(?:Num|Str|Dict|Bool|List)\(\s*[A-Za-z_][\w\.]*\s*,\s*"([^"]+)"')
    HASVAL_RE = re.compile(
        r'HasVal\(\s*[A-Za-z_][\w\.]*\s*,\s*"([^"]+)"')

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(_CS_PATH):
            raise unittest.SkipTest(f"fuente C# ausente: {_CS_PATH}")
        with open(_CS_PATH, "r", encoding="utf-8") as fh:
            cls.source = fh.read()
        cls.keys = set(cls.KEY_CALL_RE.findall(cls.source))
        cls.keys |= set(cls.HASVAL_RE.findall(cls.source))

    def test_all_read_keys_are_in_contract(self):
        unknown = sorted(self.keys - self.ALLOWED_KEYS)
        self.assertEqual(unknown, [],
                         f"el exe lee claves fuera del contrato: {unknown}")

    def test_required_keys_are_still_read(self):
        missing = sorted(self.REQUIRED_READS - self.keys)
        self.assertEqual(missing, [], f"el exe dejó de leer: {missing}")

    def test_legacy_dead_keys_are_gone(self):
        """Claves que causaban las tarjetas vacías (bug raíz) no deben volver."""
        for dead in ("signal_action", "balance_usd", "net_saved_usd"):
            self.assertNotIn(dead, self.keys,
                             f"clave muerta reintroducida en el exe: {dead}")

    def test_reads_dashboard_not_heuristic_blocks(self):
        # El exe debe basarse en dashboard.{paper_wallet,evolution,runtime}.
        for needed in ('GetDict(botData, "dashboard")',
                       'GetDict(dash, "paper_wallet")',
                       'GetDict(dash, "evolution")',
                       'GetDict(dash, "runtime")'):
            self.assertIn(needed, self.source)

    def test_chat_payload_matches_server(self):
        # El server lee params["message"]; el exe NO puede volver a mandar "prompt".
        self.assertIn('string.Format("{{\\"message\\": \\"{0}\\"}}", EscapeJson(prompt))',
                      self.source)
        self.assertNotIn('\\"prompt\\"', self.source)

    def test_process_lifecycle_cleanup_on_form_closing(self):
        """El exe debe limpiar el servidor hijo y la bandeja en FormClosing."""
        self.assertIn("FormClosing", self.source)

    def test_dwm_dark_mode_supported(self):
        """El exe debe solicitar DWM dark mode en la barra de título nativa."""
        self.assertIn("DwmSetWindowAttribute", self.source)

    def test_bot_controls_and_pipeline_integrated(self):
        """El exe debe integrar toggle de bot, cierre de trade y pipeline en vivo."""
        self.assertIn("/api/bot/toggle", self.source)
        self.assertIn("/api/bot/close", self.source)
        self.assertIn("/api/pipeline/live", self.source)
        self.assertIn("/api/dossier/inspect", self.source)


class TestDesktopEndpoints(unittest.TestCase):
    """Smoke end-to-end del servidor: /api/bot/status, /api/logs y SSE."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        _make_db(self.db)
        self.snap_path = os.path.join(self.tmp, "snap.json")
        self.state = CockpitState(self.snap_path, self.db, demo_mode=True,
                                  feed=_NullFeed())
        self.state.rebuild()
        self.srv = make_server(self.state, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.state._stop_loop.set()
        self.srv.shutdown()
        self.srv.server_close()

    def _get_json(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            self.assertEqual(r.status, 200)
            return json.loads(r.read().decode("utf-8"))

    def test_bot_status_exposes_dashboard_contract(self):
        data = self._get_json("/api/bot/status")
        self.assertTrue(data["ok"])
        dash = data["dashboard"]
        self.assertEqual(dash["schema"], DASHBOARD_SCHEMA)
        self.assertIn("paper_wallet", dash)
        self.assertIn("evolution", dash)
        self.assertIn("runtime", dash)
        self.assertIsInstance(dash["open_positions"], list)
        self.assertIsInstance(dash["shadow_vetos"], list)
        # El exe lee el SOL precio declarado: siempre debe viajar con su fuente.
        self.assertEqual(dash["paper_wallet"]["sol_price_source"], "assumed_default")
        # La provenance refleja la fuente REAL del feed inyectado (aquí, el feed nulo).
        self.assertEqual(data["provenance"]["source"], "null")
        # Composición: las claves legacy del summary siguen disponibles arriba.
        self.assertIn("open_count", data)

    def test_logs_endpoint_streams_emitted_events(self):
        first = self._get_json("/api/logs?since=0")
        self.assertTrue(first["ok"])
        self.assertEqual(first["events"], [])
        base = first["latest_id"]

        self.state.emit_event("stage", {"stage": "screen", "tokens": 4,
                                        "admitted": 1, "vetados": 3})
        second = self._get_json(f"/api/logs?since={base}")
        self.assertEqual(second["count"], 1)
        ev = second["events"][0]
        self.assertGreater(ev["id"], base)
        self.assertEqual(ev["kind"], "stage")
        self.assertEqual(ev["data"]["tokens"], 4)
        # Ya consumido: el cursor no repite eventos.
        third = self._get_json(f"/api/logs?since={ev['id']}")
        self.assertEqual(third["count"], 0)

    def test_sse_endpoint_emits_event_block(self):
        self.state.emit_event("stage", {"stage": "screen"})
        self.state.emit_event("trade_open", {"symbol": "SSE1", "size_sol": 1.0})
        # `since=0` significa "desde ahora" (live tail): para ver un evento ya
        # emitido hay que pedir el cursor inmediatamente ANTERIOR a él.
        cursor = self.state.events.latest_id() - 1
        url = f"http://127.0.0.1:{self.port}/api/events?since={cursor}"
        with urllib.request.urlopen(url, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers.get_content_type(), "text/event-stream")
            lines = []
            for _ in range(8):
                lines.append(r.readline().decode("utf-8"))
                if "event: trade_open" in "".join(lines) and "data: " in "".join(lines):
                    break
        body = "".join(lines)
        self.assertIn("event: trade_open", body)
        self.assertIn("data: ", body)
        self.assertIn("SSE1", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
