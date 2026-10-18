"""Tests del feed real (DexScreener) y del paper-forward con precios reales.

Sin red: el transporte HTTP, el reloj y el sleep son inyectados. Se verifica que
el bot del cockpit ya NO usa un paseo aleatorio y que persiste en LiveStore.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.gmgn_bridge import GMGNBridge, LivePaperTrader
from detector.live_store import LiveStore
from detector.market_feed import MarketFeed


def _payload(pairs):
    return {"pairs": pairs}


def _pair(mc, liq=1000.0, symbol="TKN", name="Token", m5=0.0, vol5=0.0):
    return {
        "marketCap": mc,
        "fdv": mc,
        "liquidity": {"usd": liq},
        "priceUsd": "0.001",
        "baseToken": {"symbol": symbol, "name": name},
        "priceChange": {"m5": m5},
        "volume": {"m5": vol5},
    }


class _Clock:
    """Reloj monotónico controlado por el test (sin sleep real)."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _FakeFeed:
    """Feed de mercado controlado, sin red."""

    def __init__(self, prices=None, discovered=None):
        self.prices = dict(prices or {})
        self.discovered = list(discovered or [])
        self.calls = 0
        self.last_error = None

    def market_cap_usd(self, mint):
        self.calls += 1
        return self.prices.get(mint)

    def token_snapshot(self, mint):
        mc = self.prices.get(mint)
        return None if mc is None else {"mint": mint, "market_cap_usd": mc}

    def discover_new_tokens(self, limit=10):
        return self.discovered[:limit]

    def health(self):
        return {"source": "fake", "calls": self.calls}


