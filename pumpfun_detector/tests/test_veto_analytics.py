"""Contratos de Fase 1 — "Lente de aprendizaje" honesta sobre shadow_veto_ledger.

Reglas duras que este archivo congela:

1. La etiqueta almacenada (`verdict`) **no es verdad**: se recalcula desde los
   campos crudos (CF PnL neto y drawdown real) con la regla ``cf_net_v1``.
2. Con solo mínimo/máximo no se puede reconstruir el orden de los ticks. Un
   token que tocó el SL real (-33.49%) y terminó arriba es ``AMBIGUOUS_PATH``:
   ni "salvada" ni "falso veto".
3. ``N < 30`` resueltos => ``UNVERIFIED`` (BP-003). Cero afirmaciones de edge.
4. Sin filas resueltas => ``save_accuracy_pct is None``, nunca 100.0.
5. El endpoint y el panel existen y leen la puerta de evidencia; no se
   hardcodea ningún número de progreso.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from detector.auto_evolution import (
    AutoEvolutionEngine,
    FALSE_VETO_THRESHOLD_PCT,
    LABEL_RULE_VERSION,
    MIN_EVIDENCE_N,
    SL_PANIC_PCT,
    canonical_branch,
    honest_outcome,
    summarize_veto_patterns,
)

_PKG = Path(__file__).resolve().parent.parent


def _row(**kw):
    base = {
        "mint": "Mint111",
        "symbol": "TEST",
        "veto_reason": "VETO_SAME_NAME_CLONE: clon homónimo",
        "verdict": "PENDING",
        "counterfactual_pnl_pct": None,
        "entry_mc_usd": 100.0,
        "trough_mc_usd": 100.0,
        "peak_mc_usd": 100.0,
        "checks_count": 1,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------- ramas


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("VETO_SAME_NAME_CLONE: clon homónimo / fábrica vanidosa", "SAME_NAME_CLONE"),
        ("VETO_UNVERIFIED_METRICS: concentración/dev sin fuente", "UNVERIFIED_METRICS"),
        ("VETO_BUNDLER_CLUSTER: Bundler empaquetó 22.4% del float", "BUNDLER_CLUSTER"),
        ("VETO_WASH_TRADING_STAIRCASE: micro-compras < $5", "WASH_TRADING"),
        ("CONCENTRACIÓN DE CABAL (59.5% > 30% MAX)", "TOP10_CONCENTRATION"),
        ("DEV HOLDING EXCESIVO (3.4% > 1.5% MAX)", "DEV_HOLDING"),
        ("LIQUIDEZ ANÉMICA (RATIO LIQ/MC 0.0% < 15%)", "LIQUIDITY_ANEMIC"),
        ("VETO_DEV_HOLDING: dev retiene 3.4% (> 1.5%)", "DEV_HOLDING"),
        ("", "UNCLASSIFIED"),
        (None, "UNCLASSIFIED"),
        ("VETO_MYSTERY_BRANCH: algo nuevo", "UNCLASSIFIED"),
        ("texto libre sin rama reconocible", "UNCLASSIFIED"),
    ],
)
def test_canonical_branch_reconocida_o_unclassified(reason, expected):
    assert canonical_branch(reason) == expected


def test_canonical_branch_ignora_acentos_y_mayusculas():
    assert canonical_branch("concentracion de cabal (40% > 30% max)") == "TOP10_CONCENTRATION"
    assert canonical_branch("liquidez anemica") == "LIQUIDITY_ANEMIC"


def test_canonical_branch_no_inventa_ramas_con_el_prefijo():
    # Un VETO_* desconocido no se convierte en una rama nueva: se declara sin clasificar.
    assert canonical_branch("VETO_ALGO_NUEVO: motivo") == "UNCLASSIFIED"


# ----------------------------------------------------------------- etiquetas


def test_honest_outcome_pending_sin_contrafactual():
    out = honest_outcome(_row(verdict="PENDING", counterfactual_pnl_pct=None))
    assert out["state"] == "PENDING"
    assert out["cf_pnl_pct"] is None
    assert out["label_rule_version"] == LABEL_RULE_VERSION


def test_honest_outcome_saved_con_perdida_neta():
    out = honest_outcome(_row(verdict="TRUE_SAVE", counterfactual_pnl_pct=-40.0,
                              entry_mc_usd=100.0, trough_mc_usd=95.0))
    assert out["state"] == "SAVED"
    assert out["trough_drawdown_pct"] == pytest.approx(-5.0)


def test_honest_outcome_falso_veto_claro_sin_tocar_sl():
    out = honest_outcome(_row(verdict="FALSE_VETO", counterfactual_pnl_pct=112.05,
                              entry_mc_usd=100.0, trough_mc_usd=73.0))
    assert out["state"] == "CLEAR_FALSE_VETO"
    assert out["trough_drawdown_pct"] == pytest.approx(-27.0)


def test_honest_outcome_ambiguo_si_el_trough_rompe_el_sl_real():
    # Caso real: STARMAN +217.73% de CF PnL pero tocó -75.95% en algún momento.
    # Sin orden de ticks no se puede afirmar que el veto "salvó" ni que costó.
    out = honest_outcome(_row(verdict="TRUE_SAVE", counterfactual_pnl_pct=217.73,
                              entry_mc_usd=100.0, trough_mc_usd=24.05))
    assert out["state"] == "AMBIGUOUS_PATH"
    assert out["state"] not in ("SAVED", "CLEAR_FALSE_VETO")
    assert out["stored_label_ok"] is False


def test_honest_outcome_costoso_sin_llegar_al_sl():
    out = honest_outcome(_row(verdict="NEUTRAL_HOLD", counterfactual_pnl_pct=7.09,
                              entry_mc_usd=100.0, trough_mc_usd=80.0))
    assert out["state"] == "COSTLY"


def test_honest_outcome_ignora_la_etiqueta_almacenada():
    # Guardar TRUE_SAVE no obliga a que la regla honesta diga SAVED.
    same = dict(counterfactual_pnl_pct=217.73, entry_mc_usd=100.0, trough_mc_usd=24.05)
    a = honest_outcome(_row(verdict="TRUE_SAVE", **same))
    b = honest_outcome(_row(verdict="FALSE_VETO", **same))
    assert a["state"] == b["state"] == "AMBIGUOUS_PATH"


def test_honest_outcome_no_lee_el_cero_por_defecto_como_medicion():
    # upsert_shadow_veto inserta cf=0.0 y checks_count=0: ausencia de dato.
    out = honest_outcome(_row(verdict="PENDING", counterfactual_pnl_pct=0.0,
                              entry_mc_usd=100.0, trough_mc_usd=100.0, checks_count=0))
    assert out["state"] == "PENDING"


def test_honest_outcome_pending_si_nunca_se_chequeo_aunque_el_veredicto_mienta():
    out = honest_outcome(_row(verdict="TRUE_SAVE", counterfactual_pnl_pct=0.0,
                              entry_mc_usd=100.0, trough_mc_usd=100.0, checks_count=0))
    assert out["state"] == "PENDING"
    assert out["stored_label_ok"] is None


def test_umbrales_declarados():
    assert SL_PANIC_PCT == pytest.approx(-33.49)
    assert FALSE_VETO_THRESHOLD_PCT == pytest.approx(28.0)
    assert MIN_EVIDENCE_N == 30
    assert LABEL_RULE_VERSION == "cf_net_v1"


# ---------------------------------------------------------------- resumen


def _resolved(symbol, reason, cf, trough=95.0, verdict="TRUE_SAVE"):
    return _row(symbol=symbol, veto_reason=reason, verdict=verdict,
                counterfactual_pnl_pct=cf, entry_mc_usd=100.0, trough_mc_usd=trough)


def test_summarize_ledger_vacio_no_finge_precision():
    out = summarize_veto_patterns([])
    assert out["totals"]["ledger"] == 0
    assert out["totals"]["resolved"] == 0
    assert out["totals"]["save_accuracy_pct"] is None
    assert out["branches"] == []
    assert out["edge_demostrado"] is False


def test_summarize_puerta_n30_por_rama():
    rows = []
    for i in range(35):
        rows.append(_resolved(f"S{i}", "VETO_SAME_NAME_CLONE: clon", -40.0))
    for i in range(17):
        rows.append(_resolved(f"B{i}", "VETO_BUNDLER_CLUSTER: bundler 25%", 40.0,
                              trough=80.0, verdict="FALSE_VETO"))
    rows.append(_row(symbol="P1", verdict="PENDING"))

    out = summarize_veto_patterns(rows)
    by = {b["branch"]: b for b in out["branches"]}

    assert by["SAME_NAME_CLONE"]["n_resolved"] == 35
    assert by["SAME_NAME_CLONE"]["gate"] == "VALID"
    assert by["SAME_NAME_CLONE"]["claim"] is None

    assert by["BUNDLER_CLUSTER"]["n_resolved"] == 17
    assert by["BUNDLER_CLUSTER"]["gate"] == "UNVERIFIED"
    assert by["BUNDLER_CLUSTER"]["claim"] is None

    assert out["min_n"] == MIN_EVIDENCE_N
    assert out["totals"]["pending"] == 1
    assert out["edge_demostrado"] is False


def test_summarize_mediana_y_tasas_solo_sobre_resueltos():
    rows = [
        _resolved("A", "VETO_SAME_NAME_CLONE: clon", -10.0),
        _resolved("B", "VETO_SAME_NAME_CLONE: clon", -20.0),
        _resolved("C", "VETO_SAME_NAME_CLONE: clon", 40.0, trough=90.0, verdict="FALSE_VETO"),
        _resolved("D", "VETO_SAME_NAME_CLONE: clon", -100.0),
        _row(symbol="E", verdict="PENDING"),
    ]
    out = summarize_veto_patterns(rows)
    b = out["branches"][0]
    assert b["n_total"] == 5
    assert b["n_resolved"] == 4
    assert b["n_pending"] == 1
    assert b["saved"] == 3
    assert b["clear_false_veto"] == 1
    # Valores: -100, -20, -10, +40 => mediana (-20 + -10) / 2 = -15
    assert b["median_cf_pnl_pct"] == pytest.approx(-15.0)
    assert b["saved_rate_pct"] == pytest.approx(75.0)
    assert out["totals"]["save_accuracy_pct"] == pytest.approx(75.0)


def test_summarize_mide_discrepancia_de_la_etiqueta_almacenada():
    rows = [
        _resolved("STARMAN", "VETO_SAME_NAME_CLONE: clon", 217.73,
                  trough=24.05, verdict="TRUE_SAVE"),
        _resolved("OK", "VETO_SAME_NAME_CLONE: clon", -50.0, verdict="TRUE_SAVE"),
    ]
    out = summarize_veto_patterns(rows)
    disc = out["label_discrepancy"]
    assert disc["checked"] == 2
    assert disc["stored_mismatch"] == 1
    assert disc["true_save_with_positive_cf"] == 1


def test_summarize_no_cuenta_pendientes_como_evidencia():
    out = summarize_veto_patterns([_row(symbol="X", verdict="PENDING")])
    assert out["totals"]["resolved"] == 0
    assert out["totals"]["pending"] == 1
    assert out["totals"]["save_accuracy_pct"] is None


def test_summarize_declara_los_ceros_por_defecto_del_escritor():
    rows = [
        _row(symbol="Z", verdict="PENDING", counterfactual_pnl_pct=0.0, checks_count=0),
        _row(symbol="M", verdict="PENDING", counterfactual_pnl_pct=None),
    ]
    out = summarize_veto_patterns(rows)
    assert out["totals"]["pending"] == 2
    assert out["totals"]["pending_zero_default"] == 1
    assert out["totals"]["resolved"] == 0
    assert out["totals"]["save_accuracy_pct"] is None


# ------------------------------------------------------- summary del motor


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def list_shadow_vetos(self, limit: int = 30):
        return list(self._rows)[: int(limit)]

    def list_evolution_events(self, limit: int = 30):
        return []


def _engine(rows, tmp_path):
    return AutoEvolutionEngine(store=_FakeStore(rows), runtime_dir=tmp_path)


def test_status_summary_sin_resueltos_no_reporta_100(tmp_path):
    eng = _engine([_row(symbol="X", verdict="PENDING")], tmp_path)
    s = eng.get_status_summary()["shadow_summary"]
    assert s["resolved"] == 0
    assert s["save_accuracy_pct"] is None
    assert s["true_saves"] == 0
    assert s["false_vetos"] == 0


def test_status_summary_usa_regla_honesta_y_expone_discrepancia(tmp_path):
    rows = [
        _resolved("AMB", "VETO_SAME_NAME_CLONE: clon", 217.73, trough=24.05,
                  verdict="TRUE_SAVE"),
        _resolved("SAVED", "VETO_SAME_NAME_CLONE: clon", -50.0, verdict="TRUE_SAVE"),
        _resolved("FALSE", "VETO_SAME_NAME_CLONE: clon", 40.0, trough=90.0,
                  verdict="FALSE_VETO"),
    ]
    s = _engine(rows, tmp_path).get_status_summary()["shadow_summary"]
    assert s["true_saves"] == 1
    assert s["false_vetos"] == 1
    assert s["ambiguous_path"] == 1
    assert s["resolved"] == 3
    assert s["save_accuracy_pct"] == pytest.approx(33.3, abs=0.1)
    assert s["label_rule_version"] == LABEL_RULE_VERSION
    assert s["stored_true_saves"] == 2
    assert s["stored_false_vetos"] == 1
    assert s["label_discrepancy"]["stored_mismatch"] == 1


def test_status_summary_conserva_claves_del_contrato(tmp_path):
    eng = _engine([], tmp_path)
    summary = eng.get_status_summary()
    assert "shadow_vetos" in summary
    for key in ("true_saves", "false_vetos", "avoided_loss_sum_pct",
                "save_accuracy_pct", "resolved", "pending"):
        assert key in summary["shadow_summary"]


# ------------------------------------------- contrato endpoint + panel web


def test_server_expone_endpoint_de_aprendizaje():
    src = (_PKG / "cockpit" / "server.py").read_text(encoding="utf-8")
    assert "/api/learning/veto-patterns" in src
    assert "summarize_veto_patterns" in src
    assert "gate_progress" in src
    assert "edge_demostrado" in src


def test_panel_web_lee_el_endpoint_y_no_hardcodea_el_gate():
    html = (_PKG / "cockpit" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="lv-branches"' in html
    assert "fetchVetoPatterns" in html
    assert "/api/learning/veto-patterns" in html
    assert "gate_progress" in html
    assert "edge_demostrado" in html
    # El progreso del gate sale del payload, no de un literal de la UI.
    assert "closed_forward" in html
