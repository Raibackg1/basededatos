"""
MomentumStallExit — Salida temprana de posiciones SIN momentum (anti-TIMEOUT).

Motivación (auditoría de la base canónica, 5,021 posiciones paper):
    - El TIMEOUT es la 2a causa de pérdida: -21,361 USD en 3,187 trades.
    - La MEDIANA del drawup (max subida on-chain durante el trade) es 0.00%:
      la mayoría de trades NUNCA supera la entrada. Son capital muerto.
    - Solo 459/5,021 trades llegan a +20%. El dinero está en pocos trades con
      momentum; el resto es sangrado lento hasta el timeout fijo (300s).

Qué hace:
    Decide, a partir de la trayectoria observada (precio pico respecto a la
    entrada y tiempo en posición), si una posición está "estancada sin momentum"
    y debe cerrarse YA, sin esperar al timeout largo. No sustituye al TP/SL:
    es una guarda ADICIONAL y opt-in.

Diseño honesto (sin lookahead):
    - Usa SOLO maxima.subida ya observada (peak_price) y tiempo transcurrido.
    - NO mira el futuro ni el precio de salida; no infiere el outcome.
    - Es conservador: exige haber vivido la ventana completa y cierto número de
      ticks antes de declarar estancamiento, para no matar trades que arrancan
      lento pero luego disparan.

Integración:
    PaperBroker acepta opcionalmente `momentum_exit`. Por defecto None (off),
    para no alterar el comportamiento de producción existente. Si se activa,
    se evalúa ANTES de las reglas TP/SL/timeout en `on_price`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Tipo de salida etiquetado al cerrar por estancamiento de momentum.
EXIT_MOMENTUM_STALL = "momentum_stall"


@dataclass(frozen=True)
class MomentumDecision:
    """Veredicto de la guarda de momentum para una posición."""
    exit_now: bool
    reason: str
    drawup_pct: float
    held_s: float
    ticks_seen: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "exit_now": self.exit_now,
            "reason": self.reason,
            "drawup_pct": self.drawup_pct,
            "held_s": self.held_s,
            "ticks_seen": self.ticks_seen,
        }


class MomentumStallExit:
    """
    Guarda fail-safe: cierra una posición si, tras una ventana completa, su
    máxima subida observada no alcanza un mínimo de momentum.

    Umbrales por defecto (conservadores):
        - stall_window_s = 90.0   (ventana mínima antes de poder decretar estancamiento)
        - min_drawup_pct = 1.0    (subida mínima desde la entrada para considerar "vivo")
        - min_ticks      = 3      (ruido mínimo de precios antes de juzgar)

    Semántica: exit_now = True SOLO si
        held_s >= stall_window_s  AND  ticks_seen >= min_ticks  AND  drawup < min_drawup.

    Uso:
        gate = MomentumStallExit()
        d = gate.evaluate(entry_price=1.0, peak_price=1.003, held_s=95.0, ticks_seen=8)
        if d.exit_now:
            ...  # cerrar como EXIT_MOMENTUM_STALL
    """

    def __init__(
        self,
        stall_window_s: float = 90.0,
        min_drawup_pct: float = 1.0,
        min_ticks: int = 3,
    ) -> None:
        # Fail-safe: umbrales negativos convertirían la guarda en un cierre
        # masivo inmediato. Se acotan a valores inocuos en lugar de confiar
        # en que el llamador configura bien.
        self.stall_window_s = max(0.0, float(stall_window_s))
        self.min_drawup_pct = max(0.0, float(min_drawup_pct))
        self.min_ticks = max(1, int(min_ticks))

    def evaluate(
        self,
        entry_price: float,
        peak_price: float,
        held_s: float,
        ticks_seen: int = 0,
    ) -> MomentumDecision:
        """
        Evalúa si una posición está estancada sin momentum.

        Fail-safe ante datos degenerados: si entry_price no es positivo o los
        valores no son finitos, NO ordena salida (exit_now=False) para no
        destruir capital por un dato corrupto.
        """
        ep = _safe_float(entry_price)
        pk = _safe_float(peak_price)
        hs = _safe_float(held_s)
        if ep is None or ep <= 0.0 or hs is None or hs < 0.0 or pk is None:
            return MomentumDecision(
                exit_now=False, reason="INVALID_INPUT", drawup_pct=0.0,
                held_s=hs or 0.0, ticks_seen=int(ticks_seen or 0),
            )

        drawup_pct = (pk / ep - 1.0) * 100.0
        ticks = int(ticks_seen or 0)

        if hs < self.stall_window_s:
            return MomentumDecision(
                exit_now=False, reason="WINDOW_NOT_ELAPSED", drawup_pct=drawup_pct,
                held_s=hs, ticks_seen=ticks,
            )
        if ticks < self.min_ticks:
            return MomentumDecision(
                exit_now=False, reason="INSUFFICIENT_TICKS", drawup_pct=drawup_pct,
                held_s=hs, ticks_seen=ticks,
            )
        if drawup_pct >= self.min_drawup_pct:
            return MomentumDecision(
                exit_now=False, reason="HAS_MOMENTUM", drawup_pct=drawup_pct,
                held_s=hs, ticks_seen=ticks,
            )
        return MomentumDecision(
            exit_now=True, reason="MOMENTUM_STALL", drawup_pct=drawup_pct,
            held_s=hs, ticks_seen=ticks,
        )

    def config(self) -> Dict[str, Any]:
        return {
            "stall_window_s": self.stall_window_s,
            "min_drawup_pct": self.min_drawup_pct,
            "min_ticks": self.min_ticks,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f