class TestMarketFeed(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.urls = []

    def _feed(self, responses):
        def http_get(url):
            self.urls.append(url)
            return responses.get(url)
        return MarketFeed(http_get=http_get, clock=self.clock, sleep=lambda s: None,
                          min_interval_s=0.0)

    def test_market_cap_parses_and_caches(self):
        url = "https://api.dexscreener.com/latest/dex/tokens/MINT1"
        feed = self._feed({url: _payload([_pair(12345.0)])})
        self.assertEqual(feed.market_cap_usd("MINT1"), 12345.0)
        # Segunda llamada dentro del TTL: sale de caché, no repite el GET.
        self.assertEqual(feed.market_cap_usd("MINT1"), 12345.0)
        self.assertEqual(len(self.urls), 1)
        self.assertEqual(feed.health()["cache_hits"], 1)

    def test_market_cap_returns_none_on_failure(self):
        url = "https://api.dexscreener.com/latest/dex/tokens/GONE"
        feed = self._feed({url: None})
        self.assertIsNone(feed.market_cap_usd("GONE"))
        self.assertGreaterEqual(feed.health()["errors"], 1)

    def test_market_cap_prefers_highest_liquidity(self):
        url = "https://api.dexscreener.com/latest/dex/tokens/M2"
        feed = self._feed({url: _payload([_pair(1000.0, liq=5.0), _pair(9000.0, liq=500.0)])})
        self.assertEqual(feed.market_cap_usd("M2"), 9000.0)

    def test_market_cap_none_when_no_valid_pair(self):
        url = "https://api.dexscreener.com/latest/dex/tokens/M3"
        feed = self._feed({url: _payload([_pair(0.0)])})
        self.assertIsNone(feed.market_cap_usd("M3"))

    def test_discover_filters_non_solana(self):
        feed = self._feed({
            "https://api.dexscreener.com/token-profiles/latest/v1": [
                {"chainId": "solana", "tokenAddress": "SOLA"},
                {"chainId": "ethereum", "tokenAddress": "ETHX"},
            ],
            "https://api.dexscreener.com/latest/dex/tokens/SOLA": _payload([_pair(7000.0)]),
        })
        out = feed.discover_new_tokens(10)
        self.assertEqual([t["mint"] for t in out], ["SOLA"])

    def test_health_reports_source(self):
        feed = self._feed({})
        self.assertEqual(feed.health()["source"], "dexscreener")


class TestLivePaperTrader(unittest.TestCase):
    def test_no_seed_positions(self):
        t = LivePaperTrader(feed=_FakeFeed())
        self.assertEqual(t.open_positions, {})
        self.assertEqual(t.closed_positions, [])

    def test_tick_uses_real_price_not_random(self):
        feed = _FakeFeed({"MINTX": 13000.0})
        t = LivePaperTrader(feed=feed)
        t.open_trade("MINTX", "X", 10000.0, size_sol=1.0)
        t.tick_prices()
        pos = t.open_positions["MINTX"]
        self.assertAlmostEqual(pos.pnl_pct, 30.0, places=2)
        self.assertTrue(pos.be_hit)          # cruzó +25%
        self.assertFalse(pos.tp1_hit)        # no llegó a +50%
        # Determinista: repetir el tick con el mismo precio da el mismo resultado.
        t.tick_prices()
        self.assertAlmostEqual(t.open_positions["MINTX"].pnl_pct, 30.0, places=2)

    def test_tick_skips_when_no_real_price(self):
        feed = _FakeFeed({})  # sin dato para el mint
        t = LivePaperTrader(feed=feed)
        t.open_trade("MINTY", "Y", 10000.0, size_sol=1.0)
        t.tick_prices()
        self.assertEqual(t.open_positions["MINTY"].current_mc, 10000.0)  # no se movió
        self.assertEqual(t.open_positions["MINTY"].pnl_pct, 0.0)
        self.assertEqual(t.feed_misses, 1)

    def test_tp2_closes_at_150(self):
        feed = _FakeFeed({"MINTZ": 25000.0})
        t = LivePaperTrader(feed=feed)
        t.open_trade("MINTZ", "Z", 10000.0, size_sol=1.0)
        t.tick_prices()
        self.assertNotIn("MINTZ", t.open_positions)
        self.assertEqual(t.closed_positions[0].exit_reason, "TP2_RUNNER_150%_TAKEN")

    def test_sl_closes_on_real_drop(self):
        feed = _FakeFeed({"MINTW": 6000.0})  # -40% < -33.49%
        t = LivePaperTrader(feed=feed)
        t.open_trade("MINTW", "W", 10000.0, size_sol=1.0)
        t.tick_prices()
        self.assertEqual(t.closed_positions[0].exit_reason, "PANIC_SL_SLIPPAGE_EXIT")

    def test_persists_decision_and_outcome(self):
        store = LiveStore(":memory:")
        try:
            feed = _FakeFeed({"MINTP": 25000.0})
            t = LivePaperTrader(feed=feed, store=store)
            t.open_trade("MINTP", "P", 10000.0, size_sol=1.0)
            self.assertEqual(store.count_decisions(origin="cockpit_forward"), 1)
            t.tick_prices()
            audit = store.net_audit(origin="cockpit_forward", admitted_only=True)
            self.assertEqual(audit["resolved"], 1)
            self.assertAlmostEqual(audit["total_pnl_sol"], 1.5, places=6)
        finally:
            store.close()

    def test_summary_exposes_feed_and_net_audit(self):
        store = LiveStore(":memory:")
        try:
            t = LivePaperTrader(feed=_FakeFeed({"M": 12000.0}), store=store)
            t.open_trade("M", "M", 10000.0, size_sol=1.0)
            t.tick_prices()
            s = t.get_summary()
            self.assertEqual(s["mode"], "PAPER_FORWARD_REAL_PRICES")
            self.assertEqual(s["feed"]["source"], "fake")
            self.assertIn("net_audit", s)
        finally:
            store.close()


class TestScreenTokensWithFeed(unittest.TestCase):
    def test_screen_uses_feed_and_marks_metrics_unknown(self):
        feed = _FakeFeed(discovered=[{
            "mint": "REALMINT", "market_cap_usd": 9000.0, "liquidity_usd": 2000.0,
            "symbol": "REAL", "name": "Real Token",
        }])
        bridge = GMGNBridge(cli_path="__missing__", feed=feed, demo_mode=False)
        recs = bridge.screen_tokens_to_buy(max_tokens=5)
        self.assertEqual(bridge.last_screen_source, "dexscreener")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].mint, "REALMINT")
        self.assertFalse(recs[0].metrics_known)

    def test_screen_without_feed_and_without_demo_is_empty(self):
        bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False)
        self.assertEqual(bridge.screen_tokens_to_buy(max_tokens=5), [])
        self.assertIsNone(bridge.last_screen_source)

    def test_demo_mode_still_available_for_offline(self):
        bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=True)
        recs = bridge.screen_tokens_to_buy(max_tokens=5)
        self.assertEqual(bridge.last_screen_source, "demo")
        self.assertGreater(len(recs), 0)


