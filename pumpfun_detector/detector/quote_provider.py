#!/usr/bin/env python3
"""
QuoteProvider con Veto por Price Impact, Quote Stale, Profundidad y Latencia.
Única fuente de cotizaciones para ExecutionCostModel y decisiones de entrada.
"""
from __future__ import annotations
import time, hashlib
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
from abc import ABC, abstractmethod


class VetoReason(Enum):
    """Razones de veto para cotizaciones."""
    OK = "ok"
    PRICE_IMPACT_EXCEEDED = "price_impact_exceeded"
    QUOTE_STALE = "quote_stale"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    LATENCY_EXCEEDED = "latency_exceeded"
    ROUTE_UNAVAILABLE = "route_unavailable"
    SLIPPAGE_PREDICTION_FAILED = "slippage_prediction_failed"
    NO_LIQUIDITY_SOURCE = "no_liquidity_source"


@dataclass
class Quote:
    """Cotización con metadata completa."""
    token_id: str
    price: float
    out_amount: float          # amount out para size_sol dado
    price_impact_pct: float    # % impacto en precio
    slippage_bps: int          # slippage en basis points
    route: str                 # ruta de ejecución (ej. "pump.fun->raydium")
    priority_fee_sol: float    # fee de prioridad estimado
    depth_sol: float           # liquidez disponible en la ruta
    source: str                # dexscreener|pumpportal|helius|gmgn
    ts_ms: int                 # timestamp de la cotización
    latency_ms: int            # latencia para obtener quote
    
    # Veto
    veto: VetoReason = VetoReason.OK
    veto_details: str = ""
    
    def is_valid(self, max_impact_pct: float = 5.0,
                 max_stale_ms: int = 5000,
                 min_depth_sol: float = 10.0,
                 max_latency_ms: int = 2000) -> tuple[bool, VetoReason]:
        """Valida la cotización contra umbrales."""
        if self.price <= 0 or self.out_amount <= 0:
            return False, VetoReason.NO_LIQUIDITY_SOURCE
        
        if self.price_impact_pct > max_impact_pct:
            return False, VetoReason.PRICE_IMPACT_EXCEEDED
        
        age_ms = int(time.time() * 1000) - self.ts_ms
        if age_ms > max_stale_ms:
            return False, VetoReason.QUOTE_STALE
        
        if self.depth_sol < min_depth_sol:
            return False, VetoReason.INSUFFICIENT_DEPTH
        
        if self.latency_ms > max_latency_ms:
            return False, VetoReason.LATENCY_EXCEEDED
        
        return True, VetoReason.OK


class LiquiditySource(ABC):
    """Interfaz para fuentes de liquidez."""
    
    @abstractmethod
    async def get_quote(self, token_id: str, size_sol: float) -> Optional[Quote]:
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        pass


class DexScreenerSource(LiquiditySource):
    """Fuente DexScreener (REST)."""
    
    def __init__(self, base_url: str = "https://api.dexscreener.com"):
        self.base_url = base_url
    
    def get_name(self) -> str:
        return "dexscreener"
    
    async def get_quote(self, token_id: str, size_sol: float) -> Optional[Quote]:
        # Implementación real haría HTTP request
        # Placeholder:
        import random
        return Quote(
            token_id=token_id,
            price=1.0 + random.random() * 0.1,
            out_amount=size_sol * 0.98,
            price_impact_pct=random.random() * 3,
            slippage_bps=int(random.random() * 100),
            route="pump.fun->raydium",
            priority_fee_sol=0.0005,
            depth_sol=50.0 + random.random() * 100,
            source="dexscreener",
            ts_ms=int(time.time() * 1000),
            latency_ms=int(random.random() * 200)
        )


class PumpPortalSource(LiquiditySource):
    """Fuente PumpPortal (WebSocket)."""
    
    def get_name(self) -> str:
        return "pumpportal"
    
    async def get_quote(self, token_id: str, size_sol: float) -> Optional[Quote]:
        # Implementación real usaría WebSocket
        return None


