"""Tests del ticker contrafactual de Shadow Vetos (auto_evolution).

Congela el contrato REAL del feed: `AutoEvolutionEngine.tick_shadow_veto_outcomes()`
debe actualizar los vetos usando la API pública de `MarketFeed` (`market_cap_usd` /
`token_snapshot`). El bug de producción (2026-10-15) fue exactamente este:
el ticker sólo aceptaba un método `fetch_token` que `MarketFeed` NO tiene, así que
en vivo el ledger quedaba 28/28 en `PENDING` con `checks_count=0` para siempre,
mientras el test viejo pasaba en verde porque su doble sí tenía `fetch_token`.
Este archivo mata esa clase de drift: test contra la CLASE REAL `MarketFeed`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.auto_evolution import AutoEvolutionEngine
from detector.live_store import LiveStore


DEX_PAYLOAD_TEMPLATE = {
    "pairs": [
        {
            "chainId": "solana",
            "dexId": "pumpfun",
            "baseToken": {"address": "MINT", "symbol": "TOK", "name": "Token"},
            "marketCap": 12345.0,
            "liquidity": {"usd": 9000.0},
            "txns": {"m5": {"buys": 10, "sells": 4}},
            "volume": {"m5": 1500.0, "h1": 8000.0},
            "priceChange": {"m5": 1.0, "h1": 2.0},
        }
    ]
}


def _dex_payload(mint: str, mc: float):
    return {
        "pairs": [
            {**DEX_PAYLOAD_TEMPLATE["pairs"][0],
             "baseToken": {"address": mint, "symbol": "TOK", "name": "Token"},
             "marketCap": mc}
        ]
    }


class _MarketCapOnlyFeed:
    """Forma exacta de `MarketFeed`: sólo `market_cap_usd()` público."""

    def __init__(self, prices):
        self.prices = dict(prices)

    def market_cap_usd(self, mint):
        return self.prices.get(mint)


class _SnapshotDictFeed:
    """Sólo `token_snapshot()` devolviendo dict (forma real de MarketFeed)."""

    def __init__(self, prices):
        self.prices = dict(prices)

    def token_snapshot(self, mint):
        mc = self.prices.get(mint)
        return None if mc is None else {"mint": mint, "market_cap_usd": mc}


class _LegacyFetchTokenFeed:
    """Doble legacy: objeto con atributo `.market_cap_usd` (compatibilidad)."""

    def __init__(self, prices):
        self.prices = dict(prices)

    def fetch_token(self, mint):
        mc = self.prices.get(mint)
        if mc is None:
            return None
        return type("Snap", (), {"market_cap_usd": mc})()


class _NullFeed:
    """Feed caído: devuelve None (fail-closed, nunca inventa precio)."""

    def market_cap_usd(self, mint):
        return None


class ShadowTickerContractTest(unittest.TestCase):
    def _make_engine(self, tmp_dir: Path, db_name="shadow.db"):
        store = LiveStore(str(tmp_dir / db_name))
        return AutoEvolutionEngine(store=store, runtime_dir=tmp_dir), store

    def _register_one(self, evo, mint="VETO_1", entry_mc=40000.0):
        evo.register_vetoed_token(mint, "SCAM", "BUNDLER_CLUSTER", entry_mc_usd=entry_mc)

    def test_tick_updates_with_market_cap_usd_api_only(self):
        """Contrato con MarketFeed real: `market_cap_usd()` debe bastar para actualizar."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            evo, store = self._make_engine(tmp_dir)
            self._register_one(evo)
            feed = _MarketCapOnlyFeed({"VETO_1": 4200.0})

            rows = evo.tick_shadow_veto_outcomes(feed=feed, max_check=12)
            by_mint = {r["mint"]: r for r in rows}

            self.assertEqual(by_mint["VETO_1"]["checks_count"], 1)
            self.assertAlmostEqual(by_mint["VETO_1"]["current_mc_usd"], 4200.0, places=2)
            self.assertEqual(by_mint["VETO_1"]["verdict"], "TRUE_SAVE")
            store.close()

    def test_tick_updates_with_token_snapshot_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            evo, store = self._make_engine(tmp_dir)
            self._register_one(evo)
            feed = _SnapshotDictFeed({"VETO_1": 30000.0})

            rows = evo.tick_shadow_veto_outcomes(feed=feed, max_check=12)
            by_mint = {r["mint"]: r for r in rows}

            self.assertEqual(by_mint["VETO_1"]["checks_count"], 1)
            self.assertAlmostEqual(by_mint["VETO_1"]["current_mc_usd"], 30000.0, places=2)
            store.close()

    def test_tick_keeps_legacy_fetch_token_compat(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            evo, store = self._make_engine(tmp_dir)
            self._register_one(evo)
            feed = _LegacyFetchTokenFeed({"VETO_1": 60000.0})

            rows = evo.tick_shadow_veto_outcomes(feed=feed, max_check=12)
            by_mint = {r["mint"]: r for r in rows}

            self.assertEqual(by_mint["VETO_1"]["checks_count"], 1)
            self.assertEqual(by_mint["VETO_1"]["verdict"], "FALSE_VETO")
            store.close()

    def test_tick_with_null_feed_counts_misses_and_never_crashes(self):
        """Feed caído: fila queda PENDING, misión contada, sin excepción."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            evo, store = self._make_engine(tmp_dir)
            self._register_one(evo)

            rows = evo.tick_shadow_veto_outcomes(feed=_NullFeed(), max_check=12)

            self.assertEqual(rows[0]["verdict"], "PENDING")
            self.assertEqual(rows[0]["checks_count"], 0)
            self.assertEqual(evo.last_shadow_tick.get("misses"), 1)
            self.assertGreaterEqual(evo.shadow_miss_total, 1)
            store.close()

    def test_real_marketfeed_satisfies_ticker_contract(self):
        """Anti-drift: el ticker debe funcionar contra la CLASE REAL MarketFeed.

        Se inyecta el transporte HTTP (diseño testable de MarketFeed); el parseo
        de payload es el código real de producción.
        """
        from detector.market_feed import MarketFeed

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            evo, store = self._make_engine(tmp_dir)
            self._register_one(evo, mint="RealMint1111111111111111111111111111111pump")

            seen_urls = []

            def fake_http_get(url):
                seen_urls.append(url)
                return _dex_payload("RealMint1111111111111111111111111111111pump", 5000.0)

            feed = MarketFeed(http_get=fake_http_get, sleep=lambda s: None)
            rows = evo.tick_shadow_veto_outcomes(feed=feed, max_check=12)
            by_mint = {r["mint"]: r for r in rows}

            self.assertTrue(seen_urls, "el ticker no llamó al feed real")
            self.assertEqual(by_mint["RealMint1111111111111111111111111111111pump"]["checks_count"], 1)
            self.assertAlmostEqual(
                by_mint["RealMint1111111111111111111111111111111pump"]["current_mc_usd"], 5000.0, places=2)
            store.close()

    def test_rotation_checks_least_recently_checked_first(self):
        """Con max_check=1 deben rotar TODOS los vetos, no morir los viejos."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            evo, store = self._make_engine(tmp_dir)
            # ts explícito: VETO_OLD es más antiguo que VETO_NEW
            evo.register_vetoed_token("VETO_OLD", "OLD", "TOP10_HIGH", entry_mc_usd=10000.0, ts=1000.0)
            evo.register_vetoed_token("VETO_NEW", "NEW", "BUNDLER_CLUSTER", entry_mc_usd=10000.0, ts=2000.0)
            feed = _MarketCapOnlyFeed({"VETO_OLD": 11000.0, "VETO_NEW": 11000.0})

            r1 = evo.tick_shadow_veto_outcomes(feed=feed, max_check=1)
            self.assertEqual(len(r1), 1)
            first_checked = r1[0]["mint"]
            r2 = evo.tick_shadow_veto_outcomes(feed=feed, max_check=1)
            second_checked = r2[0]["mint"]

            self.assertEqual({first_checked, second_checked}, {"VETO_OLD", "VETO_NEW"},
                             "la rotación debe cubrir ambos vetos en 2 ticks, no repetir el mismo")

            all_rows = {r["mint"]: r for r in store.list_shadow_vetos(limit=10)}
            self.assertEqual(all_rows["VETO_OLD"]["checks_count"], 1)
            self.assertEqual(all_rows["VETO_NEW"]["checks_count"], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
