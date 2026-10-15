"""AI-driven pattern confidence scorer and trade setup calculator."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from numbers import Integral
from typing import Any, cast

import numpy as np
import pandas as pd

from ..data import validate_ohlc

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        """Fallback StrEnum for Python < 3.11."""

        pass


_DATACLASS_SLOTS_FROZEN = (
    {"slots": True, "frozen": True} if sys.version_info >= (3, 10) else {"frozen": True}
)


class SignalGrade(StrEnum):
    """Categorical evaluation grade for pattern quality."""

    EXCELLENT = "EXCELLENT"  # >= 0.80
    STRONG = "STRONG"  # >= 0.70
    MODERATE = "MODERATE"  # >= 0.55
    WEAK = "WEAK"  # >= 0.40
    FALSE_SIGNAL = "FALSE"  # < 0.40

    @classmethod
    def from_score(cls, score: float) -> SignalGrade:
        """Map a confluence score in [0, 1] to its categorical grade.

        Single source of truth for the thresholds above, so a result built from a bare
        score can never disagree with the grade its score implies.
        """
        if score >= 0.80:
            return cls.EXCELLENT
        if score >= 0.70:
            return cls.STRONG
        if score >= 0.55:
            return cls.MODERATE
        if score >= 0.40:
            return cls.WEAK
        return cls.FALSE_SIGNAL


@dataclass(**_DATACLASS_SLOTS_FROZEN)
class TradeSetup:
    """Actionable trade setup parameters based on ATR and pattern invalidation."""

    direction: str  # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: float
    risk_per_unit: float
    rr_tp1: float = 1.5
    rr_tp2: float = 3.0

    def to_dict(self) -> dict[str, Any]:
        """Convert setup to plain dictionary."""
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "risk_reward_ratio": round(float(self.risk_reward_ratio), 2),
            "rr_tp1": round(float(self.rr_tp1), 2),
            "rr_tp2": round(float(self.rr_tp2), 2),
            "risk_per_unit": self.risk_per_unit,
        }


# Canonical candle lookback counts for TA-Lib candlestick patterns
PATTERN_CANDLE_COUNTS: dict[str, int] = {
    # 1-candle patterns
    "BELTHOLD": 1,
    "CLOSINGMARUBOZU": 1,
    "DOJI": 1,
    "DRAGONFLYDOJI": 1,
    "GRAVESTONEDOJI": 1,
    "HAMMER": 1,
    "HANGINGMAN": 1,
    "HIGHWAVE": 1,
    "INVERTEDHAMMER": 1,
    "LONGLEGGEDDOJI": 1,
    "LONGLINE": 1,
    "MARUBOZU": 1,
    "RICKSHAWMAN": 1,
    "SHOOTINGSTAR": 1,
    "SHORTLINE": 1,
    "SPINNINGTOP": 1,
    "TAKURI": 1,
    # 2-candle patterns
    "COUNTERATTACK": 2,
    "DARKCLOUDCOVER": 2,
    "DOJISTAR": 2,
    "ENGULFING": 2,
    "HARAMI": 2,
    "HARAMICROSS": 2,
    "HOMINGPIGEON": 2,
    "INNECK": 2,
    "KICKING": 2,
    "KICKINGBYLENGTH": 2,
    "MATCHINGLOW": 2,
    "ONNECK": 2,
    "PIERCING": 2,
    "SEPARATINGLINES": 2,
    "THRUSTING": 2,
    # 3-candle patterns
    "2CROWS": 3,
    "3BLACKCROWS": 3,
    "3INSIDE": 3,
    "3OUTSIDE": 3,
    "3STARSINSOUTH": 3,
    "3WHITESOLDIERS": 3,
    "ABANDONEDBABY": 3,
    "ADVANCEBLOCK": 3,
    "EVENINGDOJISTAR": 3,
    "EVENINGSTAR": 3,
    "GAPSIDESIDEWHITE": 3,
    "IDENTICAL3CROWS": 3,
    "MORNINGDOJISTAR": 3,
    "MORNINGSTAR": 3,
    "STALLEDPATTERN": 3,
    "STICKSANDWICH": 3,
    "TASUKIGAP": 3,
    "TRISTAR": 3,
    "UNIQUE3RIVER": 3,
    "UPSIDEGAP2CROWS": 3,
    "XSIDEGAP3METHODS": 3,
    # 4-candle patterns
    "3LINESTRIKE": 4,
    "CONCEALBABYSWALL": 4,
    # 5-candle patterns
    "BREAKAWAY": 5,
    "HIKKAKE": 5,
    "HIKKAKEMOD": 5,
    "LADDERBOTTOM": 5,
    "MATHOLD": 5,
    "RISEFALL3METHODS": 5,
}


def get_pattern_lookback(pattern_name: str) -> int:
    """Return candle lookback length (k) for a candlestick pattern (default 1)."""
    norm = pattern_name.upper().replace("CDL", "").strip()
    return PATTERN_CANDLE_COUNTS.get(norm, 1)


@dataclass(**_DATACLASS_SLOTS_FROZEN)
class PatternConfidenceResult:
    """Comprehensive AI evaluation of a detected candlestick pattern.

    Note: The score (`confluence_score` / `confidence_score`) is an interpretable, deterministic
    quantitative confluence heuristic (evaluating trend regime, volume expansion, RSI momentum,
    and candle geometry), not an uncalibrated historical win-rate probability.
    """

    pattern_name: str
    timestamp: pd.Timestamp
    raw_signal: int
    confidence_score: float | None = None
    grade: SignalGrade = SignalGrade.WEAK
    trend_regime: str = "NEUTRAL"
    rvol: float = 1.0
    rsi: float = 50.0
    atr: float = 0.0
    confluence_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    trade_setup: TradeSetup | None = None
    insufficient_history: bool = False
    confluence_score: float | None = None

    def __post_init__(self) -> None:
        conf = self.confidence_score
        confl = self.confluence_score

        if conf is not None and confl is not None:
            if not np.isfinite(conf) or isinstance(conf, bool):
                raise ValueError(f"confidence_score must be a finite number, got {conf}")
            if not np.isfinite(confl) or isinstance(confl, bool):
                raise ValueError(f"confluence_score must be a finite number, got {confl}")
            if not (0.0 <= conf <= 1.0):
                raise ValueError(f"confidence_score must be between 0.0 and 1.0, got {conf}")
            if not (0.0 <= confl <= 1.0):
                raise ValueError(f"confluence_score must be between 0.0 and 1.0, got {confl}")
            if abs(conf - confl) > 1e-6:
                raise ValueError(
                    f"Conflicting scores provided: confidence_score={conf} and "
                    f"confluence_score={confl}. They must be equal."
                )
            score = float(conf)
        elif conf is not None:
            if not np.isfinite(conf) or isinstance(conf, bool):
                raise ValueError(f"confidence_score must be a finite number, got {conf}")
            if not (0.0 <= conf <= 1.0):
                raise ValueError(f"confidence_score must be between 0.0 and 1.0, got {conf}")
            score = float(conf)
        elif confl is not None:
            if not np.isfinite(confl) or isinstance(confl, bool):
                raise ValueError(f"confluence_score must be a finite number, got {confl}")
            if not (0.0 <= confl <= 1.0):
                raise ValueError(f"confluence_score must be between 0.0 and 1.0, got {confl}")
            score = float(confl)
        else:
            score = 0.0

        object.__setattr__(self, "confidence_score", score)
        object.__setattr__(self, "confluence_score", score)

        # An explicit grade always wins, but a result constructed from a bare score must not
        # keep the dataclass default (WEAK) regardless of how high that score is. WEAK is
        # also the default value, so it is indistinguishable from "not supplied" here.
        if self.grade is SignalGrade.WEAK:
            object.__setattr__(self, "grade", SignalGrade.from_score(score))

    def to_dict(self) -> dict[str, Any]:
        """Convert result to a structured dictionary for JSON / LLM consumption."""
        return {
            "pattern": self.pattern_name,
            "timestamp": str(self.timestamp),
            "raw_signal": self.raw_signal,
            "confluence_score": round(float(self.confidence), 4),
            "confidence_score": round(float(self.confidence), 4),
            "grade": self.grade.value,
            "trend_regime": self.trend_regime,
            "insufficient_history": self.insufficient_history,
            "metrics": {
                "rvol": round(float(self.rvol), 2),
                "rsi": round(float(self.rsi), 2) if not np.isnan(self.rsi) else None,
                "atr": round(float(self.atr), 5) if not np.isnan(self.atr) else None,
            },
            "confluence_factors": self.confluence_factors,
            "risk_factors": self.risk_factors,
            "trade_setup": self.trade_setup.to_dict() if self.trade_setup else None,
        }

    @property
    def confidence(self) -> float:
        """Convenience alias for confidence_score / confluence_score."""
        return float(self.confidence_score if self.confidence_score is not None else 0.0)

    @property
    def confluence(self) -> float:
        """Convenience alias for confluence_score."""
        return float(self.confluence_score if self.confluence_score is not None else 0.0)

    @property
    def setup(self) -> TradeSetup | None:
        """Convenience alias for trade_setup."""
        return self.trade_setup

    @property
    def confluences(self) -> list[str]:
        """Convenience alias for confluence_factors."""
        return self.confluence_factors

    @property
    def risks(self) -> list[str]:
        """Convenience alias for risk_factors."""
        return self.risk_factors

    @property
    def action(self) -> str:
        """Trading action direction ('BUY', 'SELL', or 'HOLD')."""
        if self.trade_setup:
            return self.trade_setup.direction
        return "BUY" if self.raw_signal > 0 else ("SELL" if self.raw_signal < 0 else "HOLD")


def calc_wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI) using canonical Wilder's exponential smoothing.

    Initial bars before period remain NaN in accordance with TA-Lib lookback invariants.
    """
    if type(period) is not int or period <= 0:
        raise ValueError(f"Period must be a positive integer, got {period!r}")

    n = len(close)
    if n <= 1:
        return pd.Series(np.nan, index=close.index)

    if not np.all(np.isfinite(close.to_numpy())):
        raise ValueError("Input series contains NaN or infinite values")

    delta = close.diff().to_numpy()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    rsi = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return pd.Series(rsi, index=close.index)

    eff_period = period

    # First value at eff_period using SMA of initial gains/losses
    avg_g = float(np.mean(gain[1 : eff_period + 1]))
    avg_l = float(np.mean(loss[1 : eff_period + 1]))

    total = avg_g + avg_l
    if total == 0.0:
        rsi[eff_period] = 0.0
    else:
        rsi[eff_period] = 100.0 * (avg_g / total)

    for i in range(eff_period + 1, n):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
        total = avg_g + avg_l
        if total == 0.0:
            rsi[i] = 0.0
        else:
            rsi[i] = 100.0 * (avg_g / total)

    # Initial bars before eff_period remain NaN (zero future lookahead)
    return pd.Series(rsi, index=close.index)


