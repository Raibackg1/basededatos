"""
Suite de Pruebas Unitarias Deterministas del Motor Cuantitativo y Señal Maestra.
Ejecuta: python -m unittest tests/test_engine.py
"""
import os
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.advanced_engine import (
    benjamini_hochberg,
    compute_q_values,
    SubgroupMiner,
    realistic_tpsl_outcome,
    calculate_buyer_hhi,
    calculate_burstiness,
    calculate_amihud_illiq,
    calculate_cpmm_kyle_lambda,
    DEXVolumeSynchronizedToxicity,
    CalibratedMetaLabeler,
    calculate_quantstats_metrics,
)
from detector.master_signal import MasterSnipeSignal
from detector.loss_learner import LossPostMortemLearner


class TestPumpfunDetectorEngine(unittest.TestCase):

    def test_benjamini_hochberg_fdr(self):
        p_vals = np.array([0.001, 0.004, 0.008, 0.15, 0.22, 0.35, 0.50, 0.65, 0.80, 0.95])
        rejected = benjamini_hochberg(p_vals, alpha=0.05)
        q_vals = compute_q_values(p_vals)
        self.assertEqual(len(rejected), 10)
        self.assertTrue(rejected[0])
        self.assertTrue(rejected[1])
        self.assertFalse(rejected[9])
        self.assertTrue(all(q_vals <= 1.0))
        self.assertTrue(all(q_vals >= 0.0))

    def test_subgroup_discovery_wracc(self):
        rng = np.random.default_rng(42)
        n = 500
        fa = rng.uniform(0, 20, n)
        fb = rng.uniform(0, 10, n)
        y = np.zeros(n, dtype=int)
        for i in range(n):
            if fa[i] > 10 and fb[i] < 5:
                y[i] = 1 if rng.random() < 0.85 else 0
            else:
                y[i] = 1 if rng.random() < 0.20 else 0

        df = pd.DataFrame({"fa": fa, "fb": fb, "win": y})
        miner = SubgroupMiner(min_support=25, max_depth=2, alpha_fdr=0.10)
        rules = miner.mine(df, target_col="win", feature_cols=["fa", "fb"])
        self.assertGreater(len(rules), 0)
        best = rules[0]
        self.assertGreater(best["lift"], 1.5)
        self.assertIn("wracc", best)
        self.assertIn("q_value", best)

    def test_realistic_slippage_and_fees(self):
        ts = np.array([0, 10, 20, 30, 40])
        px = np.array([1.0, 0.95, 0.85, 0.65, 0.60])
        path = (ts, px)
        outcome = realistic_tpsl_outcome(
            path=path, t_entry=0.0, p_entry=1.0,
            tp=0.5, sl=0.3, window_s=600.0,
            realistic=True, position_size_sol=0.5
        )
        self.assertEqual(outcome["result"], "loss")
        self.assertLess(outcome["pnl_pct"], -30.0)
        self.assertAlmostEqual(outcome["slippage_applied"], 0.3349, places=2)
        self.assertGreater(outcome["fee_sol"], 0.0)

    def test_microstructure_vpin_amihud_lambda(self):
        vpin_eng = DEXVolumeSynchronizedToxicity(bucket_size_sol=1.0, window_buckets=5)
        for _ in range(5):
            vpin_eng.update_swap(1.0, is_buy=True)
        vpin_val = vpin_eng.update_swap(1.0, is_buy=True)
        self.assertGreater(vpin_val, 0.5)

        lam = calculate_cpmm_kyle_lambda(net_sol=10.0)
        self.assertGreater(lam, 0.0)

    def test_master_snipe_signal_rules(self):
        # 1. Dead Launch VETO
        dec_dead = MasterSnipeSignal.evaluate({"marketCap": 3200.0, "firstMarketCap": 3000.0, "firstLiquidity": 1200.0})
        self.assertFalse(dec_dead.admitted)
        self.assertEqual(dec_dead.branch, "VETO")
        self.assertIn("DEAD_LAUNCH", dec_dead.veto_reason)

        # 2. Dev Dump VETO
        dec_dev = MasterSnipeSignal.evaluate({"marketCap": 6000.0, "firstMarketCap": 6000.0, "firstLiquidity": 1200.0, "devBalancePct": 2.5})
        self.assertFalse(dec_dev.admitted)
        self.assertEqual(dec_dev.branch, "VETO")
        self.assertIn("DEV_DUMP", dec_dev.veto_reason)

        # 3. Branch A Admission
        dec_branch_a = MasterSnipeSignal.evaluate({
            "marketCap": 5500.0,
            "firstMarketCap": 4100.0,
            "firstLiquidity": 1500.0,
            "devBalancePct": 0.5,
            "migrationProgress": 15.0,
            "realSolReserves": 25.0,
        })
        self.assertTrue(dec_branch_a.admitted)
        self.assertEqual(dec_branch_a.branch, "MASTER_ALPHA_EXPANSION")
        self.assertGreater(dec_branch_a.confidence, 0.80)

        # 4. Robustness to NaNs
        dec_nan = MasterSnipeSignal.evaluate({"marketCap": float("nan"), "firstMarketCap": None, "holderCount": float("nan")})
        self.assertFalse(dec_nan.admitted)

    def test_loss_post_mortem_clustering(self):
        df_losses = pd.DataFrame([
            {"market_cap": 3000.0, "pnl_pct": -25.0, "reason": "TIMEOUT", "devBalancePct": 0.5, "realSolReserves": 30.0, "migrationProgress": 10.0},
            {"market_cap": 3500.0, "pnl_pct": -30.0, "reason": "TIMEOUT", "devBalancePct": 0.5, "realSolReserves": 30.0, "migrationProgress": 12.0},
            {"market_cap": 8000.0, "pnl_pct": -58.0, "reason": "STOP_LOSS", "devBalancePct": 0.5, "realSolReserves": 10.0, "migrationProgress": 20.0},
            {"market_cap": 8000.0, "pnl_pct": -65.0, "reason": "STOP_LOSS", "devBalancePct": 0.5, "realSolReserves": 8.0, "migrationProgress": 25.0},
        ])
        learner = LossPostMortemLearner(df_losses)
        clusters = learner.analyze_failures()
        self.assertGreater(len(clusters), 0)
        names = [c.name for c in clusters]
        self.assertIn("DEAD_LAUNCH_TIMEOUT", names)
        self.assertIn("CATASTROPHIC_SLIPPAGE_RUG", names)

    def test_win_loss_learner_archetypes_and_bayesian(self):
        from detector.loss_learner import WinLossLearner, WinArchetype
        df_trades = pd.DataFrame([
            {"market_cap": 5000.0, "first_market_cap": 3000.0, "first_liquidity": 1200.0, "pnl_pct": 85.0, "buys_m5": 30, "sells_m5": 10, "dev_balance_pct": 0.5, "real_sol_reserves": 30.0},
            {"market_cap": 12000.0, "first_market_cap": 8000.0, "first_liquidity": 3000.0, "pnl_pct": 45.0, "buys_m5": 25, "sells_m5": 12, "dev_balance_pct": 0.4, "real_sol_reserves": 32.0},
            {"market_cap": 3000.0, "first_market_cap": 3000.0, "first_liquidity": 1000.0, "pnl_pct": -30.0, "buys_m5": 2, "sells_m5": 15, "dev_balance_pct": 2.5, "real_sol_reserves": 8.0},
            {"market_cap": 3200.0, "first_market_cap": 3200.0, "first_liquidity": 1200.0, "pnl_pct": -50.0, "buys_m5": 1, "sells_m5": 10, "dev_balance_pct": 2.0, "real_sol_reserves": 9.0},
        ])
        learner = WinLossLearner(df_trades)
        wins = learner.analyze_wins()
        failures = learner.analyze_failures()
        self.assertGreater(len(wins), 0)
        self.assertGreater(len(failures), 0)
        win_names = [w.name for w in wins]
        self.assertTrue("ALPHA_EXPANSION_BURST" in win_names or "POSITIVE_FLOW_MOMENTUM" in win_names)

        # Test Bayesian Log Odds
        win_vec = {"marketCap": 15000.0, "buysM5": 40, "sellsM5": 15, "devBalancePct": 0.5, "realSolReserves": 35.0}
        loss_vec = {"marketCap": 3000.0, "buysM5": 1, "sellsM5": 20, "devBalancePct": 2.8, "realSolReserves": 8.0}
        lr_win, p_win = learner.calculate_bayesian_log_odds(win_vec)
        lr_loss, p_loss = learner.calculate_bayesian_log_odds(loss_vec)
        self.assertGreater(lr_win, lr_loss)
        self.assertGreater(p_win, p_loss)

    def test_master_signal_branch_d_flow_momentum(self):
        # Branch D: Positive flow momentum in mid-cap
        dec = MasterSnipeSignal.evaluate({
            "marketCap": 12000.0,
            "firstMarketCap": 8000.0,
            "firstLiquidity": 3500.0,
            "buysM5": 25,
            "sellsM5": 10,
            "devBalancePct": 0.5,
            "migrationProgress": 20.0,
            "realSolReserves": 30.0,
        })
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.branch, "MASTER_ALPHA_FLOW_MOMENTUM")
        self.assertGreater(dec.expected_winrate, 0.50)

    def test_multi_stage_exit_policy(self):
        policy = MasterSnipeSignal.get_exit_policy()
        self.assertIn("tp1_partial_pct", policy)
        self.assertIn("tp1_scale_fraction", policy)
        self.assertIn("breakeven_lock_pct", policy)
        self.assertIn("flow_stall_seconds", policy)
        self.assertEqual(policy["tp1_partial_pct"], 50.0)
        self.assertEqual(policy["tp1_scale_fraction"], 0.50)
        self.assertEqual(policy["breakeven_lock_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
