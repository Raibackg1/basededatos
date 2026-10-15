"""
Suite de Pruebas TDD para Auditoría de Winrate, Integraciones y Mejoras Reales (P0 y P1).

Cubre:
1. FeatureAvailabilityGuard: detección y bloqueo de variables post-entrada (fuga temporal).
2. ExecutionCostModel: unificación de costes condicionales (impacto, comisiones, slippage real, latencia).
3. MasterSnipeSignal (anti-lookahead & P1): prohibición de market_cap final, presupuestos de latencia,
   veto por price impact, quote stale y sizing que puede retornar 0.0 SOL.
4. Interfaces Modulares de Ejecución (Hummingbot / Chainstack / Jupiter):
   SignalProvider, QuoteProvider, OrderSubmitter, PositionManager, TradeRecorder y ExecutionAdapter.
5. Exportación de Reglas Estructuradas con Validación Temporal OOS Estricta.
6. Evaluación Temporal en 3 Bloques Aislados (Train, Validation, Test OOS).
"""
import math
import os
import sys
import unittest
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from detector.costs import ExecutionCostModel, ExecutionQuote
from detector.features import (
    FeatureAvailabilityGuard,
    ENTRY_SAFE_FEATURES,
    POST_ENTRY_FEATURES,
)
from detector.master_signal import MasterSnipeSignal, MasterSignalDecision
from detector.execution import (
    SignalProvider,
    QuoteProvider,
    OrderSubmitter,
    PositionManager,
    TradeRecorder,
    ExecutionAdapter,
    OrderResult,
)
from detector.advanced_engine import (
    export_production_strategy,
    evaluate_by_splits,
    temporal_train_val_test_split,
)


class TestFeatureAvailabilityGuard(unittest.TestCase):
    """P0 - Verificación de aislamiento temporal y bloqueo de variables futuras."""

    def test_guard_rejects_post_entry_columns_in_training(self):
        df = pd.DataFrame({
            "ef_n_buys": [10, 20],
            "firstMarketCap": [4000.0, 5000.0],
            "graduated": [1, 0],  # Fuga crítica P0
        })
        with self.assertRaises(ValueError) as ctx:
            FeatureAvailabilityGuard.validate(df, stage="training")
        self.assertIn("graduated", str(ctx.exception))

    def test_guard_rejects_final_market_cap_without_asof(self):
        df = pd.DataFrame({
            "ef_n_buys": [10, 20],
            "market_cap": [100000.0, 20000.0],  # Final market cap es fuga
        })
        with self.assertRaises(ValueError) as ctx:
            FeatureAvailabilityGuard.validate(df, stage="training")
        self.assertIn("market_cap", str(ctx.exception))

    def test_guard_accepts_clean_entry_features(self):
        df = pd.DataFrame({
            "ef_n_buys": [10, 20],
            "ef_curve_progress": [0.25, 0.35],
            "firstMarketCap": [4000.0, 4200.0],
            "firstLiquidity": [1500.0, 1600.0],
            "mc_at_entry_est": [4500.0, 4800.0],
        })
        # Debe pasar sin lanzar excepción
        safe_df = FeatureAvailabilityGuard.filter_entry_safe(df)
        self.assertEqual(len(safe_df.columns), len(df.columns))
        FeatureAvailabilityGuard.validate(safe_df, stage="training")

    def test_replay_lookahead_validator(self):
        """Validador estilo Freqtrade lookahead-analysis: cálculo incremental vs batch."""
        # Un generador determinista no debe diferir entre batch e incremental
        records = [
            {"ts": 100, "price": 1.0, "side": "buy", "sol": 0.5},
            {"ts": 105, "price": 1.1, "side": "buy", "sol": 0.8},
            {"ts": 120, "price": 0.9, "side": "sell", "sol": 0.3},
        ]
        is_clean, diffs = FeatureAvailabilityGuard.audit_incremental_consistency(
            records, time_key="ts"
        )
        self.assertTrue(is_clean)
        self.assertEqual(len(diffs), 0)


