"""Pattern Analyzer for detecting TA-Lib candlestick patterns in market data."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

try:
    import numpy as np
except (ImportError, ModuleNotFoundError, RuntimeError, TypeError):  # pragma: no cover
    from yfinance_ta_patterns.talib_compat import np  # type: ignore[no-redef]

try:
    import pandas as pd

    HAS_PANDAS = True
except (ImportError, ModuleNotFoundError, RuntimeError, TypeError):  # pragma: no cover
    pd = None  # type: ignore[assignment]
    HAS_PANDAS = False

from yfinance_ta_patterns.talib_compat import (
    HAS_NATIVE_TALIB,
    SUPPORTED_FALLBACK_PATTERNS,
    UNSUPPORTED_FALLBACK_PATTERNS,
    talib,
)


class PatternAnalyzer:
    """Analyze OHLC market data for TA-Lib candlestick patterns."""

    data: Any

    def __init__(self, data: Any) -> None:
        if HAS_PANDAS:
            if not isinstance(data, pd.DataFrame):
                raise TypeError("data must be a pandas DataFrame")

            required = {"Open", "High", "Low", "Close"}
            missing = required.difference(data.columns)
            if missing:
                raise ValueError(f"Missing OHLC columns: {sorted(missing)}")

            if not data.empty:
                for col in ("Open", "High", "Low", "Close"):
                    if not pd.api.types.is_numeric_dtype(data[col]):
                        raise TypeError(f"Column '{col}' must be numeric")
                    if hasattr(np, "isfinite") and not np.isfinite(data[col]).all():
                        raise ValueError("OHLC data contains non-finite values (NaN or inf)")

                if data.index.has_duplicates:
                    raise ValueError("Index contains duplicate timestamps")
                if not data.index.is_monotonic_increasing:
                    raise ValueError("Index must be monotonically sorted in chronological order")

            self.data = data
        else:
            if not isinstance(data, dict):
                raise TypeError("data must be a mapping with OHLC keys")
            required = {"Open", "High", "Low", "Close"}
            missing = required.difference(data.keys())
            if missing:
                raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
            self.data = data
        funcs: list[str] = (
            [f for f in dir(talib) if f.startswith("CDL")]
            if HAS_NATIVE_TALIB
            else sorted(SUPPORTED_FALLBACK_PATTERNS)
        )
        self.pattern_functions: list[str] = funcs

    @classmethod
    def get_supported_fallback_patterns(cls) -> list[str]:
        """Return canonical list of patterns supported in pure-Python fallback mode."""
        return sorted(SUPPORTED_FALLBACK_PATTERNS)

    def _normalize_pattern(self, pattern: str) -> str:
        pattern_upper = pattern.upper()
        return pattern_upper if pattern_upper.startswith("CDL") else f"CDL{pattern_upper}"

    def _normalize_dates(
        self,
        date: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[pd.Timestamp | None, pd.Timestamp | None, pd.Timestamp | None]:
        idx = self.data.index
        tz = idx.tz if isinstance(idx, pd.DatetimeIndex) else None

        def convert(dt: str | None) -> pd.Timestamp | None:
            if dt is None:
                return None
            parsed = pd.to_datetime(dt)
            if parsed.tzinfo is not None and tz is None:
                raise ValueError("Timezone-aware date requires a timezone-aware index")
            if parsed.tzinfo is None and tz is not None:
                parsed = parsed.tz_localize(tz)
            elif parsed.tzinfo is not None and tz is not None:
                parsed = parsed.tz_convert(tz)
            return parsed.normalize()

        return convert(date), convert(start_date), convert(end_date)

    def get_signals(
        self,
        pattern: str,
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.Series:
        """Return non-zero signals for a single candlestick pattern with optional date filters."""
        normalized = self._normalize_pattern(pattern)
        if normalized not in self.pattern_functions:
            if not HAS_NATIVE_TALIB and normalized in UNSUPPORTED_FALLBACK_PATTERNS:
                raise NotImplementedError(
                    f"Candlestick pattern '{pattern}' requires native TA-Lib binary. "
                    f"Pure-Python fallback is currently implemented for {len(SUPPORTED_FALLBACK_PATTERNS)} patterns: "
                    f"{', '.join(sorted(p.replace('CDL', '') for p in SUPPORTED_FALLBACK_PATTERNS))}."
                )
            available = ", ".join(p.replace("CDL", "") for p in sorted(self.pattern_functions))
            raise ValueError(f"Unknown pattern '{pattern}'. Available: {available}")

        if not (HAS_PANDAS and isinstance(self.data, pd.DataFrame)):
            pattern_func = getattr(talib, normalized)
            result = pattern_func(
                self.data["Open"],
                self.data["High"],
                self.data["Low"],
                self.data["Close"],
            )
            res_list = result.tolist() if hasattr(result, "tolist") else list(result)
            return {i: val for i, val in enumerate(res_list) if val != 0}  # type: ignore[return-value]

        if self.data.empty:
            return pd.Series(dtype=int, name=normalized)

        pattern_func = getattr(talib, normalized)
        result = pattern_func(
            self.data["Open"],
            self.data["High"],
            self.data["Low"],
            self.data["Close"],
        )
        series = pd.Series(result, index=self.data.index, name=normalized)
        signals = series[series != 0]

        target_date, start_dt, end_dt = self._normalize_dates(date, start_date, end_date)

        if target_date is not None or start_dt is not None or end_dt is not None:
            idx = signals.index
            day_index = (
                idx.normalize()
                if isinstance(idx, pd.DatetimeIndex)
                else pd.to_datetime(idx).normalize()
            )

            if target_date is not None:
                signals = signals[day_index == target_date]
            else:
                mask = pd.Series(True, index=signals.index)
                if start_dt is not None:
                    mask &= day_index >= start_dt
                if end_dt is not None:
                    mask &= day_index <= end_dt
                signals = signals[mask]

        return cast(pd.Series, signals)

    def analyze_all_for_date(self, date: str) -> Iterable[str]:
        """Analyze and format pattern signals for a specific date."""
        messages: list[str] = []
        for pattern in sorted(self.pattern_functions):
            try:
                signals = self.get_signals(pattern, date=date)
                pattern_name = pattern.replace("CDL", "")
                has_signals = not signals.empty if hasattr(signals, "empty") else len(signals) > 0
                if has_signals:
                    sig_repr = (
                        signals.to_string() if hasattr(signals, "to_string") else str(signals)
                    )
                    messages.append(f"{pattern_name} on {date} (non-zero values):\n{sig_repr}")
                else:
                    messages.append(f"{pattern_name} on {date}: all values are 0")
            except Exception as exc:
                messages.append(f"Error in {pattern}: {exc}")
        return messages
