import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
