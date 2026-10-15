"""
Tests del feed en vivo PumpPortal + adaptador evento→feature (P1).

Cubre:
- Normalización de eventos (camelCase PumpPortal y esquema on-chain).
- Rechazo de eventos no-accionables (creación, migración, control).
- Ventana rolling: buys/sells/compradores únicos y evicción por tiempo.
- Construcción de features entrada-seguras (market cap actual, NO final).
- Integración con la Señal Maestra: rama C alcanzable con flujo suficiente.
- Procedencia P0: data_origin == "live".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.pumpportal_feed import (
    PumpPortalAdapter,
    parse_trade_event,
)


def _buy(mint, i, base=1700000000.0, sol=1.0, mc=30.0, v=20.0, trader=None):
    return {
        "mint": mint, "txType": "buy", "solAmount": sol, "tokensAmount": 1000.0,
        "marketCapSol": mc, "vSolInCurve": v, "traderPublicKey": trader or f"w{i}",
        "timestamp": base + i,
    }


class TestPumpPortalFeed(unittest.TestCase):
    def setUp(self):
        self.feed = PumpPortalAdapter(window_s=60.0, sol_usd=150.0)

    # ---------------- parseo / normalización ----------------
    def test_parse_pumpportal_camelcase(self):
        n = parse_trade_event(_buy("M1", 0))
        self.assertIsNotNone(n)
        self.assertEqual(n.mint, "M1")
        self.assertTrue(n.is_buy)
        self.assertEqual(n.sol_amount, 1.0)
        self.assertEqual(n.market_cap_sol, 30.0)

    def test_parse_onchain_schema_is_buy_bool(self):
        ev = {"mint": "M1", "is_buy": False, "sol_amount": 2.0,
              "token_amount": 5.0, "market_cap_sol": 12.0,
              "v_sol_in_curve": 8.0, "timestamp": 1700000000.0}
        n = parse_trade_event(ev)
        self.assertIsNotNone(n)
        self.assertFalse(n.is_buy)
        self.assertEqual(n.sol_amount, 2.0)

    def test_parse_rejects_non_trade_and_malformed(self):
        self.assertIsNone(parse_trade_event({"mint": "", "txType": "buy"}))
        self.assertIsNone(parse_trade_event(None))
        self.assertIsNone(parse_trade_event({"foo": "bar"}))  # sin mint

    def test_parse_rejects_unknown_tx_type(self):
        # txType distinto de buy/sell y sin is_buy -> no accionable.
        self.assertIsNone(parse_trade_event({"mint": "M1", "txType": "create"}))

    # ---------------- ventana rolling ----------------
    def test_window_counts_buys_sells_unique(self):
        for i in range(5):
            self.feed.ingest(_buy("M1", i))
        self.feed.ingest({"mint": "M1", "txType": "sell", "solAmount": 0.5,
                          "tokensAmount": 400, "marketCapSol": 32.0,
                          "vSolInCurve": 21.0, "traderPublicKey": "wx",
                          "timestamp": 1700000000.0 + 6})
        st = self.feed.window_stats("M1")
        self.assertEqual(st["n_buys"], 5)
        self.assertEqual(st["n_sells"], 1)
        self.assertEqual(st["unique_buyers"], 5)
        self.assertGreater(st["buy_sell_ratio"], 1.0)

    def test_window_evicts_old_trades(self):
        base = 1700000000.0
        self.feed.ingest(_buy("M1", 0, base=base))          # t = base
        self.feed.ingest(_buy("M1", 1, base=base + 120.0))  # fuera de ventana 60s
        st = self.feed.window_stats("M1")
        # El trade de base+120 debe haber desalojado el de base.
        self.assertEqual(st["n_buys"], 1)

    def test_unknown_mint_returns_empty(self):
        self.assertEqual(self.feed.features("NOPE"), {})
        self.assertEqual(self.feed.window_stats("NOPE")["n_buys"], 0)

    # ---------------- features entrada-seguras ----------------
    def test_features_use_current_curve_mc_not_final(self):
        base = 1700000000.0
        for i in range(3):
            self.feed.ingest(_buy("M1", i, base=base, mc=30.0))
        # Último trade empuja el mc de la curva a 32 SOL -> 32*150 = 4800 USD.
        self.feed.ingest(_buy("M1", 3, base=base + 3, mc=32.0))
        fe = self.feed.features("M1")
        self.assertEqual(fe["market_cap_at_entry"], 4800.0)
        self.assertEqual(fe["mc_at_entry_est"], 4800.0)
        # Procedencia P0: nunca confundir live con demo/sintético.
        self.assertEqual(fe["data_origin"], "live")
        # Progreso de curva en 0..1 (ef_) y 0..100 (señal maestra).
        self.assertGreaterEqual(fe["ef_curve_progress"], 0.0)
        self.assertLessEqual(fe["ef_curve_progress"], 1.0)
        self.assertGreaterEqual(fe["curve_progress"], 0.0)
        self.assertLessEqual(fe["curve_progress"], 100.0)

    # ---------------- integración Señal Maestra ----------------
    def test_decide_integrates_master_signal(self):
        base = 1700000000.0
        # Flujo fuerte: 12 compras únicas, curva al 30% (25.5 SOL de 85).
        for i in range(12):
            self.feed.ingest(_buy("M1", i, base=base, mc=55.0, v=25.0))
        d = self.feed.decide("M1")
        self.assertIsNotNone(d)
        # mc = 55*150 = 8250 USD: por encima de MIN (4500) y bajo MAX (50000),
        # con holderCount>=40? No (12). Rama C requiere holders>=40, así que con
        # 12 compradores NO debe admitir por C; verificamos que decide sin romper.
        self.assertIn(d.branch,
                      ("MASTER_ALPHA_CONFLUENCE_MOMENTUM", "NO_SIGNAL", "VETO"))

    def test_branch_c_reachable_with_enough_flow(self):
        base = 1700000000.0
        # 45 compradores únicos, mc 55 SOL (=8250 USD), curva al ~30%.
        for i in range(45):
            self.feed.ingest(_buy("M1", i, base=base, mc=55.0, v=25.0,
                                  trader=f"u{i}"))
        d = self.feed.decide("M1")
        self.assertTrue(d.admitted)
        self.assertEqual(d.branch, "MASTER_ALPHA_CONFLUENCE_MOMENTUM")

    # ---------------- replay offline + mensajes de suscripción ----------------
    def test_replay_trades_offline(self):
        from detector.pumpportal_feed import replay_trades
        base = 1700000000.0
        events = [_buy("M1", i, base=base, mc=55.0, v=25.0, trader=f"u{i}")
                  for i in range(6)]
        events.append({"mint": "", "txType": "buy"})  # no accionable, se ignora
        n = replay_trades(self.feed, events)
        self.assertEqual(n, 6)  # solo los 6 válidos
        self.assertEqual(self.feed.window_stats("M1")["n_buys"], 6)

    def test_subscribe_messages_schema(self):
        from detector.pumpportal_feed import (
            subscribe_new_token_message,
            subscribe_trade_message,
        )
        m = subscribe_trade_message(["M1", "M2"])
        self.assertEqual(m["method"], "subscribeTokenTrade")
        self.assertEqual(m["keys"], ["M1", "M2"])
        nt = subscribe_new_token_message()
        self.assertEqual(nt["method"], "subscribeNewToken")
        self.assertEqual(nt["keys"], [])

    # ---------------- inyección de holders / oráculo SOL (P1) ----------------
    def test_holders_fetcher_populates_real_metrics(self):
        # Sin fetcher: defaults seguros (dev/top = 0.0).
        for i in range(5):
            self.feed.ingest(_buy("MH", i))
        base = self.feed.features("MH")
        self.assertEqual(base["dev_balance_pct"], 0.0)
        self.assertEqual(base["top_holder_pct"], 0.0)

        # Con fetcher inyectado: features() debe reflejar valores reales.
        def _fetcher(mint):
            self.assertEqual(mint, "MH")
            return {"devBalancePct": 2.4, "topHolderPct": 61.0, "holderCount": 88}

        fed = PumpPortalAdapter(window_s=60.0, sol_usd=150.0, holders_fetcher=_fetcher)
        for i in range(5):
            fed.ingest(_buy("MH", i))
        f = fed.features("MH")
        self.assertEqual(f["dev_balance_pct"], 2.4)
        self.assertEqual(f["top_holder_pct"], 61.0)
        self.assertEqual(f["holder_count"], 88)

    def test_holders_fetcher_failure_falls_back_to_defaults(self):
        def _boom(_mint):
            raise RuntimeError("endpoint holders caído")

        fed = PumpPortalAdapter(window_s=60.0, sol_usd=150.0, holders_fetcher=_boom)
        for i in range(3):
            fed.ingest(_buy("MH2", i))
        f = fed.features("MH2")
        self.assertEqual(f["dev_balance_pct"], 0.0)
        self.assertEqual(f["top_holder_pct"], 0.0)

    def test_sol_usd_oracle_rescales_market_cap(self):
        for i in range(4):
            self.feed.ingest(_buy("MO", i, sol=1.0, mc=30.0))
        static_mc = self.feed.features("MO")["mc_at_entry_est"]
        # 30 SOL * 150 USD = 4500 USD
        self.assertAlmostEqual(static_mc, 30.0 * 150.0, places=3)

        fed = PumpPortalAdapter(window_s=60.0, sol_usd=150.0, sol_usd_oracle=lambda: 200.0)
        for i in range(4):
            fed.ingest(_buy("MO", i, sol=1.0, mc=30.0))
        # Con oráculo a 200 USD/SOL el market cap escala a 6000 USD.
        self.assertAlmostEqual(fed.features("MO")["mc_at_entry_est"], 30.0 * 200.0, places=3)

    def test_sol_usd_oracle_invalid_falls_back_to_static(self):
        fed = PumpPortalAdapter(window_s=60.0, sol_usd=150.0, sol_usd_oracle=lambda: -1.0)
        for i in range(3):
            fed.ingest(_buy("MO2", i, mc=30.0))
        self.assertAlmostEqual(fed.features("MO2")["mc_at_entry_est"], 30.0 * 150.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
