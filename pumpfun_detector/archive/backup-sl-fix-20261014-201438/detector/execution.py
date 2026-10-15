"""
Módulo de Ejecución Modular de Alta Frecuencia (P1 - Integración Open Source).

Inspirado en:
- Hummingbot: separación estricta de proveedores (SignalProvider, QuoteProvider,
  OrderSubmitter, PositionManager, TradeRecorder).
- Chainstack pumpfun-bonkfun-bot: presupuesto estricto de latencia (latency_budget)
  y cancelación si el quote no refresca o llega tarde.
- Jupiter Swap API: validación de impacto en precio (priceImpactPct), slippage y rutas.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any
import time

from .costs import ExecutionCostModel, ExecutionQuote, DEFAULT_COSTS
from .master_signal import MasterSnipeSignal, MasterSignalDecision


@dataclass(frozen=True)
class OrderResult:
    """Resultado inmutable de sumisión de orden on-chain."""
    success: bool
    tx_hash: Optional[str]
    in_sol: float
    out_tokens: float
    fees_sol: float
    error: Optional[str] = None


class SignalProvider(ABC):
    """Interfaz para generadores de señal (emisión de intención sin conocer detalles de red)."""
    @abstractmethod
    def get_signal(self, token_data: dict) -> MasterSignalDecision:
        pass


class QuoteProvider(ABC):
    """Interfaz para cotización en tiempo real de swaps."""
    @abstractmethod
    def get_quote(self, token_id: str, in_sol: float, route: str = "pump_curve") -> ExecutionQuote:
        pass


class OrderSubmitter(ABC):
    """Interfaz para envío y confirmación de transacciones on-chain."""
    @abstractmethod
    def submit_order(self, quote: ExecutionQuote) -> OrderResult:
        pass


class PositionManager(ABC):
    """Interfaz para seguimiento de ciclo de vida de posiciones y stops dinámicos."""
    @abstractmethod
    def update_positions(self, ticks: list) -> list:
        pass


class TradeRecorder(ABC):
    """Interfaz para persistencia y auditoría de operaciones."""
    @abstractmethod
    def record_trade(self, trade: dict) -> None:
        pass


class DefaultMasterSignalProvider(SignalProvider):
    """Implementación por defecto usando MasterSnipeSignal."""
    def get_signal(self, token_data: dict) -> MasterSignalDecision:
        return MasterSnipeSignal.evaluate(token_data)


class DefaultCostQuoteProvider(QuoteProvider):
    """Implementación por defecto de cotización usando ExecutionCostModel."""
    def __init__(self, cost_model: Optional[ExecutionCostModel] = None):
        self.cost_model = cost_model or DEFAULT_COSTS

    def get_quote(self, token_id: str, in_sol: float, route: str = "pump_curve") -> ExecutionQuote:
        # Por defecto asume 30 SOL de reservas estándar en curva
        return self.cost_model.calculate_quote(
            in_sol=in_sol,
            liquidity_sol=30.0,
            spot_price_sol=0.00001,
            route=route,
        )


class InMemoryTradeRecorder(TradeRecorder):
    """Registrador en memoria para auditoría y backtesting."""
    def __init__(self):
        self.recorded = []

    def record_trade(self, trade: dict) -> None:
        self.recorded.append(trade)


class ExecutionAdapter:
    """
    Adaptador Coordinador de Ejecución.
    Verifica que la señal sea admitida, que el presupuesto de latencia no esté excedido,
    que la cotización sea válida y ejecutable antes de enviar fondos.
    """

    def __init__(
        self,
        signal_provider: Optional[SignalProvider] = None,
        quote_provider: Optional[QuoteProvider] = None,
        submitter: Optional[OrderSubmitter] = None,
        position_manager: Optional[PositionManager] = None,
        recorder: Optional[TradeRecorder] = None,
        max_latency_budget_ms: float = 1500.0,
        max_price_impact_pct: float = 5.0,
    ):
        self.signal_provider = signal_provider or DefaultMasterSignalProvider()
        self.quote_provider = quote_provider or DefaultCostQuoteProvider()
        self.submitter = submitter
        self.position_manager = position_manager
        self.recorder = recorder or InMemoryTradeRecorder()
        self.max_latency_budget_ms = max_latency_budget_ms
        self.max_price_impact_pct = max_price_impact_pct

    def process_candidate(self, token_data: dict, position_size_sol: float = 0.5) -> dict:
        """
        Ciclo completo de evaluación y ejecución de una oportunidad.
        Retorna dict con decisión, cotización y resultado de orden.
        """
        token_id = str(token_data.get("token_id", ""))
        t_start = time.perf_counter()

        # 1. Evaluar señal
        decision = self.signal_provider.get_signal(token_data)
        if not decision.admitted:
            return {
                "token_id": token_id,
                "executed": False,
                "decision": decision,
                "rejection_stage": "SIGNAL_VETO",
                "reason": decision.veto_reason,
            }

        # 2. Verificar latencia acumulada
        latency_ms = float(token_data.get("latency_ms", 0.0))
        if latency_ms > self.max_latency_budget_ms:
            return {
                "token_id": token_id,
                "executed": False,
                "decision": decision,
                "rejection_stage": "LATENCY_BUDGET",
                "reason": f"Latencia {latency_ms:.0f}ms > {self.max_latency_budget_ms:.0f}ms",
            }

        # 3. Solicitar quote
        quote = self.quote_provider.get_quote(token_id, in_sol=position_size_sol)
        if not quote.is_executable or quote.price_impact_pct > self.max_price_impact_pct:
            return {
                "token_id": token_id,
                "executed": False,
                "decision": decision,
                "quote": quote,
                "rejection_stage": "QUOTE_INVALID",
                "reason": quote.rejection_reason or "Price impact superior al límite permitido",
            }

        # 4. Enviar orden si submitter está configurado
        order_res = None
        if self.submitter is not None:
            order_res = self.submitter.submit_order(quote)
            if order_res.success:
                trade_record = {
                    "token_id": token_id,
                    "timestamp": time.time(),
                    "in_sol": order_res.in_sol,
                    "out_tokens": order_res.out_tokens,
                    "fees_sol": order_res.fees_sol,
                    "tx_hash": order_res.tx_hash,
                    "branch": decision.branch,
                }
                self.recorder.record_trade(trade_record)
                return {
                    "token_id": token_id,
                    "executed": True,
                    "decision": decision,
                    "quote": quote,
                    "order": order_res,
                }
            else:
                return {
                    "token_id": token_id,
                    "executed": False,
                    "decision": decision,
                    "quote": quote,
                    "order": order_res,
                    "rejection_stage": "SUBMISSION_FAILED",
                    "reason": order_res.error,
                }

        return {
            "token_id": token_id,
            "executed": True,
            "decision": decision,
            "quote": quote,
            "order": None,
        }
