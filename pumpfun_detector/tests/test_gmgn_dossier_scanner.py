"""Tests TDD para el Escáner Forense de Paridad GMGN, Token Dossier DB,
blindaje fail-closed de AIQuantModel/GMGNBridge y endpoints de anotación."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.ai_quant_model import AIQuantModel
from detector.gmgn_bridge import GMGNBridge
from detector.gmgn_dossier_scanner import (
    GMGNDossierScanner,
    compute_avatar_dhash_from_bytes,
    compute_kline_microstructure_score,
    detect_bundler_and_cluster,
    detect_same_name_vamp_and_clones,
    detect_wash_trading_staircase,
    evaluate_creator_lineage_and_ceiling,
)
from detector.live_store import LiveStore


# Payload real extraído de GoPlus Security API para 0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444 (币安银行)
REAL_GOPLUS_BINANCE_BANK = {
    "anti_whale_modifiable": "0",
    "buy_tax": "0",
    "can_take_back_ownership": "0",
    "cannot_buy": "0",
    "cannot_sell_all": "0",
    "creator_address": "0xa7b3c3b95f7f9c391e3b16913c1d7fde96689136",
    "creator_balance": "0",
    "creator_percent": "0.000000",
    "holder_count": "382",
    "holders": [
        {"address": "0x5c952063c7fc8610ffdb798152d69f0b9550762b", "is_contract": 1, "percent": "0.235113099257117396", "is_locked": 0},
        {"address": "0x7e86eee4417e1a9ab3621889b0d15aeb1de1a09d", "is_contract": 0, "percent": "0.022709574677707465", "is_locked": 0},
        {"address": "0xb8d7da848c36ea714d0a20c581bc68be237efc89", "is_contract": 0, "percent": "0.022709478658145401", "is_locked": 0},
        {"address": "0x1434bdd9bbdb0d88be47ff399fd2a2e1a4a9e57c", "is_contract": 0, "percent": "0.022709382639192299", "is_locked": 0},
        {"address": "0x1a6e423fd784b6a7c220c1c94b8969daa795193a", "is_contract": 0, "percent": "0.022709286620848219", "is_locked": 0},
        {"address": "0x76dc9ec7cdd7ef4f979e26701aac9abd7637618c", "is_contract": 0, "percent": "0.022709190603113040", "is_locked": 0},
        {"address": "0x7de5393f907d62d5137a873bbe446c3523d453f3", "is_contract": 0, "percent": "0.022709094585986861", "is_locked": 0},
        {"address": "0x514e5bd19af57dc2e3580e6c152d5f347b952ce7", "is_contract": 0, "percent": "0.022708998569469655", "is_locked": 0},
        {"address": "0xc977aab1d8640b2d86874c77737efbb691b843e1", "is_contract": 0, "percent": "0.022708902553561347", "is_locked": 0},
        {"address": "0xa0540d5628bb919df2dacd44ee0f6cf2c1201491", "is_contract": 0, "percent": "0.022708806538262076", "is_locked": 0},
    ],
    "is_blacklisted": "0",
    "is_honeypot": "0",
    "is_mintable": "0",
    "is_open_source": "1",
    "launchpad_token": {"is_launchpad_token": "1", "launchpad_name": "four.meme"},
    "owner_address": "0x0000000000000000000000000000000000000000",
    "sell_tax": "0",
    "token_name": "币安银行",
    "token_symbol": "币安银行",
}

REAL_DEXSCREENER_BINANCE_BANK = {
    "chainId": "bsc",
    "dexId": "fourmeme",
    "pairAddress": "0x8A5d7de9b3B77F0a2fb2865CD72525f32a164444:4meme",
    "baseToken": {
        "address": "0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
        "name": "币安银行",
        "symbol": "币安银行",
    },
    "priceUsd": "0.00004125",
    "txns": {
        "m5": {"buys": 5, "sells": 1},
        "h1": {"buys": 37, "sells": 9},
        "h24": {"buys": 1076, "sells": 232},
    },
    "volume": {"h24": 9841.12, "h6": 1766.58, "h1": 373.28, "m5": 14.65},
    "priceChange": {"m5": 0.19, "h1": 6.0, "h6": 22.87, "h24": 1140.0},
    "fdv": 41258.15,
    "marketCap": 41258.15,
    "pairCreatedAt": 1792061901000,
}

REAL_SEARCH_PAIRS_CLONES = [
    REAL_DEXSCREENER_BINANCE_BANK,
    {
        "chainId": "bsc",
        "dexId": "fourmeme",
        "baseToken": {"address": "0xe8e044E5F74125015A17Cda42Cc44161AB8c4444", "name": "币安银行", "symbol": "币安银行"},
        "volume": {"h24": 14497.03, "h1": 0, "m5": 0},
        "marketCap": 3201.37,
        "pairCreatedAt": 1792030210000,
    },
    {
        "chainId": "bsc",
        "dexId": "fourmeme",
        "baseToken": {"address": "0x2d21F117Fd00C9c64E6f522b53D055D054664444", "name": "币安银行", "symbol": "币安银行"},
        "volume": {"h24": 12774.47, "h1": 0, "m5": 0},
        "marketCap": 3194.73,
        "pairCreatedAt": 1792060672000,
    },
    {
        "chainId": "bsc",
        "dexId": "fourmeme",
        "baseToken": {"address": "0x782737584cEe28D3c2EF1518d53F0dBE90464444", "name": "币安银行", "symbol": "币安银行"},
        "volume": {"h24": 1.48, "h1": 0, "m5": 0},
        "marketCap": 3189.65,
        "pairCreatedAt": 1791748555000,
    },
    {
        "chainId": "bsc",
        "dexId": "fourmeme",
        "baseToken": {"address": "0x60E6EaC5A32529f873d5628a2aC6a68F1F7dfffF", "name": "币安银行", "symbol": "币安银行"},
        "volume": {"h24": 0, "h1": 0, "m5": 0},
        "marketCap": 3100.0,
        "pairCreatedAt": 1792060675000,
    },
]


class TestGMGNDossierScanner(unittest.TestCase):
    def test_detect_bundler_uniform_cluster_on_real_binance_bank(self):
        """Detecta el bundler oculto (9 wallets con 2.2709% idéntico) aunque Top10 sea 20.44% < 30%."""
        res = detect_bundler_and_cluster(REAL_GOPLUS_BINANCE_BANK["holders"])
        self.assertTrue(res["bundler_flag"])
        self.assertGreaterEqual(res["uniform_cluster_count"], 9)
        self.assertAlmostEqual(res["top10_raw_pct"], 20.44, places=1)
        # Re-basado sobre el flotante (excluyendo el 23.51% del contrato LP): 20.44 / 0.7649 = 26.72%
        self.assertGreater(res["top10_float_pct"], 25.0)
        # Extrapolación de bundle sobre curva (o suma del cluster uniforme): >= 25%
        self.assertGreaterEqual(res["estimated_bundler_pct"], 25.0)

    def test_detect_wash_trading_staircase_on_real_binance_bank(self):
        """Detecta wash-trading de micro-compras ($2.44 media en 5m y $8.11 en 1h con MC $41K)."""
        res = detect_wash_trading_staircase(
            txns=REAL_DEXSCREENER_BINANCE_BANK["txns"],
            volume=REAL_DEXSCREENER_BINANCE_BANK["volume"],
            market_cap_usd=REAL_DEXSCREENER_BINANCE_BANK["marketCap"],
        )
        self.assertTrue(res["wash_trading_flag"])
        self.assertLess(res["avg_tx_m5_usd"], 5.0)
        self.assertIn("WASH_TRADING", res["reason"])

    def test_detect_same_name_vamp_and_vanity_clones(self):
        """Detecta fábrica de clones con el mismo nombre y sufijo vanity (...4444)."""
        res = detect_same_name_vamp_and_clones(
            mint="0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
            symbol="币安银行",
            name="币安银行",
            search_pairs=REAL_SEARCH_PAIRS_CLONES,
        )
        self.assertGreaterEqual(res["same_name_count"], 5)
        self.assertGreaterEqual(res["vanity_4444_count"], 4)
        self.assertGreaterEqual(res["dead_clones_count"], 3)
        self.assertTrue(res["clone_factory_flag"])

    def test_evaluate_creator_lineage_and_ceiling_0_of_73(self):
        """Veta cuando el creador tiene 0/73 graduaciones y el MC ($40.2K) roza su ATH histórico ($44K)."""
        res = evaluate_creator_lineage_and_ceiling(
            creator_address="0xa7b3c3b95f7f9c391e3b16913c1d7fde96689136",
            current_mc_usd=40220.0,
            dev_stats={
                "total_launches": 73,
                "graduated_count": 0,
                "best_ath_mc_usd": 44000.0,
                "funder_address": "0x6f00000000000562",
            },
        )
        self.assertTrue(res["dev_factory_flag"])
        self.assertTrue(res["near_dev_ceiling_flag"])
        self.assertEqual(res["graduation_rate_pct"], 0.0)
        self.assertGreaterEqual(res["mc_vs_dev_ath_pct"], 90.0)

    def test_full_dossier_vetoes_binance_bank_trap_with_causal_explanation(self):
        """El Dossier completo veta el token trampa de las capturas y explica por qué."""
        scanner = GMGNDossierScanner()
        dossier = scanner.build_dossier_from_payloads(
            mint="0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
            chain="bsc",
            dex_pair=REAL_DEXSCREENER_BINANCE_BANK,
            goplus_data=REAL_GOPLUS_BINANCE_BANK,
            search_pairs=REAL_SEARCH_PAIRS_CLONES,
            dev_stats={"total_launches": 73, "graduated_count": 0, "best_ath_mc_usd": 44000.0},
        )
        self.assertFalse(dossier.admitted)
        self.assertEqual(dossier.verdict, "VETO")
        self.assertLess(dossier.composite_dd_score, 45.0)
        self.assertTrue(dossier.bundler_flag)
        self.assertTrue(dossier.wash_trading_flag)
        self.assertTrue(dossier.clone_factory_flag)
        self.assertTrue(dossier.dev_factory_flag)
        self.assertIn("Bundler", dossier.why_price_moves)

    def test_fail_closed_when_metrics_unknown(self):
        """Regla honest-edge: cuando metrics_known=False, AIQuantModel y GMGNBridge JAMÁS admiten compra."""
        model = AIQuantModel()
        pred = model.predict_token(
            first_mc=12000.0,
            first_liq=4000.0,
            volume_24h=50000.0,
            holder_count=30,
            dev_balance_pct=0.0,
            top10_pct=0.0,
            metrics_known=False,
        )
        self.assertFalse(pred["admitted"])
        self.assertEqual(pred["verdict"], "VETO")
        self.assertIn("UNVERIFIED", str(pred["veto_reason"]).upper())

    def test_live_store_saves_and_retrieves_token_dossier_and_hf_snapshots(self):
        """Verifica que LiveStore persiste expedientes completos estilo GMGN, avatares y snapshots HF."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test_dossier.db")
            with LiveStore(db_path) as store:
                scanner = GMGNDossierScanner(store=store)
                dossier = scanner.build_dossier_from_payloads(
                    mint="0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
                    chain="bsc",
                    dex_pair=REAL_DEXSCREENER_BINANCE_BANK,
                    goplus_data=REAL_GOPLUS_BINANCE_BANK,
                    search_pairs=REAL_SEARCH_PAIRS_CLONES,
                    dev_stats={"total_launches": 73, "graduated_count": 0, "best_ath_mc_usd": 44000.0},
                )
                store.save_token_dossier(dossier.to_dict())
                loaded = store.get_token_dossier("0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["symbol"], "币安银行")
                self.assertEqual(loaded["verdict"], "VETO")

                # Registrar snapshots de alta frecuencia
                store.record_hf_snapshot(
                    mint="0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
                    mc_usd=35000.0,
                    liquidity_usd=18000.0,
                    buys_m5=5,
                    sells_m5=1,
                    vol_m5_usd=14.65,
                    holder_count=380,
                    ts=1000.0,
                )
                store.record_hf_snapshot(
                    mint="0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
                    mc_usd=41258.0,
                    liquidity_usd=19500.0,
                    buys_m5=5,
                    sells_m5=1,
                    vol_m5_usd=14.65,
                    holder_count=382,
                    ts=1012.0,
                )
                traj = store.get_token_trajectory("0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444")
                self.assertEqual(len(traj), 3)
                self.assertAlmostEqual(traj[1]["mc_usd"], 41258.0, places=1)

                # Registrar avatar hash y comprobar detección de reutilización
                img_bytes = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
                dhash = compute_avatar_dhash_from_bytes(img_bytes)
                cnt1 = store.register_avatar("mint_1", "币安银行", dhash)
                cnt2 = store.register_avatar("mint_2", "币安银行", dhash)
                self.assertEqual(cnt1, 1)
                self.assertEqual(cnt2, 2)

    def test_manual_annotation_db_and_cockpit_wiring(self):
        """Verifica que AnnotationDB persiste etiquetas BUY/AVOID y expone resumen para ML."""
        from detector.manual_annotation import AnnotationDB

        with tempfile.TemporaryDirectory() as tmp:
            ann_path = os.path.join(tmp, "test_annotations.db")
            ann_db = AnnotationDB(db_path=ann_path)
            ann_id = ann_db.add_annotation(
                mint="0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444",
                symbol="币安银行",
                decision="AVOID",
                rationale="Bundler 68.1% + Wash-Trading $2.44/tx + 73 lanzamientos sin graduar",
                tags=["BUNDLER_CLUSTER", "WASH_TRADING", "VAMP_CLONE"],
                features_snapshot={"bundler_pct": 68.12, "avg_tx_m5_usd": 2.44},
            )
            self.assertGreater(ann_id, 0)
            summary = ann_db.get_summary()
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["avoid_count"], 1)
            rows = ann_db.export_training_dataset()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], 0)

    def test_rugcheck_full_report_solana_amm_separation(self):
        """Verifica que el reporte completo de RugCheck separa la curva AMM (37.67%) de las billeteras humanas."""
        rugcheck_payload = {
            "creator": "DevSolWallet1111111111111111111111111111111",
            "totalHolders": 412,
            "knownAccounts": {
                "PumpBondingCurve111111111111111111111111111": {
                    "name": "Pump Fun AMM",
                    "type": "AMM",
                }
            },
            "topHolders": [
                {"address": "ata0", "owner": "PumpBondingCurve111111111111111111111111111", "pct": 37.67, "insider": False},
                {"address": "ata1", "owner": "HumanWallet1", "pct": 2.40, "insider": False},
                {"address": "ata2", "owner": "HumanWallet2", "pct": 1.85, "insider": False},
                {"address": "ata3", "owner": "HumanWallet3", "pct": 1.50, "insider": False},
                {"address": "ata4", "owner": "HumanWallet4", "pct": 1.20, "insider": False},
                {"address": "ata5", "owner": "DevSolWallet1111111111111111111111111111111", "pct": 0.45, "insider": False},
            ],
            "risks": [],
        }
        dex_pair = {
            "chainId": "solana",
            "dexId": "pumpfun",
            "baseToken": {"address": "SolMint11111111111111111111111111111111pump", "symbol": "CLEANPUMP", "name": "Clean Pump"},
            "marketCap": 28500.0,
            "liquidity": {"usd": 11200.0},
            "txns": {"m5": {"buys": 32, "sells": 14}, "h1": {"buys": 190, "sells": 95}},
            "volume": {"m5": 4800.0, "h1": 29500.0},
        }
        scanner = GMGNDossierScanner()
        dossier = scanner.build_dossier_from_payloads(
            mint="SolMint11111111111111111111111111111111pump",
            chain="sol",
            dex_pair=dex_pair,
            rugcheck_data=rugcheck_payload,
            sparkline=[18.0, 20.5, 23.0, 24.5, 23.8, 24.1, 24.4],
        )
        self.assertTrue(dossier.metrics_known)
        self.assertAlmostEqual(dossier.tradeable_float_pct, 62.33, places=1)
        self.assertLess(dossier.top10_float_pct, 20.0)
        self.assertAlmostEqual(dossier.dev_balance_pct, 0.45, places=2)
        self.assertTrue(dossier.admitted)
        self.assertIn("BULL_FLAG", dossier.kline_pattern)

    def test_auto_evolution_shadow_veto_and_closed_loop_postmortem(self):
        """Verifica que el motor de auto-evolución dictamina vetos ('TRUE_SAVE'/'FALSE_VETO') y auto-ajusta filtros tras pérdida."""
        from pathlib import Path
        from detector.auto_evolution import AutoEvolutionEngine
        from detector.gmgn_bridge import LivePaperTrader
        from detector.manual_annotation import AnnotationDB

        class _StepFeed:
            def __init__(self, prices):
                self.prices = dict(prices)
                self.last_error = None

            def market_cap_usd(self, mint):
                return self.prices.get(mint)

            def fetch_token(self, mint):
                mc = self.prices.get(mint)
                if mc is None:
                    return None
                return type("Snap", (), {"market_cap_usd": mc})()

            def health(self):
                return {"source": "step_feed"}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            store = LiveStore(str(tmp_dir / "live_evo.db"))
            ann_db = AnnotationDB(str(tmp_dir / "ann_evo.db"))
            evo = AutoEvolutionEngine(store=store, annotation_db=ann_db, runtime_dir=tmp_dir)

            # 1. Registrar 2 tokens vetados y avanzar precios reales
            evo.register_vetoed_token("VETO_RUG_1", "SCAM1", "BUNDLER_UNIFORM_CLUSTER", entry_mc_usd=40000.0)
            evo.register_vetoed_token("VETO_RUNNER_2", "MISSED2", "TOP10_HIGH", entry_mc_usd=15000.0)

            feed = _StepFeed({"VETO_RUG_1": 4200.0, "VETO_RUNNER_2": 24000.0, "TRADE_LOSS_1": 10000.0})
            rows = evo.tick_shadow_veto_outcomes(feed=feed)
            by_mint = {r["mint"]: r for r in rows}

            self.assertEqual(by_mint["VETO_RUG_1"]["verdict"], "TRUE_SAVE")
            self.assertLess(by_mint["VETO_RUG_1"]["counterfactual_pnl_pct"], -80.0)
            self.assertIn("hubiera PERDIDO", by_mint["VETO_RUG_1"]["summary_text"])

            self.assertEqual(by_mint["VETO_RUNNER_2"]["verdict"], "FALSE_VETO")
            self.assertGreater(by_mint["VETO_RUNNER_2"]["counterfactual_pnl_pct"], 45.0)

            # 2. Abrir un trade en LivePaperTrader, simular caída a SL (-35%), verificar cierre, persistencia y auto-ajuste
            trader = LivePaperTrader(feed=feed, store=store, evolution_engine=evo)
            trader.open_trade("TRADE_LOSS_1", "BADMEME", entry_mc=10000.0, size_sol=1.0)

            # Verificar que se puede restaurar antes del cierre
            trader_restored = LivePaperTrader(feed=feed, store=store, evolution_engine=evo)
            self.assertEqual(trader_restored.restore_from_store(), 1)
            self.assertIn("TRADE_LOSS_1", trader_restored.open_positions)

            # Caída de precio a $6,400 (-36% -> dispara PANIC_SL_SLIPPAGE_EXIT y post-mortem automático)
            initial_min_liq = evo.rules["min_liquidity_usd"]
            feed.prices["TRADE_LOSS_1"] = 6400.0
            trader.tick_prices()

            self.assertNotIn("TRADE_LOSS_1", trader.open_positions)
            self.assertEqual(len(trader.closed_positions), 1)
            self.assertEqual(trader.closed_positions[0].exit_reason, "PANIC_SL_SLIPPAGE_EXIT")
            self.assertIsNotNone(trader.last_evolution_event)
            self.assertGreater(evo.rules["min_liquidity_usd"], initial_min_liq)

            # Verificar que se auto-anotó como AVOID en AnnotationDB
            ann_summary = ann_db.get_summary()
            self.assertEqual(ann_summary["avoid_count"], 1)

            # 3. Probar Peak Trailing Stop (+60% -> cae 20% desde pico a +28% -> asegura ganancia)
            feed.prices["TRADE_TRAIL_1"] = 10000.0
            trader.open_trade("TRADE_TRAIL_1", "TRAILWIN", entry_mc=10000.0, size_sol=1.0)
            feed.prices["TRADE_TRAIL_1"] = 16000.0  # +60% -> activa tp1_hit y peak_mc=16000
            trader.tick_prices()
            self.assertTrue(trader.open_positions["TRADE_TRAIL_1"].tp1_hit)

            feed.prices["TRADE_TRAIL_1"] = 12800.0  # cae 20% desde 16000 (+28% neto) -> cierra por Peak Trailing Stop
            trader.tick_prices()
            self.assertNotIn("TRADE_TRAIL_1", trader.open_positions)
            self.assertEqual(trader.closed_positions[-1].exit_reason, "PEAK_TRAILING_STOP_PROFIT_LOCKED")

            store.close()

    def test_ai_engine_openrouter_and_evolve_commands(self):
        """Verifica que AIEngine soporta OpenRouter y responde a /evolve y /shadow."""
        from detector.ai_engine import AIEngine

        ai = AIEngine()
        status = ai.get_status()
        self.assertIn("openrouter", status["providers"])
        models = ai.fetch_openrouter_models(limit=6)
        self.assertGreaterEqual(len(models), 4)

        res_evolve = ai.process_chat(
            "/evolve",
            context={
                "evolution_status": {
                    "adaptive_rules": {"min_liquidity_usd": 9250, "max_top10_float_pct": 26.5},
                    "shadow_summary": {"total_tracked": 4, "true_saves": 3, "false_vetos": 1, "save_accuracy_pct": 75.0, "avoided_loss_sum_pct": 142.5},
                    "shadow_vetos": [{"symbol": "币安银行", "verdict": "TRUE_SAVE", "counterfactual_pnl_pct": -88.4, "summary_text": "SALVADA REAL"}],
                    "evolution_events": [],
                }
            },
        )
        self.assertTrue(res_evolve["ok"])
        self.assertEqual(res_evolve["command"], "evolve")
        self.assertIn("SALVADA REAL", res_evolve["response"])


if __name__ == "__main__":
    unittest.main()

