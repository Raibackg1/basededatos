#!/usr/bin/env python3
"""
Arranque EN VIVO del loop de "ganancia real medible" (P1).

Conecta al websocket de PumpPortal, convierte cada trade en features
entrada-seguro, decide con la Señal Maestra, ejecuta en paper contra el cost
model real y persiste decisión+outcome en un LiveStore. Al parar (Ctrl+C o
--max-events) cierra las colas abiertas y emite el ``net_audit`` final.

Uso
---
    python run_live.py --api-key TU_KEY --db live.db                 # todos los tokens
    python run_live.py --api-key TU_KEY --mints M1,M2 --max-events 5000
    python run_live.py --demo-feed                                  # sin red, prueba local

Requiere ``websockets`` (ya en el entorno). Sin API key, PumpPortal funciona
en modo público con rate-limit; pásala para producción.

Honestidad: solo cuenta PnL de trades con outcome real unido (nunca la etiqueta
del demo). ``origin="live"`` aísla esta ejecución de cualquier replay.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import Any, Dict, List, Optional

from detector.costs import CostModel, ConditionalSlippageModel
from detector.live_store import LiveStore
from detector.paper_broker import PaperBroker
from detector.pumpportal_feed import (
    PumpPortalAdapter,
    stream_trades,
)
from detector.master_signal import MasterSnipeSignal
from detector.alerts import AlertManager, AlertSeverity, FileAlertSink
from detector.regime import MarketRegime, RegimeClassifier
from detector.drift_monitor import DriftMonitor
from detector.feature_drift import FeatureDriftMonitor


class LiveEngine:
    """Motor síncrono que se alimenta tick a tick desde el stream async.

    Mantiene el adaptador, el broker (paper) y el store. Expone ``on_trade``
    para el callback del websocket y ``finalize`` para el cierre/auditoría.

    Integra: BranchGate (auto-disable), RegimeClassifier (suspensión por
    régimen), DriftMonitor+CircuitBreaker (CUSUM/Page-Hinkley), FeatureDrift
    (PSI/KS) y AlertManager (hub de alertas con FileAlertSink persistente).
    """

    def __init__(
        self,
        store: LiveStore,
        entry_sol: float = 1.0,
        sol_usd: float = 150.0,
        window_s: float = 60.0,
        take_profit_pct: float = 0.30,
        stop_loss_pct: float = -0.35,
        timeout_s: float = 300.0,
        max_open: int = 50,
        alert_log_path: Optional[str] = "alerts.jsonl",
    ) -> None:
        self.adapter = PumpPortalAdapter(sol_usd=sol_usd, window_s=window_s)
        self.store = store

        # Hub de alertas + sink de archivo (sobrevive al proceso).
        self.alerts = AlertManager()
        if alert_log_path:
            self.alerts.subscribe(FileAlertSink(alert_log_path))

        # Régimen de mercado: suspende entradas en CONTRACTING.
        self.regime = RegimeClassifier()

        # Drift de PnL (CUSUM/Page-Hinkley) + drift de features (PSI/KS).
        self.drift_monitor = DriftMonitor(alert_manager=self.alerts)
        self.drift_monitor.attach_feature_drift(FeatureDriftMonitor(
            reference={}  # se calibra con los primeros N trades reales
        ))

        # BranchGate: auto-disable de ramas por EV neto negativo.
        from detector.branch_gate import BranchGate
        self.gate = BranchGate(store, origin="live", alert_manager=self.alerts)

        # Slippage condicional (no fijo): usa el modelo que aprende del histórico.
        self.broker = PaperBroker(
            store,
            CostModel(),
            entry_sol=entry_sol,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            timeout_s=timeout_s,
            max_open=max_open,
            slippage_model=ConditionalSlippageModel(),
        )

        # L11 (seguridad): estructuras acotadas. En 24/7 entra un mint por token
        # creado; sin poda, `_decided`/`_last_price` crecen sin límite. `_decided`
        # se usa solo como membership, así que un dict es suficiente.
        self._decided: Dict[str, float] = {}
        self._last_price: Dict[str, float] = {}
        self._max_tracked = 200_000
        self._last_ts: float = 0.0
        self.n_events = 0
        self.n_opened = 0
        self.n_gate_vetoes = 0
        self.n_regime_suspensions = 0
        self.n_drift_alerts = 0

    def on_trade(self, payload: Dict[str, Any]) -> None:
        """Callback por trade normalizado: cierra abiertas y evalúa señal."""
        tr = payload.get("trade")
        if tr is None:
            return
        self.n_events += 1
        self._last_ts = max(self._last_ts, tr.ts)
        # Proxy de precio = market cap observado (lineal con el precio, supply fija).
        price = tr.market_cap_sol
        # L1 (seguridad): sin market cap válido NO se llama a on_price. Antes,
        # price=0 producía pct=-100% y liquidaba la posición a ~-98% en silencio.
        if price > 0.0:
            self._last_price[tr.mint] = price
            self.broker.on_price(tr.mint, price, tr.ts)

        # Alimenta drift de features con el vector actual (PSI/KS).
        feats = self.adapter.features(tr.mint)
        if feats:
            self.drift_monitor.record_feature_values(feats)

        if tr.mint in self._decided:
            return
        if not feats:
            return
        feats["mint"] = tr.mint
        feats["entry_price"] = price

        # Suspensión de entradas por régimen CONTRACTING.
        if self.regime.should_suspend_entries():
            self.n_regime_suspensions += 1
            self.alerts.emit(
                "regime", AlertSeverity.WARNING,
                "Entradas suspendidas por régimen CONTRACTING",
                payload={"winrate": self.regime.snapshot().winrate})
            return

        decision = MasterSnipeSignal.evaluate(feats)

        # Veto por rama desactivada (EV neto < breakeven con n suficiente).
        from detector.branch_gate import veto_if_disabled
        vetted = veto_if_disabled(decision, self.gate)
        if vetted is not decision:
            self.n_gate_vetoes += 1
            decision = vetted

        did = self.broker.on_signal(feats, decision, tr.ts)
        if did is not None:
            self._decided[tr.mint] = tr.ts
            self.n_opened += 1
        if self.n_events % 1000 == 0:
            self._prune_tracking()

    def _prune_tracking(self) -> None:
        """Acota `_last_price`/`_decided` para corridas 24/7 sin fugas de memoria."""
        for struct in (self._last_price, self._decided):
            if len(struct) > self._max_tracked:
                for mint in list(struct.keys())[: self._max_tracked // 10]:
                    struct.pop(mint, None)

    def finalize(self) -> Dict[str, Any]:
        """Cierra colas abiertas por timeout, refresca gate/regime y devuelve audit."""
        flush_ts = self._last_ts + self.broker.timeout_s + 1.0
        for mint in list(self.broker._open.keys()):
            self.broker.on_price(mint, self._last_price.get(mint, 0.0), flush_ts)

        # Última pasada del gate para dejar el estado persistido al día.
        self.gate.refresh()

        # Alimenta el clasificador de régimen con los outcomes netos nuevos.
        rows = self.store._resolved_rows(origin="live", admitted_only=True)
        for r in rows:
            pnl_r = r.get("pnl")
            if pnl_r is not None and pnl_r == pnl_r:  # no NaN
                self.regime.record_outcome(pnl_r=pnl_r)

        audit = self.broker.audit(origin="live", admitted_only=True)
        audit["control"] = {
            "events_ingested": self.n_events,
            "positions_opened": self.n_opened,
            "mints_seen": len(self._last_price),
            "flush_ts": flush_ts,
            "branch_gate_vetoes": self.n_gate_vetoes,
            "branch_gate_disabled": self.gate.disabled_branches(),
            "regime": self.regime.regime.value,
            "regime_suspensions": self.n_regime_suspensions,
            "alerts_emitted": self.alerts.count,
            "circuit_breaker": self.drift_monitor.get_status()["state"].value,
        }
        # Cierra handles de archivo y SQLite antes de devolver (evita
        # PermissionError en Windows al limpiar temporales).
        self.alerts.close_all()
        self.store.close()
        return audit

def _demo_events() -> List[Dict[str, Any]]:
    """Eventos PumpPortal deterministas (offline) para probar el loop.

    Fuerza el escenario que la Señal Maestra admite (rama C) y que el broker
    cierra: un token que revienta al alza (TP) y otro que se desploma (SL).
    Sin red, reproducible. NO es evidencia de rentabilidad real.
    """
    events: List[Dict[str, Any]] = []
    ts0 = 1_700_000_000.0

    def _ev(mint, trader, is_buy, mc_sol, ts, v_sol=30.0):
        return {
            "mint": mint,
            "txType": "buy" if is_buy else "sell",
            "traderPublicKey": trader,
            "solAmount": 0.5,
            "tokensAmount": 100_000,
            "marketCapSol": round(mc_sol, 4),
            "vSolInCurve": v_sol,          # 30/85 = 35% curva (< 45% saturación)
            "vTokensInCurve": 1_000_000_000.0,
            "pool": "pump",
            "signature": f"{mint}-{trader}-{ts}",
            "timestamp": ts,
        }

    # Token A: 45 compradores únicos (hc>=40) -> apertura rama C; luego TP (+~45%).
    for i in range(45):
        events.append(_ev("DEMOTP", f"walletA{i}", True, 50.0 + i * 0.12, ts0 + i))
    events.append(_ev("DEMOTP", "whaleTP", True, 80.0, ts0 + 50))

    # Token B: idem pero revienta -> SL (-~45%).
    for i in range(45):
        events.append(_ev("DEMOSL", f"walletB{i}", True, 50.0 + i * 0.12, ts0 + 100 + i))
    events.append(_ev("DEMOSL", "dumper", False, 30.0, ts0 + 150))
    return events


def _run_demo(engine: LiveEngine) -> int:
    """Replay offline: alimenta el engine tick a tick (equivalente al callback)."""
    n = 0
    for ev in _demo_events():
        tr = engine.adapter.ingest(ev)
        if tr is not None:
            engine.on_trade({"trade": tr, "features": engine.adapter.features(tr.mint)})
            n += 1
    return n


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Loop en vivo de ganancia real medible (Señal Maestra -> paper -> LiveStore).",
    )
    p.add_argument("--api-key", default="", help="API key de PumpPortal (opcional).")
    p.add_argument("--db", default="live.db", help="Ruta del LiveStore SQLite.")
    p.add_argument("--mints", default="", help="Mints separados por coma (vacío = todos).")
    p.add_argument("--max-events", type=int, default=None, help="Corta tras N eventos.")
    p.add_argument("--demo-feed", action="store_true",
                   help="Sin red: replay determinista offline para probar la tubería.")
    p.add_argument("--entry-sol", type=float, default=1.0)
    p.add_argument("--sol-usd", type=float, default=150.0, help="Precio SOL/USD de referencia.")
    p.add_argument("--window-s", type=float, default=60.0)
    p.add_argument("--tp", type=float, default=0.30, help="Take profit (fracción o %%).")
    p.add_argument("--sl", type=float, default=-0.35, help="Stop loss (fracción o %%).")
    p.add_argument("--timeout-s", type=float, default=300.0)
    p.add_argument("--max-open", type=int, default=50)
    p.add_argument("--alert-log", default="alerts.jsonl",
                   help="Ruta del JSONL de alertas (vacío = solo en memoria).")
    return p


def _print_audit(audit: Dict[str, Any]) -> None:
    n = audit.get("n", 0)
    wr = audit.get("winrate", 0.0) * 100.0
    pf = audit.get("profit_factor", 0.0)
    total = audit.get("total_pnl_sol", 0.0)
    ctrl = audit.get("control", {})
    print("\n===== AUDITORÍA NETA (origin=live) =====")
    print(f"  eventos ingeridos : {ctrl.get('events_ingested', '?')}")
    print(f"  posiciones abiertas: {ctrl.get('positions_opened', '?')}")
    print(f"  trades cerrados   : {n}")
    print(f"  winrate real      : {wr:.1f}%")
    print(f"  profit factor     : {pf:.2f}")
    print(f"  PnL neto agregado : {total:+.4f} SOL")
    if "branch_gate_vetoes" in ctrl:
        print(f"  gate vetoes       : {ctrl['branch_gate_vetoes']}"
              f" | desactivadas: {ctrl.get('branch_gate_disabled', [])}")
    if "regime" in ctrl:
        print(f"  régimen           : {ctrl['regime']}"
              f" | suspensiones: {ctrl.get('regime_suspensions', 0)}")
    if "circuit_breaker" in ctrl:
        print(f"  circuit breaker   : {ctrl['circuit_breaker']}"
              f" | alertas: {ctrl.get('alerts_emitted', 0)}")
    if audit.get("note"):
        print(f"  nota: {audit['note']}")



async def _amain(args: argparse.Namespace) -> int:
    """Orquesta el loop: feed (demo o websocket) -> Señal Maestra -> paper -> audit."""
    mints = [m.strip() for m in args.mints.split(",") if m.strip()] or None

    with LiveStore(args.db) as store:
        engine = LiveEngine(
            store,
            entry_sol=args.entry_sol,
            sol_usd=args.sol_usd,
            window_s=args.window_s,
            take_profit_pct=args.tp,
            stop_loss_pct=args.sl,
            timeout_s=args.timeout_s,
            max_open=args.max_open,
            alert_log_path=args.alert_log or None,
        )

        audit: Dict[str, Any] = {}
        try:
            if args.demo_feed:
                n = _run_demo(engine)
                print(f"   [demo-feed] eventos replayed: {n} (sin red, determinista)")
            else:
                print(f"   Conectando a PumpPortal (mints={mints or 'TODOS'})... Ctrl+C para parar.")
                try:
                    await stream_trades(
                        engine.adapter,
                        mints=mints,
                        api_key=args.api_key,
                        on_event=engine.on_trade,
                        max_events=args.max_events,
                    )
                except KeyboardInterrupt:
                    print("\n   ⏹ Interrupción recibida: cerrando posiciones y auditando...")
        finally:
            # L2 (seguridad): finalize() SIEMPRE se ejecuta (incluso si el feed
            # muere por error), cerrando colas abiertas, auditando y liberando
            # handles. Antes, una desconexión saltaba la auditoría por completo.
            audit = engine.finalize()
        _print_audit(audit)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        # Ctrl+C durante la conexión o el cierre ordenado.
        return 0


if __name__ == "__main__":
    sys.exit(main())

