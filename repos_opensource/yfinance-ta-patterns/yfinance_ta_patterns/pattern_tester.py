"""Pattern ranking tester - find best performing candlestick patterns."""

from __future__ import annotations

import datetime
import logging
import warnings
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd
import pytz

from yfinance_ta_patterns.data import (
    classify_asset,
    normalize_interval,
    resolve_asset_currencies,
    validate_asset_type,
)
from yfinance_ta_patterns.talib_compat import talib

logger = logging.getLogger(__name__)

# Standard annual periods for timeframe-aware Sharpe Ratio calculation (Equities: 252 days, 6.5h session -> 7 observations/day)
TIMEFRAME_PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 252.0 * 390.0,  # 98,280 periods/year
    "2m": 252.0 * 195.0,  # 49,140 periods/year
    "5m": 252.0 * 78.0,  # 19,656 periods/year
    "15m": 252.0 * 26.0,  # 6,552 periods/year
    "30m": 252.0 * 13.0,  # 3,276 periods/year
    "60m": 252.0
    * 7.0,  # 1,764 periods/year (7 hourly candles/day: 9:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30)
    "1h": 252.0 * 7.0,  # 1,764 periods/year
    "90m": 252.0 * 5.0,  # 1,260 periods/year (5 observations/day: 9:30, 11:00, 12:30, 14:00, 15:30)
    "4h": 252.0 * 2.0,  # 504 periods/year
    "1d": 252.0,  # 252 trading days/year
    "5d": 252.0 / 5.0,  # 50.4 periods/year
    "1wk": 52.0,  # 52 weeks/year
    "1mo": 12.0,  # 12 months/year
    "3mo": 4.0,  # 4 quarters/year
}

# 24/7 continuous markets (Cryptocurrency: 365 days, 24h = 8,760 hours/year)
CRYPTO_PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 365.0 * 1440.0,  # 525,600 periods/year
    "2m": 365.0 * 720.0,  # 262,800 periods/year
    "5m": 365.0 * 288.0,  # 105,120 periods/year
    "15m": 365.0 * 96.0,  # 35,040 periods/year
    "30m": 365.0 * 48.0,  # 17,520 periods/year
    "60m": 365.0 * 24.0,  # 8,760 periods/year
    "1h": 365.0 * 24.0,  # 8,760 periods/year
    "90m": 365.0 * 16.0,  # 5,840 periods/year
    "4h": 365.0 * 6.0,  # 2,190 periods/year
    "1d": 365.0,  # 365 days/year
    "5d": 365.0 / 5.0,  # 73 periods/year
    "1wk": 52.0,
    "1mo": 12.0,
    "3mo": 4.0,
}

# 24/5 continuous markets (Forex: ~260 trading days, 24h = 6,240 hours/year)
FOREX_PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 260.0 * 1440.0,  # 374,400 periods/year
    "2m": 260.0 * 720.0,  # 187,200 periods/year
    "5m": 260.0 * 288.0,  # 74,880 periods/year
    "15m": 260.0 * 96.0,  # 24,960 periods/year
    "30m": 260.0 * 48.0,  # 12,480 periods/year
    "60m": 260.0 * 24.0,  # 6,240 periods/year
    "1h": 260.0 * 24.0,  # 6,240 periods/year
    "90m": 260.0 * 16.0,  # 4,160 periods/year
    "4h": 260.0 * 6.0,  # 1,560 periods/year
    "1d": 260.0,  # 260 days/year
    "5d": 260.0 / 5.0,  # 52 periods/year
    "1wk": 52.0,
    "1mo": 12.0,
    "3mo": 4.0,
}

# Tokyo Stock Exchange (.T): 245 trading days/year
# 09:00-11:30 (150m) + 12:30-15:30 (180m from 2024-11-05) = 330m/day
TOKYO_PERIODS_PER_YEAR_POST_2024: dict[str, float] = {
    "1m": 245.0 * 330.0,  # 80,850.0
    "2m": 245.0 * 165.0,  # 40,425.0
    "5m": 245.0 * 66.0,  # 16,170.0
    "15m": 245.0 * 22.0,  # 5,390.0
    "30m": 245.0 * 11.0,  # 2,695.0
    "60m": 245.0 * 6.0,  # 1,470.0
    "1h": 245.0 * 6.0,  # 1,470.0
    "90m": 245.0 * 4.0,  # 980.0
    "4h": 245.0 * 2.0,  # 490.0
    "1d": 245.0,
    "5d": 245.0 / 5.0,  # 49.0
    "1wk": 52.0,
    "1mo": 12.0,
    "3mo": 4.0,
}

# Prior to 2024-11-05: 09:00-11:30 (150m) + 12:30-15:00 (150m) = 300m/day
TOKYO_PERIODS_PER_YEAR_PRE_2024: dict[str, float] = {
    "1m": 245.0 * 300.0,  # 73,500.0
    "2m": 245.0 * 150.0,  # 36,750.0
    "5m": 245.0 * 60.0,  # 14,700.0
    "15m": 245.0 * 20.0,  # 4,900.0
    "30m": 245.0 * 10.0,  # 2,450.0
    "60m": 245.0 * 5.0,  # 1,225.0
    "1h": 245.0 * 5.0,  # 1,225.0
    "90m": 245.0 * 4.0,  # 980.0
    "4h": 245.0 * 2.0,  # 490.0
    "1d": 245.0,
    "5d": 245.0 / 5.0,  # 49.0
    "1wk": 52.0,
    "1mo": 12.0,
    "3mo": 4.0,
}

# Hong Kong Stock Exchange (.HK): 250 trading days/year
# 09:30-12:00 (150m) + 13:00-16:00 (180m) = 330m/day
HONG_KONG_PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 250.0 * 330.0,  # 82,500.0
    "2m": 250.0 * 165.0,  # 41,250.0
    "5m": 250.0 * 66.0,  # 16,500.0
    "15m": 250.0 * 22.0,  # 5,500.0
    "30m": 250.0 * 11.0,  # 2,750.0
    "60m": 250.0 * 6.0,  # 1,500.0
    "1h": 250.0 * 6.0,  # 1,500.0
    "90m": 250.0 * 4.0,  # 1,000.0
    "4h": 250.0 * 2.0,  # 500.0
    "1d": 250.0,
    "5d": 250.0 / 5.0,  # 50.0
    "1wk": 52.0,
    "1mo": 12.0,
    "3mo": 4.0,
}

# Default baseline FX exchange rates to USD for major cross currencies
DEFAULT_FX_USD_RATES: dict[str, float] = {
    "EURUSD": 1.08,
    "GBPUSD": 1.28,
    "AUDUSD": 0.65,
    "NZDUSD": 0.60,
    "USDJPY": 150.0,
    "USDCHF": 0.89,
    "USDCAD": 1.37,
    "USDILS": 3.70,
    "USDZAR": 18.0,
}


def is_crypto_symbol(symbol: str) -> bool:
    """Identify if a symbol is a 24/7 cryptocurrency."""
    if not symbol:
        return False
    return classify_asset(symbol) == "crypto"


def is_forex_symbol(symbol: str) -> bool:
    """Identify if a symbol is a 24/5 Forex pair."""
    if not symbol:
        return False
    return classify_asset(symbol) == "forex"


