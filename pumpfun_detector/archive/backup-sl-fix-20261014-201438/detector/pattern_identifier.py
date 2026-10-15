"""
Identificador Autónomo de Patrones y Arquetipos (Pattern Identifier & Archetype Miner).

Inspirado en STUMPY (Matrix Profile / Motifs), Catch22 (características dinámicas canónicas)
y algoritmos de minería de subgrupos y grafos.

Clasifica automáticamente tokens, transacciones y rutas de precios en:
1. Arquetipos Alfa Ganadores (Winners DNA)
2. Clústeres Forenses de Falla (Loss & Rug Regimes)
3. Anti-Patrones de Microestructura (Cabales, Snipers Bloque 0, Falsos Rompimientos)
4. Huellas Dinámicas de Series Temporales (MFE/MAE, Volatilidad, Asimetría)
"""
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class IdentifiedArchetype:
    name: str
    category: str  # "WIN_ALPHA", "LOSS_TOXIC", "NEUTRAL_CHOP"
    confidence: float
    description: str
    key_signals: Dict[str, Any]
    action_recommendation: str  # "ENTER_AGGRESSIVE", "ENTER_NORMAL", "VETO_AVOID", "MONITOR"
    expected_winrate: float
    expected_ev: float
    anti_patterns_detected: List[str] = field(default_factory=list)


@dataclass
class PriceDynamicsFingerprint:
    volatility_pct: float
    max_gain_pct: float  # MFE
    max_drawdown_pct: float  # MAE
    time_to_peak_seconds: float
    dump_velocity_pct_per_min: float
    is_flash_pump_and_dump: bool
    hurst_proxy: float  # < 0.5 mean-reverting, > 0.5 trending