class TestExecutionCostModel(unittest.TestCase):
    """P0 & P1 - Modelo unificado de costes y fricciones condicionales."""

    def setUp(self):
        self.cost_model = ExecutionCostModel()

    def test_quote_computes_cpmm_price_impact(self):
        # 0.5 SOL sobre curva pump.fun con 30 SOL virtuales -> impacto ~ 0.5 / (30 + 0.5) ~ 1.64%
        quote = self.cost_model.calculate_quote(
            in_sol=0.5,
            liquidity_sol=30.0,
            spot_price_sol=0.00001,
            route="pump_curve",
        )
        self.assertIsInstance(quote, ExecutionQuote)
        self.assertTrue(quote.is_executable)
        self.assertGreater(quote.price_impact_pct, 1.0)
        self.assertLess(quote.price_impact_pct, 3.0)
        self.assertGreater(quote.effective_price_sol, quote.spot_price_sol)

    def test_quote_rejects_excessive_price_impact(self):
        # 10 SOL sobre liquidez de 5 SOL -> impacto masivo > 60%
        quote = self.cost_model.calculate_quote(
            in_sol=10.0,
            liquidity_sol=5.0,
            spot_price_sol=0.00001,
            max_price_impact_pct=15.0,
        )
        self.assertFalse(quote.is_executable)
        self.assertIn("EXCESSIVE_PRICE_IMPACT", quote.rejection_reason)

    def test_panic_stop_loss_models_empirical_haircut(self):
        # Stop nominal de 15% con liquidez normal debe arrojar slippage severo (-33.49% o peor)
        res = self.cost_model.compute_exit_pnl(
            entry_sol=0.5,
            nominal_exit_pct=-15.0,
            exit_type="sl_panic",
            liquidity_sol=25.0,
        )
        # La pérdida neta debe ser mayor o igual al 45% (nominal + slippage de pánico + comisiones)
        self.assertLess(res["net_pnl_pct"], -45.0)
        self.assertGreater(res["total_fees_sol"], 0.003)

    def test_stall_exit_models_sampler_latency_loss(self):
        # Salida por STALL timeout a 60s debe modelar la caída por retraso de muestreo (-28.93%)
        res = self.cost_model.compute_exit_pnl(
            entry_sol=0.5,
            nominal_exit_pct=0.0,
            exit_type="stall",
            liquidity_sol=20.0,
        )
        self.assertLessEqual(res["net_pnl_pct"], -28.0)

    def test_breakeven_winrate_consistency(self):
        w_net = 40.0
        l_net = -45.0
        be = self.cost_model.breakeven_winrate(w_net, l_net)
        # p* = 45 / (40 + 45) = 45 / 85 ~ 0.5294 (52.94%)
        self.assertAlmostEqual(be, 45.0 / 85.0, places=4)


