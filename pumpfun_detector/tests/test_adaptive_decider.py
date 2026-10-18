"""Ítems 3-4 — Cableado del aprendizaje adaptativo al decisor con puerta BP-003.

Ley (AGENTS.md §4): prohibido alterar umbrales con muestras < 30 operaciones
forward. Estos tests congelan ese contrato punta a punta:

- ``AutoEvolutionEngine.get_effective_decider_thresholds()``: un umbral adaptado
  NO llega al decisor hasta que ``n_support >= 30``; antes devuelve el literal
  del código (20.0% bundler / 1.5% dev / 30.0% top-10 / 0.15 liq-MC).
- ``AIQuantModel.predict_token(thresholds=...)``: el override mueve de verdad la
  cadena de vetos y queda declarado en ``thresholds_used``.
- ``build_decision_stages``: la traza muestra el umbral efectivo y su fuente
  (``default_literal`` vs ``adaptive_rule``).
- Auto-``/boost``: recalibración ML cada 30 cierres paper (no antes).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.ai_engine import AIEngine  # noqa: E402
from detector.ai_quant_model import AIQuantModel  # noqa: E402
from detector.auto_evolution import AutoEvolutionEngine  # noqa: E402
from detector.gmgn_bridge import LivePaperTrader, build_decision_stages  # noqa: E402


def _loss_trade(i: int, bundler_pct: float = 15.0, top10: float = 0.0):
    return dict(
        pos_dict={"mint": f"M{i}", "symbol": f"S{i}", "size_sol": 1.0,
                  "realized_pnl_sol": -0.3, "exit_reason": "SL"},
        token_features={"bundler_pct": bundler_pct, "top10_float_pct": top10,
                        "avg_tx_m5_usd": 15.0, "liquidity_usd": 3000.0},
    )


class TestBP003Gate(unittest.TestCase):
    def test_locked_below_30_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            evo = AutoEvolutionEngine(runtime_dir=Path(tmp))
            evo.diagnose_and_learn_from_closed_trade(**_loss_trade(1))
            eff = evo.get_effective_decider_thresholds()

            b = eff["max_bundler_pct"]
            self.assertFalse(b["unlocked"], "N=1 no puede mover al decisor (BP-003)")
            self.assertEqual(b["value"], 20.0, "debe caer al literal del código")
            self.assertEqual(b["source"], "default_literal")
            self.assertEqual(int(evo.rules["closed_trades_seen"]), 1)

    def test_unlocked_at_30_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            evo = AutoEvolutionEngine(runtime_dir=Path(tmp))
            for i in range(30):
                evo.diagnose_and_learn_from_closed_trade(**_loss_trade(i))
            eff = evo.get_effective_decider_thresholds()

            b = eff["max_bundler_pct"]
            self.assertTrue(b["unlocked"], "con N=30 la regla aprendida debe activarse")
            self.assertEqual(b["source"], "adaptive_rule")
            self.assertGreaterEqual(b["n_support"], 30)
            self.assertNotEqual(b["value"], 20.0, "el valor adaptado difiere del literal")
            self.assertLess(b["value"], 20.0, "la pérdida por bundler APRIETA el umbral")
            self.assertEqual(int(evo.rules["closed_trades_seen"]), 30)

    def test_top10_rule_maps_to_decider_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            evo = AutoEvolutionEngine(runtime_dir=Path(tmp))
            for i in range(30):
                evo.diagnose_and_learn_from_closed_trade(**_loss_trade(i, bundler_pct=0.0, top10=40.0))
            eff = evo.get_effective_decider_thresholds()

            t = eff["max_top10_pct"]
            self.assertTrue(t["unlocked"])
            self.assertLess(t["value"], 28.0)
            # La regla de bundler NO fue ajustada → sigue literal.
            self.assertFalse(eff["max_bundler_pct"]["unlocked"])
            self.assertEqual(eff["max_bundler_pct"]["value"], 20.0)


class TestPredictTokenThresholds(unittest.TestCase):
    def _bare_model(self):
        """Modelo sin entrenamiento: sólo la cadena de vetos (predict_proba fijo)."""
        m = AIQuantModel.__new__(AIQuantModel)

        class _FakeModel:
            def predict_proba(self, X):
                return [[0.3, 0.7]]

        m.model = _FakeModel()
        return m

    def test_default_bundler_threshold_keeps_20(self):
        r = self._bare_model().predict_token(first_mc=10000.0, first_liq=3000.0, bundler_pct=16.0)
        self.assertNotIn("BUNDLER", str(r["veto_reason"] or ""))
        self.assertEqual(r["thresholds_used"]["max_bundler_pct"], 20.0)

    def test_bundler_override_changes_verdict(self):
        r = self._bare_model().predict_token(
            first_mc=10000.0, first_liq=3000.0, bundler_pct=16.0,
            thresholds={"max_bundler_pct": 15.0})
        self.assertIn("BUNDLER", str(r["veto_reason"] or ""))
        self.assertEqual(r["thresholds_used"]["max_bundler_pct"], 15.0)

    def test_top10_override_changes_verdict(self):
        r = self._bare_model().predict_token(
            first_mc=10000.0, first_liq=3000.0, top10_pct=15.0,
            thresholds={"max_top10_pct": 12.0})
        self.assertIn("Cabal", str(r["veto_reason"] or ""))


class TestStageTraceThresholds(unittest.TestCase):
    def test_stage_shows_effective_threshold_and_source(self):
        ml = {
            "probability_success": 0.71, "confluence_score": 20.0, "expected_ev": "+1.0%",
            "ev_fraction": 0.01, "admitted": False, "verdict": "VETO",
            "veto_reason": "VETO_BUNDLER_CLUSTER: ...", "pattern_id": "BUNDLER_UNIFORM_TRAP",
            "pattern_label": "Trampa", "bias": "BEARISH_VETO", "causal_trigger": "x",
            "metrics_known": True, "thresholds_used": {"max_bundler_pct": 14.0},
        }
        stages = build_decision_stages(
            source="dexscreener", mint="M1", symbol="S1", mc_usd=25000.0, liq_usd=6000.0,
            metrics_known=True, bundler_pct=15.0, wash_trading_flag=False,
            clone_factory_flag=False, dev_factory_flag=False, dev_balance_pct=0.5,
            top10_pct=18.0, ml_eval=ml,
            thresholds_meta={"max_bundler_pct": {"unlocked": True, "value": 14.0}})
        by_stage = {s["stage"]: s for s in stages}

        b = by_stage["E3_bundler"]
        self.assertEqual(b["threshold"], 14.0, "la traza debe mostrar el umbral EFECTIVO")
        self.assertEqual(b["source_kind"], "adaptive_rule")
        self.assertEqual(by_stage["E3_cabal_top10"]["threshold"], 30.0)
        self.assertEqual(by_stage["E3_cabal_top10"]["source_kind"], "default_literal")


class TestAutoBoostCadence(unittest.TestCase):
    def test_boost_every_30_closed_trades(self):
        trader = LivePaperTrader(feed=None, store=None, evolution_engine=None)
        calls = {"n": 0}

        class _QM:
            def train_and_boost(self):
                calls["n"] += 1

        trader.quant_model = _QM()
        for i in range(29):
            trader.open_trade(f"m{i}", f"S{i}", 1000.0)
            trader.close_trade(f"m{i}", "MANUAL")
        self.assertEqual(calls["n"], 0, "no debe recalibrar antes de N=30 (BP-003)")
        self.assertIsNone(trader.last_auto_boost)

        trader.open_trade("m29", "S29", 1000.0)
        trader.close_trade("m29", "MANUAL")
        self.assertEqual(calls["n"], 1, "a los 30 cierres debe recalibrar el modelo")
        self.assertEqual(trader.last_auto_boost["at_n"], 30)
        self.assertTrue(trader.last_auto_boost["ok"])


class TestAIEngineConsistency(unittest.TestCase):
    """El chat manual debe auditar con los MISMOS umbrales que el decisor."""

    def test_none_without_engine(self):
        eng = AIEngine.__new__(AIEngine)
        eng.evolution_engine = None
        self.assertIsNone(eng._effective_decider_thresholds())

    def test_only_unlocked_rules_pass_through(self):
        class _Evo:
            def get_effective_decider_thresholds(self):
                return {
                    "max_bundler_pct": {"value": 14.0, "unlocked": True},
                    "max_top10_pct": {"value": 30.0, "unlocked": False},
                }

        eng = AIEngine.__new__(AIEngine)
        eng.evolution_engine = _Evo()
        self.assertEqual(eng._effective_decider_thresholds(), {"max_bundler_pct": 14.0})


if __name__ == "__main__":
    unittest.main()
