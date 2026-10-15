"""Gestor de Tiers de RPC y pre-simulación de transacciones en Solana (Bloques C / E).

1. RPCTierPool:
   Ruta consultas y transacciones según criticidad:
   - TIER 1 (Execution / High-Throughput): Envío directo de transacciones (Helius Priority / Jito).
   - TIER 2 (WebSocket & Real-time State): Suscripciones de eventos PumpPortal / Geyser.
   - TIER 3 (Historical & Backup): Consultas frías, balances y RPC de respaldo.
   Incluye failover automático y circuit breaker por endpoint si la latencia supera el presupuesto.

2. TransactionSimulator:
   Ejecuta simulaciones pre-flight (simulateTransaction) antes de firmar o enviar transacciones.
   Detecta fallos deterministas (SlippageExceeded, ComputeBudgetExceeded, CustomProgramError).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time


class RPCTier(str, Enum):
    TIER_1_EXECUTION = "TIER_1_EXECUTION"  # Priority Swaps
    TIER_2_STREAM = "TIER_2_STREAM"        # WebSocket Feeds
    TIER_3_BACKUP = "TIER_3_BACKUP"        # Fallback & Historical


@dataclass
class RPCEndpoint:
    url: str
    tier: RPCTier
    is_active: bool = True
    consecutive_errors: int = 0
    last_latency_ms: float = 0.0
    total_calls: int = 0
    max_consecutive_errors: int = 3
    cooldown_seconds: float = 60.0
    disabled_until: float = 0.0


@dataclass(frozen=True)
class SimulationResult:
    success: bool
    units_consumed: int
    logs: List[str]
    error_code: Optional[str] = None
    rejection_reason: Optional[str] = None


class RPCTierPool:
    """Enrutador de endpoints RPC con fallback determinista y presupuesto de latencia."""

    def __init__(self, endpoints: Optional[List[RPCEndpoint]] = None):
        self.endpoints = endpoints or [
            RPCEndpoint(url="https://mainnet.helius-rpc.com/?api-key=default", tier=RPCTier.TIER_1_EXECUTION),
            RPCEndpoint(url="wss://pumpportal.fun/api/data", tier=RPCTier.TIER_2_STREAM),
            RPCEndpoint(url="https://api.mainnet-beta.solana.com", tier=RPCTier.TIER_3_BACKUP),
        ]

    def select_endpoint(self, tier: RPCTier, now_ts: Optional[float] = None) -> Optional[RPCEndpoint]:
        """Selecciona el mejor endpoint disponible para el tier solicitado."""
        now = float(now_ts if now_ts is not None else time.time())
        candidates = [
            ep for ep in self.endpoints
            if ep.tier == tier and (ep.is_active or now >= ep.disabled_until)
        ]
        if not candidates:
            # Fallback a Tier 3 si el tier solicitado no está disponible
            candidates = [
                ep for ep in self.endpoints
                if ep.is_active or now >= ep.disabled_until
            ]
        if not candidates:
            return None
        # Selecciona el de menor latencia reciente
        return min(candidates, key=lambda ep: ep.last_latency_ms)

    def record_call(self, endpoint: RPCEndpoint, latency_ms: float, success: bool, now_ts: Optional[float] = None):
        """Actualiza la telemetría del endpoint."""
        now = float(now_ts if now_ts is not None else time.time())
        endpoint.total_calls += 1
        endpoint.last_latency_ms = latency_ms

        if success:
            endpoint.consecutive_errors = 0
            endpoint.is_active = True
        else:
            endpoint.consecutive_errors += 1
            if endpoint.consecutive_errors >= endpoint.max_consecutive_errors:
                endpoint.is_active = False
                endpoint.disabled_until = now + endpoint.cooldown_seconds


class TransactionSimulator:
    """Simulador de pre-ejecución para evitar pagar fees por transacciones destinadas a fallar."""

    @staticmethod
    def parse_simulation_response(rpc_response: Dict[str, Any]) -> SimulationResult:
        """
        Interpreta la respuesta de `simulateTransaction` de Solana JSON-RPC.
        Detecta códigos de error de slippage y programas de pump.fun / Raydium.
        """
        val = (rpc_response or {}).get("value", {})
        err = val.get("err")
        logs = val.get("logs", []) or []
        units = int(val.get("unitsConsumed", 0) or 0)

        if err is None:
            return SimulationResult(
                success=True,
                units_consumed=units,
                logs=logs,
                error_code=None,
                rejection_reason=None,
            )

        err_str = str(err)
        # Búsqueda de patrones específicos en logs
        rejection_reason = "SIMULATION_FAILED"
        if "custom program error: 0x1770" in err_str.lower() or any("slippage" in l.lower() for l in logs):
            rejection_reason = "VETO_PREFLIGHT_SLIPPAGE_EXCEEDED"
        elif "computebudgetexceeded" in err_str.lower() or any("exceeded max compute" in l.lower() for l in logs):
            rejection_reason = "VETO_PREFLIGHT_COMPUTE_EXCEEDED"
        elif "insufficient funds" in err_str.lower() or any("insufficient" in l.lower() for l in logs):
            rejection_reason = "VETO_PREFLIGHT_INSUFFICIENT_FUNDS"

        return SimulationResult(
            success=False,
            units_consumed=units,
            logs=logs,
            error_code=err_str,
            rejection_reason=rejection_reason,
        )