class TestBridgeAntiFalacias(unittest.TestCase):
    """P1 — anti-falacias en gmgn_bridge: `N/D` honesto en vivo, demo solo explícito.

    Ley (§1.5): si una fuente no responde se reporta el estado sin conexión;
    jamás se fabrican métricas, PnL ni juicios de seguridad con literales.
    """

    # ---- "A quién seguir" -------------------------------------------------

    def test_rank_wallets_production_fail_closed_without_source(self):
        """Sin GMGN CLI, sin DB de wallets y sin demo: lista vacía, no catálogo inventado."""
        bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False)
        self.assertEqual(bridge.rank_wallets_to_follow(), [])

    def test_rank_wallets_demo_catalog_is_explicit_demo_only(self):
        """El catálogo de demostración existe SOLO cuando demo_mode está activo."""
        bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=True)
        wallets = bridge.rank_wallets_to_follow()
        verdicts = {w.verdict for w in wallets}
        self.assertGreater(len(wallets), 0)
        self.assertIn("FOLLOW", verdicts)
        self.assertIn("AVOID", verdicts)

    def test_db_wallets_do_not_invent_pnl_30d_or_holding(self):
        """WalletProfile real solo expone balance/cierres/wins: nada más se inventa."""
        import sqlite3
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "custom.db")
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE WalletProfile (id TEXT, label TEXT, solBalance REAL, "
                "closedTrades INTEGER, wins INTEGER)"
            )
            con.execute("INSERT INTO WalletProfile VALUES ('AdDr1', 'Ballena Prueba', 12.5, 10, 6)")
            con.commit()
            con.close()
            with mock.patch.dict(os.environ, {"PUMPSCOPE_DB": db}):
                bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False)
                wallets = bridge.rank_wallets_to_follow()

        self.assertEqual(len(wallets), 1)
        w = wallets[0]
        self.assertAlmostEqual(w.winrate_7d, 0.6)
        self.assertEqual(w.total_trades, 10)          # sin floor artificial max(trades, 5)
        self.assertIsNone(w.winrate_30d)              # sin wr * 0.95
        self.assertIsNone(w.realized_pnl_usd)         # sin solBalance * 150
        self.assertIsNone(w.avg_holding_time_s)       # sin 2490/134 fabricados
        self.assertIsNone(w.is_copyable)              # sin evidencia no hay juicio
        self.assertEqual(w.verdict, "WATCH")
        self.assertIn("incomplet", w.reason.lower())

    # ---- "Qué token comprar" ----------------------------------------------

    def test_metrics_less_tokens_schema_never_fabricates(self):
        """Esquema `tokens` sin métricas on-chain: fail-closed, no candidatos inventados."""
        import sqlite3
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "custom.db")
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE tokens (mint TEXT, symbol TEXT, name TEXT, "
                "total_supply REAL, created_at TEXT, dev_wallet TEXT)"
            )
            con.execute("INSERT INTO tokens VALUES ('MINTDEMO1', 'DEMO', 'Demo', 1000.0, '2024-01-01', 'd1')")
            con.commit()
            con.close()
            with mock.patch.dict(os.environ, {"PUMPSCOPE_DB": db}):
                bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False,
                                    use_db_fallback=True)
                recs = bridge.screen_tokens_to_buy(max_tokens=5)

        self.assertEqual(recs, [])
        self.assertIsNone(bridge.last_screen_source)

    def test_token_schema_uses_measured_values_only(self):
        """Tabla `Token` real: valores medidos; filas sin métricas críticas → no verificadas."""
        import sqlite3
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "custom.db")
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE Token (mint TEXT, symbol TEXT, name TEXT, marketCap REAL, "
                "firstMarketCap REAL, firstLiquidity REAL, liquidity REAL, devBalancePct REAL, "
                "top10HoldersPct REAL, bundlerHoldingPct REAL, changeM5 REAL, volume24h REAL, "
                "holderCount INTEGER, firstSeenAt REAL)"
            )
            con.execute(
                "INSERT INTO Token VALUES ('MINTREAL1', 'REALX', 'Real X', 9000.0, 8000.0, 2000.0, "
                "2500.0, 0.5, 18.0, 6.0, 4.0, 28800.0, 42, 1000.0)"
            )
            con.execute(
                "INSERT INTO Token VALUES ('MINTNULL1', 'NULLY', 'Null Y', 7000.0, 7000.0, 1500.0, "
                "1600.0, 0.4, NULL, 5.0, 2.0, 14400.0, NULL, 999.0)"
            )
            con.commit()
            con.close()
            with mock.patch.dict(os.environ, {"PUMPSCOPE_DB": db}):
                bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False,
                                    use_db_fallback=True)
                recs = bridge.screen_tokens_to_buy(max_tokens=5)

        self.assertEqual(bridge.last_screen_source, "custom_db")
        r1 = next(r for r in recs if r.mint == "MINTREAL1")
        self.assertEqual(r1.market_cap_usd, 9000.0)
        self.assertEqual(r1.liquidity_usd, 2500.0)
        self.assertEqual(r1.top10_concentration_pct, 18.0)
        self.assertEqual(r1.bundler_pct, 6.0)
        self.assertTrue(r1.metrics_known)
        self.assertEqual(r1.smart_buyers_count, 0)   # jamás "Ballena Verificada" sin detección
        self.assertEqual(r1.smart_buyers_tags, [])
        self.assertEqual(r1.sparkline, [])           # sin serie sintética

        r2 = next(r for r in recs if r.mint == "MINTNULL1")
        self.assertFalse(r2.metrics_known)           # top10 NULL -> no verificable
        self.assertEqual(r2.verdict, "VETO")

    # ---- Auditoría forense ------------------------------------------------

    def test_audit_token_without_data_is_fail_closed(self):
        """Sin CLI y sin escáner: UNVERIFIED, sin literales sustitutivos (nunca PASS falso)."""
        from unittest import mock

        bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False)
        with mock.patch.object(bridge.dossier_scanner, "scan_token_live",
                               side_effect=RuntimeError("sin red")):
            res = bridge.audit_token("SoL1111111111111111111111111111111111111112")

        self.assertFalse(res["is_safe"])
        self.assertEqual(res["verdict"], "UNVERIFIED")
        self.assertFalse(res["metrics_known"])
        self.assertIsNone(res["rug_ratio"])
        self.assertIsNone(res["top10_concentration_pct"])
        self.assertIsNone(res["dev_holding_pct"])
        self.assertIsNone(res["sniper_pct"])
        self.assertEqual(res["veto_flags"], [])
        self.assertEqual(res["pattern"]["pattern_id"], "UNVERIFIED_METRICS")

    def test_audit_token_dossier_never_invents_metrics(self):
        """Dossier real: PASS solo con métricas completas; faltante -> UNVERIFIED parcial."""
        from unittest import mock

        class _Dossier:
            metrics_known = True
            admitted = True

            def __init__(self, top10):
                self._top10 = top10

            def to_dict(self):
                return {
                    "composite_dd_score": 80.0, "top10_float_pct": self._top10,
                    "dev_balance_pct": 0.5, "bundler_pct": 3.0, "sniper_pct": 4.0,
                    "market_cap_usd": None, "liquidity_usd": None, "admitted": True,
                    "bundler_flag": False, "wash_trading_flag": False,
                    "clone_factory_flag": False,
                }

        bridge = GMGNBridge(cli_path="__missing__", feed=None, demo_mode=False)
        with mock.patch.object(bridge.dossier_scanner, "scan_token_live",
                               return_value=_Dossier(12.0)):
            ok = bridge.audit_token("SoL1111111111111111111111111111111111111112")
        self.assertTrue(ok["metrics_known"])
        self.assertTrue(ok["is_safe"])
        self.assertEqual(ok["verdict"], "PASS")
        self.assertAlmostEqual(ok["rug_ratio"], 0.2)

        with mock.patch.object(bridge.dossier_scanner, "scan_token_live",
                               return_value=_Dossier(None)):
            partial = bridge.audit_token("SoL1111111111111111111111111111111111111112")
        self.assertFalse(partial["metrics_known"])
        self.assertFalse(partial["is_safe"])
        self.assertEqual(partial["verdict"], "UNVERIFIED")
        self.assertEqual(partial["pattern"]["pattern_id"], "UNVERIFIED_METRICS")
        self.assertIsNone(partial["top10_concentration_pct"])

    # ---- Trazas y lock estático -------------------------------------------

    def test_build_decision_stages_marks_missing_values_as_nd(self):
        """E0/E4b con dato ausente: `value=None` y 'N/D' — nunca $0/0.0% fingidos."""
        from detector.gmgn_bridge import build_decision_stages

        stages = build_decision_stages(
            source="", mint="MINTX", symbol="SHOW", mc_usd=None, liq_usd=None,
            metrics_known=False, bundler_pct=0.0, wash_trading_flag=False,
            clone_factory_flag=False, dev_factory_flag=False, dev_balance_pct=0.0,
            top10_pct=0.0,
            ml_eval={
                "pattern_id": "UNVERIFIED_SECURITY_TRAP", "admitted": False,
                "verdict": "VETO", "veto_reason": "VETO_UNVERIFIED_METRICS",
                "probability_success": None, "confluence_score": None,
                "expected_ev": "N/D", "ev_fraction": None,
            },
        )
        by_stage = {s["stage"]: s for s in stages}
        self.assertIsNone(by_stage["E0_discovery"]["value"])
        self.assertIn("N/D", by_stage["E0_discovery"]["reason"])
        self.assertIsNone(by_stage["E4_ml_probability"]["value"])
        self.assertIn("N/D", by_stage["E4_ml_probability"]["reason"])

    def test_static_source_lock_forbids_reintroducing_fabricated_literals(self):
        """Lock estático: los literales sustitutivos erradicados no deben volver."""
        src_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "detector", "gmgn_bridge.py")
        with open(src_path, "r", encoding="utf-8") as fh:
            src = fh.read()

        forbidden = [
            '"market_cap_usd": 6800.0',
            '"liquidity_usd": 2100.0',
            '"top10_concentration_pct": 21.5',
            '"smart_buyers_count": 2 if top10 < 30',
            "or 15000.0",
            "or 4500.0",
            "sol_bal * 150.0",
            "wr * 0.95",
            "max(trades, 5)",
            "vol5m / 10.0",
            "[10, 11, 10.5, 12, 11.8, 13]",
        ]
        for frag in forbidden:
            self.assertNotIn(frag, src, f"literal fabricado reintroducido: {frag}")

        # El catálogo demo permanece, pero SOLO bajo demo_mode explícito.
        self.assertIn("if not raw_wallets and self.demo_mode:", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