def resolve_periods_per_year(
    timeframe: str,
    symbol: str = "",
    asset_type: str = "auto",
    periods_per_year: float | None = None,
    as_of_date: datetime.date | None = None,
    date_range: tuple[datetime.date, datetime.date] | None = None,
) -> float:
    """Resolve asset-aware annualization factor for Sharpe Ratio calculation."""
    if periods_per_year is not None:
        if not np.isfinite(periods_per_year) or periods_per_year <= 0:
            raise ValueError(
                f"periods_per_year must be positive and finite, got {periods_per_year}"
            )
        return float(periods_per_year)

    tf = normalize_interval(timeframe) if timeframe else "1d"
    a_type = validate_asset_type(asset_type)
    clean_sym = symbol.strip().upper() if symbol else ""

    if a_type == "crypto":
        return CRYPTO_PERIODS_PER_YEAR.get(tf, 365.0)
    elif a_type == "forex":
        return FOREX_PERIODS_PER_YEAR.get(tf, 260.0)
    elif a_type == "auto" and clean_sym:
        detected = classify_asset(clean_sym, asset_type=asset_type)
        if detected == "crypto":
            return CRYPTO_PERIODS_PER_YEAR.get(tf, 365.0)
        elif detected == "forex":
            return FOREX_PERIODS_PER_YEAR.get(tf, 260.0)

    # Asian exchanges
    if clean_sym.endswith(".T"):
        split_d = datetime.date(2024, 11, 5)
        pre_factor = TOKYO_PERIODS_PER_YEAR_PRE_2024.get(tf, 245.0)
        post_factor = TOKYO_PERIODS_PER_YEAR_POST_2024.get(tf, 245.0)
        if date_range is not None:
            start_d, end_d = date_range
            if end_d < split_d:
                return pre_factor
            if start_d >= split_d:
                return post_factor
            # Mixed period spanning across 2024-11-05
            days_pre = max((split_d - start_d).days, 0)
            days_post = max((end_d - split_d).days + 1, 0)
            total_days = days_pre + days_post
            if total_days > 0:
                w_pre = days_pre / total_days
                w_post = days_post / total_days
                return float(w_pre * pre_factor + w_post * post_factor)
            return post_factor
        if as_of_date is not None:
            if as_of_date < split_d:
                return pre_factor
            return post_factor
        return post_factor

    if clean_sym.endswith(".HK"):
        return HONG_KONG_PERIODS_PER_YEAR.get(tf, 250.0)

    # Equities / stocks
    # LSE (.L) or European exchanges (.DE, .PA, etc.) have 8.5h trading sessions
    if clean_sym.endswith(".L") or any(
        clean_sym.endswith(sfx)
        for sfx in (
            ".DE",
            ".PA",
            ".AS",
            ".BR",
            ".LS",
            ".MI",
            ".MC",
            ".VI",
            ".HE",
            ".F",
            ".AT",
            ".SW",
            ".ST",
            ".OL",
            ".CO",
        )
    ):
        if tf in ("1h", "60m"):
            return (
                252.0 * 9.0
            )  # 2,268 periods/year (9 hourly observations/day: 8:00-16:00 + 16:00-16:30)
        if tf == "4h":
            return (
                252.0 * 3.0
            )  # 756.0 periods/year (3 resampled 4h bars/day: 08:00-12:00, 12:00-16:00, 16:00-20:00)
        if tf == "90m":
            return 252.0 * 6.0  # 1,512 periods/year (6 observations/day: 5 full 90m + 1 60m)
        if tf == "30m":
            return 252.0 * 17.0  # 4,284 periods/year
        if tf == "15m":
            return 252.0 * 34.0  # 8,568 periods/year
        if tf == "5m":
            return 252.0 * 102.0  # 25,704 periods/year (102 observations/day)
        if tf == "2m":
            return 252.0 * 255.0  # 64,260 periods/year (255 observations/day)
        if tf == "1m":
            return 252.0 * 510.0  # 128,520 periods/year (510 observations/day)

    return TIMEFRAME_PERIODS_PER_YEAR.get(tf, 252.0)


# Candlestick patterns representing market indecision/hesitation rather than directional momentum
INDECISION_PATTERNS: frozenset[str] = frozenset(
    {
        "CDLDOJI",
        "CDLLONGLEGGEDDOJI",
        "CDLRICKSHAWMAN",
        "CDLHIGHWAVE",
        "CDLSPINNINGTOP",
    }
)

# Candlestick patterns capable of generating both bullish (+100) and bearish (-100) signals
TWO_WAY_PATTERNS: frozenset[str] = frozenset(
    {
        "CDL2CROWS",
        "CDL3BLACKCROWS",
        "CDL3INSIDE",
        "CDL3LINESTRIKE",
        "CDL3OUTSIDE",
        "CDL3STARSINSOUTH",
        "CDL3WHITESOLDIERS",
        "CDLABANDONEDBABY",
        "CDLADVANCEBLOCK",
        "CDLBELTHOLD",
        "CDLBREAKAWAY",
        "CDLCLOSINGMARUBOZU",
        "CDLCONCEALBABYSWALL",
        "CDLCOUNTERATTACK",
        "CDLDARKCLOUDCOVER",
        "CDLDOJISTAR",
        "CDLENGULFING",
        "CDLEVENINGDOJISTAR",
        "CDLEVENINGSTAR",
        "CDLGAPSIDESIDEWHITE",
        "CDLHARAMI",
        "CDLHARAMICROSS",
        "CDLHIKKAKE",
        "CDLHIKKAKEMOD",
        "CDLHOMINGPIGEON",
        "CDLINNECK",
        "CDLKICKING",
        "CDLKICKINGBYLENGTH",
        "CDLLADDERBOTTOM",
        "CDLLONGLINE",
        "CDLMARUBOZU",
        "CDLMATCHINGLOW",
        "CDLMATHOLD",
        "CDLMORNINGDOJISTAR",
        "CDLMORNINGSTAR",
        "CDLONNECK",
        "CDLPIERCING",
        "CDLRISEFALL3METHODS",
        "CDLSEPARATINGLINES",
        "CDLSHORTLINE",
        "CDLSTALLEDPATTERN",
        "CDLSTICKSANDWICH",
        "CDLTASUKIGAP",
        "CDLTHRUSTING",
        "CDLTRISTAR",
        "CDLUNIQUE3RIVER",
        "CDLUPSIDEGAP2CROWS",
        "CDLXSIDEGAP3METHODS",
    }
)


@dataclass
class PatternResult:
    """Result for a single pattern test."""

    pattern_name: str
    total_signals: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    max_profit: float
    max_loss: float
    sharpe_ratio: float
    max_drawdown: float = 0.0
    equity_curve: list[float] | None = None
    periodic_sharpe: float = 0.0
    trade_sharpe: float = 0.0
    avg_strength: float = 100.0
    profit_factor: float = 0.0
    score: float = 0.0
    total_trades: int = 0
    open_trade: dict[str, Any] | None = None
    fx_source: str = "same_currency"


def _safe_get_time(times: Any, idx: int | None) -> Any:
    """Safely retrieve timestamp from index without crashing on PyPy/Cython tslibs limitations."""
    if idx is None or times is None:
        return None
    try:
        n = len(times)
        if -n <= idx < n:
            return times[idx]
    except Exception:
        # e.g. a tslibs index that rejects len()/indexing on some PyPy/Cython builds.
        return None
    return None


