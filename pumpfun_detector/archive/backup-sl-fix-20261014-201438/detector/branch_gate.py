"""Auto-desactivacion de ramas por EV neto < breakeven (regla P2 exigida).

Por que existe
--------------
`MasterSnipeSignal.DISABLED_BRANCHES` es una lista ESTATICA (BP-003). Aqui la
decision de desactivar una rama sale de los resultados NETOS REALES persistidos
en `LiveStore` (pnl_sol tras fees + slippage), con dos condiciones:

1. Regla economica (principal): n >= min_samples y expectancy_r (EV neto por
   trade en SOL) < min_ev_sol (breakeven = 0.0 por defecto).
2. Regla bayesiana (secundaria): posterior Beta-Binomial con
   P(winrate > breakeven) < 0.15 -> la rama ya no tiene evidencia de edge.

Todo el estado se persiste en la tabla `branch_gate_state` de la misma BD, de
modo que un reinicio del proceso NO reactiva una rama quebrada. La
rehabilitacion es automatica: si al refrescar el EV vuelve a ser >= breakeven
con n suficiente, la rama pasa a ENABLED.

Uso típico (loop vivo o replay):
    gate = BranchGate(store)
    decision = veto_if_disabled(MasterSnipeSignal.evaluate(feats), gate)
"""
from __future__ import annotations

import copy
import dataclasses
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .survival_model import BayesianBranchTracker


@dataclass(frozen=True)
class BranchGateState:
    """Estado de gobernanza de una rama (valor de auditar/monitorear)."""
    branch: str
    status: str          # ENABLED | DISABLED | INSUFFICIENT_DATA
    n: int
    ev_sol: float
    winrate: float
    p_profitable: Optional[float]
    reason: str
    decided_at: float

    @property
    def enabled(self) -> bool:
        return self.status != "DISABLED"


