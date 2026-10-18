"""
Tests de las olas de mejora aplicadas (informe teorico $PUMPSCOPE_DB + $STRATEGY_MEMORY):

- Ola 1: purged split temporal (purge_gap).
- Ola 2: modelo de costes reales CostModel + etiqueta win_real_net.
- Ola 3: minería de confluencias multivariante A ∧ B ∧ C con control FDR.
- Fix Señal Maestra: fallbacks al esquema nativo del detector (n_buys_60s,
  unique_buyers_60s, ef_curve_progress, mc_at_entry_est).
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.advanced_engine import compute_q_values, temporal_train_val_test_split
from detector.config import Cfg
from detector.costs import DEFAULT_COSTS, CostModel
from detector.labels import compute_trade_labels
from detector.loss_learner import WinLossLearner
from detector.master_signal import MasterSnipeSignal
from detector.patterns import confluence_rules


class _DummyPaths(dict):
    pass


class _DummyData:
    def __init__(self):
        self.paths = _DummyPaths()


class TestImprovementWaves(unittest.TestCase):

    # ---------------- Ola 1: purged split ----------------
    def test_purge_gap_removes_boundary_rows(self):
        df = pd.DataFrame({
            "created_at": list(range(100)),
            "y": [i % 2 for i in range(100)],
        })
        tr0, va0, te0 = temporal_train_val_test_split(df, purge_gap=0)
        tr5, va5, te5 = temporal_train_val_test_split(df, purge_gap=5)
        self.assertEqual(len(tr0), 60)
        self.assertEqual(len(tr5), 55)
        self.assertEqual(len(va5), len(va0))
        self.assertEqual(len(te5), len(te0) - 5)
        # train purgado no debe tocar la frontera con val
        self.assertLess(tr5["created_at"].max(), va5["created_at"].min())

    # ---------------- Ola 2: costes reales ----------------
    def test_cost_model_roundtrip_and_breakeven(self):
        c = CostModel()
        self.assertAlmostEqual(c.net_pnl_pct(10.0), 10.0 - 5.64, places=6)
        self.assertAlmostEqual(c.gross_needed_for_net(0.0), 5.64, places=6)
        # W=20 neto bruto, L=-10 -> netos (14.36, -15.64) -> p* = 15.64/30
        p = c.breakeven_winrate(c.net_pnl_pct(20.0), c.net_pnl_pct(-10.0))
        self.assertAlmostEqual(p, 15.64 / 30.0, places=6)
        # Caso degenerado: sin pérdidas no hay breakeven definido
        self.assertTrue(np.isnan(c.breakeven_winrate(20.0, 5.0)))

    def test_default_costs_exposed(self):
        self.assertIsInstance(DEFAULT_COSTS, CostModel)
        self.assertGreater(DEFAULT_COSTS.roundtrip_frac, 0.0)

    def test_win_real_net_label_discounts_costs(self):
        d = _DummyData()
        cfg = Cfg()
        df = pd.DataFrame({
            "token_id": ["a", "b", "c"],
            "pnl_sol": [0.10, -0.02, 0.01],
            "size_sol": [0.5, 0.5, 0.5],  # => +20%, -4%, +2%
        })
        out = compute_trade_labels(d, df, cfg)
        self.assertIn("win_real_net", out.columns)
        # +20% bruto -> neto +14.36% -> win; -4% -> -9.64% -> loss; +2% -> -3.64% -> LOSS neto
        self.assertEqual(list(out["win_real"]), [1.0, 0.0, 1.0])
        self.assertEqual(list(out["win_real_net"]), [1.0, 0.0, 0.0])

    # ---------------- Ola 3: confluencias ----------------
    def test_confluence_discovers_ab_joint_effect(self):
        rng = np.random.default_rng(7)
        n = 900
        a = rng.uniform(0, 10, n)
        b = rng.uniform(0, 10, n)
        c = rng.uniform(0, 10, n)
        # Solo la conjunción A alto ∧ B bajo predice win (efecto conjunto puro)
        y = np.where((a > 6.67) & (b < 3.34), 1.0, 0.0)
        # ruido controlado
        flip = rng.random(n) < 0.05
        y[flip] = 1.0 - y[flip]
        df = pd.DataFrame({"fa": a, "fb": b, "fc": c, "win": y})
        cfg = Cfg()
        cfg.min_bin_n = 10
        out = confluence_rules(df, "win", ["fa", "fb", "fc"], cfg, max_depth=2)
        self.assertGreater(len(out), 0)
        best = out.iloc[0]
        self.assertGreater(best["lift"], 2.0)
        self.assertIn("fa", best["confluencia"])
        self.assertIn("fb", best["confluencia"])
        self.assertIn("q_value", out.columns)
        self.assertTrue((out["q_value"] <= 1.0).all())

    def test_confluence_empty_without_enough_features(self):
        df = pd.DataFrame({"fa": [1.0, 2.0] * 30, "win": [0.0, 1.0] * 30})
        out = confluence_rules(df, "win", ["fa"], Cfg())
        self.assertEqual(len(out), 0)

    def test_evaluate_confluence_oos_applies_bounds(self):
        from detector.patterns import evaluate_confluence_oos
        conf = pd.DataFrame([{
            "confluencia": "fa=tercil2", "depth": 2, "n": 30, "wins": 27,
            "winrate": 0.9, "baseline": 0.5, "lift": 1.8, "p_value": 1e-6,
            "q_value": 1e-5, "fdr_sig": True,
            "bounds": [("fa", 6.0, 10.0)],
        }])
        rng = np.random.default_rng(3)
        test = pd.DataFrame({
            "fa": rng.uniform(0, 10, 200),
            "win": np.zeros(200),
        })
        # Dentro de los bounds el winrate es 1.0
        test.loc[test["fa"].between(6.0, 10.0), "win"] = 1.0
        out = evaluate_confluence_oos(conf, test, "win")
        self.assertGreater(out.iloc[0]["oos_n"], 0)
        self.assertAlmostEqual(out.iloc[0]["oos_winrate"], 1.0)
        self.assertTrue(out.iloc[0]["oos_pass"])

    # ---------------- Fix Señal Maestra: fallbacks esquema nativo ----------------
    def test_master_signal_native_schema_fallbacks(self):
        # Sin buysM5 pero con n_buys_60s del esquema pumpfun -> rama C alcanzable.
        # P0 anti-lookahead: se usa mc_at_entry_est (disponible a la entrada y
        # generado por el pipeline real), NO market_cap final (fuga). Los
        # fallbacks nativos (n_buys_60s, unique_buyers_60s, ef_curve_progress)
        # sí deben resolver la rama C.
        dec = MasterSnipeSignal.evaluate({
            "mc_at_entry_est": 8000.0,
            "n_buys_60s": 20,
            "n_sells_60s": 5,
            "unique_buyers_60s": 45,
            "ef_curve_progress": 0.30,  # 30% < 45% saturation
        })
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.branch, "MASTER_ALPHA_CONFLUENCE_MOMENTUM")

    def test_master_signal_mc_fallback_to_entry_estimate(self):
        # market_cap ausente (NaN->0) pero mc_at_entry_est disponible
        dec = MasterSnipeSignal.evaluate({
            "market_cap": float("nan"),
            "mc_at_entry_est": 8000.0,
            "n_buys_60s": 20,
            "n_sells_60s": 3,
            "unique_buyers_60s": 50,
            "ef_curve_progress": 0.2,
        })
        self.assertTrue(dec.admitted)

    def test_master_signal_still_vetoes_dead_launch(self):
        dec = MasterSnipeSignal.evaluate({"market_cap": 2000.0, "n_buys_60s": 30})
        self.assertFalse(dec.admitted)
        self.assertIn("DEAD_LAUNCH", dec.veto_reason)

    def test_compute_q_values_finite(self):
        q = compute_q_values(np.array([0.001, 0.2, 0.9]))
        self.assertTrue(np.all((q >= 0) & (q <= 1)))

    # ---------------- P0: regresión del purge anti-fuga ----------------
    def test_loss_learner_survives_purged_market_cap(self):
        # El purge anti-fuga P0 elimina market_cap/exit_reason del DataFrame.
        # analyze_failures NO debe lanzar KeyError: debe apoyarse en
        # mc_at_entry_est (entrada-segura) para el cluster DEAD_LAUNCH.
        df = pd.DataFrame({
            "pnl_pct": [-20.0, -80.0, 5.0],
            "pnl_sol": [-0.2, -0.8, 0.05],
            "mc_at_entry_est": [8000.0, 3000.0, 12000.0],
        })
        clusters = WinLossLearner(df).analyze_failures()
        dead = [c for c in clusters if c.name == "DEAD_LAUNCH_TIMEOUT"]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0].trade_count, 1)  # solo la fila con mc<=4500

    def test_loss_learner_no_crash_without_any_mc_or_reason(self):
        # Sin market_cap, sin mc_at_entry_est y sin exit_reason: la máscara
        # DEAD_LAUNCH debe ser todo-False (no KeyError).
        df = pd.DataFrame({"pnl_pct": [-20.0, 5.0], "pnl_sol": [-0.2, 0.05]})
        clusters = WinLossLearner(df).analyze_failures()
        self.assertFalse([c for c in clusters if c.name == "DEAD_LAUNCH_TIMEOUT"])

    # ---- Fix PatternIdentifier: degradación silenciosa tras el purge P0 ----
    def test_pattern_identifier_uses_entry_safe_mc_after_purge(self):
        # Tras el purge anti-fuga NO existe market_cap final; solo mc_at_entry_est.
        # El token sano (mc entrada-segura alta) NO debe caer en DEAD_LAUNCH.
        from detector.pattern_identifier import PatternIdentifier
        res = PatternIdentifier.identify_token_pattern({
            "mc_at_entry_est": 8000.0,
            "first_market_cap": 4000.0,
            "first_liquidity": 1200.0,
            "migration_progress": 20.0,
            "dev_balance_pct": 0.5,
        })
        self.assertNotEqual(res.name, "DEAD_LAUNCH_TIMEOUT")

    def test_pattern_identifier_still_flags_true_dead_launch(self):
        # Con mc entrada-segura realmente baja SÍ debe marcarse DEAD_LAUNCH.
        from detector.pattern_identifier import PatternIdentifier
        res = PatternIdentifier.identify_token_pattern({
            "mc_at_entry_est": 3000.0,
            "migration_progress": 10.0,
            "dev_balance_pct": 0.5,
        })
        self.assertEqual(res.name, "DEAD_LAUNCH_TIMEOUT")

    def test_pattern_identifier_no_mc_at_all_is_not_dead(self):
        # Sin ninguna métrica de mc: no debe degradar TODO a DEAD_LAUNCH
        # (comportamiento silencioso que inflaba el 98.6% del demo).
        from detector.pattern_identifier import PatternIdentifier
        res = PatternIdentifier.identify_token_pattern({
            "migration_progress": 30.0,
            "dev_balance_pct": 0.5,
        })
        self.assertNotEqual(res.name, "DEAD_LAUNCH_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
