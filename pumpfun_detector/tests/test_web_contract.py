"""Contrato web (P2): ``cockpit/static/index.html`` consume el bloque ``dashboard``.

Candado anti-regresión del contrato entre:

- ``cockpit/server.py``     -> ``/api/bot/status`` (bloque ``dashboard``),
  ``/api/pipeline/live``, ``/api/pipeline/trace/{mint}``, ``/api/logs`` y
  ``/api/events`` (SSE del ``EventBus``).
- ``cockpit/static/index.html`` -> la UI web (misma superficie que el exe C#).

Reglas duras (espejo del contrato de escritorio):

1. La UI web lee el bloque ``dashboard`` (``paper_wallet`` / ``runtime`` /
   ``evolution`` / ``metrics_known`` / ``open_positions`` / ``shadow_vetos``) y
   NO las claves raíz legacy del summary que causaban tarjetas mentirosas.
2. Ley anti-falacia: prohibidos los literales por defecto que se pintaban como
   datos reales (``0.9100``, ``29,598``, ``91.66%``, ``100.00 SOL``, ``$150.00``,
   sparklines sintéticas). Dato ausente => ``N/D — sin dato``.
3. La UI debe consumir el pipeline en vivo (P1) y el bus SSE (P0.3), y exponer
   la procedencia de cada umbral (``source_kind``).
4. Guías de interfaz: sin ``transition: all``, con ``prefers-reduced-motion``,
   ``color-scheme: dark`` y formatos numéricos vía ``Intl``.
"""
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
sys.path.insert(0, _PKG)

_HTML_PATH = os.path.join(_PKG, "cockpit", "static", "index.html")


class TestWebContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_HTML_PATH, "r", encoding="utf-8") as fh:
            cls.src = fh.read()

    # ------------------------------------------------------------------ 1. dashboard

    DASH_KEYS = frozenset({
        "schema", "ts", "paper_wallet", "evolution", "runtime",
        "metrics_known", "open_positions", "shadow_vetos",
    })
    PW_KEYS = frozenset({
        "initial_usd", "equity_usd", "pnl_usd", "pnl_sol", "open_count",
        "closed_count", "exposure_usd", "exposure_pct",
        "sol_price_usd", "sol_price_source",
    })
    RT_KEYS = frozenset({
        "feed_source", "feed_age_s", "stale", "loop_alive", "last_stage",
        "stage_age_s", "feed_misses", "last_feed_error",
    })
    EVO_KEYS = frozenset({
        "true_saves", "false_vetos", "avoided_loss_pct", "evolutions_count",
        "last_rule_change",
    })
    MK_KEYS = frozenset({"pnl", "evolution", "feed", "loop", "exposure", "sol_price_live"})

    @staticmethod
    def _reads(alias):
        return set(re.findall(r"\b%s\.([a-z_]+)" % alias, TestWebContract.src))

    def test_reads_dashboard_block_not_legacy_roots(self):
        self.assertIn("botStatus.dashboard", self.src)
        dash_reads = self._reads("dash")
        self.assertEqual(dash_reads - self.DASH_KEYS, set(),
                         "la UI lee claves fuera del bloque dashboard")
        # Bloques obligatorios: si la UI deja de leer uno, algo se rompió.
        for needed in ("paper_wallet", "runtime", "evolution", "metrics_known",
                       "open_positions", "shadow_vetos"):
            self.assertIn(needed, dash_reads, f"la UI dejó de leer dashboard.{needed}")

        pw_reads = self._reads("pw")
        self.assertEqual(pw_reads - self.PW_KEYS, set(),
                         "lecturas fuera de paper_wallet")
        for needed in ("initial_usd", "equity_usd", "pnl_sol", "pnl_usd",
                       "open_count", "closed_count", "sol_price_usd",
                       "sol_price_source"):
            self.assertIn(needed, pw_reads, f"la UI dejó de leer paper_wallet.{needed}")

        rt_reads = self._reads("rt")
        self.assertEqual(rt_reads - self.RT_KEYS, set(),
                         "lecturas fuera de runtime")
        for needed in ("feed_source", "stale", "loop_alive", "feed_misses"):
            self.assertIn(needed, rt_reads, f"la UI dejó de leer runtime.{needed}")

        evo_reads = self._reads("evo")
        self.assertEqual(evo_reads - self.EVO_KEYS, set(),
                         "lecturas fuera de evolution")
        for needed in ("true_saves", "false_vetos", "evolutions_count"):
            self.assertIn(needed, evo_reads, f"la UI dejó de leer evolution.{needed}")

        mk_reads = self._reads("mk")
        self.assertEqual(mk_reads - self.MK_KEYS, set(),
                         "lecturas fuera de metrics_known")

    def test_legacy_root_reads_are_gone(self):
        """Claves raíz que pintaban datos duplicados/legacy no deben volver."""
        for dead in ("botStatus.total_pnl_sol", "botStatus.open_positions",
                     "botStatus.open_count", "botStatus.feed",
                     "botStatus.shadow_vetos"):
            self.assertNotIn(dead, self.src,
                             f"lectura legacy reintroducida en la UI web: {dead}")

    def test_root_reads_are_declared(self):
        """Las lecturas raíz permitidas son SOLO las declaradas en este contrato."""
        allowed = frozenset({
            "dashboard", "recent_closed", "evolution", "auto_bot_enabled",
            "winrate_pct", "ok", "command", "data", "response", "error",
        })
        found = set(re.findall(r"\bbotStatus\.([a-z_]+)", self.src))
        self.assertEqual(found - allowed, set(),
                         f"la UI lee claves raíz no declaradas: {sorted(found - allowed)}")

    # ------------------------------------------------------------- 2. anti-falacia

    FORBIDDEN_LITERALS = (
        # Defaults JS que se pintaban como métricas reales.
        "qm.test_roc_auc || 0.91",
        "qm.train_samples || 29598",
        "qm.test_accuracy || 0.9166",
        "100.0 + (botStatus.total_pnl_sol ?? 0)",
        "ar.min_liquidity_usd || 8500",
        "ar.max_top10_float_pct || 28",
        # Valores estáticos del esqueleto HTML.
        "ROC-AUC: 0.9100",
        "29,598 tokens",
        "91.66%",
        "100.00 SOL",
        "$150.00",
        # Sparklines sintéticas (geometría inventada, no dato).
        "[10, 11, 10.5, 12, 13]",
        "[10, 10.5, 10.2, 10.8, 11.0]",
    )

    def test_fake_default_literals_are_gone(self):
        for bad in self.FORBIDDEN_LITERALS:
            self.assertNotIn(bad, self.src,
                             f"literal falso reintroducido en la UI web: {bad!r}")

    def test_nd_marker_and_helper_exist(self):
        self.assertIn("N/D — sin dato", self.src)
        self.assertIn("function nd(", self.src)

    def test_winrate_is_gated_by_closed_count(self):
        # El WR con 0 trades cerrados no es una métrica: debe condicionarse.
        self.assertRegex(self.src, r"closed_count[^\n]{0,120}winrate_pct|winrate_pct[^\n]{0,120}closed_count")

    # ----------------------------------------------------------------- 3. pipeline

    def test_pipeline_endpoints_are_consumed(self):
        for endpoint in ("/api/pipeline/live", "/api/pipeline/trace/",
                         "/api/events", "/api/logs?limit="):
            self.assertIn(endpoint, self.src,
                          f"la UI web no consume {endpoint}")

    def test_pipeline_schema_and_stage_vocabulary(self):
        self.assertIn("pipeline/1", self.src)
        for key in ("veto_by_stage", "executed", "admitted", "neutral",
                    "source_kind", "skip", "fail", "pass", "info"):
            self.assertIn(key, self.src,
                          f"vocabulario de pipeline ausente en la UI: {key}")

    def test_sse_uses_named_events(self):
        self.assertIn("EventSource", self.src)
        for kind in ("stage", "decision", "trade_open", "trade_close",
                     "evo", "feed", "error"):
            self.assertRegex(self.src, r"(?s)EVENT_KINDS\s*=\s*\[[^\]]*\"%s\"" % kind)

    def test_evolution_status_is_consumed(self):
        self.assertIn("/api/evolution/status", self.src)
        self.assertIn("decider_thresholds", self.src)

    # ------------------------------------------------------------ 4. guías web

    def test_web_guidelines(self):
        self.assertNotIn("transition: all", self.src)
        self.assertIn("prefers-reduced-motion", self.src)
        self.assertIn("color-scheme: dark", self.src)
        self.assertIn("Intl.NumberFormat", self.src)
        self.assertIn('aria-label="Recargar datos"', self.src)
        self.assertIn('role="status"', self.src)
        self.assertIn("touch-action: manipulation", self.src)

    # ------------------------------------------- 5. contratos campo UI<->server
    # Candados agregados tras la revisión adversarial de la migración web:
    # mismos shapes que server.py emite y que la app nativa consume.

    def test_annotation_post_matches_server_contract(self):
        """El POST de anotación debe enviar ``verdict`` (no ``decision``) y
        leer ``signal_id``/``reviews`` (no ``annotation_id``/``summary``)."""
        self.assertIn('verdict: decision === "BUY" ? "buy" : "veto"', self.src)
        self.assertIn("data.signal_id", self.src)
        for dead in ("data.annotation_id", "data.summary", "features_snapshot",
                     "decision: decision"):
            self.assertNotIn(dead, self.src,
                             f"campo fuera del contrato del server: {dead!r}")

    def test_dossier_mapping_matches_dossier_dataclass(self):
        """Campos reales de ``TokenDossier`` (los alias viejos quedan prohibidos)."""
        for good in ("d.chain ||", "d.volume_m5_usd", "d.composite_dd_score",
                     "d.dev_balance_pct", "d.same_name_count", "d.veto_reasons"):
            self.assertIn(good, self.src, f"campo real del dossier sin usar: {good}")
        for wrong in ("d.chain_id", "d.price_usd", "d.volume_5m_usd",
                      "d.price_change_5m", "d.composite_score", "d.dev_hold_pct",
                      "d.same_name_count_24h", "d.risk_reasons"):
            self.assertNotIn(wrong, self.src,
                             f"campo inexistente del dossier reintroducido: {wrong}")

    def test_same_name_count_field_is_canonical(self):
        self.assertNotIn("same_name_count_24h", self.src,
                         "campo inexistente same_name_count_24h reintroducido")

    def test_sse_payload_fields_match_emitters(self):
        """``trade_open`` emite ``entry_mc_usd``; ``evo`` emite ``old``/``new``."""
        self.assertIn("dd.entry_mc_usd", self.src)
        self.assertIn("dd.old", self.src)
        self.assertIn("dd.new", self.src)
        for dead in ("dd.entry_mc,", "dd.old_val", "dd.new_val"):
            self.assertNotIn(dead, self.src,
                             f"campo SSE fuera del contrato del server: {dead!r}")

    def test_external_text_is_html_escaped(self):
        self.assertIn("function escapeHtml(", self.src)
        for sink in ("escapeHtml(t.symbol", "escapeHtml(p.symbol",
                     "escapeHtml(c.symbol", "escapeHtml(r.symbol",
                     "escapeHtml(e.symbol"):
            self.assertIn(sink, self.src, f"escape faltante en: {sink}")

    def test_ui_stability_guards(self):
        """Chip con id canónico, EventSource recuperable, SL/TP sin crash."""
        self.assertIn('id="chip-cat-all"', self.src)
        self.assertIn("EventSource.CLOSED", self.src)
        self.assertIn("nd(t.sl_target,", self.src)

    def test_static_optimistic_badges_are_gone(self):
        """Badges estáticos no deben afirmar conexión antes del primer fetch."""
        for dead in (">GEMINI CONECTADO<", ">PLAYWRIGHT LISTO<"):
            self.assertNotIn(dead, self.src, f"badge estático optimista: {dead!r}")

    def test_ml_metrics_are_gated_on_nonzero(self):
        """``0.0`` del modelo significa "sin dato", no métrica medida."""
        self.assertIn("qm.test_roc_auc > 0", self.src)
        self.assertIn("qm.train_samples > 0", self.src)
        self.assertNotIn('qm.status === "trained"', self.src)

    def test_numeric_fallback_literals_regex_scan(self):
        """Variantes con espaciado arbitrario de los fallbacks fabricados."""
        for bad in (r"\|\|\s*0\.91", r"\|\|\s*29598", r"\|\|\s*0\.9166",
                    r"\|\|\s*8500", r"\|\|\s*28\b"):
            self.assertIsNone(re.search(bad, self.src),
                              f"fallback numérico fabricado reintroducido: {bad!r}")


if __name__ == "__main__":
    unittest.main()