class BranchGate:
    """Gate persistente de ramas alimentado por el audit neto de LiveStore."""

    def __init__(
        self,
        store: Any,
        min_samples: int = 10,
        min_ev_sol: float = 0.0,
        use_bayes: bool = True,
        bayes_breakeven_wr: float = 0.40,
        bayes_min_confidence: float = 0.15,
        origin: str = "live",
        alert_manager: Any = None,
    ):
        self.store = store
        self.min_samples = int(min_samples)
        self.min_ev_sol = float(min_ev_sol)
        self.use_bayes = bool(use_bayes)
        self.bayes_breakeven_wr = float(bayes_breakeven_wr)
        self.bayes_min_confidence = float(bayes_min_confidence)
        self.origin = origin
        self._alerts = alert_manager

    def _emit_alert(self, severity: Any, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self._alerts is None:
            return
        try:
            self._alerts.emit("branch_gate", severity, message, payload or {})
        except Exception:
            pass

    def refresh(self, now: Optional[float] = None) -> Dict[str, BranchGateState]:
        """Recalcula el estado de cada rama desde outcomes netos y lo persiste."""
        now = float(now if now is not None else time.time())
        stats = self.store.net_audit_by_branch(origin=self.origin, admitted_only=True)
        states: Dict[str, BranchGateState] = {}
        for s in stats:
            branch = str(s["branch"])
            n = int(s["n"])
            ev = float(s["expectancy_r"])
            wr = float(s["winrate"])
            p_profit: Optional[float] = None

            if n < self.min_samples:
                status = "INSUFFICIENT_DATA"
                reason = f"n={n} < min_samples={self.min_samples}"
            else:
                reasons = []
                status = "ENABLED"
                if ev < self.min_ev_sol:
                    status = "DISABLED"
                    reasons.append(
                        f"EV neto {ev:.6f} SOL < breakeven {self.min_ev_sol:.6f}")
                p_profit = self._posterior_prob(branch=branch, wins=int(round(wr * n)),
                                                losses=n - int(round(wr * n)))
                if self.use_bayes and p_profit is not None \
                        and p_profit < self.bayes_min_confidence:
                    status = "DISABLED"
                    reasons.append(
                        f"P(wr>{self.bayes_breakeven_wr:.2f})={p_profit:.3f} "
                        f"< {self.bayes_min_confidence:.2f}")
                if status == "ENABLED":
                    reasons.append("EV neto >= breakeven")
                reason = "; ".join(reasons)

            st = BranchGateState(
                branch=branch, status=status, n=n, ev_sol=ev, winrate=wr,
                p_profitable=p_profit, reason=reason, decided_at=now)
            states[branch] = st
            self._emit_transition_alert(st)
            self._persist(st)
        return states

    def _emit_transition_alert(self, st: BranchGateState) -> None:
        """Emite alerta si la rama transiciona a DISABLED respecto al estado persistido."""
        if self._alerts is None:
            return
        prev_status = None
        if hasattr(self.store, "get_branch_state"):
            row = self.store.get_branch_state(st.branch)
            if row is not None:
                prev_status = str(row.get("status"))
        if st.status == "DISABLED" and prev_status != "DISABLED":
            from .alerts import AlertSeverity
            self._emit_alert(
                AlertSeverity.CRITICAL,
                f"Rama {st.branch} DESACTIVADA: {st.reason}",
                {"branch": st.branch, "status": st.status, "n": st.n,
                 "ev_sol": st.ev_sol, "winrate": st.winrate,
                 "prev_status": prev_status})

    def _posterior_prob(self, branch: str, wins: int, losses: int) -> Optional[float]:
        """P(wr > breakeven | datos) reconstruido de forma idempotente."""
        if not self.use_bayes:
            return None
        tracker = BayesianBranchTracker(
            breakeven_winrate=self.bayes_breakeven_wr,
            min_samples=self.min_samples,
        )
        for _ in range(max(0, wins)):
            tracker.record_outcome(branch, True)
        for _ in range(max(0, losses)):
            tracker.record_outcome(branch, False)
        return tracker.get_posterior_prob_above_breakeven(branch)

    def _persist(self, st: BranchGateState) -> None:
        if not hasattr(self.store, "save_branch_state"):
            return
        self.store.save_branch_state({
            "branch": st.branch, "status": st.status, "n": st.n,
            "ev_sol": st.ev_sol, "winrate": st.winrate,
            "p_profitable": st.p_profitable, "reason": st.reason,
            "decided_at": st.decided_at,
        })

    def is_enabled(self, branch: str) -> bool:
        """True si la rama no esta vetada (estado persistido, no memoria)."""
        if not hasattr(self.store, "get_branch_state"):
            return True
        row = self.store.get_branch_state(branch)
        if row is None:
            return True  # sin evidencia -> no se veto
        return str(row.get("status")) != "DISABLED"

    def disabled_branches(self) -> list:
        if not hasattr(self.store, "list_branch_states"):
            return []
        return [r["branch"] for r in self.store.list_branch_states()
                if str(r.get("status")) == "DISABLED"]


def veto_if_disabled(decision: Any, gate: Optional[BranchGate]) -> Any:
    """Aplica el veto del gate a una decision admitida.

    - Sin gate, sin rama admitida o rama habilitada -> devuelve la decision
      original sin tocar (misma referencia, cero overhead).
    - Rama desactivada -> decision rechazada con veto causal VETO_BRANCH_GATE.
    """
    if gate is None or decision is None:
        return decision
    if not getattr(decision, "admitted", False):
        return decision
    branch = str(getattr(decision, "branch", ""))
    if gate.is_enabled(branch):
        return decision

    reason = f"VETO_BRANCH_GATE: rama {branch} desactivada por EV neto < breakeven"
    if dataclasses.is_dataclass(decision) and not isinstance(decision, type):
        return dataclasses.replace(
            decision, admitted=False, confidence=0.0, veto_reason=reason)
    # Stub / objetos mutables (tests, adapters): copia defensiva + veto
    out = copy.copy(decision)
    out.admitted = False
    out.confidence = 0.0
    out.veto_reason = reason
    return out
