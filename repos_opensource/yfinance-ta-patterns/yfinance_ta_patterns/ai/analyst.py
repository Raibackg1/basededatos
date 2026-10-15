"""AI Market Analyst: Synthesizes technical patterns into structured intelligence briefs and LLM prompts."""

from __future__ import annotations

import json
from numbers import Integral
from typing import Any

import numpy as np
import pandas as pd

from ..data import format_price, format_timestamp, validate_asset_type
from .scorer import PatternConfidenceResult, SignalGrade


def _sort_key(r: PatternConfidenceResult) -> tuple[float, float]:
    ts_val = 0.0
    if r.timestamp is not None:
        try:
            ts_val = pd.to_datetime(r.timestamp).timestamp()
        except Exception:
            ts_val = 0.0
    return (float(r.confidence), ts_val)


def _json_safe(obj: Any) -> Any:
    """Recursively make a report JSON-safe.

    `json.dumps` serializes non-finite floats as the bare tokens `NaN` / `Infinity`, which
    are invalid strict JSON, and its `default` hook is never consulted for them. Market data
    can legitimately carry NaN (e.g. a missing Volume on the forming bar), so every
    non-finite float is mapped to `None` — matching how `PatternConfidenceResult.to_dict`
    already treats NaN `rsi` / `atr`. NumPy scalars are normalized to native types so they
    are emitted as numbers rather than falling through to `default=str`.
    """
    if isinstance(obj, (float, np.floating)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    return obj


class AIMarketAnalyst:
    """Institutional-grade market intelligence synthesizer.

    Aggregates scored candlestick patterns and macro-technical context to generate:
    1. Human-readable executive market briefs.
    2. Structured JSON payloads for automated trading systems.
    3. Context-rich prompt templates optimized for frontier reasoning LLMs (Claude 3.7 Sonnet, o3-mini, DeepSeek-R1, Gemini 2.5).
    """

    def __init__(
        self,
        data: pd.DataFrame,
        scored_results: list[PatternConfidenceResult] | None = None,
        *,
        symbol: str = "ASSET",
        timeframe: str = "1d",
        asset_type: str = "auto",
    ) -> None:
        """Initialize analyst with market data and optional evaluated patterns.

        Args:
            data: OHLCV market DataFrame.
            scored_results: Optional pre-computed list of PatternConfidenceResult.
            symbol: Ticker symbol (e.g. 'BTC-USD', 'EURUSD'). Default is 'ASSET'.
            timeframe: Timeframe or interval (e.g. '4h', '1d'). Default is '1d'.
            asset_type: Asset classification ('auto', 'stock', 'forex', 'crypto', 'commodity', 'index').
        """
        self.data = data
        self.symbol = symbol
        self.timeframe = timeframe
        self.asset_type: str = validate_asset_type(asset_type)
        self.scored_results: list[PatternConfidenceResult] = (
            sorted(scored_results, key=_sort_key, reverse=True)
            if scored_results is not None
            else []
        )

    def analyze(
        self,
        min_confidence: float = 0.5,
        patterns: list[str] | None = None,
        date: str | None = None,
        lookback_bars: int | None = 1,
    ) -> list[PatternConfidenceResult]:
        """Automatically scan and score candlestick patterns across the dataset.

        Args:
            min_confidence: Threshold between 0.0 and 1.0 to filter low-conviction signals.
            patterns: Optional list of specific pattern names to scan. If None, scans all patterns.
            date: Optional single-date filter string.
            lookback_bars: Number of most recent bars to evaluate for active signals.
                           Defaults to 1. Set to None for full historical scan.

        Returns:
            List of PatternConfidenceResult sorted by confidence score descending.
        """
        if not np.isfinite(min_confidence) or not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be finite and within [0, 1]")

        if lookback_bars is not None:
            if isinstance(lookback_bars, bool) or not isinstance(lookback_bars, Integral):
                raise TypeError("lookback_bars must be an integer")
            if lookback_bars <= 0:
                raise ValueError("lookback_bars must be a positive integer (>= 1)")

        from .scorer import AIPatternScorer

        scorer = AIPatternScorer(self.data)
        self.scored_results = scorer.score_all_active(
            min_confidence=min_confidence,
            patterns=patterns,
            date=date,
            lookback_bars=lookback_bars,
        )
        self.scored_results.sort(key=_sort_key, reverse=True)
        return self.scored_results

    def get_market_regime_summary(self) -> dict[str, Any]:
        """Extract current volatility, volume, and price trends from data."""
        if self.data.empty:
            return {"status": "NO_DATA"}

        last_row = self.data.iloc[-1]
        close = float(last_row["Close"])
        high = float(last_row["High"])
        low = float(last_row["Low"])
        vol = float(last_row["Volume"]) if "Volume" in last_row else 0.0

        ret_20 = 0.0
        if len(self.data) >= 21:
            first_20 = float(self.data.iloc[-21]["Close"])
            ret_20 = ((close - first_20) / first_20) * 100.0
        elif len(self.data) > 1:
            first_20 = float(self.data.iloc[0]["Close"])
            ret_20 = ((close - first_20) / first_20) * 100.0

        return {
            "current_price": close,
            "last_high": high,
            "last_low": low,
            "last_volume": vol,
            "return_20_bars_pct": round(float(ret_20), 2),
            "total_signals_detected": len(self.scored_results),
            "high_conviction_signals": sum(
                1
                for r in self.scored_results
                if r.grade in (SignalGrade.EXCELLENT, SignalGrade.STRONG)
            ),
        }

    def _resolve_context(
        self,
        symbol_or_results: str | list[PatternConfidenceResult] | None = None,
        timeframe: str | None = None,
    ) -> tuple[str, str, list[PatternConfidenceResult]]:
        """Flexibly resolve (symbol, timeframe, results) across overloaded method calls."""
        if isinstance(symbol_or_results, list):
            results = symbol_or_results
            symbol = self.symbol
            tf = timeframe or self.timeframe
        elif isinstance(symbol_or_results, str):
            results = self.scored_results
            symbol = symbol_or_results
            tf = timeframe or self.timeframe
        else:
            results = self.scored_results
            symbol = self.symbol
            tf = timeframe or self.timeframe
        return symbol, tf, results

    def generate_brief(
        self,
        symbol_or_results: str | list[PatternConfidenceResult] | None = None,
        timeframe: str | None = None,
    ) -> str:
        """Format an executive markdown brief of technical findings."""
        symbol, tf, results = self._resolve_context(symbol_or_results, timeframe)
        regime = self.get_market_regime_summary()
        high_conv = sum(
            1 for r in results if r.grade in (SignalGrade.EXCELLENT, SignalGrade.STRONG)
        )
        lines: list[str] = [f"# AI Technical Intelligence Brief: {symbol} ({tf})"]
        if regime.get("status") == "NO_DATA":
            # get_market_regime_summary() returns only a status marker for an empty
            # dataset, so the price/return fields must not be read unconditionally.
            lines.append("**Market Data:** `unavailable` (empty dataset)")
        else:
            curr_price_str = format_price(regime["current_price"])
            lines.append(
                f"**Current Price:** `{curr_price_str}` | "
                f"**20-Bar Return:** `{regime['return_20_bars_pct']:+.2f}%`"
            )
        lines.extend(
            [
                f"**Signals Analyzed:** `{len(results)}` (High Conviction: `{high_conv}`)",
                "",
                "---",
                "## Key Pattern Setups",
            ]
        )

        if not results:
            lines.append("No active patterns identified matching the selected criteria.")
            return "\n".join(lines)

        for i, res in enumerate(results[:5], 1):
            grade_badge = f"[{res.grade.value}]"
            conf_pct = f"{res.confidence * 100:.1f}%"
            lines.append(f"### {i}. {res.pattern_name} - {grade_badge} (Confluence: {conf_pct})")
            ts_str = format_timestamp(res.timestamp, tf, include_tz=True)
            lines.append(f"- **Timestamp:** `{ts_str}` | **Regime:** `{res.trend_regime}`")
            atr_str = format_price(res.atr) if res.atr is not None else "-"
            lines.append(
                f"- **Metrics:** RVOL: `{res.rvol:.2f}x` | RSI(14): `{res.rsi:.1f}` | ATR: `{atr_str}`"
            )

            if res.confluence_factors:
                lines.append("- **Confluence Drivers:**")
                for c in res.confluence_factors:
                    lines.append(f"  * {c}")

            if res.risk_factors:
                lines.append("- **Risk & Caveats:**")
                for rf in res.risk_factors:
                    lines.append(f"  * {rf}")

            if res.trade_setup:
                ts = res.trade_setup
                entry_str = format_price(ts.entry_price)
                sl_str = format_price(ts.stop_loss)
                tp1_str = format_price(ts.take_profit_1)
                tp2_str = format_price(ts.take_profit_2)
                lines.append(
                    f"- **Trade Setup:** `{ts.direction}` @ `{entry_str}` | "
                    f"Stop: `{sl_str}` | TP1: `{tp1_str}` | TP2: `{tp2_str}` "
                    f"(R/R: `{ts.risk_reward_ratio:.1f}:1`)"
                )
            lines.append("")

        return "\n".join(lines)

    def to_dict(
        self,
        symbol_or_results: str | list[PatternConfidenceResult] | None = None,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        """Convert intelligence report to a complete dictionary."""
        symbol, tf, results = self._resolve_context(symbol_or_results, timeframe)
        return {
            "symbol": symbol,
            "timeframe": tf,
            "asset_type": self.asset_type,
            "market_summary": self.get_market_regime_summary(),
            "patterns": [r.to_dict() for r in results],
        }

    def to_json(
        self,
        symbol_or_results: str | list[PatternConfidenceResult] | None = None,
        timeframe: str | None = None,
        indent: int = 2,
    ) -> str:
        """Serialize intelligence report to JSON string."""
        return json.dumps(
            _json_safe(self.to_dict(symbol_or_results, timeframe)),
            indent=indent,
            default=str,
            # `_json_safe` removes every non-finite float, so this only guards against a
            # serialization path that slips past it: fail loudly instead of emitting
            # invalid JSON.
            allow_nan=False,
        )

    def to_llm_prompt(
        self,
        symbol_or_results: str | list[PatternConfidenceResult] | None = None,
        timeframe: str | None = None,
    ) -> str:
        """Create a tailored prompt for frontier reasoning LLMs (Claude 3.7 Sonnet, o3-mini, DeepSeek-R1, Gemini 2.5) to provide second-opinion analysis."""
        brief = self.generate_brief(symbol_or_results, timeframe)
        return (
            "You are a Senior Quantitative Portfolio Manager and Technical Analyst.\n"
            "Review the following algorithmic pattern detection and market intelligence report.\n"
            "Perform a rigorous step-by-step risk-adjusted evaluation:\n"
            "1. Evaluate whether the identified pattern setups offer positive expected value (+EV) under current trend regime.\n"
            "2. Assess macroeconomic, liquidity, volatility (Wilder ATR), and correlation risks.\n"
            "3. Critique the ATR-based stop-loss and take-profit targets, and recommend optimal risk-adjusted position sizing.\n\n"
            f"{brief}\n\n"
            "Provide your structured investment committee assessment with clear trade execution guidance."
        )