class TestMasterSnipeSignalEnhanced(unittest.TestCase):
    """P0 & P1 - MasterSnipeSignal con anti-lookahead y vetos de ejecución reales."""

    def test_master_signal_strictly_ignores_final_market_cap(self):
        # Si se pasa un diccionario que contiene market_cap final alto pero mc_at_entry_est bajo,
        # la señal DEBE usar mc_at_entry_est y vetar DEAD_LAUNCH.
        features = {
            "market_cap": 80000.0,     # Final engañoso (fuga)
            "mc_at_entry_est": 2500.0, # Valor real a la entrada (< 4500)
            "n_buys_60s": 25,
            "unique_buyers_60s": 30,
            "ef_curve_progress": 0.20,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertFalse(dec.admitted)
        self.assertIn("DEAD_LAUNCH", dec.veto_reason)

    def test_latency_budget_veto(self):
        # Si la latencia total supera el presupuesto (ej. 1500 ms), vetar ejecución
        features = {
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 4000.0,
            "first_liquidity": 1500.0,
            "latency_ms": 2200.0,  # Excedido
            "max_latency_budget_ms": 1500.0,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertFalse(dec.admitted)
        self.assertIn("LATENCY_EXCEEDED", dec.veto_reason)

    def test_price_impact_veto(self):
        features = {
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 4000.0,
            "first_liquidity": 1500.0,
            "expected_price_impact_pct": 6.5,  # Excede límite de 5%
            "max_allowed_impact_pct": 5.0,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertFalse(dec.admitted)
        self.assertIn("PRICE_IMPACT", dec.veto_reason)

    def test_stale_quote_veto(self):
        features = {
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 4000.0,
            "first_liquidity": 1500.0,
            "quote_age_s": 4.5,  # Quote mayor a 3s
            "max_quote_age_s": 3.0,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertFalse(dec.admitted)
        self.assertIn("STALE_QUOTE", dec.veto_reason)

    def test_position_sizing_can_return_zero(self):
        # Si el EV neto esperado es negativo o se alcanzó el límite de drawdown, size = 0.0 SOL
        size_neg_ev = MasterSnipeSignal.calculate_position_size_sol(
            portfolio_sol=10.0,
            expected_net_ev=-0.05,
        )
        self.assertEqual(size_neg_ev, 0.0)

        size_dd_hit = MasterSnipeSignal.calculate_position_size_sol(
            portfolio_sol=10.0,
            expected_net_ev=0.15,
            daily_drawdown_sol=1.5,
            max_daily_drawdown_sol=1.0,
        )
        self.assertEqual(size_dd_hit, 0.0)

    def test_branch_e_social_and_netbuyers_confluence(self):
        """Rama E: Confluencia empírica OOS (FMC 3.5k-39.6M, n_socials 1-2, netBuyers 12-90)."""
        features = {
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 8000.0,  # Excede Branch A (<4285)
            "first_liquidity": 3000.0,   # Excede Branch A (<1725)
            "n_socials": 1,
            "netBuyers1h": 25,
            "dev_balance_pct": 0.5,
            "migration_progress": 20.0,
            "real_sol_reserves": 25.0,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.branch, "MASTER_ALPHA_SOCIAL_BUYERS_CONFLUENCE")
        self.assertAlmostEqual(dec.expected_winrate, 0.3125, places=4)
        self.assertEqual(dec.metrics["n_socials"], 1)
        self.assertEqual(dec.metrics["net_buyers_1h"], 25)

    def test_branch_e_rejects_zero_socials_or_excess(self):
        base = {
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 8000.0,
            "first_liquidity": 3000.0,
            "netBuyers1h": 25,
            "dev_balance_pct": 0.5,
            "migration_progress": 20.0,
            "real_sol_reserves": 25.0,
        }
        # 0 redes (riesgo de token anónimo o rug)
        dec_zero = MasterSnipeSignal.evaluate({**base, "n_socials": 0})
        self.assertFalse(dec_zero.admitted)

        # 3 redes (fuera del tercil óptimo)
        dec_three = MasterSnipeSignal.evaluate({**base, "n_socials": 3})
        self.assertFalse(dec_three.admitted)

    def test_branch_e_rejects_insufficient_net_buyers(self):
        features = {
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 8000.0,
            "first_liquidity": 3000.0,
            "n_socials": 1,
            "netBuyers1h": 5,  # < 12
            "dev_balance_pct": 0.5,
            "migration_progress": 20.0,
            "real_sol_reserves": 25.0,
        }
        dec = MasterSnipeSignal.evaluate(features)
        self.assertFalse(dec.admitted)


class TestModularExecutionAdapter(unittest.TestCase):
    """P1 - Arquitectura modular de ejecución inspirada en Hummingbot, Chainstack y Jupiter."""

    def test_execution_adapter_lifecycle(self):
        class MockSignal(SignalProvider):
            def get_signal(self, token_data):
                return MasterSnipeSignal.evaluate(token_data)

        class MockQuote(QuoteProvider):
            def get_quote(self, token_id, in_sol, route="pump_curve"):
                return ExecutionQuote(
                    in_amount_sol=in_sol,
                    out_amount_tokens=1000.0,
                    spot_price_sol=0.0005,
                    effective_price_sol=0.00051,
                    price_impact_pct=2.0,
                    priority_fee_sol=0.002,
                    network_fee_sol=0.002,
                    is_executable=True,
                )

        class MockSubmitter(OrderSubmitter):
            def submit_order(self, quote):
                return OrderResult(
                    success=True,
                    tx_hash="5abc123testsignature",
                    in_sol=quote.in_amount_sol,
                    out_tokens=quote.out_amount_tokens,
                    fees_sol=quote.priority_fee_sol + quote.network_fee_sol,
                )

        class MockRecorder(TradeRecorder):
            def __init__(self):
                self.recorded = []
            def record_trade(self, trade):
                self.recorded.append(trade)

        adapter = ExecutionAdapter(
            signal_provider=MockSignal(),
            quote_provider=MockQuote(),
            submitter=MockSubmitter(),
            recorder=MockRecorder(),
        )

        valid_token = {
            "token_id": "tok_123",
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 4000.0,
            "first_liquidity": 1500.0,
            "latency_ms": 300.0,
            "quote_age_s": 0.5,
        }
        res = adapter.process_candidate(valid_token, position_size_sol=0.5)
        self.assertTrue(res["executed"])
        self.assertEqual(len(adapter.recorder.recorded), 1)


class TestOOSRuleExportAndSplitEvaluation(unittest.TestCase):
    """P0 - Exportación OOS obligatoria y métricas separadas por bloque Train/Val/Test."""

    def test_export_production_strategy_requires_structured_bounds_and_oos(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Regla sin bounds y sin oos_pass no debe pasar
            untested_rules = [{
                "rule": "n_buys_60s > 20",
                "n": 50,
                "winrate": 0.85,
                "lift": 1.7,
                "oos_pass": False,
            }]
            out = export_production_strategy(untested_rules, (50.0, 30.0), tmp_dir)
            with open(out["json_path"], encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data["entry_rules"]), 0)

            # Regla validada OOS con bounds estructurados
            tested_rules = [{
                "rule": "CONFLUENCIA: ef_n_buys > 15",
                "n": 40,
                "winrate": 0.80,
                "lift": 1.6,
                "oos_pass": True,
                "oos_n": 25,
                "oos_winrate": 0.75,
                "oos_lift": 1.5,
                "bounds": [("ef_n_buys", 15.0, 100.0)],
            }]
            out_valid = export_production_strategy(tested_rules, (50.0, 30.0), tmp_dir)
            with open(out_valid["json_path"], encoding="utf-8") as f:
                data_valid = json.load(f)
            self.assertEqual(len(data_valid["entry_rules"]), 1)
            self.assertEqual(data_valid["entry_rules"][0]["bounds"], [["ef_n_buys", 15.0, 100.0]])

    def test_evaluate_by_splits_separates_train_val_test(self):
        # Crear dataset sintético temporal
        dates = pd.date_range("2026-01-01", periods=100, freq="min")
        df = pd.DataFrame({
            "created_at": [d.timestamp() for d in dates],
            "win_real_net": [1.0 if i % 2 == 0 else 0.0 for i in range(100)],
            "pnl_sol": [0.1 if i % 2 == 0 else -0.1 for i in range(100)],
            "pnl_pct": [20.0 if i % 2 == 0 else -20.0 for i in range(100)],
            "mc_at_entry_est": [8000.0] * 100,
            "first_market_cap": [4000.0] * 100,
            "first_liquidity": [1500.0] * 100,
        })
        tr, va, te = temporal_train_val_test_split(df, purge_gap=0)
        report = evaluate_by_splits(tr, va, te, label_col="win_real_net")
        self.assertIn("train", report)
        self.assertIn("val", report)
        self.assertIn("test_oos", report)
        self.assertEqual(report["train"]["n"], len(tr))
        self.assertEqual(report["val"]["n"], len(va))
        self.assertEqual(report["test_oos"]["n"], len(te))


if __name__ == "__main__":
    unittest.main()
