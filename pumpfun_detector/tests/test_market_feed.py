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


if __name__ == "__main__":
    unittest.main(verbosity=2)
