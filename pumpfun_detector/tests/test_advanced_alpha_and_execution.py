"""Tests unitarios para CreatorRegistry, RugHazardModel, BayesianBranchTracker y RPCTierPool."""
import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.creator_registry import CreatorRegistry, holder_shannon_entropy
from detector.survival_model import RugHazardModel, BayesianBranchTracker
from detector.rpc_tiers import RPCTierPool, RPCTier, RPCEndpoint, TransactionSimulator
from detector.master_signal import MasterSnipeSignal


class AdvancedAlphaAndExecutionTests(unittest.TestCase):
    def test_holder_shannon_entropy_balanced_vs_concentrated(self):
        # 10 holders con 10% cada uno (distribución equitativa)
        equitative = [10.0] * 10
        h_eq = holder_shannon_entropy(equitative)
        self.assertAlmostEqual(h_eq, 1.0, places=3)

        # Concentración extrema: 1 holder con 99%, 9 con casi nada
        concentrated = [99.0] + [0.1] * 9
        h_conc = holder_shannon_entropy(concentrated)
        self.assertLess(h_conc, 0.25)

    def test_creator_registry_state_transitions(self):
        reg = CreatorRegistry(":memory:")
        w = "Rugger1111111111111111111111111111111111111"

        # 1. Al inicio es desconocido (GREYLIST)
        rep0 = reg.get_reputation(w)
        self.assertEqual(rep0.status, "GREYLIST")
        self.assertFalse(rep0.is_blocked)

        # 2. Registramos 1 rug pull (-70%)
        reg.record_outcome(w, is_win=False, pnl_pct=-70.0, is_rug=True)
        # 3. Registramos un segundo rug pull (-80%) -> Promoción automática a DENYLIST (BP-008)
        rep2 = reg.record_outcome(w, is_win=False, pnl_pct=-80.0, is_rug=True)
        self.assertEqual(rep2.status, "DENYLIST")
        self.assertTrue(rep2.is_blocked)

        # 4. MasterSnipeSignal veta automáticamente al creador denylisted
        feat = {
            "firstMarketCap": 3500.0,
            "firstLiquidity": 1200.0,
            "market_cap": 6000.0,
            "dev_wallet": w,
            "realSolReserves": 20.0,
            "devBalancePct": 0.5,
            "buysM5": 5,
        }
        dec = MasterSnipeSignal.evaluate(feat, creator_registry=reg)
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.branch, "VETO")
        self.assertIn("VETO_CREATOR_DENYLISTED", dec.veto_reason)

    def test_rug_hazard_survival_model(self):
        model = RugHazardModel(base_hazard=0.005, min_survival_threshold=0.60)

        # Caso seguro: dev balance bajo (0.2%), buenas reservas (25 SOL), VPIN bajo
        safe_feats = {
            "devBalancePct": 0.2,
            "ef_vpin_30s": 0.15,
            "buysM5": 20,
            "sellsM5": 2,
            "realSolReserves": 28.0,
            "market_cap": 8000.0,
        }
        est_safe = model.evaluate(safe_feats)
        self.assertTrue(est_safe.is_safe)
        self.assertGreaterEqual(est_safe.survival_prob_60s, 0.60)

        # Caso peligroso: dev balance alto (1.8%), VPIN tóxico (0.60), ventas masivas
        toxic_feats = {
            "devBalancePct": 1.8,
            "ef_vpin_30s": 0.60,
            "buysM5": 5,
            "sellsM5": 25,
            "realSolReserves": 10.0,
            "market_cap": 4200.0,
        }
        est_toxic = model.evaluate(toxic_feats)
        self.assertFalse(est_toxic.is_safe)
        self.assertLess(est_toxic.survival_prob_60s, 0.60)
        self.assertIn("VETO_HAZARD_RUG_RISK", est_toxic.veto_reason)

        # Integración con MasterSnipeSignal
        dec = MasterSnipeSignal.evaluate(toxic_feats, survival_model=model)
        self.assertFalse(dec.admitted)
        self.assertEqual(dec.branch, "VETO")

    def test_bayesian_branch_tracker(self):
        tracker = BayesianBranchTracker(breakeven_winrate=0.40, min_samples=10)

        # Rama ganadora: 12 victorias, 2 derrotas
        for _ in range(12):
            tracker.record_outcome("RAMA_WINNER", is_win=True)
        for _ in range(2):
            tracker.record_outcome("RAMA_WINNER", is_win=False)

        prob_win = tracker.get_posterior_prob_above_breakeven("RAMA_WINNER")
        self.assertGreater(prob_win, 0.90)
        self.assertFalse(tracker.should_deactivate_branch("RAMA_WINNER"))

        # Rama perdedora: 1 victoria, 12 derrotas
        for _ in range(1):
            tracker.record_outcome("RAMA_LOSER", is_win=True)
        for _ in range(12):
            tracker.record_outcome("RAMA_LOSER", is_win=False)

        prob_lose = tracker.get_posterior_prob_above_breakeven("RAMA_LOSER")
        self.assertLess(prob_lose, 0.10)
        self.assertTrue(tracker.should_deactivate_branch("RAMA_LOSER"))

    def test_rpc_pool_and_transaction_simulator(self):
        pool = RPCTierPool([
            RPCEndpoint(url="https://tier1.rpc", tier=RPCTier.TIER_1_EXECUTION),
            RPCEndpoint(url="https://tier3.rpc", tier=RPCTier.TIER_3_BACKUP),
        ])
        ep = pool.select_endpoint(RPCTier.TIER_1_EXECUTION)
        self.assertIsNotNone(ep)
        self.assertEqual(ep.url, "https://tier1.rpc")

        # Simular fallo pre-flight por Slippage
        sim_resp_slippage = {
            "value": {
                "err": {"InstructionError": [2, {"Custom": 6000}]},
                "logs": ["Program 6EF8... pump invoke", "Program log: Custom program error: 0x1770 (slippage exceeded)"],
                "unitsConsumed": 45000,
            }
        }
        res = TransactionSimulator.parse_simulation_response(sim_resp_slippage)
        self.assertFalse(res.success)
        self.assertEqual(res.rejection_reason, "VETO_PREFLIGHT_SLIPPAGE_EXCEEDED")

        # Simular éxito pre-flight
        sim_resp_ok = {
            "value": {
                "err": None,
                "logs": ["Program 6EF8... success"],
                "unitsConsumed": 28000,
            }
        }
        res_ok = TransactionSimulator.parse_simulation_response(sim_resp_ok)
        self.assertTrue(res_ok.success)
        self.assertEqual(res_ok.units_consumed, 28000)


if __name__ == "__main__":
    unittest.main()
