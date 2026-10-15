#!/usr/bin/env python3
"""
run_live_detector.py — Daemon Ejecutor Autónomo de Sniping en Solana / Pump.fun (P1/Goal).

Orquesta el loop completo de trading algorítmico en modo seguro DRY-RUN / SHADOW:
1. Ingesta de eventos en tiempo real (WebSocket de PumpPortal) o Replay determinista.
2. Extracción instantánea de features en memoria vía PumpPortalAdapter.
3. Evaluación por la Señal Maestra Única (MasterSnipeSignal: Ramas A-E y 13 escudos).
4. Verificación de cotización y presupuesto de latencia vía ExecutionAdapter.
5. Gestión de salidas multi-etapa y trailing stops vía PositionTracker.
6. Supervisión estadística de régimen de mercado y CUSUM drift vía DriftMonitor.
7. Persistencia atómica de decisiones y outcomes en LiveStore (SQLite).

Uso:
    python scripts/run_live_detector.py --mode dry-run
    python scripts/run_live_detector.py --mode replay --events-file eventos.json --report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Optional, Dict, Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.master_signal import MasterSnipeSignal, MasterSignalDecision
from detector.costs import ExecutionCostModel, DEFAULT_COSTS
from detector.execution import (
    ExecutionAdapter,
    DefaultMasterSignalProvider,
    DefaultCostQuoteProvider,
)
from detector.pumpportal_feed import (
    PumpPortalAdapter,
    NormalizedTrade,
    parse_trade_event,
    stream_trades,
)
from detector.live_store import LiveStore
from detector.forward_tracker import PositionTracker
from detector.drift_monitor import DriftMonitor, CircuitBreakerState
from detector.alerts import AlertManager, AlertSeverity, FileAlertSink
from detector.regime import MarketRegime, RegimeClassifier
from detector.feature_drift import FeatureDriftMonitor
from detector.branch_gate import BranchGate


def setup_console() -> None:
    """Configura terminal para soportar salida UTF-8 en Windows."""
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    """Imprime un mensaje asegurando compatibilidad con Windows cp1252/utf-8."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        clean = str(msg).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(clean, flush=True)


