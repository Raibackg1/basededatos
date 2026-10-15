"""Tests de accesibilidad (WCAG 2.2 AA mínimo) de los informes HTML.

Verifica en el HTML generado:
- lang="es" declarado
- <caption> y scope="col" en tablas
- alt descriptivo en <img>
- <label for> asociado al input del simulador
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.audit_report import render_audit_html
from detector.report import _df_html, _img, CSS


class TestAuditReportA11y(unittest.TestCase):

    def _render(self) -> str:
        report = {
            "source": {"filename": "test.db", "origin": "synthetic",
                       "schema_sha256": "abc"},
            "program_version": "1.0",
            "depth": "basic",
            "generated_at_utc": "2026-01-01T00:00:00Z",
            "schema_version": 1,
            "inventory": {"tables": {"tokens": {
                "columns": [{"name": "id", "type": "INTEGER",
                             "not_null": True, "pk_position": 1}],
                "indexes": [], "foreign_keys": [],
            }}},
            "profiles": {"tables": {"tokens": {
                "row_count": {"value": 10, "scope": "full"},
                "columns": {"id": {"nulls": {"value": 0, "scope": "full",
                                             "n_examined": 10}}},
            }}},
            "semantic_mapping": {"tables": {}},
            "findings": [],
            "warnings": [],
        }
        return render_audit_html(report)

    def test_lang_es_declared(self):
        html = self._render()
        self.assertIn('lang="es"', html)

    def test_table_caption_present(self):
        html = self._render()
        self.assertIn("<caption>", html)

    def test_th_scope_col(self):
        html = self._render()
        self.assertIn('<th scope="col">', html)
        # No debe quedar ningún <th> sin scope en las tablas del informe
        import re
        bare_th = re.findall(r"<th>", html)
        self.assertEqual(bare_th, [], f"<th> sin scope encontrados: {bare_th}")


class TestReportA11y(unittest.TestCase):

    def test_df_html_has_scope_col(self):
        import pandas as pd
        df = pd.DataFrame({"col_a": [1, 2], "col_b": [3.0, 4.0]})
        out = _df_html(df, caption="Tabla de prueba")
        self.assertIn('<th scope="col">', out)
        self.assertIn("<caption>Tabla de prueba</caption>", out)

    def test_df_html_no_bare_th(self):
        import pandas as pd
        import re
        df = pd.DataFrame({"x": [1]})
        out = _df_html(df)
        bare = re.findall(r"<th>", out)
        self.assertEqual(bare, [])

    def test_img_has_alt(self):
        out = _img("data:image/png;base64,x", alt="Gráfico de prueba")
        self.assertIn('alt="Gráfico de prueba"', out)

    def test_img_default_alt_present(self):
        out = _img("data:image/png;base64,x")
        self.assertIn("alt=", out)

    def test_slider_label_has_for(self):
        # El CSS y el marcado del simulador deben asociar label al input
        from detector.report import build_interactive_simulator
        results = {"sim_data": {"records_json": '[{"score":0.7,"pnl":0.1,"vetoed":0}]'}}
        sim = build_interactive_simulator(results)
        self.assertIn("for='tauSlider'", sim)
        self.assertIn("id='tauSlider'", sim)


if __name__ == "__main__":
    unittest.main()
