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


def _make_perf_db(path):
    """DB con el esquema COMPLETO de PaperPosition (reason, timestamps, pnlUsd)."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE PaperPosition(
            mint TEXT, tokenId TEXT, pnlPct REAL, pnlUsd REAL, sizeSol REAL,
            status TEXT, reason TEXT, exitTime REAL, entryTime REAL);
    """)
    now = 1_800_000_000.0
    rows = [
        # (mint, pnlPct, pnlUsd, reason, exitTime)
        ("A", 120.0, 300.0, "TP", now - 10 * 86400),
        ("B", 60.0, 150.0, "TP", now - 9 * 86400),
        ("C", 20.0, 50.0, "BE", now - 8 * 86400),
        ("D", -35.49, -100.0, "SL", now - 7 * 86400),
        ("E", -35.49, -100.0, "SL", now - 55 * 86400),  # fuera de 30d
    ]
    for mint, pct, usd, reason, exit_t in rows:
        con.execute(
            "INSERT INTO PaperPosition(mint,tokenId,pnlPct,pnlUsd,sizeSol,status,reason,exitTime,entryTime)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (mint, mint, pct, usd, 1.0, "CLOSED", reason, exit_t, exit_t - 3600))
    con.execute(
        "INSERT INTO PaperPosition(mint,tokenId,pnlPct,pnlUsd,sizeSol,status,reason,exitTime,entryTime)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        ("OPEN1", "OPEN1", 5.0, 10.0, 1.0, "OPEN", None, now, now - 600))
    con.commit()
    con.close()


class TestPerformance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "perf.db")
        _make_perf_db(self.db)

    def _rows(self):
        con = snap._open_ro(self.db)
        try:
            return snap._scan_closed_trades(con)
        finally:
            con.close()

    def test_compute_counts_expectancy_and_profit_factor(self):
        rows = self._rows()
        res = snap.compute_performance(rows, period="all")
        self.assertTrue(res["ok"])
        self.assertEqual(res["n_trades"], 5)      # OPEN1 excluida
        self.assertEqual(res["wins"], 3)
        self.assertEqual(res["losses"], 2)
        self.assertEqual(res["breakeven"], 0)
        self.assertEqual(res["win_rate_pct"], 60.0)
        # avg_win=(120+60+20)/3=66.67 ; avg_loss=-35.49 ; expectancy=0.6*66.67+0.4*-35.49
        self.assertAlmostEqual(res["avg_win_pct"], 66.67, places=2)
        self.assertAlmostEqual(res["avg_loss_pct"], -35.49, places=2)
        self.assertAlmostEqual(res["expectancy_pct"], 25.8, places=1)
        # profit factor = (120+60+20)/|(-35.49-35.49)| = 200/70.98
        self.assertAlmostEqual(res["profit_factor"], 2.818, places=2)
        self.assertEqual(res["net_pnl_usd"], 300.0)

    def test_equity_curve_and_drawdown(self):
        rows = self._rows()
        res = snap.compute_performance(rows, period="all", initial_usd=10000.0)
        eq = res["equity"]
        self.assertEqual(eq["unit"], "usd")
        self.assertEqual(eq["initial"], 10000.0)
        self.assertEqual(len(eq["points"]), 5)
        # Ordenado por exitTime ascendente: el último trade cerrado (más reciente) define la equity
        self.assertAlmostEqual(eq["points"][-1]["value"], 10300.0, places=2)
        # Hay un drawdown real cuando llega la primera pérdida tras el pico
        self.assertGreater(eq["max_drawdown"], 0.0)

    def test_period_filter_excludes_old_trades(self):
        rows = self._rows()
        now = 1_800_000_000.0
        res = snap.compute_performance(rows, period="30d", now=now)
        self.assertEqual(res["n_trades"], 4)      # E queda fuera (55d)
        self.assertEqual(res["losses"], 1)

    def test_millisecond_timestamps_are_normalized(self):
        # La base canónica guarda exitTime en MILISEGUNDOS: el filtro de período
        # debe funcionar igual que si viniera en segundos.
        now = 1_800_000_000.0
        rows = [
            {"pnl_pct": 10.0, "pnl_usd": 1.0, "reason": "TP", "exit_time": (now - 3 * 86400) * 1000},
            {"pnl_pct": -5.0, "pnl_usd": -1.0, "reason": "SL", "exit_time": (now - 40 * 86400) * 1000},
        ]
        res = snap.compute_performance(rows, period="30d", now=now)
        self.assertEqual(res["n_trades"], 1)
        self.assertEqual(res["wins"], 1)
        self.assertLess(res["equity"]["points"][0]["t"], now)  # ya en segundos

    def test_histogram_and_reason_breakdown(self):
        rows = self._rows()
        res = snap.compute_performance(rows, period="all")
        self.assertTrue(res["histogram"])
        self.assertEqual(sum(b["count"] for b in res["histogram"]), 5)
        reasons = {b["reason"]: b["count"] for b in res["by_reason"]}
        self.assertEqual(reasons.get("TP"), 2)
        self.assertEqual(reasons.get("SL"), 2)
        self.assertEqual(reasons.get("BE"), 1)

    def test_pct_curve_when_usd_missing(self):
        rows = [{"pnl_pct": 10.0, "pnl_usd": None, "reason": None, "exit_time": None}]
        res = snap.compute_performance(rows)
        self.assertEqual(res["equity"]["unit"], "pct")
        self.assertIsNone(res["net_pnl_usd"])
        self.assertFalse(res["metrics_known"]["pnl_usd"])

    def test_empty_sample_is_honest(self):
        res = snap.compute_performance([], period="all")
        self.assertTrue(res["ok"])
        self.assertEqual(res["n_trades"], 0)
        self.assertIsNone(res["win_rate_pct"])
        self.assertIsNone(res["profit_factor"])
        self.assertEqual(res["equity"]["points"], [])

    def test_invalid_period(self):
        res = snap.compute_performance([], period="banana")
        self.assertFalse(res["ok"])
        self.assertIn("period", res["error"])

    def test_performance_report_missing_db(self):
        res = snap.performance_report(os.path.join(self.tmp, "nope.db"))
        self.assertFalse(res["ok"])
        self.assertIn("no encontrada", res["error"])

    def test_evaluate_filter_uses_measured_averages_for_ev(self):
        # El EV ya no es un TP/SL nominal: sale de las medias reales de la muestra.
        tokens = [(f"T{i}", 1000.0, 500.0, 2000.0, False) for i in range(4)]
        paper = {"T0": [100.0], "T1": [50.0], "T2": [-30.0], "T3": [-10.0]}
        res = snap.evaluate_custom_filter("x.db", tokens=tokens, paper=paper)
        self.assertTrue(res["ok"])
        self.assertEqual(res["trade_wr_pct"], 50.0)
        self.assertAlmostEqual(res["avg_win_pct"], 75.0, places=2)
        self.assertAlmostEqual(res["avg_loss_pct"], -20.0, places=2)
        # EV = 0.5*75 + 0.5*(-20) = 27.5  (no el 25% hardcodeado)
        self.assertAlmostEqual(res["ev_pct"], 27.5, places=2)


def _make_stub_view_db(path):
    """Base con la MISMA forma parcial que produce scripts/init_pumpscope_db.py.

    ``Token`` es una vista sin ``marketCap`` ni ``rise50At``: el caso que dejaba
    el snapshot muerto (ok:false) para siempre.
    """
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE tokens(mint TEXT, name TEXT, symbol TEXT, created_at TEXT,
                            dev_wallet TEXT, total_supply INTEGER, complete INTEGER);
        CREATE VIEW Token AS
            SELECT mint AS id, mint, name, symbol, created_at, dev_wallet,
                   0 AS firstMarketCap, 0 AS firstLiquidity, 0 AS currentMarketCap
            FROM tokens;
        CREATE TABLE PaperPosition(mint TEXT, pnlPct REAL, status TEXT, reason TEXT);
    """)
    for i in range(4):
        con.execute("INSERT INTO tokens VALUES(?,?,?,?,?,?,?)",
                    (f"STUB{i}", f"Stub {i}", f"S{i}", "2026-01-01", "dev", 1_000_000, 0))
    con.execute("INSERT INTO PaperPosition VALUES(?,?,?,?)", ("STUB0", 50.0, "CLOSED", "TP"))
    con.execute("INSERT INTO PaperPosition VALUES(?,?,?,?)", ("STUB1", -33.49, "CLOSED", "SL"))
    con.commit()
    con.close()


class TestDbResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "real.db")
        _make_db(self.db)

    def test_explicit_existing_path_wins(self):
        res = snap.resolve_db_path(self.db)
        self.assertTrue(res["exists"])
        self.assertEqual(res["source"], "explicit")
        self.assertEqual(res["path"], os.path.abspath(self.db))

    def test_env_used_when_explicit_is_bogus(self):
        bogus = os.path.join(self.tmp, "nope.db")
        prev = os.environ.get("PUMPSCOPE_DB")
        os.environ["PUMPSCOPE_DB"] = self.db
        try:
            res = snap.resolve_db_path(bogus)
        finally:
            if prev is None:
                os.environ.pop("PUMPSCOPE_DB", None)
            else:
                os.environ["PUMPSCOPE_DB"] = prev
        self.assertTrue(res["exists"])
        self.assertEqual(res["source"], "env:PUMPSCOPE_DB")
        self.assertEqual(res["path"], os.path.abspath(self.db))
        self.assertEqual(res["tried"][0], os.path.abspath(bogus))

    def test_result_is_never_a_phantom_path(self):
        res = snap.resolve_db_path(os.path.join(self.tmp, "missing_xyz.db"))
        self.assertEqual(res["exists"], bool(res["path"]))
        if res["path"]:
            self.assertTrue(os.path.exists(res["path"]))
        self.assertTrue(res["tried"])


class TestSchemaAdaptive(unittest.TestCase):
    """La base parcial debe degradar con honestidad, nunca falsear un 0%."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "stub.db")
        _make_stub_view_db(self.db)

    def test_schema_report_flags_missing_columns(self):
        rep = snap.schema_report(self.db)
        self.assertTrue(rep["available"])
        self.assertTrue(rep["has_token"])
        self.assertTrue(rep["has_paper"])
        self.assertIn("marketCap", rep["missing_token_columns"])
        self.assertIn("rise50At", rep["missing_token_columns"])
        self.assertEqual(rep["token_fallbacks"]["marketCap"], ["currentMarketCap"])
        self.assertFalse(rep["rise50_measurable"])
        self.assertTrue(rep["paper_measurable"])

    def test_schema_report_missing_db(self):
        rep = snap.schema_report(os.path.join(self.tmp, "nope.db"))
        self.assertFalse(rep["available"])
        self.assertIn("no encontrada", rep["error"])

    def test_scan_tokens_marks_hit50_as_not_measurable(self):
        con = snap._open_ro(self.db)
        try:
            tokens = snap._scan_tokens(con)
        finally:
            con.close()
        self.assertEqual(len(tokens), 4)
        for t in tokens:
            self.assertIsNone(t[4])          # None != False
            self.assertEqual(t[1], 0.0)      # firstMarketCap placeholder real

    def test_snapshot_builds_ok_and_rise50_is_null_not_zero(self):
        s = snap.build_snapshot(self.db, sample=10)
        self.assertTrue(s["ok"], s.get("error"))
        u = s["universe"]
        self.assertEqual(u["n_tokens"], 4)
        self.assertEqual(u["n_tokens_hit50_measurable"], 0)
        self.assertIsNone(u["rise50_pct"])        # antes: 0.0 (falso)
        self.assertEqual(u["paper_wr_pct"], 50.0)  # el paper SÍ es real
        self.assertIn("db_schema", s)
        self.assertFalse(s["db_schema"]["rise50_measurable"])
        for seg in s["bias"]:
            self.assertIsNone(seg["hit50_pct"])

    def test_missing_token_table_reports_error_not_crash(self):
        empty = os.path.join(self.tmp, "empty.db")
        sqlite3.connect(empty).close()
        s = snap.build_snapshot(empty, sample=1)
        self.assertFalse(s["ok"])
        self.assertTrue(s["error"])


class TestWiringAndAutoRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        _make_db(self.db)
        self.snap_path = os.path.join(self.tmp, "runtime", "snapshot.json")

    def test_health_states(self):
        state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        self.assertEqual(state.snapshot_health()["state"], "missing")
        snap.write_snapshot({"ok": False, "error": "veneno"}, self.snap_path)
        self.assertEqual(state.snapshot_health()["state"], "corrupt")
        state.rebuild()
        self.assertEqual(state.snapshot_health()["state"], "fresh")

    def test_ensure_snapshot_repairs_a_poisoned_snapshot(self):
        snap.write_snapshot({"ok": False, "error": "veneno"}, self.snap_path)
        state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        result = state.ensure_snapshot()
        self.assertTrue(result["rebuilt"])          # antes: nunca se reconstruía
        self.assertEqual(result["was"], "corrupt")
        self.assertTrue(result["ok"])
        # Y queda fresco: una segunda pasada no vuelve a reconstruir
        self.assertFalse(state.ensure_snapshot()["rebuilt"])

    def test_ensure_snapshot_keeps_fresh_untouched(self):
        state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        state.rebuild()
        mtime = os.path.getmtime(self.snap_path)
        self.assertFalse(state.ensure_snapshot()["rebuilt"])
        self.assertEqual(os.path.getmtime(self.snap_path), mtime)

    def test_wiring_reports_every_subsystem(self):
        state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        state.rebuild()
        w = state.wiring()
        self.assertTrue(w["ok"])
        self.assertEqual(w["schema"], "wiring/1")
        self.assertEqual(w["database"]["resolved_from"], "explicit")
        self.assertTrue(w["database"]["exists"])
        self.assertTrue(w["database"]["read_only"])
        self.assertEqual(w["snapshot"]["state"], "fresh")
        self.assertEqual(w["feed"]["source"], "null")
        self.assertIn("alive", w["loop"])
        self.assertFalse(w["cycle"]["alive"])       # sin auto_loop no hay ciclo
        self.assertIn("open_count", w["paper"])
        self.assertIn("ml", w)

    def test_resolve_db_switches_to_found_path(self):
        bogus = os.path.join(self.tmp, "no-existe.db")
        state = CockpitState(self.snap_path, bogus, demo_mode=True, feed=_NullFeed())
        resolved = state.resolve_db()
        self.assertTrue(resolved["exists"])
        self.assertEqual(state.db_path, resolved["path"])

    def test_cycle_thread_can_start_and_stop(self):
        state = CockpitState(self.snap_path, self.db, demo_mode=True, feed=_NullFeed())
        self.assertFalse(state.cycle_alive())
        state.CYCLE_INTERVAL_S = 0.05
        state.start_cycle_thread()
        try:
            self.assertTrue(state.cycle_alive())
        finally:
            state.stop_cycle_thread()
        self.assertFalse(state.cycle_alive())


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

    def test_probe_cockpit_detects_live_server(self):
        from cockpit.server import probe_cockpit
        data = probe_cockpit("127.0.0.1", self.port, timeout_s=3.0)
        self.assertIsNotNone(data, "el sondeo debe reconocer un cockpit vivo")
        self.assertTrue(data["ok"])
        # Puerto sin servidor: fail-closed, no inventa un cockpit.
        self.assertIsNone(probe_cockpit("127.0.0.1", 1, timeout_s=1.0))

    def test_launcher_attaches_instead_of_double_binding(self):
        """Instancia única: un segundo arranque sobre el mismo puerto NO duplica.

        Regresión: en Windows ``SO_REUSEADDR`` permitía dos listeners en el
        mismo puerto; el navegador recibía ERR_EMPTY_RESPONSE de forma aleatoria.
        """
        from cockpit import launch as launch_mod
        code = launch_mod.main(["--headless", "--port", str(self.port),
                                "--host", "127.0.0.1"])
        self.assertEqual(code, 0, "debe reutilizar el cockpit vivo y salir limpio")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/state", timeout=10) as r:
            self.assertEqual(r.status, 200)

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

    def test_api_wiring_endpoint(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/wiring", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["schema"], "wiring/1")
        self.assertTrue(data["database"]["exists"])
        self.assertIn(data["snapshot"]["state"], ("fresh", "stale"))
        self.assertIn("missing_token_columns", data["database"])

    def test_api_performance_endpoint(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/performance?period=all", timeout=10) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["n_trades"], 4)   # OPENX excluida
        self.assertIn("equity", data)
        self.assertIn("histogram", data)
        self.assertIn("by_reason", data)
        self.assertEqual(data["source"]["read_only"], True)

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