class QuoteProvider:
    """
    Proveedor único de cotizaciones con veto inteligente.
    Agrega múltiples fuentes y aplica veto por impacto, staleness, profundidad, latencia.
    """
    
    def __init__(self,
                 max_impact_pct: float = 5.0,
                 max_stale_ms: int = 5000,
                 min_depth_sol: float = 10.0,
                 max_latency_ms: int = 2000,
                 sources: List[LiquiditySource] = None):
        self.max_impact_pct = max_impact_pct
        self.max_stale_ms = max_stale_ms
        self.min_depth_sol = min_depth_sol
        self.max_latency_ms = max_latency_ms
        
        self.sources = sources or [DexScreenerSource()]
        self._cache: Dict[str, Quote] = {}
        self._stats = {
            "requests": 0, "vetoed": 0, "cache_hits": 0,
            "by_source": {}, "by_veto": {}
        }
    
    async def get_best_quote(self, token_id: str, size_sol: float) -> Quote:
        """
        Obtiene la mejor cotización válida entre todas las fuentes.
        Aplica veto y retorna la que pase con menor price_impact.
        """
        self._stats["requests"] += 1
        
        candidates = []
        for source in self.sources:
            try:
                quote = await source.get_quote(token_id, size_sol)
                if quote:
                    quote.source = source.get_name()
                    self._stats["by_source"][source.get_name()] = \
                        self._stats["by_source"].get(source.get_name(), 0) + 1
                    candidates.append(quote)
            except Exception as e:
                print(f"[QuoteProvider] Error en {source.get_name()}: {e}")
        
        if not candidates:
            q = Quote(
                token_id=token_id, price=0, out_amount=0,
                price_impact_pct=999, slippage_bps=9999,
                route="none", priority_fee_sol=0, depth_sol=0,
                source="none", ts_ms=0, latency_ms=9999,
                veto=VetoReason.NO_LIQUIDITY_SOURCE,
                veto_details="No sources returned valid quote"
            )
            self._veto(q)
            return q
        
        # Filtrar válidas
        valid_quotes = []
        for q in candidates:
            valid, veto = q.is_valid(self.max_impact_pct, self.max_stale_ms,
                                     self.min_depth_sol, self.max_latency_ms)
            if veto != VetoReason.OK:
                q.veto = veto
                q.veto_details = f"Veto by thresholds: {veto.value}"
                self._veto(q)
            else:
                valid_quotes.append(q)
        
        if not valid_quotes:
            # Retornar la "menos mala" con veto
            best = min(candidates, key=lambda q: q.price_impact_pct)
            best.veto = VetoReason.PRICE_IMPACT_EXCEEDED
            best.veto_details = "All quotes vetoed, returning best effort"
            self._veto(best)
            return best
        
        # Mejor: menor price impact
        best = min(valid_quotes, key=lambda q: q.price_impact_pct)
        return best
    
    def _veto(self, quote: Quote):
        self._stats["vetoed"] += 1
        self._stats["by_veto"][quote.veto.value] = \
            self._stats["by_veto"].get(quote.veto.value, 0) + 1
    
    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)
    
    def reset_stats(self):
        self._stats = {
            "requests": 0, "vetoed": 0, "cache_hits": 0,
            "by_source": {}, "by_veto": {}
        }


# Synchronous wrapper para uso en código no-async
class SyncQuoteProvider:
    """Wrapper síncrono para QuoteProvider."""
    
    def __init__(self, *args, **kwargs):
        self._async_provider = QuoteProvider(*args, **kwargs)
    
    def get_best_quote(self, token_id: str, size_sol: float) -> Quote:
        import asyncio
        return asyncio.run(self._async_provider.get_best_quote(token_id, size_sol))
    
    def get_stats(self) -> Dict[str, Any]:
        return self._async_provider.get_stats()


# Integration con ExecutionCostModel
@dataclass
class CostBreakdown:
    network_fee_sol: float       # 0.008 roundtrip
    platform_fee_frac: float     # 0.0564 roundtrip
    priority_fee_sol: float      # por congestión, medido no supuesto
    slippage_in_pct: float       # cuantil condicional entrada
    slippage_out_pct: float      # cuantil condicional salida (el SL sale peor)
    fill_probability: float      # 0..1, puede vetar la operación
    total_pct: float
    quote: Optional[Quote] = None  # Quote original para auditoría


