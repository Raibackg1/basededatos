"""Guarda anti-fuga de etiquetas (label causality guard).

Motivación (caso real, 2026-10-15)
----------------------------------
Un informe externo reportó "96.8% de winrate / Profit Factor 242" para la Rama A
de `MasterSnipeSignal`. Al auditarlo, la etiqueta de "ganancia" se derivaba de
`Token.rise50At` / `rise100At`: columnas que registran si el token alcanzó un
precio **en cualquier momento de su ciclo de vida**, es decir, DESPUÉS de la
entrada. El filtro de selección usaba además `marketCap` FINAL (>= 4500).

Medición honesta, en solo lectura sobre `custom.db`:

    [A] Filtro CON condición futura (MC final >= 4500):      506 tokens | +50% = 83.60% | WR real = 85.71% (n=21)
    [B] Filtro SIN condición futura (solo firstMC/firstLiq): 18,884 tokens | +50% = 15.03% | WR real = 7.65% (n=510)
    [C] Universo total:                                      65,423 tokens | +50% = 10.41% | WR real = 12.55%

El "edge" del 83% colapsa a 15% al quitar la condición futura: era **sesgo de
selección**, no señal.

Regla
-----
- Un **label** SÍ puede derivar del resultado del propio trade (`exit_price`,
  `pnl`), porque eso es lo que un label describe.
- Un **label** NUNCA puede derivar de un desenlace del ciclo de vida del token
  (`rise50At`, `peakPriceUsd`, `deadAt`, `graduatedAt`, `status`): eso es lookahead.

Uso
---
    from detector.label_guard import assert_causal_label

    assert_causal_label("hit_50", derived_from=["rise50At"])        # raises
    assert_causal_label("win_tp_sl", derived_from=["exit_price"])   # ok
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

__all__ = [
    "FutureLeakageError",
    "LIFECYCLE_FUTURE_PATTERNS",
    "DERIVED_OUTCOME_PATTERNS",
    "is_lifecycle_future",
    "is_derived_outcome",
    "validate_causal_label",
    "assert_causal_label",
    "assert_no_future_features",
]

# 1) Desenlaces del CICLO DE VIDA del token: nunca pueden alimentar un label ni
#    una feature de entrada (son posteriores al trade).
LIFECYCLE_FUTURE_PATTERNS: tuple[str, ...] = (
    r"^rise\d*",        # rise25At / rise50At / rise100At
    r"^peak",           # peakPriceUsd / peakAt
    r"^ath",            # ath / athMcap
    r"^pumpath",        # pumpAthMcap / pumpAthAt
    r"^dead",           # deadAt
    r"^graduat",        # graduatedAt / graduated
    r"^complete$",      # complete (migró a Raydium)
    r"^status$",        # ACTIVE / DEAD / WINNER / GRADUATED
    r"^reason$",        # motivo de salida
    r"^failreason$",
    r"^drawup",
    r"^minutesintrade$",
)

# 2) Columnas DERIVADAS del resultado del trade: válidas como label, prohibidas
#    como feature (se conocen después de entrar).
DERIVED_OUTCOME_PATTERNS: tuple[str, ...] = (
    r"^pnl",            # pnlPct / pnlUsd / pnl_sol
    r"^exit",           # exitPrice / exitTs / exit_time
    r"^mfe",            # mfe_pct
    r"^mae",            # mae_pct
    r"^win",            # win / win_real / win_tp_sl
    r"^fut",            # fut_ret_*
    r"^future",         # future_ret_*
    r"^tpsl",           # tpsl_timeout
    r"^hold_s$",
)

_LIFECYCLE = tuple(re.compile(p) for p in LIFECYCLE_FUTURE_PATTERNS)
_DERIVED = tuple(re.compile(p) for p in DERIVED_OUTCOME_PATTERNS)


class FutureLeakageError(ValueError):
    """La etiqueta o alguna de sus fuentes proviene de un desenlace futuro."""


def _norm(name: str) -> str:
    return str(name).strip().lower() if name else ""


def is_lifecycle_future(name: str) -> bool:
    """True si la columna describe el desenlace del ciclo de vida del token."""
    n = _norm(name)
    return bool(n) and any(rx.match(n) for rx in _LIFECYCLE)


def is_derived_outcome(name: str) -> bool:
    """True si la columna se deriva del resultado del trade (label válido)."""
    n = _norm(name)
    return bool(n) and any(rx.match(n) for rx in _DERIVED)


def validate_causal_label(label: str, derived_from: Sequence[str] = ()) -> dict:
    """Valida que una etiqueta sea causal. No lanza; devuelve diagnóstico.

    Returns
    -------
    dict con ``ok`` (bool), ``reason`` (str | None) y ``offending`` (list[str]).
    """
    offending: list[str] = []
    if is_lifecycle_future(label):
        offending.append(label)
    offending.extend(c for c in derived_from if is_lifecycle_future(c))

    if offending:
        return {
            "ok": False,
            "reason": (
                "LABEL_USES_FUTURE_OUTCOME: la etiqueta '%s' (o sus fuentes %s) se "
                "deriva de un desenlace posterior a la entrada. Prohibido: produce "
                "winrate/PF ilusorios por lookahead." % (label, sorted(set(offending)))
            ),
            "offending": sorted(set(offending)),
        }
    return {"ok": True, "reason": None, "offending": []}


def assert_causal_label(label: str, derived_from: Iterable[str] = ()) -> None:
    """Lanza ``FutureLeakageError`` si la etiqueta no es causal.

    Úsalo en cualquier pipeline de análisis/backtest ANTES de calcular métricas
    de rendimiento. Es la comprobación que habría detenido el informe del
    "96.8%" en su primera línea.
    """
    verdict = validate_causal_label(label, list(derived_from))
    if not verdict["ok"]:
        raise FutureLeakageError(verdict["reason"])


def assert_no_future_features(columns: Iterable[str]) -> None:
    """Lanza si alguna columna de ENTRADA es un desenlace futuro o derivado.

    Complementa a ``data_integrity.FeatureAvailabilityGuard`` cubriendo también
    las columnas del ciclo de vida (`rise*`, `peak*`, `dead*`, `graduated*`).
    """
    bad = sorted({c for c in columns if is_lifecycle_future(c) or is_derived_outcome(c)})
    if bad:
        raise FutureLeakageError(
            "FEATURES_USE_FUTURE_COLUMNS: columnas de desenlace usadas como "
            "features de entrada: %s" % bad
        )