def calc_wilder_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Calculate Average True Range (ATR) using canonical Wilder's exponential smoothing.

    Initial bars before index period remain NaN in accordance with TA-Lib lookback invariants.
    """
    if type(period) is not int or period <= 0:
        raise ValueError(f"Period must be a positive integer, got {period!r}")
    if len(high) != len(low) or len(high) != len(close):
        raise ValueError(
            f"Input Series must have identical lengths: high={len(high)}, low={len(low)}, close={len(close)}"
        )
    if not (high.index.equals(low.index) and high.index.equals(close.index)):
        raise ValueError("Input Series high, low, close must have aligned indices.")

    n = len(close)
    if n == 0:
        return pd.Series(dtype=np.float64)

    h = high.to_numpy()
    lo = low.to_numpy()
    c = close.to_numpy()

    if not (np.all(np.isfinite(h)) and np.all(np.isfinite(lo)) and np.all(np.isfinite(c))):
        raise ValueError("Input series contains NaN or infinite values")

    tr = np.zeros(n, dtype=np.float64)
    tr[0] = h[0] - lo[0]
    for i in range(1, n):
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))

    atr = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return pd.Series(atr, index=close.index)

    # First value at index period using mean of TR[1 : period + 1] matching TA-Lib
    atr[period] = float(np.mean(tr[1 : period + 1]))
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    # Initial bars before index period remain NaN (zero future lookahead)
    return pd.Series(atr, index=close.index)


class AIPatternScorer:
    """Interpretable multi-factor technical confluence scoring engine for candlestick patterns.

    Evaluates market context (Trend regime, RVOL volume expansion, Wilder RSI momentum,
    and ATR volatility) to transform discrete TA-Lib signals (+100/-100) into calibrated
    confluence scores (0.0 to 1.0) and actionable risk-managed trade setups.

    Note: This is an interpretable, deterministic quantitative confluence heuristic assessing
    indicator alignment, not an uncalibrated historical win-rate probability model.
    """

    def __init__(self, data: pd.DataFrame) -> None:
        """Initialize scorer with OHLCV market data."""
        if data.empty or len(data) < 5:
            raise ValueError("Data must contain at least 5 candles for technical context.")

        for col in ("Open", "High", "Low", "Close"):
            if col not in data.columns:
                raise ValueError(f"DataFrame must contain required column: {col}")

        if hasattr(data.index, "has_duplicates") and data.index.has_duplicates:
            raise ValueError("DataFrame index contains duplicate timestamps")

        if not data.index.is_monotonic_increasing:
            raise ValueError("DataFrame index must be monotonically increasing")

        ohlc_values = data[["Open", "High", "Low", "Close"]].to_numpy()
        if not np.all(np.isfinite(ohlc_values)):
            raise ValueError("DataFrame contains NaN or infinite values in OHLC")

        # Strict validation of OHLC structure and geometry
        self.df = validate_ohlc(data.copy(), strict=True)
        self._calculate_technical_indicators()

    def _calculate_technical_indicators(self) -> None:
        """Vectorized computation of EMA, Wilder RSI, Wilder ATR, and shifted RVOL indicators."""
        close = self.df["Close"]
        high = self.df["High"]
        low = self.df["Low"]

        # EMAs for Trend Regime
        # Standard lookbacks: 20, 50, and 200 bars. Initial bars before min_periods remain NaN.
        self.df["_EMA20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
        self.df["_EMA50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
        self.df["_EMA200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()

        # Average True Range (Wilder's ATR 14 - Issue 15)
        self.df["_ATR14"] = calc_wilder_atr(high, low, close, period=14)

        # Relative Strength Index (Wilder's RSI 14 - Issue 14)
        self.df["_RSI14"] = calc_wilder_rsi(close, period=14)

        # Relative Volume (RVOL 20 - strictly historical, excludes current candle from baseline)
        if "Volume" in self.df.columns:
            vol = self.df["Volume"].astype("float64")
            if not np.isfinite(vol).all() or (vol < 0).any():
                raise ValueError("Invalid Volume values: volume must be finite and non-negative")
            if (vol > 0).any():
                prev_vol = vol.shift(1).copy()
                if len(prev_vol) > 0:
                    prev_vol.iloc[0] = vol.iloc[0]
                prev_vol = prev_vol.ffill().fillna(vol)
                avg_vol = prev_vol.rolling(window=min(20, len(self.df)), min_periods=1).mean()
                self.df["_RVOL"] = (vol / avg_vol.replace(0, np.nan)).fillna(1.0)
            else:
                self.df["_RVOL"] = 1.0
        else:
            self.df["_RVOL"] = 1.0

    def _determine_trend_regime(
        self, close: float, ema20: float, ema50: float, ema200: float
    ) -> str:
        """Classify prevailing trend regime."""
        has_200 = not np.isnan(ema200)
        has_50 = not np.isnan(ema50)
        has_20 = not np.isnan(ema20)

        if has_200 and has_50 and has_20:
            if close > ema20 > ema50 > ema200:
                return "STRONG_BULLISH"
            elif close > ema50 and ema50 >= ema200:
                return "BULLISH"
            elif close < ema20 < ema50 < ema200:
                return "STRONG_BEARISH"
            elif close < ema50 and ema50 <= ema200:
                return "BEARISH"
            return "NEUTRAL"
        elif has_50 and has_20:
            if close > ema20 > ema50:
                return "STRONG_BULLISH"
            elif close > ema50:
                return "BULLISH"
            elif close < ema20 < ema50:
                return "STRONG_BEARISH"
            elif close < ema50:
                return "BEARISH"
            return "NEUTRAL"
        elif has_20:
            if close > ema20:
                return "BULLISH"
            elif close < ema20:
                return "BEARISH"
            return "NEUTRAL"
        return "NEUTRAL"

    def score_signal(
        self,
        pattern_name: str,
        timestamp: pd.Timestamp,
        raw_signal: int,
    ) -> PatternConfidenceResult:
        """Compute multi-factor AI confidence score and trade setup for a specific signal."""
        if raw_signal == 0:
            raise ValueError(
                "Cannot score an inactive signal: raw_signal must be non-zero (+100/-100 or +1/-1)."
            )

        if timestamp not in self.df.index:
            raise KeyError(f"Timestamp {timestamp} not found in market data.")

        row = self.df.loc[timestamp]
        close = float(cast(Any, row["Close"]))
        high = float(cast(Any, row["High"]))
        low = float(cast(Any, row["Low"]))
        open_val = float(cast(Any, row["Open"]))
        ema20 = float(cast(Any, row["_EMA20"]))
        ema50 = float(cast(Any, row["_EMA50"]))
        ema200 = float(cast(Any, row["_EMA200"]))

        raw_atr = float(cast(Any, row["_ATR14"]))
        insufficient_history = bool(np.isnan(raw_atr))
        min_atr = close * 1e-4
        atr = np.nan if insufficient_history else max(raw_atr, min_atr)

        raw_rsi = float(cast(Any, row["_RSI14"]))
        rsi = raw_rsi if not np.isnan(raw_rsi) else 50.0

        rvol = float(cast(Any, row["_RVOL"]))

        trend = self._determine_trend_regime(close, ema20, ema50, ema200)
        confluence_factors: list[str] = []
        risk_factors: list[str] = []

        if insufficient_history:
            risk_factors.append(
                "Insufficient History: Indicators not warmed up (< 14 bars); estimated values used."
            )

        # Baseline confluence score
        confidence = 0.50
        is_bullish = raw_signal > 0

        # Factor 1: Trend Alignment
        if is_bullish:
            if trend in ("STRONG_BULLISH", "BULLISH"):
                confidence += 0.15
                confluence_factors.append(
                    f"Trend Alignment: Bullish pattern aligned with {trend} market"
                )
            elif trend in ("STRONG_BEARISH", "BEARISH"):
                confidence -= 0.15
                risk_factors.append(
                    f"Counter-Trend Warning: Bullish signal against prevailing {trend} trend"
                )
            else:
                confluence_factors.append("Market Regime: Neutral consolidation")
        else:
            if trend in ("STRONG_BEARISH", "BEARISH"):
                confidence += 0.15
                confluence_factors.append(
                    f"Trend Alignment: Bearish pattern aligned with {trend} market"
                )
            elif trend in ("STRONG_BULLISH", "BULLISH"):
                confidence -= 0.15
                risk_factors.append(
                    f"Counter-Trend Warning: Bearish signal against prevailing {trend} trend"
                )
            else:
                confluence_factors.append("Market Regime: Neutral consolidation")

        # Factor 2: Volume Confirmation (RVOL)
        has_volume = "Volume" in self.df.columns and (self.df["Volume"] > 0).any()
        if has_volume:
            if rvol >= 1.5:
                confidence += 0.15
                confluence_factors.append(
                    f"Volume Surge: Relative volume {rvol:.2f}x signals strong institutional interest"
                )
            elif rvol >= 1.1:
                confidence += 0.05
                confluence_factors.append(
                    f"Volume Confirmation: Above average volume ({rvol:.2f}x)"
                )
            elif rvol < 0.75:
                confidence -= 0.10
                risk_factors.append(
                    f"Low Volume Warning: Subdued volume ({rvol:.2f}x) suggests lack of conviction"
                )

        # Factor 3: Momentum & Exhaustion (RSI 14)
        if not np.isnan(raw_rsi):
            if is_bullish:
                if 30 <= rsi <= 45:
                    confidence += 0.10
                    confluence_factors.append(
                        f"Momentum Reset: RSI at {rsi:.1f} indicates prime dip-buying territory"
                    )
                elif rsi < 30:
                    confidence += 0.08
                    confluence_factors.append(
                        f"Oversold Rebound: RSI at {rsi:.1f} supports potential reversal"
                    )
                elif rsi > 75:
                    confidence -= 0.15
                    risk_factors.append(
                        f"Overbought Warning: RSI at {rsi:.1f} signals exhaustion risk"
                    )
            else:
                if 55 <= rsi <= 70:
                    confidence += 0.10
                    confluence_factors.append(
                        f"Bearish Continuation: RSI at {rsi:.1f} shows sustained selling momentum"
                    )
                elif rsi > 70:
                    confidence += 0.08
                    confluence_factors.append(
                        f"Overbought Rejection: RSI at {rsi:.1f} supports bearish reversal"
                    )
                elif rsi < 25:
                    confidence -= 0.15
                    risk_factors.append(
                        f"Oversold Risk: RSI at {rsi:.1f} warns of potential snapback"
                    )

        # Factor 4: Candle Geometry & Range Significance
        candle_range = high - low
        body_size = abs(close - open_val)
        if not np.isnan(atr):
            if candle_range > 1.2 * atr:
                confidence += 0.05
                confluence_factors.append(
                    f"High Volatility Expansion: Candle range ({candle_range:.4f}) exceeds 1.2x ATR"
                )
            elif candle_range < 0.4 * atr:
                confidence -= 0.05
                risk_factors.append("Low Range Anomaly: Candle range compressed below 0.4x ATR")

        if candle_range > 0 and (body_size / candle_range) >= 0.60:
            confidence += 0.03
            confluence_factors.append(
                f"Decisive Candle Body: Body covers {(body_size / candle_range) * 100:.1f}% of range"
            )

        # Clamp confidence to [0.05, 0.98]
        final_confidence = max(0.05, min(0.98, confidence))

        # Categorize Signal Grade
        grade = SignalGrade.from_score(final_confidence)

        # Determine pattern lookback window to find true pattern high/low extremes
        k = get_pattern_lookback(pattern_name)
        loc = self.df.index.get_loc(timestamp)
        idx = (
            int(loc)
            if isinstance(loc, (int, np.integer))
            else int(loc.start if isinstance(loc, slice) else 0)
        )
        start_idx = max(0, idx - k + 1)
        window = self.df.iloc[start_idx : idx + 1]
        pattern_high = float(cast(Any, window["High"]).max())
        pattern_low = float(cast(Any, window["Low"]).min())

        # Calculate Actionable Trade Setup (omitted if indicators not warmed up)
        trade_setup = (
            None
            if insufficient_history
            else self._build_trade_setup(
                is_bullish,
                close,
                high,
                low,
                atr,
                pattern_high=pattern_high,
                pattern_low=pattern_low,
            )
        )

        return PatternConfidenceResult(
            pattern_name=pattern_name.replace("CDL", ""),
            timestamp=timestamp,
            raw_signal=raw_signal,
            confidence_score=final_confidence,
            grade=grade,
            trend_regime=trend,
            rvol=rvol,
            rsi=rsi,
            atr=atr,
            confluence_factors=confluence_factors,
            risk_factors=risk_factors,
            trade_setup=trade_setup,
            insufficient_history=insufficient_history,
        )

    def _build_trade_setup(
        self,
        is_bullish: bool,
        close: float,
        high: float,
        low: float,
        atr: float,
        pattern_high: float | None = None,
        pattern_low: float | None = None,
    ) -> TradeSetup:
        """Construct ATR-governed entry, stop loss, and tiered profit targets."""
        buffer = 0.2 * atr
        rr_tp1 = 1.5
        rr_tp2 = 3.0

        p_low = pattern_low if pattern_low is not None else low
        p_high = pattern_high if pattern_high is not None else high

        if is_bullish:
            direction = "BUY"
            entry = close
            # Stop below the lowest low of the pattern formation minus ATR buffer, strictly positive
            stop_loss = max(p_low - buffer, close * 0.001)
            risk = max(entry - stop_loss, close * 1e-5)
            tp1 = entry + (rr_tp1 * risk)
            tp2 = entry + (rr_tp2 * risk)
            actual_rr_tp1 = (tp1 - entry) / risk if risk > 0 else rr_tp1
            actual_rr_tp2 = (tp2 - entry) / risk if risk > 0 else rr_tp2
        else:
            direction = "SELL"
            entry = close
            # Stop above the highest high of the pattern formation plus ATR buffer
            stop_loss = p_high + buffer
            risk = max(stop_loss - entry, close * 1e-5)
            # Ensure profit targets remain strictly positive
            tp1 = max(entry - (rr_tp1 * risk), close * 0.001)
            tp2 = max(entry - (rr_tp2 * risk), close * 0.0005)
            actual_rr_tp1 = max(entry - tp1, 0.0) / risk if risk > 0 else 0.0
            actual_rr_tp2 = max(entry - tp2, 0.0) / risk if risk > 0 else 0.0

        actual_rr_tp1 = round(float(actual_rr_tp1), 2)
        actual_rr_tp2 = round(float(actual_rr_tp2), 2)

        return TradeSetup(
            direction=direction,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward_ratio=actual_rr_tp1,
            risk_per_unit=risk,
            rr_tp1=actual_rr_tp1,
            rr_tp2=actual_rr_tp2,
        )

    def score_all_signals(
        self,
        signals: pd.Series,
        pattern_name: str,
        min_confidence: float = 0.0,
    ) -> list[PatternConfidenceResult]:
        """Evaluate a series of TA-Lib signals and return sorted scored results."""
        if not np.isfinite(min_confidence) or not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be finite and within [0, 1]")

        results: list[PatternConfidenceResult] = []
        for ts, raw_sig in signals.items():
            if raw_sig == 0:
                continue
            scored = self.score_signal(pattern_name, pd.to_datetime(cast(Any, ts)), int(raw_sig))
            if scored.confidence >= min_confidence:
                results.append(scored)

        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def score_all_active(
        self,
        min_confidence: float = 0.5,
        patterns: list[str] | None = None,
        date: str | None = None,
        lookback_bars: int | None = 1,
    ) -> list[PatternConfidenceResult]:
        """Scan data for active patterns and return scored results above min_confidence.

        Args:
            min_confidence: Threshold between 0.0 and 1.0 to filter low-conviction signals.
            patterns: Optional list of pattern names (e.g. ['CDLHAMMER', 'CDLENGULFING']).
                      If None, scans all available patterns.
            date: Optional date filter string.
            lookback_bars: Number of most recent bars to evaluate for active signals.
                           Defaults to 1 (evaluating only the latest closed candle).
                           Set to None for full historical scanning.

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

        from ..pattern_analyzer import PatternAnalyzer

        analyzer = PatternAnalyzer(self.df)
        patterns_to_scan = patterns if patterns is not None else sorted(analyzer.pattern_functions)

        all_scored: list[PatternConfidenceResult] = []
        for pat in patterns_to_scan:
            try:
                signals = analyzer.get_signals(pat, date=date)
            except NotImplementedError:
                continue
            if signals.empty:
                continue
            if date is None and lookback_bars is not None:
                signals = signals.reindex(self.df.index[-lookback_bars:]).dropna()
                if signals.empty:
                    continue
            clean_name = pat.replace("CDL", "")
            scored = self.score_all_signals(signals, clean_name, min_confidence=min_confidence)
            all_scored.extend(scored)

        all_scored.sort(key=lambda r: r.confidence, reverse=True)
        return all_scored

    def score_all_history(
        self,
        min_confidence: float = 0.5,
        patterns: list[str] | None = None,
        date: str | None = None,
    ) -> list[PatternConfidenceResult]:
        """Scan entire data history for pattern signals without lookback restriction.

        Args:
            min_confidence: Threshold between 0.0 and 1.0 to filter low-conviction signals.
            patterns: Optional list of pattern names. If None, scans all available patterns.
            date: Optional date filter string.

        Returns:
            List of PatternConfidenceResult across full history sorted by confidence score descending.
        """
        return self.score_all_active(
            min_confidence=min_confidence,
            patterns=patterns,
            date=date,
            lookback_bars=None,
        )