class PatternRankingTester:
    """Test all candlestick patterns and rank them by performance.

    Can filter by economic news events.
    """

    __slots__ = (
        "_account_currency",
        "_allow_short",
        "_asset_type",
        "_base_currency",
        "_commission",
        "_data",
        "_execution",
        "_force_exit_on_last_bar",
        "_fx_history",
        "_fx_rates",
        "_holding_period",
        "_initial_capital",
        "_last_open_trade",
        "_max_fx_staleness",
        "_metadata_currency",
        "_min_signals",
        "_min_trades",
        "_news_dates",
        "_periods_per_year",
        "_position_size",
        "_quote_currency",
        "_results",
        "_sharpe_mode",
        "_slippage",
        "_strict_fx",
        "_symbol",
        "_timeframe",
        "_use_adj_close",
        "_used_approx_fx",
        "_verbose",
        "equity_curve",
        "trades",
    )

    def __init__(
        self,
        data: pd.DataFrame,
        initial_capital: float = 10000.0,
        position_size: float = 100.0,
        news_dates: list[str] | None = None,
        execution: str = "next_open",
        timeframe: str = "1d",
        periods_per_year: float | None = None,
        symbol: str = "",
        account_currency: str = "USD",
        force_exit_on_last_bar: bool = True,
        allow_short: bool = True,
        commission: float = 0.0,
        slippage: float = 0.0,
        min_signals: int = 1,
        fx_rates: dict[str, float] | None = None,
        fx_history: dict[str, pd.Series] | pd.DataFrame | None = None,
        strict_fx: bool = False,
        asset_type: str = "auto",
        sharpe_mode: str = "periodic",
        holding_period: int | None = None,
        base_currency: str | None = None,
        quote_currency: str | None = None,
        min_trades: int | None = None,
        max_fx_staleness: pd.Timedelta | str | None = "7D",
        use_adj_close: bool = False,
        metadata_currency: str | None = None,
        verbose: bool = False,
    ) -> None:
        """Initialize pattern tester.

        Parameters:
        -----------
        data : pd.DataFrame
            OHLCV data
        initial_capital : float
            Initial capital
        position_size : float
            Position size per trade (in base/quote units)
        news_dates : list[str], optional
            List of dates with important news (YYYY-MM-DD)
        execution : str
            Execution mode: 'next_open' (unbiased, enters on open of bar i+1)
            or 'close' (legacy, enters on close of bar i)
        timeframe : str
            Timeframe notation (e.g. '1d', '1h', '15m', '4h') for Sharpe scaling
        periods_per_year : float, optional
            Custom periods per year override for annualization
        symbol : str
            Ticker symbol for currency/asset conversion
        account_currency : str
            Account base currency (default 'USD')
        force_exit_on_last_bar : bool
            Whether to close active position at final bar close
        allow_short : bool
            Whether to execute short trades on bearish signals
        commission : float
            Fixed commission cost deducted per completed trade (default 0.0)
        slippage : float
            Slippage in price units applied adversely to entries and exits (default 0.0)
        min_signals : int
            Minimum signals required for ranking (default 1)
        fx_rates : dict[str, float], optional
            Dictionary of cross-currency exchange rates for Forex conversion
        fx_history : dict[str, pd.Series] | pd.DataFrame, optional
            Historical exchange rate time series for dynamic PnL conversion
        strict_fx : bool
            If True, raises ValueError on missing historical FX rates
        asset_type : str
            Asset classification: 'stock', 'forex', 'crypto', or 'auto'
        sharpe_mode : str
            Sharpe mode: 'periodic' (returns per bar, with 0% on idle bars) or 'trades'
        holding_period : int, optional
            Number of bars to hold a trade before exiting (useful for single-direction patterns like Hammer)
        base_currency : str, optional
            Base currency of the instrument (auto-resolved from symbol if omitted)
        quote_currency : str, optional
            Quote currency of the instrument (auto-resolved from symbol if omitted)
        min_trades : int, optional
            Minimum completed trades required for ranking
        """
        if not np.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError(f"initial_capital must be positive and finite, got {initial_capital}")
        if not np.isfinite(position_size) or position_size <= 0:
            raise ValueError(f"position_size must be positive and finite, got {position_size}")
        if not np.isfinite(commission) or commission < 0:
            raise ValueError(f"commission must be non-negative and finite, got {commission}")
        if not np.isfinite(slippage) or slippage < 0:
            raise ValueError(f"slippage must be non-negative and finite, got {slippage}")
        if not isinstance(min_signals, int) or isinstance(min_signals, bool) or min_signals < 1:
            raise ValueError(f"min_signals must be an integer >= 1, got {min_signals}")
        if min_trades is not None and (
            not isinstance(min_trades, int) or isinstance(min_trades, bool) or min_trades < 1
        ):
            raise ValueError(f"min_trades must be None or an integer >= 1, got {min_trades}")
        if holding_period is not None and (
            not isinstance(holding_period, int)
            or isinstance(holding_period, bool)
            or holding_period < 1
        ):
            raise ValueError(
                f"holding_period must be None or a positive integer >= 1, got {holding_period}"
            )
        if periods_per_year is not None and (
            not np.isfinite(periods_per_year) or periods_per_year <= 0
        ):
            raise ValueError(
                f"periods_per_year must be positive and finite, got {periods_per_year}"
            )

        if use_adj_close and "Adj Close" in data.columns and "Close" in data.columns:
            self._data = data.copy()
            raw_close = self._data["Close"].astype("float64")
            adj_close = self._data["Adj Close"].astype("float64")
            valid_mask = (
                (raw_close > 0) & np.isfinite(raw_close) & (adj_close > 0) & np.isfinite(adj_close)
            )
            ratio = np.where(valid_mask, adj_close / raw_close, 1.0)
            for col in ("Open", "High", "Low"):
                if col in self._data.columns:
                    self._data[col] = self._data[col].astype(float) * ratio
            self._data["Close"] = adj_close
        else:
            self._data = data
        self._use_adj_close: bool = use_adj_close
        self._metadata_currency: str | None = metadata_currency
        self._verbose: bool = bool(verbose)
        self._initial_capital: float = float(initial_capital)
        self._position_size: float = float(position_size)
        self._results: list[PatternResult] = []
        self._news_dates: set[str] = set(news_dates) if news_dates else set()
        self._execution: str = execution
        self._timeframe: str = normalize_interval(timeframe) if timeframe else "1d"
        self._symbol: str = symbol

        def _norm_c(c: str) -> str:
            c_str = c.strip()
            if c_str in ("GBp", "GBX", "GBx"):
                return "GBp"
            if c_str in ("ZAc", "ZAC", "Zac"):
                return "ZAc"
            if c_str.upper() == "ILA":
                return "ILA"
            return c_str.upper()

        self._account_currency: str = _norm_c(account_currency)
        self._force_exit_on_last_bar: bool = force_exit_on_last_bar
        self._allow_short: bool = allow_short
        self._commission: float = float(commission)
        self._slippage: float = float(slippage)
        self._min_signals: int = min_signals
        self._min_trades: int | None = min_trades
        self._fx_rates: dict[str, float] = (
            {k: float(v) for k, v in fx_rates.items() if np.isfinite(v) and v > 0}
            if fx_rates
            else {}
        )
        self._fx_history: dict[str, pd.Series] | pd.DataFrame | None = fx_history
        self._strict_fx: bool = strict_fx
        self._used_approx_fx: bool = False

        def _parse_staleness(val: Any) -> datetime.timedelta | pd.Timedelta | None:
            if val is None:
                return None
            if isinstance(val, (datetime.timedelta, pd.Timedelta)):
                return val
            if isinstance(val, str):
                s = val.strip()
                if s.upper().endswith("D"):
                    try:
                        return datetime.timedelta(days=float(s[:-1]))
                    except ValueError:
                        pass
                elif s.upper().endswith("H"):
                    try:
                        return datetime.timedelta(hours=float(s[:-1]))
                    except ValueError:
                        pass
                elif s.upper().endswith("M"):
                    try:
                        return datetime.timedelta(minutes=float(s[:-1]))
                    except ValueError:
                        pass
                try:
                    return cast(pd.Timedelta, pd.Timedelta(s))
                except Exception:
                    return datetime.timedelta(days=7)
            return None

        self._max_fx_staleness: datetime.timedelta | pd.Timedelta | None = _parse_staleness(
            max_fx_staleness
        )

        self._asset_type: str = validate_asset_type(asset_type)
        self._sharpe_mode: str = sharpe_mode
        self._holding_period: int | None = holding_period

        meta_curr = metadata_currency or getattr(data, "attrs", {}).get("currency")
        if base_currency and quote_currency:
            self._base_currency: str = _norm_c(base_currency)
            self._quote_currency: str = _norm_c(quote_currency)
        elif self._symbol:
            b, q = resolve_asset_currencies(
                self._symbol, asset_type=self._asset_type, metadata_currency=meta_curr
            )
            self._base_currency = _norm_c(base_currency or b)
            self._quote_currency = _norm_c(quote_currency or q)
        else:
            self._base_currency = _norm_c(base_currency or "ASSET")
            self._quote_currency = _norm_c(quote_currency or "USD")

        if periods_per_year is not None:
            self._periods_per_year: float = float(periods_per_year)
        else:
            as_of = None
            d_range = None
            if hasattr(self._data.index, "max") and len(self._data) > 0:
                try:
                    first_ts = self._data.index.min()
                    last_ts = self._data.index.max()
                    if hasattr(last_ts, "date"):
                        as_of = last_ts.date()
                    if hasattr(first_ts, "date") and hasattr(last_ts, "date"):
                        d_range = (first_ts.date(), last_ts.date())
                except Exception:
                    as_of = None
                    d_range = None
            self._periods_per_year = resolve_periods_per_year(
                self._timeframe,
                self._symbol,
                asset_type=self._asset_type,
                as_of_date=as_of,
                date_range=d_range,
            )

        self.equity_curve: list[float] = [initial_capital]
        self.trades: list[dict[str, Any]] = []
        self._last_open_trade: dict[str, Any] | None = None

    @staticmethod
    def get_all_patterns() -> list[str]:
        """Get all TA-Lib candlestick pattern function names."""
        return [f for f in dir(talib) if f.startswith("CDL")]

    def _lookup_hist_rate(
        self, direct_pair: str, inv_pair: str, timestamp: pd.Timestamp
    ) -> float | None:
        """Helper to look up exchange rate in self._fx_history."""
        if self._fx_history is None:
            return None

        def get_series(p: str) -> pd.Series | None:
            candidates = [p, f"{p}=X", p.replace("=X", "")]
            if isinstance(self._fx_history, dict):
                for c in candidates:
                    if c in self._fx_history:
                        return self._fx_history[c]
            elif isinstance(self._fx_history, pd.DataFrame):
                for c in candidates:
                    if c in self._fx_history.columns:
                        return self._fx_history[c]
            elif isinstance(self._fx_history, pd.Series) and (
                self._fx_history.name in candidates or self._fx_history.name is None
            ):
                return self._fx_history
            return None

        def check_staleness_and_val(s: pd.Series) -> float | None:
            ts = timestamp
            s_tz = getattr(s.index, "tz", None)
            if s_tz is not None and ts.tz is None:
                ts = ts.tz_localize("UTC").tz_convert(s_tz)
            elif s_tz is None and ts.tz is not None:
                ts = ts.tz_convert(pytz.UTC).tz_localize(None)

            sub = s.loc[:ts].dropna()  # type: ignore[misc]
            if sub.empty:
                return None
            last_ts = sub.index[-1]
            val = float(cast(Any, sub.iloc[-1]))
            if not np.isfinite(val) or val <= 0:
                return None

            if self._max_fx_staleness is not None:
                staleness = abs(ts - last_ts)
                if staleness > self._max_fx_staleness:
                    return None

            return val

        s_dir = get_series(direct_pair)
        if s_dir is not None:
            val = check_staleness_and_val(s_dir)
            if val is not None:
                return val

        s_inv = get_series(inv_pair)
        if s_inv is not None:
            val = check_staleness_and_val(s_inv)
            if val is not None and np.isfinite(val) and val > 0:
                inv_val = 1.0 / val
                if np.isfinite(inv_val) and inv_val > 0:
                    return inv_val

        return None

    def _resolve_fx_rate_with_source(
        self, from_curr: str, to_curr: str, timestamp: pd.Timestamp | None = None
    ) -> tuple[float, str]:
        """Resolve exchange rate and identify the source of the rate ('historical', 'custom_static', 'default_static', 'same_currency')."""
        # 0. Handle subunit currencies (GBp, ILA, ZAc)
        subunit_map: dict[str, tuple[str, float]] = {
            "GBp": ("GBP", 0.01),
            "GBX": ("GBP", 0.01),
            "GBx": ("GBP", 0.01),
            "ILA": ("ILS", 0.01),
            "ila": ("ILS", 0.01),
            "Ila": ("ILS", 0.01),
            "ZAc": ("ZAR", 0.01),
            "ZAC": ("ZAR", 0.01),
            "Zac": ("ZAR", 0.01),
            "zac": ("ZAR", 0.01),
        }
        sub_from = subunit_map.get(from_curr)
        sub_to = subunit_map.get(to_curr)
        if sub_from and sub_to and sub_from[0] == sub_to[0]:
            return sub_from[1] / sub_to[1], "same_currency"
        if sub_from:
            parent, factor = sub_from
            rate, src = self._resolve_fx_rate_with_source(parent, to_curr, timestamp)
            return factor * rate, src
        if sub_to:
            parent, factor = sub_to
            rate, src = self._resolve_fx_rate_with_source(from_curr, parent, timestamp)
            return rate * (1.0 / factor), src

        from_curr = from_curr.upper()
        to_curr = to_curr.upper()
        if from_curr == to_curr:
            return 1.0, "same_currency"

        direct_pair = f"{from_curr}{to_curr}"
        inv_pair = f"{to_curr}{from_curr}"

        # 1. Historical rate lookup
        if self._strict_fx:
            if self._fx_history is None:
                raise ValueError(
                    f"strict_fx=True requires fx_history for currency conversion from {from_curr} to {to_curr}"
                )
            if timestamp is None:
                raise ValueError(
                    f"strict_fx=True requires timestamp for historical currency conversion from {from_curr} to {to_curr}"
                )
            hist_rate = self._lookup_hist_rate(direct_pair, inv_pair, timestamp)
            if hist_rate is not None:
                return hist_rate, "historical"

            # USD bridge via historical rates
            if from_curr != "USD" and to_curr != "USD":
                from_usd = self._lookup_hist_rate(f"{from_curr}USD", f"USD{from_curr}", timestamp)
                to_usd = self._lookup_hist_rate(f"{to_curr}USD", f"USD{to_curr}", timestamp)
                if (
                    from_usd is not None
                    and to_usd is not None
                    and np.isfinite(from_usd)
                    and np.isfinite(to_usd)
                    and to_usd > 0
                ):
                    bridge_rate = from_usd / to_usd
                    if np.isfinite(bridge_rate) and bridge_rate > 0:
                        return bridge_rate, "historical"

            raise ValueError(f"Missing historical FX rate for {direct_pair} at {timestamp}")

        if self._fx_history is not None and timestamp is not None:
            hist_rate = self._lookup_hist_rate(direct_pair, inv_pair, timestamp)
            if hist_rate is not None:
                return hist_rate, "historical"

            # USD bridge via historical rates
            if from_curr != "USD" and to_curr != "USD":
                from_usd = self._lookup_hist_rate(f"{from_curr}USD", f"USD{from_curr}", timestamp)
                to_usd = self._lookup_hist_rate(f"{to_curr}USD", f"USD{to_curr}", timestamp)
                if (
                    from_usd is not None
                    and to_usd is not None
                    and np.isfinite(from_usd)
                    and np.isfinite(to_usd)
                    and to_usd > 0
                ):
                    bridge_rate = from_usd / to_usd
                    if np.isfinite(bridge_rate) and bridge_rate > 0:
                        return bridge_rate, "historical"

        # 2. Static rates table
        used_default_static = False
        rates = {**DEFAULT_FX_USD_RATES, **self._fx_rates}
        if direct_pair in rates and np.isfinite(rates[direct_pair]) and rates[direct_pair] > 0:
            self._used_approx_fx = True
            if direct_pair not in self._fx_rates:
                warnings.warn(
                    f"Static default FX rate used for {from_curr}->{to_curr} currency conversion at {timestamp}. "
                    "For historical accuracy, provide fx_history or enable strict_fx=True.",
                    UserWarning,
                    stacklevel=2,
                )
                return rates[direct_pair], "default_static"
            return rates[direct_pair], "custom_static"
        if inv_pair in rates and np.isfinite(rates[inv_pair]) and rates[inv_pair] > 0:
            inv_rate = 1.0 / rates[inv_pair]
            if np.isfinite(inv_rate) and inv_rate > 0:
                self._used_approx_fx = True
                if inv_pair not in self._fx_rates:
                    warnings.warn(
                        f"Static default FX rate used for {from_curr}->{to_curr} currency conversion at {timestamp}. "
                        "For historical accuracy, provide fx_history or enable strict_fx=True.",
                        UserWarning,
                        stacklevel=2,
                    )
                    return inv_rate, "default_static"
                return inv_rate, "custom_static"

        # USD bridge via static rates
        from_usd_rate: float | None = 1.0 if from_curr == "USD" else None
        if from_usd_rate is None:
            if (
                f"{from_curr}USD" in rates
                and np.isfinite(rates[f"{from_curr}USD"])
                and rates[f"{from_curr}USD"] > 0
            ):
                from_usd_rate = rates[f"{from_curr}USD"]
                if f"{from_curr}USD" not in self._fx_rates:
                    used_default_static = True
            elif (
                f"USD{from_curr}" in rates
                and np.isfinite(rates[f"USD{from_curr}"])
                and rates[f"USD{from_curr}"] > 0
            ):
                inv_bridge = 1.0 / rates[f"USD{from_curr}"]
                if np.isfinite(inv_bridge) and inv_bridge > 0:
                    from_usd_rate = inv_bridge
                    if f"USD{from_curr}" not in self._fx_rates:
                        used_default_static = True

        to_usd_rate: float | None = 1.0 if to_curr == "USD" else None
        if to_usd_rate is None:
            if (
                f"{to_curr}USD" in rates
                and np.isfinite(rates[f"{to_curr}USD"])
                and rates[f"{to_curr}USD"] > 0
            ):
                to_usd_rate = rates[f"{to_curr}USD"]
                if f"{to_curr}USD" not in self._fx_rates:
                    used_default_static = True
            elif (
                f"USD{to_curr}" in rates
                and np.isfinite(rates[f"USD{to_curr}"])
                and rates[f"USD{to_curr}"] > 0
            ):
                inv_bridge = 1.0 / rates[f"USD{to_curr}"]
                if np.isfinite(inv_bridge) and inv_bridge > 0:
                    to_usd_rate = inv_bridge
                    if f"USD{to_curr}" not in self._fx_rates:
                        used_default_static = True

        if (
            from_usd_rate is not None
            and to_usd_rate is not None
            and np.isfinite(from_usd_rate)
            and np.isfinite(to_usd_rate)
            and to_usd_rate > 0
        ):
            bridge_val = from_usd_rate / to_usd_rate
            if np.isfinite(bridge_val) and bridge_val > 0:
                self._used_approx_fx = True
                if used_default_static:
                    warnings.warn(
                        f"Static default FX rate used for {from_curr}->{to_curr} currency conversion at {timestamp}. "
                        "For historical accuracy, provide fx_history or enable strict_fx=True.",
                        UserWarning,
                        stacklevel=2,
                    )
                    return bridge_val, "default_static"
                return bridge_val, "custom_static"

        raise ValueError(
            f"Unable to convert currency from {from_curr} to {to_curr}: no exchange rate available. "
            f"Please provide fx_history or fx_rates."
        )

    def _get_fx_rate(
        self, from_curr: str, to_curr: str, timestamp: pd.Timestamp | None = None
    ) -> float:
        """Get exchange rate converting 1 unit of from_curr to to_curr at timestamp."""
        rate, _ = self._resolve_fx_rate_with_source(from_curr, to_curr, timestamp)
        return rate

    def _convert_pnl_with_source(
        self, raw_pnl: float, exit_price: float, exit_time: pd.Timestamp | None = None
    ) -> tuple[float, float, str]:
        """Universal conversion: normalize raw PnL into account base currency with FX rate and source tracking."""
        if raw_pnl == 0.0:
            return 0.0, 1.0, "same_currency"

        # 1. Quote currency matches account currency
        if self._quote_currency == self._account_currency:
            return raw_pnl, 1.0, "same_currency"

        # 2. Base currency is account currency and instrument price is exit_price
        if self._base_currency == self._account_currency and exit_price > 0:
            return raw_pnl / exit_price, 1.0 / exit_price, "instrument_price"

        # 3. Universal conversion from quote currency to account currency
        rate, source = self._resolve_fx_rate_with_source(
            self._quote_currency, self._account_currency, exit_time
        )
        return raw_pnl * rate, rate, source

    def _convert_pnl_to_account_currency(
        self, raw_pnl: float, exit_price: float, exit_time: pd.Timestamp | None = None
    ) -> float:
        """Universal conversion: normalize raw PnL (in quote currency) into account base currency."""
        converted, _, _ = self._convert_pnl_with_source(raw_pnl, exit_price, exit_time)
        return converted

    def test_all_patterns(
        self,
        filter_news: bool = False,
        min_signals: int | None = None,
        min_trades: int | None = None,
        sort_by: str = "win_rate",
        split_directions: bool = False,
        exclude_indecision: bool = False,
        trade_indecision: bool = True,
    ) -> list[PatternResult]:
        """Test all patterns and return ranked results.

        Parameters:
        -----------
        filter_news : bool
            If True, exclude trades during news events
        min_signals : int, optional
            Minimum total signals required to include in ranked results (default from __init__)
        min_trades : int, optional
            Minimum completed trades required to include in ranked results (default from __init__)
        sort_by : str
            Ranking criteria: 'win_rate' (default, sorts by win_rate then total_pnl) or 'composite' (sorts by score)
        split_directions : bool
            If True, test and report bullish and bearish occurrences of two-way patterns separately (e.g. CDLENGULFING_BULL, CDLENGULFING_BEAR)
        exclude_indecision : bool
            If True, exclude neutral/indecision patterns (Doji, Spinning Top, High Wave) from directional ranking (default False)
        trade_indecision : bool
            If False, suppress directional trades on neutral/indecision patterns (default True for backwards compatibility)

        Returns:
        --------
        list[PatternResult] : Ranked pattern results
        """
        self._results = []
        all_patterns = self.get_all_patterns()
        thresh_signals = min_signals if min_signals is not None else self._min_signals
        thresh_trades = min_trades if min_trades is not None else self._min_trades

        patterns_to_test: list[str] = []
        for pat in all_patterns:
            if (exclude_indecision or not trade_indecision) and pat in INDECISION_PATTERNS:
                continue
            if split_directions and pat in TWO_WAY_PATTERNS:
                patterns_to_test.append(f"{pat}_BULL")
                patterns_to_test.append(f"{pat}_BEAR")
            else:
                patterns_to_test.append(pat)

        for pattern_name in patterns_to_test:
            try:
                result = self._test_single_pattern(
                    pattern_name, filter_news, trade_indecision=trade_indecision
                )
                if (
                    result
                    and result.total_signals >= thresh_signals
                    and (thresh_trades is None or result.total_trades >= thresh_trades)
                ):
                    self._results.append(result)
            except NotImplementedError:
                # Silently skip patterns not implemented in pure-Python fallback
                continue
            except Exception as exc:
                logger.debug("Error testing %s: %s", pattern_name, exc)
                if self._verbose:
                    print(f"Error testing {pattern_name}: {exc}")
                continue

        # Sort results
        if sort_by == "composite":
            self._results.sort(key=lambda x: (x.score, x.total_pnl), reverse=True)
        else:
            self._results.sort(key=lambda x: (x.win_rate, x.total_pnl), reverse=True)
        return self._results

    def test_pattern(
        self,
        pattern_name: str,
        filter_news: bool = False,
        direction: str | None = None,
        trade_indecision: bool = True,
    ) -> PatternResult | None:
        """Test a single candlestick pattern by name."""
        self.trades = []
        self.equity_curve = [self._initial_capital]
        self._last_open_trade = None
        return self._test_single_pattern(
            pattern_name,
            filter_news=filter_news,
            direction=direction,
            trade_indecision=trade_indecision,
        )

    def _test_single_pattern(
        self,
        pattern_name: str,
        filter_news: bool,
        direction: str | None = None,
        trade_indecision: bool = True,
    ) -> PatternResult | None:
        """Test a single pattern."""
        self.trades = []
        self.equity_curve = [self._initial_capital]
        self._last_open_trade = None

        pat = pattern_name.upper()
        dir_filter = direction.lower() if direction else None
        if pat.endswith("_BULL") or pat.endswith("_BULLISH"):
            pat = pat.rsplit("_", 1)[0]
            dir_filter = "bullish"
        elif pat.endswith("_BEAR") or pat.endswith("_BEARISH"):
            pat = pat.rsplit("_", 1)[0]
            dir_filter = "bearish"

        if not pat.startswith("CDL"):
            pat = f"CDL{pat}"

        pattern_func = getattr(talib, pat, None)
        if not pattern_func:
            logger.debug("Pattern function not found: %s", pattern_name)
            if self._verbose:
                print(f"Pattern function not found: {pattern_name}")
            return None

        try:
            pattern_values = pattern_func(
                self._data["Open"].values,
                self._data["High"].values,
                self._data["Low"].values,
                self._data["Close"].values,
            )
        except NotImplementedError:
            # Propagate up so test_all_patterns can skip
            raise
        except Exception as exc:
            logger.debug("Error detecting pattern %s: %s", pattern_name, exc)
            if self._verbose:
                print(f"Error detecting pattern {pattern_name}: {exc}")
            return None

        # Generate signals and preserve pattern strength
        signals = np.zeros(len(self._data))
        strengths = np.zeros(len(self._data))

        is_indecision = pat in INDECISION_PATTERNS
        if is_indecision and not trade_indecision and dir_filter is None:
            # Neutral/indecision candles denote market equilibrium, not directional bias
            pass
        else:
            for i in range(len(pattern_values)):
                val = pattern_values[i]
                if val > 0 and dir_filter in (None, "bullish", "long"):
                    signals[i] = 1
                    strengths[i] = abs(val)
                elif val < 0 and dir_filter in (None, "bearish", "short"):
                    signals[i] = -1
                    strengths[i] = abs(val)

        # Filter by news if requested
        if filter_news and self._news_dates:
            for i in range(len(signals)):
                idx_time = _safe_get_time(self._data.index, i)
                if idx_time is not None:
                    date_str = (
                        idx_time.strftime("%Y-%m-%d")
                        if hasattr(idx_time, "strftime")
                        else str(idx_time)[:10]
                    )
                    if date_str in self._news_dates:
                        signals[i] = 0

        # Calculate trades
        trades = self._calculate_trades(signals)

        # Return None if no trades and no open position
        if not trades and self._last_open_trade is None:
            total_signals = int(np.sum(signals != 0))
            if total_signals == 0:
                logger.debug("%s: No signals generated", pattern_name)
                if self._verbose:
                    print(f"{pattern_name}: No signals generated")
            else:
                logger.debug("%s: %d signals but no completed trades", pattern_name, total_signals)
                if self._verbose:
                    print(f"{pattern_name}: {total_signals} signals but no completed trades")
            return None

        # Calculate statistics
        winning_trades = [t for t in trades if t["pnl"] > 0]
        losing_trades = [t for t in trades if t["pnl"] <= 0]

        total_pnl = sum(t["pnl"] for t in trades)
        avg_pnl = total_pnl / len(trades) if trades else 0.0
        win_rate = len(winning_trades) / len(trades) * 100.0 if trades else 0.0

        max_profit = max(t["pnl"] for t in trades) if trades else 0.0
        max_loss = min(t["pnl"] for t in trades) if trades else 0.0

        # Build bar-by-bar MTM equity curve across all n_bars
        n_bars = len(self._data)
        closes = self._data["Close"].values
        times = self._data.index

        exits_by_bar: dict[int, list[dict[str, Any]]] = {}
        active_by_bar: dict[int, dict[str, Any]] = {}

        for t in trades:
            e_idx = t.get("entry_idx")
            x_idx = t.get("exit_idx")
            if x_idx is not None:
                exits_by_bar.setdefault(x_idx, []).append(t)
            if e_idx is not None and x_idx is not None:
                for bar in range(e_idx, x_idx):
                    active_by_bar[bar] = t

        if self._last_open_trade is not None:
            e_idx = self._last_open_trade.get("entry_idx")
            if e_idx is not None:
                for bar in range(e_idx, n_bars):
                    active_by_bar[bar] = self._last_open_trade

        bar_equity = np.full(n_bars, self._initial_capital, dtype=float)
        cash = self._initial_capital
        for b in range(n_bars):
            if b in exits_by_bar:
                for t in exits_by_bar[b]:
                    cash += t["pnl"]

            active_t = active_by_bar.get(b)
            if active_t is not None:
                units = active_t.get("position", 0.0)
                entry_p = active_t.get("entry_price", closes[b])
                curr_p = closes[b]
                if active_t.get("direction") == "LONG":
                    raw_unrealized = units * (curr_p - entry_p)
                else:
                    raw_unrealized = units * (entry_p - curr_p)
                exit_time_val = _safe_get_time(times, b) if self._fx_history is not None else None
                unrealized = self._convert_pnl_to_account_currency(
                    raw_unrealized, curr_p, exit_time=exit_time_val
                )
                bar_equity[b] = cash + unrealized
            else:
                bar_equity[b] = cash

        curve = [self._initial_capital, *bar_equity.tolist()]

        peak = self._initial_capital
        max_drawdown = 0.0
        for eq in curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_drawdown:
                max_drawdown = dd

        self.equity_curve = curve
        self.trades = trades

        # Trade-level Sharpe ratio (scaled by annual trade frequency)
        pnls = [t["pnl"] for t in trades]
        if len(pnls) > 1:
            pnl_arr = np.asarray(pnls, dtype=float)
            std_pnl = float(np.sqrt(np.mean((pnl_arr - np.mean(pnl_arr)) ** 2)))
        else:
            std_pnl = 0.0
        duration_years = max(n_bars / self._periods_per_year, 1e-6)
        trades_per_year = len(trades) / duration_years
        trade_sharpe = (
            (float(np.mean(pnls)) / std_pnl) * float(np.sqrt(trades_per_year))
            if len(pnls) > 1 and std_pnl > 0 and trades_per_year > 0
            else 0.0
        )

        # Bar-level periodic returns & Periodic Sharpe (accounting for 0% return during idle holding periods)
        bar_returns = np.zeros(max(n_bars - 1, 1), dtype=float)
        if n_bars > 1:
            prev_eq = bar_equity[:-1]
            non_zero = prev_eq > 0
            bar_returns[non_zero] = (bar_equity[1:][non_zero] - prev_eq[non_zero]) / prev_eq[
                non_zero
            ]

        std_bar_ret = (
            float(np.sqrt(np.mean((bar_returns - np.mean(bar_returns)) ** 2)))
            if bar_returns.size > 0
            else 0.0
        )
        periodic_sharpe = (
            (float(np.mean(bar_returns)) / std_bar_ret) * float(np.sqrt(self._periods_per_year))
            if std_bar_ret > 0
            else 0.0
        )

        primary_sharpe = periodic_sharpe if self._sharpe_mode == "periodic" else trade_sharpe

        # Pattern strength tracking
        active_strengths = [s for s in strengths if s > 0]
        avg_strength = float(np.mean(active_strengths)) if active_strengths else 100.0

        # Profit Factor
        win_sum = sum(t["pnl"] for t in winning_trades)
        loss_sum = abs(sum(t["pnl"] for t in losing_trades))
        if loss_sum > 0:
            profit_factor = win_sum / loss_sum
        elif win_sum > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        # Composite performance score balancing win rate, profit factor, Sharpe, and trade volume
        pf_capped = min(profit_factor, 10.0) if np.isfinite(profit_factor) else 10.0
        sharpe_clamped = max(primary_sharpe, 0.0)
        score = (
            (win_rate / 100.0)
            * (1.0 + pf_capped)
            * (1.0 + sharpe_clamped)
            * float(np.log1p(len(trades)))
        )

        if any(t.get("fx_source") == "default_static" for t in trades):
            overall_fx = "default_static"
        elif any(t.get("fx_source") == "custom_static" for t in trades):
            overall_fx = "custom_static"
        elif any(t.get("fx_source") == "historical" for t in trades):
            overall_fx = "historical"
        elif any(t.get("fx_source") == "instrument_price" for t in trades):
            overall_fx = "instrument_price"
        else:
            overall_fx = "same_currency"

        return PatternResult(
            pattern_name=pattern_name.replace("CDL", ""),
            total_signals=int(np.sum(signals != 0)),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            max_profit=max_profit,
            max_loss=max_loss,
            sharpe_ratio=primary_sharpe,
            max_drawdown=max_drawdown,
            equity_curve=curve,
            periodic_sharpe=periodic_sharpe,
            trade_sharpe=trade_sharpe,
            avg_strength=avg_strength,
            profit_factor=profit_factor,
            score=score,
            total_trades=len(trades),
            open_trade=self._last_open_trade,
            fx_source=overall_fx,
        )

    def _calc_position_units(
        self, exec_price: float, entry_time: pd.Timestamp | None = None
    ) -> float:
        """Calculate position units in base asset for given account currency position size."""
        if exec_price <= 0:
            return 0.0

        if self._base_currency == self._account_currency:
            return self._position_size

        if self._quote_currency == self._account_currency:
            return self._position_size / exec_price

        # Cross currency: convert account_currency to quote_currency at entry_time
        rate = self._get_fx_rate(self._account_currency, self._quote_currency, entry_time)
        return (self._position_size * rate) / exec_price

    def _calculate_trades(self, signals: np.ndarray) -> list[dict[str, Any]]:
        """Calculate trades from signals with short support, slippage, and commissions."""
        trades: list[dict[str, Any]] = []
        position: float = 0.0
        entry_idx: int | None = None
        entry_price: float = 0.0

        closes = self._data["Close"].values
        opens = self._data["Open"].values
        times = self._data.index
        n = len(signals)

        is_next_open = self._execution == "next_open"
        loop_limit = (n - 1) if is_next_open else n

        for i in range(loop_limit):
            exec_price = opens[i + 1] if is_next_open else closes[i]
            exec_idx = (i + 1) if is_next_open else i
            entry_time_curr = _safe_get_time(times, exec_idx)
            if entry_time_curr is not None and not isinstance(entry_time_curr, pd.Timestamp):
                try:
                    entry_time_curr = pd.to_datetime(entry_time_curr)
                except Exception:
                    entry_time_curr = None

            # Check time-based holding period exit
            if (
                self._holding_period is not None
                and position != 0.0
                and entry_idx is not None
                and (exec_idx - entry_idx) >= self._holding_period
            ):
                if position > 0.0:
                    eff_exit = exec_price - self._slippage
                    raw_pnl = position * (eff_exit - entry_price)
                    direction = "LONG"
                else:
                    eff_exit = exec_price + self._slippage
                    raw_pnl = (-position) * (entry_price - eff_exit)
                    direction = "SHORT"
                exit_time_val = (
                    _safe_get_time(times, exec_idx) if self._fx_history is not None else None
                )
                conv_pnl, fx_rate, fx_src = self._convert_pnl_with_source(
                    raw_pnl, exec_price, exit_time=exit_time_val
                )
                pnl = conv_pnl - self._commission
                trades.append(
                    {
                        "entry_idx": entry_idx,
                        "entry_time": _safe_get_time(times, entry_idx),
                        "exit_time": _safe_get_time(times, exec_idx),
                        "exit_idx": exec_idx,
                        "direction": direction,
                        "position": abs(position),
                        "entry_price": entry_price,
                        "exit_price": eff_exit,
                        "market_exit_price": exec_price,
                        "pnl": pnl,
                        "fx_rate": fx_rate,
                        "fx_source": fx_src,
                        "time_exit": True,
                    }
                )
                position = 0.0
                entry_idx = None

            signal = signals[i]
            if signal == 0:
                continue

            if signal == 1:
                # Close Short if currently short
                if position < 0.0 and entry_idx is not None:
                    eff_exit = exec_price + self._slippage
                    raw_pnl = (-position) * (entry_price - eff_exit)
                    exit_time_val = (
                        _safe_get_time(times, exec_idx) if self._fx_history is not None else None
                    )
                    conv_pnl, fx_rate, fx_src = self._convert_pnl_with_source(
                        raw_pnl, exec_price, exit_time=exit_time_val
                    )
                    pnl = conv_pnl - self._commission
                    trades.append(
                        {
                            "entry_idx": entry_idx,
                            "entry_time": _safe_get_time(times, entry_idx),
                            "exit_time": _safe_get_time(times, exec_idx),
                            "exit_idx": exec_idx,
                            "direction": "SHORT",
                            "position": abs(position),
                            "entry_price": entry_price,
                            "exit_price": eff_exit,
                            "market_exit_price": exec_price,
                            "pnl": pnl,
                            "fx_rate": fx_rate,
                            "fx_source": fx_src,
                        }
                    )
                    position = 0.0
                    entry_idx = None
                # Open Long if flat
                elif position == 0.0:
                    entry_idx = exec_idx
                    entry_price = exec_price + self._slippage
                    position = self._calc_position_units(entry_price, entry_time=entry_time_curr)

            elif signal == -1:
                # Close Long if currently long
                if position > 0.0 and entry_idx is not None:
                    eff_exit = exec_price - self._slippage
                    raw_pnl = position * (eff_exit - entry_price)
                    exit_time_val = (
                        _safe_get_time(times, exec_idx) if self._fx_history is not None else None
                    )
                    conv_pnl, fx_rate, fx_src = self._convert_pnl_with_source(
                        raw_pnl, exec_price, exit_time=exit_time_val
                    )
                    pnl = conv_pnl - self._commission
                    trades.append(
                        {
                            "entry_idx": entry_idx,
                            "entry_time": _safe_get_time(times, entry_idx),
                            "exit_time": _safe_get_time(times, exec_idx),
                            "exit_idx": exec_idx,
                            "direction": "LONG",
                            "position": abs(position),
                            "entry_price": entry_price,
                            "exit_price": eff_exit,
                            "market_exit_price": exec_price,
                            "pnl": pnl,
                            "fx_rate": fx_rate,
                            "fx_source": fx_src,
                        }
                    )
                    position = 0.0
                    entry_idx = None
                # Open Short if flat and shorts are allowed
                elif position == 0.0 and self._allow_short:
                    entry_idx = exec_idx
                    entry_price = exec_price - self._slippage
                    position = -self._calc_position_units(entry_price, entry_time=entry_time_curr)

        # Force-close position on the last bar if still open
        if self._force_exit_on_last_bar and position != 0.0 and entry_idx is not None:
            last_exit_price = closes[-1]
            if position > 0.0:
                eff_exit = last_exit_price - self._slippage
                raw_pnl = position * (eff_exit - entry_price)
                direction = "LONG"
            else:
                eff_exit = last_exit_price + self._slippage
                raw_pnl = (-position) * (entry_price - eff_exit)
                direction = "SHORT"
            exit_time_val = _safe_get_time(times, -1) if self._fx_history is not None else None
            conv_pnl, fx_rate, fx_src = self._convert_pnl_with_source(
                raw_pnl, last_exit_price, exit_time=exit_time_val
            )
            pnl = conv_pnl - self._commission
            trades.append(
                {
                    "entry_idx": entry_idx,
                    "entry_time": _safe_get_time(times, entry_idx),
                    "exit_time": _safe_get_time(times, -1),
                    "exit_idx": n - 1,
                    "direction": direction,
                    "position": abs(position),
                    "entry_price": entry_price,
                    "exit_price": eff_exit,
                    "market_exit_price": last_exit_price,
                    "pnl": pnl,
                    "fx_rate": fx_rate,
                    "fx_source": fx_src,
                    "forced_exit": True,
                }
            )
            self._last_open_trade = None
        elif position != 0.0 and entry_idx is not None:
            self._last_open_trade = {
                "entry_idx": entry_idx,
                "entry_time": _safe_get_time(times, entry_idx),
                "direction": "LONG" if position > 0 else "SHORT",
                "position": abs(position),
                "entry_price": entry_price,
            }
        else:
            self._last_open_trade = None

        return trades

    @property
    def used_approx_fx(self) -> bool:
        """Whether approximate/static FX rates were used during currency conversion."""
        return self._used_approx_fx

    @property
    def has_static_fx_conversion(self) -> bool:
        """Whether approximate/static FX rates were used during currency conversion."""
        return self._used_approx_fx

    def get_top_patterns(self, n: int = 10) -> list[PatternResult]:
        """Get top N patterns by performance."""
        return self._results[:n]

    def get_comparison_report(self) -> pd.DataFrame:
        """Get comparison report with/without news filter, correctly matched by pattern.

        Both rankings are recomputed internally, which resets the tester's own result and
        trade buffers. The caller's previous run is snapshotted and restored afterwards, so
        this read-only report does not change what `get_results()` or `export_results()`
        return.
        """
        saved_results = self._results
        saved_trades = list(self.trades)
        saved_equity_curve = list(self.equity_curve)
        saved_open_trade = self._last_open_trade
        saved_approx_fx = self._used_approx_fx
        try:
            results_no_filter = list(self.test_all_patterns(filter_news=False))
            results_with_filter = list(self.test_all_patterns(filter_news=True))
            # Captured from the internal runs: the warning must describe this report's data,
            # not the restored state of the caller's run.
            report_approx_fx = self._used_approx_fx
        finally:
            self._results = saved_results
            self.trades = saved_trades
            self.equity_curve = saved_equity_curve
            self._last_open_trade = saved_open_trade
            self._used_approx_fx = saved_approx_fx

        map_with_filter = {r.pattern_name: r for r in results_with_filter}

        data: list[dict[str, Any]] = []
        for r_no in results_no_filter[:20]:
            r_yes = map_with_filter.get(r_no.pattern_name)
            data.append(
                {
                    "Pattern": r_no.pattern_name,
                    "Win Rate (No News Filter)": f"{r_no.win_rate:.1f}%",
                    "Win Rate (With News Filter)": f"{r_yes.win_rate:.1f}%" if r_yes else "N/A",
                    "Total PnL (No Filter)": f"${r_no.total_pnl:.2f}",
                    "Total PnL (With Filter)": f"${r_yes.total_pnl:.2f}" if r_yes else "N/A",
                    "FX Source": r_no.fx_source,
                    "Signals (No Filter)": r_no.total_signals,
                    "Signals (With Filter)": r_yes.total_signals if r_yes else 0,
                    "Trades (No Filter)": r_no.total_trades,
                    "Trades (With Filter)": r_yes.total_trades if r_yes else 0,
                    "Sharpe (No Filter)": f"{r_no.sharpe_ratio:.2f}",
                    "Sharpe (With Filter)": f"{r_yes.sharpe_ratio:.2f}" if r_yes else "N/A",
                }
            )

        df = pd.DataFrame(data)
        if report_approx_fx:
            df.attrs["fx_warning"] = (
                "Static/approximate FX rates were used during currency conversion. "
                "For historical accuracy, provide fx_history or enable strict_fx=True."
            )
        else:
            df.attrs["fx_warning"] = None
        return df

    def export_results(self, filename: str = "pattern_ranking.csv") -> None:
        """Export results to CSV."""
        data: list[dict[str, Any]] = []
        for result in self._results:
            data.append(
                {
                    "Rank": len(data) + 1,
                    "Pattern": result.pattern_name,
                    "Total Signals": result.total_signals,
                    "Total Trades": result.total_trades,
                    "Winning Trades": result.winning_trades,
                    "Losing Trades": result.losing_trades,
                    "Win Rate %": f"{result.win_rate:.2f}",
                    "Total PnL": f"{result.total_pnl:.2f}",
                    "FX Source": result.fx_source,
                    "Avg PnL": f"{result.avg_pnl:.2f}",
                    "Max Profit": f"{result.max_profit:.2f}",
                    "Max Loss": f"{result.max_loss:.2f}",
                    "Max Drawdown": f"{result.max_drawdown:.2f}",
                    "Profit Factor": f"{result.profit_factor:.2f}",
                    "Sharpe Ratio": f"{result.sharpe_ratio:.2f}",
                    "Score": f"{result.score:.2f}",
                }
            )

        columns = [
            "Rank",
            "Pattern",
            "Total Signals",
            "Total Trades",
            "Winning Trades",
            "Losing Trades",
            "Win Rate %",
            "Total PnL",
            "FX Source",
            "Avg PnL",
            "Max Profit",
            "Max Loss",
            "Max Drawdown",
            "Profit Factor",
            "Sharpe Ratio",
            "Score",
        ]
        if self._used_approx_fx:
            warnings.warn(
                "Exporting results that used static/approximate FX rates. "
                "For historical accuracy, provide fx_history or enable strict_fx=True.",
                UserWarning,
                stacklevel=2,
            )
            columns.append("FX Warning")
            for row in data:
                row["FX Warning"] = (
                    "Static/approximate FX rate used. For historical accuracy, provide fx_history or enable strict_fx=True."
                )
        df = pd.DataFrame(data, columns=columns)
        df.to_csv(filename, index=False)
        logger.info("Results exported to %s", filename)
        if self._verbose:
            print(f"Results exported to {filename}")
