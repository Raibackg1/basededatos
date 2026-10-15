"""
Runner end-to-end del loop de "ganancia real medible" (P1).

Orquesta, en un único paso reproducible y sin red:

    eventos PumpPortal (replay)
        -> PumpPortalAdapter  (evento -> features entrada-seguro)
        -> MasterSnipeSignal  (decisión con umbrales auditados)
        -> PaperBroker        (abre/cierra contra el cost model real)
        -> LiveStore          (persiste decisión + outcome)
        -> net_audit          (winrate + PnL neto, aislado por procedencia)

Por qué existe
--------------
Todas las piezas ya existían y estaban testeadas por separado, pero nadie las
conectaba. Este runner es el pegamento que convierte la honestidad de métricas
(P0) en una **rentabilidad medible y auditable**: mismo loop que correría en
vivo, pero reproducible de forma determinista sobre un replay de eventos.

Decisiones honestas y explícitas
--------------------------------
- **Proxy de precio = market cap observado.** La supply de un token pump.fun es
  fija (≈1.073e9), y market_cap = precio × supply, por lo que el % de cambio del
  market cap es idéntico al % de cambio del precio. Así `PaperBroker` trackea el
  PnL correcto sin necesitar el precio por token ni la supply en el evento.
- **Inyección de `mint` y `entry_price`.** `PumpPortalAdapter.features()` no
  emite esos campos (no son features de la señal); el broker sí los necesita
  para abrir y trackear la posición. Los inyecta el runner, no el adaptador.
- **Decisión única por token.** El sniper entra en el primer evento que evalúa;
  no re-decide en cada tick (evita reabr/doblar exposición sobre el mismo mint).
- **Cierre forzado por timeout al final.** Las posiciones que no tocaron TP/SL
  durante el replay se cierran como timeout en `último_ts + timeout_s + 1`, de
  modo que el audit refleje TODO el replay y no ignore colas abiertas.
- **Aislamiento por procedencia.** El feed marca `data_origin="live"`; el audit
  filtra por ese origen, de modo que un replay etiquetado como tal jamás
  contamine la lectura de outcomes reales de producción.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .alerts import AlertManager, AlertSeverity
from .branch_gate import BranchGate, veto_if_disabled
from .costs import CostModel
from .live_store import LiveStore
from .master_signal import MasterSnipeSignal
from .paper_broker import PaperBroker
from .pumpportal_feed import PumpPortalAdapter
from .regime import MarketRegime, RegimeClassifier


def run_replay(
    events: List[Dict[str, Any]],
    db_path: Optional[str] = None,
    cost_model: Optional[CostModel] = None,
    entry_sol: float = 1.0,
    sol_usd: float = 150.0,
    window_s: float = 60.0,
    take_profit_pct: float = 0.30,
    stop_loss_pct: float = -0.35,
    timeout_s: float = 300.0,
    max_open: int = 50,
    holders_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    origin: str = "live",
    enable_branch_gate: bool = True,
    slippage_model: Optional[Any] = None,
    alert_manager: Optional[AlertManager] = None,
    regime_classifier: Optional[RegimeClassifier] = None,
    drift_monitor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ejecuta el loop completo sobre un replay de eventos y devuelve el audit.

    Devuelve un dict con el ``net_audit`` del store (winrate, PnL neto, n),
    más contadores de control (eventos ingeridos, decisiones, mints vistos).
    Es determinista: mismo replay -> mismo resultado, sin red ni reloj real.
    """
    adapter = PumpPortalAdapter(
        sol_usd=sol_usd, window_s=window_s, holders_fetcher=holders_fetcher
    )
    # ":memory:" por defecto: el replay es autocontenido y no deja rastro en disco
    # salvo que el llamador pase un db_path para auditar después.
    store = LiveStore(db_path or ":memory:")
    broker = PaperBroker(
        store,
        cost_model or CostModel(),
        entry_sol=entry_sol,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        timeout_s=timeout_s,
        max_open=max_open,
        slippage_model=slippage_model,
    )
    # Auto-desactivación de ramas por EV neto < breakeven (persistente en store).
    gate = BranchGate(store, origin=origin,
                      alert_manager=alert_manager) if enable_branch_gate else None
    # Clasificador de régimen de mercado (opcional).
    regime = regime_classifier or RegimeClassifier()
    # Drift monitor de features (PSI/KS) — opcional; se adjunta si se pasa.
    if drift_monitor is not None and alert_manager is not None:
        # Asegura que el drift monitor emita alertas al hub.
        if getattr(drift_monitor, "_alerts", None) is None:
            drift_monitor._alerts = alert_manager
    last_gate_outcomes = -1
    last_regime_outcomes = -1
    last_regime = MarketRegime.UNKNOWN
    regime_recorded = 0  # cuántos outcomes ya alimentaron al clasificador

    decided: set = set()
    last_price: Dict[str, float] = {}
    last_ts: float = 0.0
    ingested = 0
    n_signals = 0
    n_opened = 0
    n_gate_vetoes = 0
    n_regime_suspensions = 0

    for ev in events:
        tr = adapter.ingest(ev)
        if tr is None:
            continue
        ingested += 1
        last_ts = max(last_ts, tr.ts)
        # Proxy de precio: market cap observado (lineal con el precio a supply fija).
        price = tr.market_cap_sol
        if price > 0:
            last_price[tr.mint] = price
        # Actualiza posiciones abiertas (puede cerrar por TP/SL/timeout).
        broker.on_price(tr.mint, price, tr.ts)
        # Refresca el gate SOLO cuando hay outcomes nuevos (evita recalcular).
        n_out_now = store.count_outcomes()
        if gate is not None and n_out_now != last_gate_outcomes:
            gate.refresh()
            last_gate_outcomes = n_out_now
        # Alimenta clasificador de régimen solo con outcomes NUEVOS.
        if n_out_now != last_regime_outcomes:
            rows = store._resolved_rows(origin=origin, admitted_only=True)
            new_rows = rows[regime_recorded:]
            for r in new_rows:
                pnl_r = float(r.get("pnl") or 0.0) / max(entry_sol, 1e-9)
                regime.record_outcome(pnl_r=pnl_r)
            regime_recorded = len(rows)
            last_regime_outcomes = n_out_now
            snap = regime.snapshot()
            if snap.regime != last_regime and snap.regime != MarketRegime.UNKNOWN:
                if alert_manager is not None:
                    alert_manager.emit(
                        "regime", AlertSeverity.INFO,
                        f"Regimen cambiado a {snap.regime.value}",
                        payload={"winrate": snap.winrate, "n": snap.n})
                last_regime = snap.regime
        # El sniper evalúa la señal en cada tick y entra en cuanto confirma
        # (no en el primer tick, donde aún no hay flujo). Una vez abierta la
        # posición de un mint, no se re-decide ese mint.
        if tr.mint in decided:
            continue
        feats = adapter.features(tr.mint)
        if not feats:
            continue
        # El broker necesita identificar el mint y el precio de entrada;
        # no son features de la señal, los inyecta el runner.
        feats["mint"] = tr.mint
        feats["entry_price"] = price
        # Alimenta el drift monitor de features (PSI/KS) si está adjunto.
        if drift_monitor is not None:
            drift_monitor.record_feature_values(feats)
        decision = MasterSnipeSignal.evaluate(feats)
        # Veto por rama desactivada (EV neto < breakeven con n suficiente).
        if gate is not None:
            vetted = veto_if_disabled(decision, gate)
            if vetted is not decision:
                n_gate_vetoes += 1
            decision = vetted
        # Suspensión de entradas en régimen CONTRACTING.
        if regime.should_suspend_entries():
            n_regime_suspensions += 1
            if alert_manager is not None:
                alert_manager.emit(
                    "regime", AlertSeverity.WARNING,
                    "Entradas suspendidas por régimen CONTRACTING",
                    payload={"winrate": regime.snapshot().winrate})
            continue
        n_signals += 1
        did = broker.on_signal(feats, decision, tr.ts)
        if did is not None:
            decided.add(tr.mint)
            n_opened += 1

    # Cierre forzado por timeout de las colas abiertas al final del replay.
    flush_ts = last_ts + timeout_s + 1.0
    for mint in list(broker._open.keys()):
        broker.on_price(mint, last_price.get(mint, 0.0), flush_ts)

    # Última pasada del gate para dejar el estado persistido al día.
    if gate is not None:
        gate.refresh()

    audit = broker.audit(origin=origin, admitted_only=True)
    audit["control"] = {
        "events_ingested": ingested,
        "decisions": n_signals,
        "mints_seen": len(last_price),
        "positions_opened": n_opened,
        "flush_ts": flush_ts,
        "branch_gate_vetoes": n_gate_vetoes,
        "branch_gate_disabled": gate.disabled_branches() if gate is not None else [],
        "regime": regime.regime.value,
        "regime_suspensions": n_regime_suspensions,
        "alerts_emitted": alert_manager.count if alert_manager is not None else 0,
        "drift_monitor_attached": drift_monitor is not None,
    }
    if drift_monitor is not None and hasattr(drift_monitor, "get_status"):
        audit["drift_monitor"] = drift_monitor.get_status()
    audit["branch_net_audit"] = store.net_audit_by_branch(
        origin=origin, admitted_only=True)
    return audit