class ExecutionCostModel:
    """ÚNICA fuente de costes. La usan labels, tpsl_grid, backtest, sizing y report."""
    
    # Constantes (fuente: detector/costs.py)
    ROUNDTRIP_TX_FEE_SOL = 0.008
    PLATFORM_FEE_ROUNDTRIP_FRAC = 0.0564
    
    def __init__(self, quote_provider: QuoteProvider = None):
        self.quote_provider = quote_provider or QuoteProvider()
    
    async def quote(self, size_sol: float, liquidity_sol: float, 
                    route: str, urgency: str = "normal") -> CostBreakdown:
        """Obtiene quote real y calcula breakdown completo."""
        # En producción: obtener quote real del provider
        # quote = await self.quote_provider.get_best_quote(token_id, size_sol)
        
        # Placeholder: usar valores medidos
        priority_fee = 0.001 if urgency == "high" else 0.0005
        
        # Slippage condicional basado en size/liquidity ratio
        ratio = size_sol / max(liquidity_sol, 1.0)
        if ratio < 0.01:
            slippage_in = 0.5
            slippage_out = 1.5
        elif ratio < 0.05:
            slippage_in = 1.5
            slippage_out = 3.5
        else:
            slippage_in = 3.0
            slippage_out = 8.0  # SL sale peor
        
        fill_prob = 1.0 if ratio < 0.1 else max(0.5, 1.0 - ratio)
        
        return CostBreakdown(
            network_fee_sol=self.ROUNDTRIP_TX_FEE_SOL,
            platform_fee_frac=self.PLATFORM_FEE_ROUNDTRIP_FRAC,
            priority_fee_sol=priority_fee,
            slippage_in_pct=slippage_in,
            slippage_out_pct=slippage_out,
            fill_probability=fill_prob,
            total_pct=slippage_in + slippage_out + self.PLATFORM_FEE_ROUNDTRIP_FRAC * 100,
        )
    
    def net_pnl(self, entry_px: float, exit_px: float, size_sol: float, 
                liq: float) -> tuple[float, CostBreakdown]:
        """Calcula PnL neto con costes completos."""
        cb = self.quote(size_sol, liq, "default")
        gross_pct = (exit_px - entry_px) / entry_px * 100
        net_pct = gross_pct - cb.total_pct
        pnl = size_sol * net_pct / 100
        return pnl, cb
    
    def realistic_sl(self, level_pct: float, observed_px: float, liq: float) -> float:
        """
        Empate TP/SL → siempre SL.
        SL se llena al precio OBSERVADO (min(nivel, px)), no al nivel ideal.
        """
        # El nivel nominal es negativo (ej. -15%)
        # observed_px es el precio real donde se llenaría (peor que nivel)
        # Si observed_px es peor (más negativo) que level_pct, usar observed
        return min(level_pct, observed_px)


if __name__ == "__main__":
    # Demo sync
    provider = SyncQuoteProvider()
    quote = provider.get_best_quote("TEST123", 0.1)
    print(f"Quote: {quote.price:.4f} SOL, impact: {quote.price_impact_pct:.2f}%, "
          f"depth: {quote.depth_sol:.1f} SOL, veto: {quote.veto.value}")
    
    # Cost model
    model = ExecutionCostModel()
    cb = model.quote(0.1, 50.0, "raydium")
    print(f"Cost breakdown: {cb}")
    
    pnl, cb2 = model.net_pnl(1.0, 1.15, 0.1, 50.0)  # +15% gross
    print(f"PnL neto: {pnl:.4f} SOL (gross +15%, net {cb2.total_pct:.2f}% costes)")
    
    # SL realista
    sl = model.realistic_sl(-15.0, -22.0, 50.0)  # gap: precio observado -22%
    print(f"SL realista: {sl:.1f}% (nivel -15%, observado -22%)")