class LiveDetectorDaemon:
    """Orquestador maestro de ejecución y telemetría forward."""

    def __init__(
        self,
        db_path: str = "live_decisions.db",
        dry_run: bool = True,
        max_events: Optional[int] = None,
        position_size_sol: float = 0.5,
        target_winrate: float = 0.45,
        alert_log_path: Optional[str] = "alerts.jsonl",
    ):
        self.db_path = db_path
        self.dry_run = dry_run
        self.max_events = max_events
        self.position_size_sol = position_size_sol

        # 1. Base de datos y almacenamiento
        self.store = LiveStore(db_path)

        # 2. Adaptador de eventos
        self.portal_adapter = PumpPortalAdapter()

        # 3. Hub de alertas + sink de archivo (sobrevive al proceso)
        self.alerts = AlertManager()
        if alert_log_path:
            self.alerts.subscribe(FileAlertSink(alert_log_path))

        # 4. Clasificador de régimen de mercado (suspende en CONTRACTING)
        self.regime = RegimeClassifier()

        # 5. Adaptador de ejecución
        self.cost_model = DEFAULT_COSTS
        self.exec_adapter = ExecutionAdapter(
            signal_provider=DefaultMasterSignalProvider(),
            quote_provider=DefaultCostQuoteProvider(self.cost_model),
            max_latency_budget_ms=1500.0,
            max_price_impact_pct=5.0,
        )

        # 6. Gestor de posiciones y salidas dinámicas
        self.tracker = PositionTracker(
            live_store=self.store,
            cost_model=self.cost_model,
            take_profit_pct=30.0,
            initial_sl_pct=60.0,
            realistic_slippage_pct=33.49,
            breakeven_trigger_pct=15.0,
            breakeven_lock_pct=5.0,
            trailing_stop_pct=10.0,
            stall_window_s=60.0,
            network_fee_sol=0.002,
        )

        # 7. Monitor de drift y circuit breaker (con feature-drift PSI/KS)
        self.drift_monitor = DriftMonitor(
            target_winrate=target_winrate, min_sample=10, alert_manager=self.alerts)
        self.drift_monitor.attach_feature_drift(FeatureDriftMonitor(reference={}))

        # 8. BranchGate: auto-disable de ramas por EV neto negativo
        self.gate = BranchGate(self.store, origin="live", alert_manager=self.alerts)

        # Telemetría
        self.events_processed = 0
        self.decisions_count = 0
        self.admitted_count = 0
        self.vetoed_count = 0
        self.n_regime_suspensions = 0
        self.n_gate_vetoes = 0

    def process_event(self, raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Procesa un trade entrante de principio a fin."""
        self.events_processed += 1
        tr = self.portal_adapter.ingest(raw_event)
        if tr is None:
            return None

        mint = tr.mint
        ts_now = tr.ts

        # 1. Actualizar posiciones abiertas con el nuevo precio del tick
        # El precio por token aproximado en USD: market_cap_sol * sol_usd / supply
        sol_usd = self.portal_adapter._sol_usd_now()
        price_usd = (tr.market_cap_sol * sol_usd) / 1e9 if tr.market_cap_sol > 0 else 0.0

        if price_usd > 0:
            outcome = self.tracker.process_tick(mint, price=price_usd, ts=ts_now)
            if outcome is not None:
                self.drift_monitor.record_trade_outcome(
                    pnl_pct=outcome["pnl_pct"],
                    is_win=outcome["is_win"],
                )
                safe_print(
                    f"[{time.strftime('%H:%M:%S')}] [CLOSED] POSICIÓN CERRADA: {mint[:8]}... "
                    f"| Razón: {outcome['exit_reason']} | PnL: {outcome['pnl_pct']:+.1f}% "
                    f"({outcome['pnl_sol']:+.4f} SOL) | Win: {outcome['is_win']}"
                )

        # 2. Si ya hay posición abierta para este token, no duplicar entrada
        if self.tracker.get_position(mint) is not None and self.tracker.get_position(mint).is_open:
            return None

        # 3. Comprobar Circuit Breaker antes de evaluar nuevas señales
        circuit = self.drift_monitor.get_status()
        if not circuit["trading_allowed"]:
            return None

        # 4. Construir vector de características en memoria
        feats = self.portal_adapter.features(mint)
        if not feats:
            return None

        # 4b. Alimenta drift de features (PSI/KS) y verifica circuit breaker.
        self.drift_monitor.record_feature_values(feats)
        circuit = self.drift_monitor.get_status()
        if not circuit["trading_allowed"]:
            return None

        # 4c. Suspensión de entradas por régimen CONTRACTING.
        if self.regime.should_suspend_entries():
            self.n_regime_suspensions += 1
            self.alerts.emit(
                "regime", AlertSeverity.WARNING,
                "Entradas suspendidas por régimen CONTRACTING",
                payload={"winrate": self.regime.snapshot().winrate})
            return None

        # 5. Evaluar señal maestra única
        decision = MasterSnipeSignal.evaluate(feats)
        self.decisions_count += 1

        # 5b. Veto por rama desactivada (EV neto < breakeven con n suficiente).
        from detector.branch_gate import veto_if_disabled
        vetted = veto_if_disabled(decision, self.gate)
        if vetted is not decision:
            self.n_gate_vetoes += 1
            decision = vetted

        # 6. Persistir decisión en LiveStore (idempotente)
        decision_id = self.store.record_decision(mint, feats, decision, ts=ts_now)

        if decision.admitted:
            self.admitted_count += 1
            size_mult = circuit["size_multiplier"]
            actual_size_sol = self.position_size_sol * size_mult

            # Verificar ejecución vía ExecutionAdapter
            cand_payload = {
                "token_id": mint,
                "latency_ms": 250.0,  # Latencia de red estimada en dry-run
                **feats,
            }
            exec_res = self.exec_adapter.process_candidate(cand_payload, position_size_sol=actual_size_sol)

            if exec_res["executed"]:
                # Abrir posición en PositionTracker
                self.tracker.open_position(
                    mint=mint,
                    entry_price=price_usd if price_usd > 0 else 0.0001,
                    entry_ts=ts_now,
                    position_size_sol=actual_size_sol,
                    branch=decision.branch,
                    decision_id=decision_id,
                )
                safe_print(
                    f"[{time.strftime('%H:%M:%S')}] [BUY] ENTRADA EJECUTADA ({decision.branch}): {mint[:8]}... "
                    f"| MC: ${feats.get('market_cap_at_entry', 0):,.0f} | Size: {actual_size_sol:.2f} SOL"
                )
        else:
            self.vetoed_count += 1

        return {
            "mint": mint,
            "decision": decision,
            "decision_id": decision_id,
        }

    def run_replay(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ejecuta una simulación determinista sobre una lista de eventos históricos."""
        safe_print(f"[*] Iniciando Replay de {len(events)} eventos históricos...")
        t0 = time.perf_counter()

        for ev in events:
            if self.max_events is not None and self.events_processed >= self.max_events:
                break
            self.process_event(ev)

        # Refresca gate + alimenta régimen con outcomes netos.
        self.gate.refresh()
        rows = self.store._resolved_rows(origin="live", admitted_only=True)
        for r in rows:
            pnl_r = r.get("pnl")
            if pnl_r is not None and pnl_r == pnl_r:
                self.regime.record_outcome(pnl_r=pnl_r)

        # Cierra handles de archivo de alertas (evita PermissionError en Windows).
        self.alerts.close_all()

        duration = time.perf_counter() - t0
        safe_print(f"[+] Replay finalizado en {duration:.2f}s ({self.events_processed} eventos procesados)")
        summary = self.generate_summary()
        self.store.close()
        return summary

    def close(self) -> None:
        """Cierra recursos abiertos (alertas + SQLite). Idempotente."""
        self.alerts.close_all()
        self.store.close()

    async def run_live(self) -> None:
        """Inicia el bucle en vivo consumiendo WebSocket de PumpPortal."""
        print(f"[*] Conectando a stream en vivo de PumpPortal (Modo: {'DRY-RUN' if self.dry_run else 'LIVE'})...")

        def _on_event(payload: Dict[str, Any]):
            tr = payload.get("trade")
            if tr is not None:
                raw = {
                    "mint": tr.mint,
                    "txType": "buy" if tr.is_buy else "sell",
                    "solAmount": tr.sol_amount,
                    "tokensAmount": tr.token_amount,
                    "marketCapSol": tr.market_cap_sol,
                    "vSolInCurve": tr.v_sol_curve,
                    "vTokensInCurve": tr.v_tokens_curve,
                    "pool": tr.pool,
                    "traderPublicKey": tr.trader,
                    "signature": tr.signature,
                    "timestamp": tr.ts,
                }
                self.process_event(raw)

        try:
            await stream_trades(
                self.portal_adapter,
                on_event=_on_event,
                max_events=self.max_events,
            )
        finally:
            self.close()

    def generate_summary(self) -> Dict[str, Any]:
        """Genera resumen cuantitativo de la sesión."""
        pos_stats = self.tracker.get_stats()
        circuit = self.drift_monitor.get_status()
        wilson = circuit["wilson_ci_95"]

        summary = {
            "events_processed": self.events_processed,
            "decisions_evaluated": self.decisions_count,
            "signals_admitted": self.admitted_count,
            "signals_vetoed": self.vetoed_count,
            "positions_closed": pos_stats["n"],
            "positions_open": pos_stats["open_positions"],
            "winrate_pct": pos_stats["winrate"] * 100.0,
            "wilson_ci_95": f"[{wilson[0]*100:.1f}%, {wilson[1]*100:.1f}%]",
            "total_pnl_sol": pos_stats["total_pnl_sol"],
            "profit_factor": pos_stats["profit_factor"],
            "circuit_breaker_state": circuit["state"].value,
            "drift_detected": circuit["drift_detected"],
            "regime": self.regime.regime.value,
            "regime_suspensions": self.n_regime_suspensions,
            "branch_gate_vetoes": self.n_gate_vetoes,
            "branch_gate_disabled": self.gate.disabled_branches(),
            "alerts_emitted": self.alerts.count,
        }
        return summary

    def print_report(self) -> None:
        """Imprime informe formateado por consola."""
        s = self.generate_summary()
        safe_print("\n" + "=" * 65)
        safe_print("  INFORME DE TELEMETRÍA FORWARD (PumpFun Live Detector)")
        safe_print("=" * 65)
        safe_print(f" Eventos Procesados:        {s['events_processed']:,}")
        safe_print(f" Decisiones Evaluadas:      {s['decisions_evaluated']:,}")
        safe_print(f" Señales Admitidas:         {s['signals_admitted']:,} ({s['signals_admitted']/max(s['decisions_evaluated'],1)*100:.1f}%)")
        safe_print(f" Señales Vetadas:           {s['signals_vetoed']:,}")
        safe_print("-" * 65)
        safe_print(f" Posiciones Cerradas:       {s['positions_closed']:,}")
        safe_print(f" Posiciones Abiertas:       {s['positions_open']:,}")
        safe_print(f" Winrate Forward:           {s['winrate_pct']:.1f}% (IC 95%: {s['wilson_ci_95']})")
        safe_print(f" PnL Total Neto (SOL):      {s['total_pnl_sol']:+.4f} SOL")
        safe_print(f" Profit Factor:             {s['profit_factor']:.2f}")
        safe_print(f" Estado Circuit Breaker:    {s['circuit_breaker_state']}")
        safe_print(f" Régimen de Mercado:        {s.get('regime', 'unknown')}"
                   f" (suspensiones: {s.get('regime_suspensions', 0)})")
        safe_print(f" BranchGate vetos:          {s.get('branch_gate_vetoes', 0)}"
                   f" | desactivadas: {s.get('branch_gate_disabled', [])}")
        safe_print(f" Alertas emitidas:          {s.get('alerts_emitted', 0)}")
        safe_print("=" * 65 + "\n")


def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser(description="Daemon Ejecutor Autónomo de Sniping en Pump.fun")
    ap.add_argument("--mode", choices=("dry-run", "replay"), default="dry-run", help="Modo operativo")
    ap.add_argument("--events-file", help="Ruta a archivo JSON con eventos de trades para replay")
    ap.add_argument("--db-store", default="live_decisions.db", help="Ruta a base SQLite de almacenamiento")
    ap.add_argument("--max-events", type=int, default=None, help="Límite máximo de eventos a procesar")
    ap.add_argument("--size-sol", type=float, default=0.5, help="Tamaño de entrada por trade en SOL")
    ap.add_argument("--report", action="store_true", help="Imprime informe detallado al finalizar")
    ap.add_argument("--alert-log", default="alerts.jsonl",
                    help="Ruta del JSONL de alertas (vacío = solo en memoria)")
    args = ap.parse_args()

    daemon = LiveDetectorDaemon(
        db_path=args.db_store,
        dry_run=True,
        max_events=args.max_events,
        position_size_sol=args.size_sol,
        alert_log_path=args.alert_log or None,
    )

    if args.mode == "replay":
        if not args.events_file or not os.path.exists(args.events_file):
            print(f"[!] Error: Archivo de eventos no encontrado: {args.events_file}")
            return 1
        with open(args.events_file, encoding="utf-8") as f:
            events = json.load(f)
        if not isinstance(events, list):
            print("[!] Error: El archivo de eventos debe ser un array JSON de objetos")
            return 1
        daemon.run_replay(events)
    else:
        try:
            asyncio.run(daemon.run_live())
        except KeyboardInterrupt:
            print("\n[*] Interrupción manual recibida. Cerrando daemon limpiamente...")

    if args.report:
        daemon.print_report()

    # Cierre defensivo (idempotente): asegura que SQLite + sinks queden
    # liberados aunque run_replay/run_live no hayan llegado a su close().
    daemon.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
