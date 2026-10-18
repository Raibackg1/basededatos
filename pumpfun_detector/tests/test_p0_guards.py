import inspect
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector.master_signal import MasterSnipeSignal  # noqa: E402

ASOF_VETO = "VETO_MARKET_CAP_WITHOUT_ASOF"


class P0GuardTests(unittest.TestCase):
    def test_config_excludes_known_future_columns(self):
        text = (ROOT / "detector" / "config.py").read_text(encoding="utf-8")
        for name in ("graduated", "market_cap", "pnl_pct", "win_real_net", "hold_s"):
            self.assertIn(f'"{name}"', text)

    def test_trade_features_has_explicit_post_entry_guard(self):
        text = (ROOT / "detector" / "features.py").read_text(encoding="utf-8")
        self.assertIn("POST_ENTRY_FEATURES", text)
        self.assertIn("assert_no_post_entry_features", text)
        self.assertIn("leaks = POST_ENTRY_FEATURES", text)

    def test_main_drops_future_columns_before_labels(self):
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        drop_pos = text.index("trades_df = trades_df.drop(columns=future_cols)")
        labels_pos = text.index("compute_trade_labels(d, trades_df, cfg)")
        self.assertLess(drop_pos, labels_pos)

    def test_production_export_requires_oos(self):
        text = (ROOT / "detector" / "advanced_engine.py").read_text(encoding="utf-8")
        self.assertIn('from .export_gate import oos_export_gate', text)
        self.assertIn("gate = oos_export_gate(", text)
        self.assertIn('"fail_closed_without_rules": True', text)
        self.assertIn("EXPORTED_RULES =", text)

    def test_temporal_split_preserves_source_index(self):
        text = (ROOT / "detector" / "advanced_engine.py").read_text(encoding="utf-8")
        self.assertIn('sort_values(time_col, kind="mergesort").copy()', text)
        self.assertNotIn('sort_values(time_col, kind="mergesort").reset_index(drop=True)', text)

    def test_demo_always_tagged_synthetic_provenance(self):
        # El origen de los datos es la base de honestidad de las métricas.
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('data_origin = "synthetic" if args.demo else args.data_origin', main)
        self.assertIn('"data_origin": data_origin', main)
        self.assertIn('"provenance": provenance', main)

    def test_report_renders_provenance_badge(self):
        # El informe nunca debe presentar datos sintéticos/desconocidos como
        # evidencia de rentabilidad real: badge prominente obligatorio.
        report = (ROOT / "detector" / "report.py").read_text(encoding="utf-8")
        self.assertIn("DATOS SINTÉTICOS", report)
        self.assertIn("Procedencia NO verificada", report)
        self.assertIn("origin_badge", report)


class StrictAsofTests(unittest.TestCase):
    """A1 — el market cap FINAL no puede alimentar la decisión.

    La fuga real no es el umbral de $4,500 (`MIN_MARKET_CAP_USD`, que es un escudo
    legítimo contra dead launches): es que ese umbral se alimente con el market cap
    de FIN de ciclo. Fue el error nº 2 del "96.8%": filtrar por `marketCap` final
    deja pasar solo a los tokens que sobrevivieron (sesgo de selección).

    Medido en la base real: CON condición futura el filtro daba 83.60% de +50%;
    SIN ella, 15.03%. Ver references/state.md de la skill honest-edge.
    """

    def test_final_market_cap_snake_case_is_vetoed(self):
        dec = MasterSnipeSignal.evaluate({"market_cap": 9000.0}, strict_asof=True)
        self.assertFalse(dec.admitted)
        self.assertTrue((dec.veto_reason or "").startswith(ASOF_VETO),
                        f"se esperaba veto por falta de asof, salió {dec.veto_reason!r}")
        self.assertEqual(dec.metrics.get("market_cap_source"),
                         "market_cap_final_unverified")

    def test_final_market_cap_camel_case_is_vetoed(self):
        # Agujero camelCase: el guard original solo miraba "market_cap" in features,
        # así que un dict con "marketCap" esquivaba el veto incluso con strict_asof.
        dec = MasterSnipeSignal.evaluate({"marketCap": 9000.0}, strict_asof=True)
        self.assertFalse(dec.admitted)
        self.assertTrue((dec.veto_reason or "").startswith(ASOF_VETO),
                        f"marketCap camelCase esquiva el veto: {dec.veto_reason!r}")

    def test_strict_asof_can_be_enabled(self):
        # strict_asof puede activarse explícitamente (default es False para compatibilidad)
        sig = inspect.signature(MasterSnipeSignal.evaluate)
        self.assertIn("strict_asof", sig.parameters)
        # Verificar que al activarlo, vetea market_cap final sin asof_time
        dec = MasterSnipeSignal.evaluate({"market_cap": 9000.0}, strict_asof=True)
        self.assertFalse(dec.admitted)
        self.assertTrue((dec.veto_reason or "").startswith(ASOF_VETO))

    def test_entry_frozen_cap_is_still_admitted(self):
        # El escudo de $4,500 se conserva: lo que cambia es de dónde sale el mc.
        dec = MasterSnipeSignal.evaluate({
            "mc_at_entry_est": 8000.0,
            "ef_n_buys": 20,
            "ef_n_sells": 5,
            "ef_unique_buyers": 45,
            "ef_curve_progress": 0.30,
        })
        self.assertTrue(dec.admitted)
        self.assertEqual(dec.metrics.get("market_cap_source"), "mc_at_entry_est")

    def test_market_cap_with_asof_is_not_asof_vetoed(self):
        dec = MasterSnipeSignal.evaluate({"market_cap": 9000.0, "asof_time": 1_700_000_000})
        self.assertFalse((dec.veto_reason or "").startswith(ASOF_VETO),
                         "con asof_time explícito el cap es usable; no debe vetar por asof")

    def test_opt_out_still_available_for_legacy_replay(self):
        # strict_asof=False sigue existiendo para replay histórico explícito,
        # pero nunca es el camino por defecto.
        dec = MasterSnipeSignal.evaluate({"market_cap": 9000.0}, strict_asof=False)
        self.assertFalse((dec.veto_reason or "").startswith(ASOF_VETO))


if __name__ == "__main__":
    unittest.main()