class PatternIdentifier:
    """
    Identificador inteligente de patrones on-chain y series temporales de precios.
    """

    # Definición de Arquetipos Canónicos
    ARCHETYPES = {
        "MICRO_CAP_BREAKOUT": {
            "category": "WIN_ALPHA",
            "desc": "Expansión temprana con MC inicial bajo ($< $4,500) y fuerte presión compradora inicial.",
            "expected_wr": 0.811,
            "expected_ev": 1.25,
            "action": "ENTER_AGGRESSIVE",
        },
        "MID_CAP_FLOW_MOMENTUM": {
            "category": "WIN_ALPHA",
            "desc": "Momentum sostenido en mediana capitalización ($10k-$40k) con ratio compra/venta > 2.0.",
            "expected_wr": 0.596,
            "expected_ev": 0.68,
            "action": "ENTER_NORMAL",
        },
        "CATASTROPHIC_SLIPPAGE_TRAP": {
            "category": "LOSS_TOXIC",
            "desc": "Reservas reales < 12 SOL. La liquidez de salida se evapora causando pérdidas reales de -56%.",
            "expected_wr": 0.08,
            "expected_ev": -1.80,
            "action": "VETO_AVOID",
        },
        "DEAD_LAUNCH_TIMEOUT": {
            "category": "LOSS_TOXIC",
            "desc": "Market Cap < $4,500 sin compras de seguimiento. Pérdida por inanición y timeout.",
            "expected_wr": 0.12,
            "expected_ev": -0.85,
            "action": "VETO_AVOID",
        },
        "DEV_LIQUIDATION_DUMP": {
            "category": "LOSS_TOXIC",
            "desc": "Creador con > 1.5% del suministro liquidando tokens sobre compradores minoristas.",
            "expected_wr": 0.05,
            "expected_ev": -1.95,
            "action": "VETO_AVOID",
        },
        "CABAL_NETWORK_TRAP": {
            "category": "LOSS_TOXIC",
            "desc": "Billeteras coludidas controlan > 30% del volumen, coordinando compras y dumping conjunto.",
            "expected_wr": 0.15,
            "expected_ev": -1.40,
            "action": "VETO_AVOID",
        },
        "BLOCK0_SNIPER_SQUEEZE": {
            "category": "LOSS_TOXIC",
            "desc": "Snipers tomaron > 25% del suministro en bloque 0 vía Jito. Riesgo extremo de venta masiva.",
            "expected_wr": 0.18,
            "expected_ev": -1.20,
            "action": "VETO_AVOID",
        },
        "PRE_MIGRATION_STALL": {
            "category": "LOSS_TOXIC",
            "desc": "Curva de enlace > 85%. Los compradores se detienen antes de la migración a Raydium.",
            "expected_wr": 0.22,
            "expected_ev": -0.95,
            "action": "VETO_AVOID",
        },
    }

    @classmethod
    def identify_token_pattern(cls, feat: Dict[str, Any]) -> IdentifiedArchetype:
        """
        Analiza las características de un token e identifica su arquetipo dominante y anti-patrones.
        """
        def _get(keys: List[str], default=0.0):
            for k in keys:
                if k in feat and feat[k] is not None:
                    try:
                        val = float(feat[k])
                        if not math.isnan(val):
                            return val
                    except (ValueError, TypeError):
                        pass
            return default

        # Anti-lookahead (P0): tras el purge, market_cap final ya no existe.
        # Preferir mc_at_entry_est (disponible al entrar) para no degradar a 0.0.
        mc = _get(["mc_at_entry_est", "market_cap", "marketCap", "marketCapUsd", "mc"])
        first_mc = _get(["first_market_cap", "firstMarketCap", "initialMarketCapUsd"], default=mc)
        first_liq = _get(["first_liquidity", "firstLiquidity", "initialLiquidityUsd"], default=1500.0)
        dev_bal = _get(["dev_balance_pct", "devBalancePct", "creatorHoldingPct"])
        real_sol = _get(["real_sol_reserves", "realSolReserves", "solReserves"], default=30.0)
        migration = _get(["migration_progress", "migrationProgress", "bondingCurveProgress"])
        buys = _get(["buys_m5", "buysM5", "n_buys", "unique_buyers"])
        sells = _get(["sells_m5", "sellsM5", "n_sells"])
        cabal_share = _get(["cabal_share", "cabalShare"])
        is_b0 = bool(feat.get("is_block0_trap") or feat.get("isBlock0Trap") or feat.get("b0_trap"))

        anti_patterns = []

        # 1. Comprobación de Anti-Patrones Inmutables (BP-001 a BP-010)
        if mc > 0 and mc <= 4500.0:
            anti_patterns.append("BP-001_DEAD_LAUNCH: Market Cap <= $4,500 (60.9% de pérdidas históricas)")
        if dev_bal > 1.5:
            anti_patterns.append(f"BP-008_DEV_DUMP_RISK: Dev con {dev_bal:.2f}% (> 1.5% umbral de veto)")
        if real_sol < 12.0:
            anti_patterns.append(f"BP-001_ILLIQUID_RESERVES: Reservas en {real_sol:.1f} SOL (< 12 SOL, slippage -56%)")
        if migration > 85.0:
            anti_patterns.append(f"BP-007_SATURATION_PRE_MIGRATION: Progreso en {migration:.1f}% (> 85%)")
        if cabal_share > 0.30:
            anti_patterns.append(f"BP-005_CABAL_CONCENTRATION: {cabal_share*100:.1f}% en billeteras coludidas")
        if is_b0:
            anti_patterns.append("BP-006_BLOCK0_SNIPER_TRAP: Snipers acapararon > 25% en bloque 0")

        # 2. Asignación de Arquetipo de Fallo si hay vetos críticos
        if dev_bal > 1.5:
            arc = cls.ARCHETYPES["DEV_LIQUIDATION_DUMP"]
            return IdentifiedArchetype(
                name="DEV_LIQUIDATION_DUMP", category=arc["category"], confidence=0.95,
                description=arc["desc"], key_signals={"dev_balance_pct": dev_bal},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        if real_sol < 12.0:
            arc = cls.ARCHETYPES["CATASTROPHIC_SLIPPAGE_TRAP"]
            return IdentifiedArchetype(
                name="CATASTROPHIC_SLIPPAGE_TRAP", category=arc["category"], confidence=0.92,
                description=arc["desc"], key_signals={"real_sol_reserves": real_sol},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        if cabal_share > 0.30:
            arc = cls.ARCHETYPES["CABAL_NETWORK_TRAP"]
            return IdentifiedArchetype(
                name="CABAL_NETWORK_TRAP", category=arc["category"], confidence=0.88,
                description=arc["desc"], key_signals={"cabal_share": cabal_share},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        if is_b0:
            arc = cls.ARCHETYPES["BLOCK0_SNIPER_SQUEEZE"]
            return IdentifiedArchetype(
                name="BLOCK0_SNIPER_SQUEEZE", category=arc["category"], confidence=0.85,
                description=arc["desc"], key_signals={"block0_trap": True},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        if migration > 85.0:
            arc = cls.ARCHETYPES["PRE_MIGRATION_STALL"]
            return IdentifiedArchetype(
                name="PRE_MIGRATION_STALL", category=arc["category"], confidence=0.82,
                description=arc["desc"], key_signals={"migration_progress": migration},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        if mc > 0 and mc <= 4500.0:
            arc = cls.ARCHETYPES["DEAD_LAUNCH_TIMEOUT"]
            return IdentifiedArchetype(
                name="DEAD_LAUNCH_TIMEOUT", category=arc["category"], confidence=0.90,
                description=arc["desc"], key_signals={"market_cap": mc},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        # 3. Comprobación de Arquetipos Alfa Ganadores (Winners DNA)
        # Rama A: Early Expansion Breakout
        if first_mc <= 4285.0 and first_liq <= 1725.0 and mc > 4500.0 and dev_bal <= 1.5:
            arc = cls.ARCHETYPES["MICRO_CAP_BREAKOUT"]
            return IdentifiedArchetype(
                name="MICRO_CAP_BREAKOUT", category=arc["category"], confidence=0.89,
                description=arc["desc"],
                key_signals={"market_cap": mc, "first_market_cap": first_mc, "first_liquidity": first_liq},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        # Rama B: Mid-Cap Flow Momentum
        buy_sell_ratio = (buys + 1.0) / (sells + 1.0)
        if mc >= 10000.0 and buy_sell_ratio >= 1.8 and dev_bal <= 0.8:
            arc = cls.ARCHETYPES["MID_CAP_FLOW_MOMENTUM"]
            return IdentifiedArchetype(
                name="MID_CAP_FLOW_MOMENTUM", category=arc["category"], confidence=0.78,
                description=arc["desc"],
                key_signals={"market_cap": mc, "buy_sell_ratio": round(buy_sell_ratio, 2), "dev_balance_pct": dev_bal},
                action_recommendation=arc["action"], expected_winrate=arc["expected_wr"],
                expected_ev=arc["expected_ev"], anti_patterns_detected=anti_patterns,
            )

        # 4. Caso Neutral
        return IdentifiedArchetype(
            name="NEUTRAL_UNCERTAIN",
            category="NEUTRAL_CHOP",
            confidence=0.50,
            description="Sin confluencia suficiente de señal alfa ni vetos activos de alto riesgo.",
            key_signals={"market_cap": mc, "buy_sell_ratio": round(buy_sell_ratio, 2)},
            action_recommendation="MONITOR",
            expected_winrate=0.45,
            expected_ev=0.0,
            anti_patterns_detected=anti_patterns,
        )

    @classmethod
    def fingerprint_price_path(cls, timestamps: np.ndarray, prices: np.ndarray) -> PriceDynamicsFingerprint:
        """
        Extrae la huella digital dinámica de una serie temporal de precios (ticks).
        Inspirado en Matrix Profile y Catch22.
        """
        if len(prices) < 2:
            return PriceDynamicsFingerprint(
                volatility_pct=0.0, max_gain_pct=0.0, max_drawdown_pct=0.0,
                time_to_peak_seconds=0.0, dump_velocity_pct_per_min=0.0,
                is_flash_pump_and_dump=False, hurst_proxy=0.5
            )

        p0 = prices[0]
        if p0 <= 0:
            return PriceDynamicsFingerprint(
                volatility_pct=0.0, max_gain_pct=0.0, max_drawdown_pct=0.0,
                time_to_peak_seconds=0.0, dump_velocity_pct_per_min=0.0,
                is_flash_pump_and_dump=False, hurst_proxy=0.5
            )

        # Retornos relativos desde el inicio
        rel_returns = (prices - p0) / p0 * 100.0
        peak_idx = int(np.argmax(prices))
        t_peak = float(timestamps[peak_idx] - timestamps[0])
        max_gain = float(rel_returns[peak_idx])

        # Drawdown máximo posterior al pico
        post_peak_prices = prices[peak_idx:]
        if len(post_peak_prices) > 1:
            trough_post_peak = np.min(post_peak_prices)
            dump_pct = (prices[peak_idx] - trough_post_peak) / prices[peak_idx] * 100.0
            dump_duration_s = max(float(timestamps[-1] - timestamps[peak_idx]), 1.0)
            dump_velocity = dump_pct / (dump_duration_s / 60.0)
        else:
            dump_pct = 0.0
            dump_velocity = 0.0

        # Máxima excursión adversa total (MAE)
        min_price = float(np.min(prices))
        max_dd = float((p0 - min_price) / p0 * 100.0)

        # Volatilidad realizada simple
        diffs = np.diff(prices) / prices[:-1]
        vol = float(np.std(diffs) * 100.0) if len(diffs) else 0.0

        # Proxy de persistencia (Hurst) basado en auto-correlación lag 1
        if len(diffs) >= 4:
            corr = float(np.corrcoef(diffs[:-1], diffs[1:])[0, 1])
            hurst_proxy = 0.5 + 0.5 * corr if not math.isnan(corr) else 0.5
        else:
            hurst_proxy = 0.5

        # Detección de Flash Pump and Dump
        is_pnd = bool(max_gain >= 100.0 and dump_pct >= 70.0 and t_peak <= 300.0)

        return PriceDynamicsFingerprint(
            volatility_pct=round(vol, 2),
            max_gain_pct=round(max_gain, 2),
            max_drawdown_pct=round(max_dd, 2),
            time_to_peak_seconds=round(t_peak, 1),
            dump_velocity_pct_per_min=round(dump_velocity, 2),
            is_flash_pump_and_dump=is_pnd,
            hurst_proxy=round(hurst_proxy, 3),
        )

    @classmethod
    def batch_identify(cls, df: pd.DataFrame) -> pd.DataFrame:
        """
        Enriquece un DataFrame de tokens o trades con arquetipos identificados,
        puntuaciones de confianza y lista de anti-patrones.
        """
        records = df.to_dict("records")
        archetypes = []
        categories = []
        confidences = []
        actions = []
        expected_wrs = []
        expected_evs = []
        anti_pattern_counts = []
        anti_pattern_details = []

        for r in records:
            res = cls.identify_token_pattern(r)
            archetypes.append(res.name)
            categories.append(res.category)
            confidences.append(res.confidence)
            actions.append(res.action_recommendation)
            expected_wrs.append(res.expected_winrate)
            expected_evs.append(res.expected_ev)
            anti_pattern_counts.append(len(res.anti_patterns_detected))
            anti_pattern_details.append("; ".join(res.anti_patterns_detected))

        out_df = df.copy()
        out_df["archetype_name"] = archetypes
        out_df["archetype_category"] = categories
        out_df["archetype_confidence"] = confidences
        out_df["recommended_action"] = actions
        out_df["expected_winrate"] = expected_wrs
        out_df["expected_ev"] = expected_evs
        out_df["anti_patterns_count"] = anti_pattern_counts
        out_df["anti_patterns_detail"] = anti_pattern_details
        return out_df
