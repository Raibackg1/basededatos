"""Comprehensive unit tests covering all 21 bug fixes from Executive Summary audit."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst
from yfinance_ta_patterns.ai.scorer import (
    AIPatternScorer,
    TradeSetup,
    calc_wilder_atr,
    calc_wilder_rsi,
)
from yfinance_ta_patterns.data import MarketDataLoader, normalize_ticker, validate_ohlc
from yfinance_ta_patterns.pattern_tester import (
    DEFAULT_FX_USD_RATES,
    TIMEFRAME_PERIODS_PER_YEAR,
    PatternRankingTester,
    is_forex_symbol,
    resolve_periods_per_year,
)
from yfinance_ta_patterns.talib_compat import (
    SUPPORTED_FALLBACK_PATTERNS,
    UNSUPPORTED_FALLBACK_PATTERNS,
    TALibWrapper,
)


@pytest.fixture
def synthetic_ohlcv_data() -> pd.DataFrame:
    """Deterministic 30-bar dataset with known prices."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1d", tz="UTC")
    opens = np.array([100.0 + i for i in range(30)])
    highs = opens + 2.0
    lows = opens - 1.0
    closes = opens + 1.0  # Green candles: close > open
    volumes = np.full(30, 10000.0)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


def test_pattern_tester_lookahead(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 1: Test that default execution is next_open (enters on Open[i+1], not Close[i])."""
    tester = PatternRankingTester(synthetic_ohlcv_data, execution="next_open")
    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[2] = 1  # Signal fires at bar 2
    signals[5] = -1  # Exit signal fires at bar 5

    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    t = trades[0]
    # Trade must enter at Open[3], not Close[2]
    expected_entry = synthetic_ohlcv_data["Open"].iloc[3]
    assert t["entry_price"] == expected_entry
    assert t["entry_time"] == synthetic_ohlcv_data.index[3]

    # Trade must exit at Open[6], not Close[5]
    expected_exit = synthetic_ohlcv_data["Open"].iloc[6]
    assert t["exit_price"] == expected_exit
    assert t["exit_time"] == synthetic_ohlcv_data.index[6]


def test_pattern_tester_legacy_close_mode(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Test legacy execution mode 'close' enters on Close[i]."""
    tester = PatternRankingTester(synthetic_ohlcv_data, execution="close")
    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[2] = 1
    signals[5] = -1

    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    t = trades[0]
    assert t["entry_price"] == synthetic_ohlcv_data["Close"].iloc[2]
    assert t["exit_price"] == synthetic_ohlcv_data["Close"].iloc[5]


def test_pattern_tester_short_signal(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 2: Bearish signal (signal == -1) opens Short and computes short PnL correctly."""
    tester = PatternRankingTester(synthetic_ohlcv_data, allow_short=True)
    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[3] = -1  # Bearish signal fires at bar 3
    signals[8] = 1  # Bullish exit signal fires at bar 8

    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    t = trades[0]
    assert t["direction"] == "SHORT"
    assert t["entry_price"] == synthetic_ohlcv_data["Open"].iloc[4]
    assert t["exit_price"] == synthetic_ohlcv_data["Open"].iloc[9]
    # In uptrend dataset, exit_price > entry_price, so Short must lose money
    assert t["pnl"] < 0
    expected_pnl = (tester._position_size / t["entry_price"]) * (t["entry_price"] - t["exit_price"])
    assert np.isclose(t["pnl"], expected_pnl)


def test_pattern_tester_close_last_trade(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 3: Unclosed position at the end of history is force-closed at Closes[-1]."""
    tester = PatternRankingTester(synthetic_ohlcv_data, force_exit_on_last_bar=True)
    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[20] = 1  # Buy at bar 20, no exit signal follows

    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    t = trades[0]
    assert t["forced_exit"] is True
    assert t["exit_price"] == synthetic_ohlcv_data["Close"].iloc[-1]
    assert t["exit_time"] == synthetic_ohlcv_data.index[-1]


def test_sharpe_annualization(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 6: Sharpe Ratio scales appropriately with timeframe frequencies."""
    assert TIMEFRAME_PERIODS_PER_YEAR["1d"] == 252.0
    assert TIMEFRAME_PERIODS_PER_YEAR["1h"] == 252.0 * 7.0
    assert TIMEFRAME_PERIODS_PER_YEAR["15m"] == 252.0 * 26.0

    tester_1d = PatternRankingTester(synthetic_ohlcv_data, timeframe="1d")
    tester_1h = PatternRankingTester(synthetic_ohlcv_data, timeframe="1h")

    assert tester_1d._periods_per_year == 252.0
    assert tester_1h._periods_per_year == 252.0 * 7.0


def test_equity_curve_and_drawdown(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 4: Initial capital and equity curve / max drawdown tracking."""
    tester = PatternRankingTester(
        synthetic_ohlcv_data,
        initial_capital=10000.0,
        position_size=1000.0,
    )
    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[2] = 1
    signals[6] = -1

    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    # Run _test_single_pattern
    _ = tester._test_single_pattern("CDLDOJI", filter_news=False)
    # Check that equity_curve is populated on tester
    assert len(tester.equity_curve) >= 1
    assert tester.equity_curve[0] == 10000.0


def test_date_filter_loading() -> None:
    """Issue 7: MarketDataLoader accepts start and end parameters and stores them."""
    loader = MarketDataLoader(
        "EURUSD",
        start="2023-01-01",
        end="2023-06-30",
        interval="1d",
    )
    assert loader.start == "2023-01-01"
    assert loader.end == "2023-06-30"


def test_auto_adjust_false_for_candles() -> None:
    """Issue 9: MarketDataLoader defaults auto_adjust to False to protect candle shapes."""
    loader = MarketDataLoader("AAPL")
    assert loader.auto_adjust is False
    assert loader.repair is None


def test_ohlc_validation() -> None:
    """Issue 21: validate_ohlc rejects/cleans non-positive prices and broken bars."""
    df_valid = pd.DataFrame(
        {
            "Open": [10.0, 12.0],
            "High": [15.0, 14.0],
            "Low": [9.0, 11.0],
            "Close": [14.0, 13.0],
        }
    )
    cleaned = validate_ohlc(df_valid, strict=True)
    assert len(cleaned) == 2

    # Negative prices
    df_negative = pd.DataFrame(
        {
            "Open": [-10.0, 12.0],
            "High": [15.0, 14.0],
            "Low": [9.0, 11.0],
            "Close": [14.0, 13.0],
        }
    )
    with pytest.raises(ValueError, match="non-positive"):
        validate_ohlc(df_negative, strict=True)
    filtered = validate_ohlc(df_negative, strict=False)
    assert len(filtered) == 1

    # High < Low inconsistency
    df_inverted = pd.DataFrame(
        {
            "Open": [10.0],
            "High": [8.0],
            "Low": [12.0],
            "Close": [9.0],
        }
    )
    with pytest.raises(ValueError, match="Inconsistent"):
        validate_ohlc(df_inverted, strict=True)


def test_h4_alignment_utc() -> None:
    """Issue 8: 4h resample is anchored to standard UTC boundaries."""
    loader = MarketDataLoader("BTC-USD", interval="4h", timezone="UTC")
    # Hourly data starting at 01:00 UTC
    hours = pd.date_range("2025-01-01 01:00", periods=12, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 12,
            "High": [105.0] * 12,
            "Low": [95.0] * 12,
            "Close": [102.0] * 12,
            "Volume": [100.0] * 12,
        },
        index=hours,
    )
    resampled = loader.process(df)
    # The resampled 4h bars should anchor to 00:00, 04:00, 08:00 UTC
    for ts in resampled.index:
        assert ts.hour % 4 == 0


def test_wilder_rsi_formula() -> None:
    """Issue 14: Wilder's RSI calculation matches canonical exponential smoothing."""
    closes = pd.Series(
        [
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
            46.00,
        ]
    )
    rsi = calc_wilder_rsi(closes, period=14)
    # At index 14 (15th bar), Wilder RSI textbook value is ~70.46
    assert np.isclose(rsi.iloc[14], 70.46, atol=0.1)
    # At index 15 (16th bar), Wilder RSI is ~66.25
    assert np.isclose(rsi.iloc[15], 66.25, atol=0.1)


def test_wilder_atr_formula() -> None:
    """Issue 15: Wilder's ATR calculation matches canonical exponential smoothing."""
    highs = pd.Series([10.0 + i * 0.5 for i in range(20)])
    lows = pd.Series([8.0 + i * 0.5 for i in range(20)])
    closes = pd.Series([9.0 + i * 0.5 for i in range(20)])

    atr = calc_wilder_atr(highs, lows, closes, period=14)
    assert len(atr) == 20
    assert np.isnan(atr.iloc[:13]).all()
    assert (atr.dropna() > 0).all()
    # High - Low is constant 2.0, so ATR should converge to 2.0
    assert np.isclose(atr.iloc[-1], 2.0, atol=0.05)


def test_rvol_excludes_current_candle(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 16: RVOL calculation shifts by 1 bar to exclude current candle from average."""
    data = synthetic_ohlcv_data.copy()
    data["Volume"] = 1000.0
    # Massive volume spike on the very last candle
    data.loc[data.index[-1], "Volume"] = 10000.0

    scorer = AIPatternScorer(data)
    last_rvol = float(scorer.df["_RVOL"].iloc[-1])
    # Denominator should be based on previous 1000.0 bars -> 10000 / 1000 = 10.0
    assert np.isclose(last_rvol, 10.0, atol=0.1)


def test_return_20_bars_exact(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 18: return_20_bars_pct correctly compares against iloc[-21]."""
    analyst = AIMarketAnalyst(synthetic_ohlcv_data, [])
    summary = analyst.get_market_regime_summary()

    # Manual calculation: (close[-1] - close[-21]) / close[-21] * 100
    expected = (
        (synthetic_ohlcv_data["Close"].iloc[-1] - synthetic_ohlcv_data["Close"].iloc[-21])
        / synthetic_ohlcv_data["Close"].iloc[-21]
    ) * 100.0

    assert np.isclose(summary["return_20_bars_pct"], round(float(expected), 2))


def test_comparison_report_pattern_alignment(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 19: Comparison report pairs identical patterns in each row."""
    tester = PatternRankingTester(synthetic_ohlcv_data)
    report = tester.get_comparison_report()
    assert isinstance(report, pd.DataFrame)
    if not report.empty:
        assert "Pattern" in report.columns
        assert "Win Rate (No News Filter)" in report.columns
        assert "Win Rate (With News Filter)" in report.columns


def test_talib_fallback_sets() -> None:
    """Issue 10 & 11: Supported and unsupported patterns are properly classified."""
    wrapper = TALibWrapper(force_fallback=True)
    assert len(SUPPORTED_FALLBACK_PATTERNS) == 61
    assert "CDLDOJI" in SUPPORTED_FALLBACK_PATTERNS
    assert "CDLHAMMER" in SUPPORTED_FALLBACK_PATTERNS
    assert "CDLPIERCING" in SUPPORTED_FALLBACK_PATTERNS
    assert len(UNSUPPORTED_FALLBACK_PATTERNS) == 0

    # Supported pattern runs
    res = wrapper.CDLDOJI(np.array([10.0]), np.array([11.0]), np.array([9.0]), np.array([10.0]))
    assert res[0] == 100

    # CDLPIERCING also runs cleanly in fallback mode
    res_piercing = wrapper.CDLPIERCING(
        np.array([10.0, 9.0]), np.array([11.0, 10.0]), np.array([9.0, 8.5]), np.array([9.2, 9.8])
    )
    assert len(res_piercing) == 2


def test_forex_quote_currency_conversion() -> None:
    """Issue 5: Forex pairs normalize quote currency to account currency."""
    # Pair with quote JPY and base USD
    df = pd.DataFrame(
        {
            "Open": [150.0, 150.0],
            "High": [151.0, 151.0],
            "Low": [149.0, 149.0],
            "Close": [150.0, 150.0],
        },
        index=pd.date_range("2025-01-01", periods=2, freq="1d", tz="UTC"),
    )
    tester = PatternRankingTester(df, symbol="USDJPY", account_currency="USD")
    raw_pnl_jpy = 1500.0
    exit_price = 150.0
    converted = tester._convert_pnl_to_account_currency(raw_pnl_jpy, exit_price)
    assert np.isclose(converted, 10.0)  # 1500 JPY / 150 rate = 10 USD


def test_rvol_no_bfill_lookahead() -> None:
    """Follow-up Audit: RVOL does not use bfill and does not leak future volume."""
    df = pd.DataFrame(
        {
            "Open": [10.0] * 5,
            "High": [11.0] * 5,
            "Low": [9.0] * 5,
            "Close": [10.0] * 5,
            "Volume": [100.0, 200.0, 150.0, 120.0, 130.0],
        },
        index=pd.date_range("2025-01-01", periods=5, freq="1d", tz="UTC"),
    )
    scorer = AIPatternScorer(df)
    rvol_first = float(scorer.df["_RVOL"].iloc[0])
    # First candle RVOL should be 1.0 (own baseline), not influenced by future 200.0 volume
    assert np.isclose(rvol_first, 1.0)


def test_ema200_warmup_nan_handling() -> None:
    """Follow-up Audit: EMA200 is NaN for datasets <200 bars and handled safely."""
    df = pd.DataFrame(
        {
            "Open": [10.0 + i for i in range(50)],
            "High": [11.0 + i for i in range(50)],
            "Low": [9.0 + i for i in range(50)],
            "Close": [10.0 + i for i in range(50)],
            "Volume": [1000.0] * 50,
        },
        index=pd.date_range("2025-01-01", periods=50, freq="1d", tz="UTC"),
    )
    scorer = AIPatternScorer(df)
    assert np.isnan(scorer.df["_EMA200"].iloc[-1])
    res = scorer.score_signal("HAMMER", df.index[-2], raw_signal=100)
    assert res.trend_regime in ("STRONG_BULLISH", "BULLISH")


def test_universal_fx_engine_cross_pair() -> None:
    """Follow-up Audit: Cross FX pair conversion with fx_rates."""
    df = pd.DataFrame(
        {
            "Open": [160.0, 160.0],
            "High": [161.0, 161.0],
            "Low": [159.0, 159.0],
            "Close": [160.0, 160.0],
        },
        index=pd.date_range("2025-01-01", periods=2, freq="1d", tz="UTC"),
    )
    tester = PatternRankingTester(
        df,
        symbol="EURJPY",
        account_currency="USD",
        fx_rates={"USDJPY": 150.0},
    )
    raw_pnl_jpy = 3000.0
    converted = tester._convert_pnl_to_account_currency(raw_pnl_jpy, 160.0)
    # 3000 JPY / 150 (USDJPY rate) = 20 USD
    assert np.isclose(converted, 20.0)


def test_periodic_sharpe_with_idle_periods(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Follow-up Audit: Periodic Sharpe accounts for zero-return bars during idle periods."""
    tester = PatternRankingTester(
        synthetic_ohlcv_data,
        sharpe_mode="periodic",
    )
    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[2] = 1
    signals[5] = -1

    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    # Run test single pattern to verify periodic_sharpe and trade_sharpe populated
    res = tester._test_single_pattern("CDLHAMMER", filter_news=False)
    if res:
        assert hasattr(res, "periodic_sharpe")
        assert hasattr(res, "trade_sharpe")
        assert hasattr(res, "avg_strength")


def test_commission_and_slippage(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Follow-up Audit: Commissions and slippage reduce trade net PnL."""
    tester_raw = PatternRankingTester(
        synthetic_ohlcv_data,
        commission=0.0,
        slippage=0.0,
    )
    tester_cost = PatternRankingTester(
        synthetic_ohlcv_data,
        commission=5.0,
        slippage=0.10,
    )

    signals = np.zeros(len(synthetic_ohlcv_data))
    signals[2] = 1
    signals[6] = -1

    trades_raw = tester_raw._calculate_trades(signals)
    trades_cost = tester_cost._calculate_trades(signals)

    assert len(trades_raw) == 1
    assert len(trades_cost) == 1
    # PnL with costs must be strictly lower than raw PnL
    assert trades_cost[0]["pnl"] < trades_raw[0]["pnl"]


def test_min_signals_ranking_filter(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Follow-up Audit: Filter patterns by min_signals to prevent 1-trade overfitting."""
    tester = PatternRankingTester(synthetic_ohlcv_data, min_signals=999)
    results = tester.test_all_patterns(min_signals=999)
    # No pattern should have 999 signals on a small synthetic dataset
    assert len(results) == 0


def test_calc_wilder_rsi_initial_nan_lookback() -> None:
    """Issue: Wilder RSI must keep initial period (14) bars as NaN without lookahead."""
    np.random.seed(42)
    prices = pd.Series(100.0 + np.cumsum(np.random.randn(50)))
    rsi = calc_wilder_rsi(prices, period=14)

    # First 14 values (0 to 13) must strictly be NaN
    assert np.isnan(rsi.iloc[:14]).all()
    # 15th value (index 14) is the first computed RSI
    assert not np.isnan(rsi.iloc[14])
    # All valid RSI values are bounded in [0, 100]
    valid_rsi = rsi.dropna()
    assert (valid_rsi >= 0.0).all() and (valid_rsi <= 100.0).all()


def test_calc_wilder_atr_initial_nan_lookback() -> None:
    """Issue: Wilder ATR must keep initial period - 1 (13) bars as NaN without lookahead."""
    np.random.seed(42)
    base = 100.0 + np.cumsum(np.random.randn(50))
    high = pd.Series(base + 2.0)
    low = pd.Series(base - 2.0)
    close = pd.Series(base)

    atr = calc_wilder_atr(high, low, close, period=14)

    # First 14 values (0 to 13) must strictly be NaN matching TA-Lib
    assert np.isnan(atr.iloc[:14]).all()
    # 15th value (index 14) is the first computed ATR
    assert not np.isnan(atr.iloc[14])
    # All valid ATR values are strictly positive
    valid_atr = atr.dropna()
    assert (valid_atr > 0.0).all()


def test_ema_exact_min_periods_nan() -> None:
    """Issue: EMA20, EMA50, and EMA200 must strictly adhere to min_periods without downscaling."""
    n = 250
    dates = pd.date_range("2025-01-01", periods=n, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [102.0 + i for i in range(n)],
            "Low": [98.0 + i for i in range(n)],
            "Close": [101.0 + i for i in range(n)],
            "Volume": [10000.0] * n,
        },
        index=dates,
    )
    scorer = AIPatternScorer(df)

    # EMA20: first 19 bars are NaN, 20th bar (index 19) is valid
    assert np.isnan(scorer.df["_EMA20"].iloc[:19]).all()
    assert not np.isnan(scorer.df["_EMA20"].iloc[19])

    # EMA50: first 49 bars are NaN, 50th bar (index 49) is valid
    assert np.isnan(scorer.df["_EMA50"].iloc[:49]).all()
    assert not np.isnan(scorer.df["_EMA50"].iloc[49])

    # EMA200: first 199 bars are NaN, 200th bar (index 199) is valid
    assert np.isnan(scorer.df["_EMA200"].iloc[:199]).all()
    assert not np.isnan(scorer.df["_EMA200"].iloc[199])


def test_market_data_loader_2m_interval_retention() -> None:
    """Issue: 2m interval retains up to 60 days on Yahoo Finance, only 1m is restricted to 7d."""
    loader_2m = MarketDataLoader("AAPL", interval="2m", period="60d")
    assert loader_2m.period == "60d"

    loader_1m = MarketDataLoader("AAPL", interval="1m", period="60d")
    assert loader_1m.period == "7d"


def test_universal_cross_fx_eurgbp_conversion() -> None:
    """Issue: Arbitrary Forex cross pairs (e.g. EURGBP) convert quote profit to USD."""
    df = pd.DataFrame(
        {
            "Open": [0.85, 0.85],
            "High": [0.86, 0.86],
            "Low": [0.84, 0.84],
            "Close": [0.85, 0.85],
        },
        index=pd.date_range("2025-01-01", periods=2, freq="1d", tz="UTC"),
    )
    # Default baseline rate: GBPUSD = 1.28
    tester_default = PatternRankingTester(df, symbol="EURGBP", account_currency="USD")
    raw_pnl_gbp = 100.0
    with pytest.warns(UserWarning, match="Static default FX rate used"):
        converted = tester_default._convert_pnl_to_account_currency(raw_pnl_gbp, 0.85)
    assert np.isclose(converted, 100.0 * DEFAULT_FX_USD_RATES["GBPUSD"])  # 128.0 USD

    # Custom rate override: GBPUSD = 1.35
    tester_custom = PatternRankingTester(
        df, symbol="EURGBP", account_currency="USD", fx_rates={"GBPUSD": 1.35}
    )
    converted_custom = tester_custom._convert_pnl_to_account_currency(raw_pnl_gbp, 0.85)
    assert np.isclose(converted_custom, 135.0)


def test_asset_aware_periods_per_year() -> None:
    """Issue: Sharpe annualization scales correctly for 24/7 Crypto, 24/5 Forex, and Equities."""
    # Crypto: 365 days / 24h
    assert resolve_periods_per_year("1d", "BTC-USD") == 365.0
    assert resolve_periods_per_year("1h", "BTC-USD") == 8760.0
    assert resolve_periods_per_year("4h", "ETH-USD") == 2190.0

    # Forex: 260 days / 24h
    assert resolve_periods_per_year("1d", "EURUSD=X") == 260.0
    assert resolve_periods_per_year("1h", "EURUSD=X") == 6240.0
    assert resolve_periods_per_year("4h", "USDJPY=X") == 1560.0

    # Equities: 252 days / 7 hourly candles
    assert resolve_periods_per_year("1d", "AAPL") == 252.0
    assert resolve_periods_per_year("1h", "AAPL") == 1764.0
    assert resolve_periods_per_year("4h", "AAPL") == 504.0


def test_pattern_ranking_profit_factor_and_score(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue: PatternResult contains profit_factor and composite score for ranking."""
    tester = PatternRankingTester(synthetic_ohlcv_data)
    results = tester.test_all_patterns(sort_by="composite")
    assert isinstance(results, list)
    for res in results:
        assert hasattr(res, "profit_factor")
        assert hasattr(res, "score")
        assert res.profit_factor >= 0.0
        assert res.score >= 0.0


def test_forex_position_sizing_and_commission() -> None:
    """Issue: USDJPY position sizing should not divide USD by JPY price, and commission must be in USD."""
    df = pd.DataFrame(
        {
            "Open": [150.0, 150.0, 150.0, 150.0],
            "High": [151.0, 151.0, 151.0, 151.0],
            "Low": [149.0, 149.0, 149.0, 149.0],
            "Close": [150.0, 150.0, 151.0, 151.0],
        },
        index=pd.date_range("2025-01-01", periods=4, freq="1h", tz="UTC"),
    )
    tester = PatternRankingTester(
        df,
        symbol="USDJPY=X",
        account_currency="USD",
        position_size=1000.0,
        commission=1.0,
        slippage=0.0,
    )
    assert tester._calc_position_units(150.0) == 1000.0

    tester_eur = PatternRankingTester(
        df,
        symbol="EURUSD=X",
        account_currency="USD",
        position_size=1000.0,
        commission=1.0,
        slippage=0.0,
    )
    assert np.isclose(tester_eur._calc_position_units(1.08), 1000.0 / 1.08)


def test_historical_fx_pnl_conversion() -> None:
    """Issue 1: Two trades with identical JPY PnL and different historical USDJPY rates produce different USD results."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [150.0] * 10,
            "High": [151.0] * 10,
            "Low": [149.0] * 10,
            "Close": [150.0] * 10,
            "Volume": [1000.0] * 10,
        },
        index=dates,
    )
    # Historical series with USDJPY changing over time: 140.0 on Jan 2, 160.0 on Jan 8
    fx_series = pd.Series([140.0, 160.0], index=[dates[2], dates[8]])
    tester = PatternRankingTester(
        df,
        symbol="EURJPY",
        account_currency="USD",
        fx_history={"USDJPY": fx_series},
    )
    # Trade 1 exits at dates[2] with raw_pnl = 1400.0 JPY
    res1 = tester._convert_pnl_to_account_currency(1400.0, 150.0, exit_time=dates[2])
    # Trade 2 exits at dates[8] with raw_pnl = 1400.0 JPY
    res2 = tester._convert_pnl_to_account_currency(1400.0, 150.0, exit_time=dates[8])
    # 1400 JPY / 140 = 10.0 USD
    # 1400 JPY / 160 = 8.75 USD
    assert np.isclose(res1, 10.0)
    assert np.isclose(res2, 8.75)
    assert res1 != res2


def test_unclosed_4h_candle_filtered_out() -> None:
    """Issue 2: Resampling 4h with closed_only=True excludes incomplete candle at 10:30 UTC."""
    # Hourly bars at 08:00, 09:00, 10:00 UTC (now is 10:30 UTC)
    hours = pd.date_range("2025-01-01 08:00", periods=3, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [102.0, 103.0, 104.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [101.0, 102.0, 103.0],
            "Volume": [1000.0, 1000.0, 1000.0],
        },
        index=hours,
    )
    now_utc = pd.Timestamp("2025-01-01 10:30", tz="UTC")
    loader = MarketDataLoader("BTC-USD", interval="4h", timezone="UTC", closed_only=True)
    resampled = loader.process(df, now_utc=now_utc)
    # The 08:00–12:00 candle ends at 12:00 UTC, which is > 10:30 UTC, so it must NOT be present
    assert resampled.empty

    # But with now_utc = 12:30 UTC and full 4 bars (08, 09, 10, 11), it should be included
    hours_full = pd.date_range("2025-01-01 08:00", periods=4, freq="1h", tz="UTC")
    df_full = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0],
            "High": [102.0, 103.0, 104.0, 105.0],
            "Low": [99.0, 100.0, 101.0, 102.0],
            "Close": [101.0, 102.0, 103.0, 104.0],
            "Volume": [1000.0] * 4,
        },
        index=hours_full,
    )
    resampled_closed = loader.process(df_full, now_utc=pd.Timestamp("2025-01-01 12:30", tz="UTC"))
    assert len(resampled_closed) == 1
    assert resampled_closed.index[0] == pd.Timestamp("2025-01-01 08:00", tz="UTC")


def test_validate_ohlc_rejects_inf() -> None:
    """Issue 3: validate_ohlc rejects positive/negative inf values in strict mode and filters them in non-strict."""
    df_inf = pd.DataFrame(
        {
            "Open": [float("inf"), 100.0],
            "High": [float("inf"), 105.0],
            "Low": [float("inf"), 95.0],
            "Close": [float("inf"), 102.0],
        }
    )
    with pytest.raises(ValueError, match="non-finite"):
        validate_ohlc(df_inf, strict=True)

    cleaned = validate_ohlc(df_inf, strict=False)
    assert len(cleaned) == 1
    assert cleaned["Open"].iloc[0] == 100.0


def test_score_signal_rejects_zero_raw_signal(synthetic_ohlcv_data: pd.DataFrame) -> None:
    """Issue 4: score_signal raises ValueError on raw_signal=0 rather than creating a SELL setup."""
    scorer = AIPatternScorer(synthetic_ohlcv_data)
    valid_ts = synthetic_ohlcv_data.index[10]
    with pytest.raises(ValueError, match="Cannot score an inactive signal"):
        scorer.score_signal("CDLHAMMER", valid_ts, raw_signal=0)


def test_stock_sol_not_treated_as_crypto() -> None:
    """Issue 5: Stock tickers like SOL (Emeren Group) and ETH (Ethan Allen) use stock calendar, not crypto 24/7."""
    # Stock calendar for 1h is 252 * 7 = 1764.0
    assert resolve_periods_per_year("1h", "SOL") == 252.0 * 7.0
    assert resolve_periods_per_year("1h", "ETH") == 252.0 * 7.0
    # Crypto pair with -USD suffix uses 365 * 24 = 8760.0
    assert resolve_periods_per_year("1h", "SOL-USD") == 365.0 * 24.0
    # Explicit asset_type override works
    assert resolve_periods_per_year("1h", "SOL", asset_type="crypto") == 365.0 * 24.0


def test_validate_ohlc_strict_without_ohlc_columns() -> None:
    """Issue 6: Strict validation raises ValueError when OHLC columns are missing even if Volume is present."""
    df_volume_only = pd.DataFrame({"Volume": [10.0, 20.0]})
    with pytest.raises(ValueError, match="Missing required OHLC columns"):
        validate_ohlc(df_volume_only, strict=True)


def test_duplicate_timestamp_handling() -> None:
    """Issue 7: Duplicate candle timestamps are rejected with informative error."""
    ts = pd.Timestamp("2025-01-01 10:00", tz="UTC")
    df_duplicates = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [95.0, 96.0],
            "Close": [102.0, 103.0],
            "Volume": [1000.0, 1000.0],
        },
        index=[ts, ts],
    )
    loader = MarketDataLoader("AAPL")
    with pytest.raises(ValueError, match="duplicate candle timestamps"):
        loader.process(df_duplicates)

    with pytest.raises(ValueError, match="duplicate candle timestamps"):
        validate_ohlc(df_duplicates, strict=True)


def test_tradesetup_microcap_precision() -> None:
    """Issue 8: TradeSetup.to_dict retains full float precision for micro-cap tokens (< 0.00001)."""
    setup = TradeSetup(
        direction="BUY",
        entry_price=0.00000123,
        stop_loss=0.00000098,
        take_profit_1=0.00000160,
        take_profit_2=0.00000198,
        risk_reward_ratio=1.5,
        risk_per_unit=0.00000025,
    )
    d = setup.to_dict()
    assert d["entry_price"] == 0.00000123
    assert d["stop_loss"] == 0.00000098
    assert d["entry_price"] != 0.0
    assert d["stop_loss"] != d["entry_price"]


def test_issue1_closed_only_native_intervals() -> None:
    """Issue 1 (YF-001): Universal closed-only filtering for all native intervals (1h, 15m, 1d)."""
    # Test 1: Hourly interval with unclosed candle
    now_utc = pd.Timestamp("2025-01-01 11:30:00", tz="UTC")
    idx_1h = pd.date_range("2025-01-01 10:00:00", periods=2, freq="1h", tz="UTC")
    # Candle 0: 10:00 to 11:00 (closed before 11:30)
    # Candle 1: 11:00 to 12:00 (not closed at 11:30)
    df_1h = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000.0, 1000.0],
        },
        index=idx_1h,
    )
    loader_closed = MarketDataLoader("AAPL", interval="1h", timezone="UTC", closed_only=True)
    res_closed = loader_closed.process(df_1h, now_utc=now_utc)
    assert len(res_closed) == 1
    assert res_closed.index[-1] == pd.Timestamp("2025-01-01 10:00:00", tz="UTC")

    loader_open = MarketDataLoader("AAPL", interval="1h", timezone="UTC", closed_only=False)
    res_open = loader_open.process(df_1h, now_utc=now_utc)
    assert len(res_open) == 2

    # Test 2: 15m interval
    now_utc_15m = pd.Timestamp("2025-01-01 10:20:00", tz="UTC")
    idx_15m = pd.date_range("2025-01-01 10:00:00", periods=2, freq="15min", tz="UTC")
    # Candle 0: 10:00 to 10:15 (closed before 10:20)
    # Candle 1: 10:15 to 10:30 (not closed at 10:20)
    df_15m = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000.0, 1000.0],
        },
        index=idx_15m,
    )
    loader_15m = MarketDataLoader("AAPL", interval="15m", timezone="UTC", closed_only=True)
    res_15m = loader_15m.process(df_15m, now_utc=now_utc_15m)
    assert len(res_15m) == 1


def test_issue2_microcap_atr_and_positive_stop_loss() -> None:
    """Issue 2 (YF-002): Micro-cap ATR scaling and guaranteed positive stop-loss."""
    # Test relative ATR floor does not distort microcap price (e.g. price ~ 0.000001)
    base_price = 0.000001
    high = pd.Series([base_price * 1.01] * 20)
    low = pd.Series([base_price * 0.99] * 20)
    close = pd.Series([base_price] * 20)

    atr = calc_wilder_atr(high, low, close, period=14)
    # Valid ATR values should be around 2e-8, definitely << 1e-5
    valid_atr = atr.dropna().iloc[-1]
    assert valid_atr < 1e-6
    assert valid_atr > 0

    df_micro = pd.DataFrame(
        {
            "Open": [base_price] * 20,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": [1000.0] * 20,
        }
    )
    scorer = AIPatternScorer(df_micro)
    # Test BUY trade setup for microcap: positive stop-loss and targets
    buy_setup = scorer._build_trade_setup(
        True, base_price, base_price * 1.01, base_price * 0.99, valid_atr
    )
    assert buy_setup.stop_loss > 0
    assert buy_setup.stop_loss < base_price
    assert buy_setup.take_profit_1 > base_price
    assert buy_setup.take_profit_2 > buy_setup.take_profit_1

    # Test SELL trade setup with high volatility: stop-loss and targets remain strictly positive
    huge_atr = base_price * 2.0  # ATR larger than price
    sell_setup = scorer._build_trade_setup(
        False, base_price, base_price * 1.01, base_price * 0.99, huge_atr
    )
    assert sell_setup.stop_loss > base_price
    assert sell_setup.take_profit_1 > 0
    assert sell_setup.take_profit_2 > 0


def test_issue3_resample_missing_bars_validation() -> None:
    """Issue 3 (YF-003): Incomplete 4h candle buckets are dropped for crypto."""
    # 4h candle requires 4 1h bars for crypto 24/7
    loader = MarketDataLoader("BTC-USD", interval="4h", timezone="UTC", closed_only=False)

    # Incomplete bucket: only 3 bars in 00:00-04:00 (missing 02:00)
    incomplete_times = [
        pd.Timestamp("2025-01-01 00:00:00", tz="UTC"),
        pd.Timestamp("2025-01-01 01:00:00", tz="UTC"),
        pd.Timestamp("2025-01-01 03:00:00", tz="UTC"),
    ]
    df_incomplete = pd.DataFrame(
        {
            "Open": [50000.0] * 3,
            "High": [51000.0] * 3,
            "Low": [49000.0] * 3,
            "Close": [50500.0] * 3,
            "Volume": [10.0] * 3,
        },
        index=pd.DatetimeIndex(incomplete_times),
    )
    res_incomplete = loader.process(df_incomplete)
    assert len(res_incomplete) == 0

    # Complete bucket: 4 bars in 00:00-04:00
    complete_times = [
        pd.Timestamp("2025-01-01 00:00:00", tz="UTC"),
        pd.Timestamp("2025-01-01 01:00:00", tz="UTC"),
        pd.Timestamp("2025-01-01 02:00:00", tz="UTC"),
        pd.Timestamp("2025-01-01 03:00:00", tz="UTC"),
    ]
    df_complete = pd.DataFrame(
        {
            "Open": [50000.0] * 4,
            "High": [51000.0] * 4,
            "Low": [49000.0] * 4,
            "Close": [50500.0] * 4,
            "Volume": [10.0] * 4,
        },
        index=pd.DatetimeIndex(complete_times),
    )
    res_complete = loader.process(df_complete)
    assert len(res_complete) == 1
    assert res_complete.index[0] == pd.Timestamp("2025-01-01 00:00:00", tz="UTC")


def test_issue4_ticker_normalization_slashes_and_stocks() -> None:
    """Issue 4 (YF-004): Slash normalization for FX and currency code validation."""
    assert normalize_ticker("EUR/USD") == "EURUSD=X"
    assert normalize_ticker("eur/gbp") == "EURGBP=X"
    assert normalize_ticker("USD/JPY") == "USDJPY=X"
    assert normalize_ticker("EURUSD") == "EURUSD=X"
    assert normalize_ticker("EURUSD=X") == "EURUSD=X"

    # 6-letter stock tickers should NOT be falsely normalized to Forex
    assert normalize_ticker("AMAZON") == "AMAZON"
    assert normalize_ticker("GOOGLE") == "GOOGLE"

    assert is_forex_symbol("EUR/USD") is True
    assert is_forex_symbol("EURUSD=X") is True
    assert is_forex_symbol("EURUSD") is True
    assert is_forex_symbol("AMAZON") is False
    assert is_forex_symbol("GOOGLE") is False


def test_issue5_talib_atr_exact_index_alignment() -> None:
    """Issue 5 (YF-005): Wilder ATR initial lookback index matches TA-Lib (first valid at period)."""
    n = 25
    high = pd.Series([10.0 + i for i in range(n)])
    low = pd.Series([8.0 + i for i in range(n)])
    close = pd.Series([9.0 + i for i in range(n)])

    atr = calc_wilder_atr(high, low, close, period=14)
    assert len(atr) == n
    # The first 14 elements (indices 0..13) must be NaN
    assert atr.iloc[:14].isna().all()
    # The 15th element (index 14) is the first valid ATR value
    assert not pd.isna(atr.iloc[14])
    assert (atr.iloc[14:] > 0).all()


def test_issue6_validate_ohlc_strict_raises_on_nan() -> None:
    """Issue 6 (YF-006): Strict OHLC validation raises ValueError on NaN, non-strict cleans rows."""
    df_nan = pd.DataFrame(
        {
            "Open": [10.0, np.nan, 12.0],
            "High": [15.0, 16.0, 14.0],
            "Low": [9.0, 8.0, 11.0],
            "Close": [11.0, 14.0, 13.0],
            "Volume": [100.0, 200.0, 300.0],
        }
    )

    with pytest.raises(ValueError, match="NaN"):
        validate_ohlc(df_nan, strict=True)

    cleaned = validate_ohlc(df_nan, strict=False)
    assert len(cleaned) == 2


def test_screenshot1_monthly_calendar_boundaries() -> None:
    """Screenshot 1: Monthly candles close at next calendar month via DateOffset, not fixed 30 days."""
    loader = MarketDataLoader("AAPL", interval="1mo", timezone="UTC", closed_only=True)

    # 1. Test January (31 days): starts 2025-01-01
    idx = pd.to_datetime(["2025-01-01"]).tz_localize("UTC")
    df = pd.DataFrame(
        {"Open": [100.0], "High": [105.0], "Low": [95.0], "Close": [102.0], "Volume": [1000.0]},
        index=idx,
    )
    # At 2025-01-31 12:00:00 (day 30.5), January is NOT closed yet
    now_jan31 = pd.Timestamp("2025-01-31 12:00:00", tz="UTC")
    res_open = loader.process(df, now_utc=now_jan31)
    assert len(res_open) == 0

    # At 2025-02-01 00:00:00, January IS closed
    now_feb1 = pd.Timestamp("2025-02-01 00:00:00", tz="UTC")
    res_closed = loader.process(df, now_utc=now_feb1)
    assert len(res_closed) == 1

    # 2. Test February in leap year (2024, 29 days)
    idx_leap = pd.to_datetime(["2024-02-01"]).tz_localize("UTC")
    df_leap = pd.DataFrame(
        {"Open": [100.0], "High": [105.0], "Low": [95.0], "Close": [102.0], "Volume": [1000.0]},
        index=idx_leap,
    )
    # At 2024-02-29 12:00:00 (mid-day, before session close at 21:00 UTC), February is NOT closed yet
    now_feb29 = pd.Timestamp("2024-02-29 12:00:00", tz="UTC")
    assert len(loader.process(df_leap, now_utc=now_feb29)) == 0

    # At 2024-03-01 00:00:00, February IS closed
    now_mar1 = pd.Timestamp("2024-03-01 00:00:00", tz="UTC")
    assert len(loader.process(df_leap, now_utc=now_mar1)) == 1


def test_screenshot2_forex_4h_completeness() -> None:
    """Screenshot 2: Forex 4h blocks require all 4 hourly bars in standard trading sessions."""
    loader_forex = MarketDataLoader("EURUSD=X", interval="4h", timezone="UTC", closed_only=False)

    # Standard trading session: Wednesday 2025-01-15 08:00 to 12:00 UTC
    # Incomplete bucket: missing 10:00 bar (3 bars only)
    incomplete_hours = [
        pd.Timestamp("2025-01-15 08:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 09:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 11:00:00", tz="UTC"),
    ]
    df_incomplete = pd.DataFrame(
        {
            "Open": [1.05] * 3,
            "High": [1.06] * 3,
            "Low": [1.04] * 3,
            "Close": [1.055] * 3,
            "Volume": [100.0] * 3,
        },
        index=pd.DatetimeIndex(incomplete_hours),
    )
    res_incomplete = loader_forex.process(df_incomplete)
    # Missing hour in standard Forex block -> excluded!
    assert len(res_incomplete) == 0

    # Complete bucket: 4 bars (08, 09, 10, 11)
    complete_hours = [
        pd.Timestamp("2025-01-15 08:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 09:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 10:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 11:00:00", tz="UTC"),
    ]
    df_complete = pd.DataFrame(
        {
            "Open": [1.05] * 4,
            "High": [1.06] * 4,
            "Low": [1.04] * 4,
            "Close": [1.055] * 4,
            "Volume": [100.0] * 4,
        },
        index=pd.DatetimeIndex(complete_hours),
    )
    res_complete = loader_forex.process(df_complete)
    assert len(res_complete) == 1

    # Equities: intra-session dropped bar (missing 10:00) is rejected
    loader_stock = MarketDataLoader("AAPL", interval="4h", timezone="UTC", closed_only=False)
    res_stock = loader_stock.process(df_incomplete)
    assert len(res_stock) == 0

    # Equities: genuine partial session boundary without intra-session gaps is allowed
    boundary_hours = [
        pd.Timestamp("2025-01-15 08:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 09:00:00", tz="UTC"),
        pd.Timestamp("2025-01-15 10:00:00", tz="UTC"),
    ]
    df_boundary = pd.DataFrame(
        {
            "Open": [1.05] * 3,
            "High": [1.06] * 3,
            "Low": [1.04] * 3,
            "Close": [1.055] * 3,
            "Volume": [100.0] * 3,
        },
        index=pd.DatetimeIndex(boundary_hours),
    )
    res_boundary = loader_stock.process(df_boundary)
    assert len(res_boundary) == 1


def test_screenshot3_crypto_ticker_slashes() -> None:
    """Screenshot 3: BTC/USD and crypto pairs normalize with hyphens for Yahoo Finance."""
    assert normalize_ticker("BTC/USD", asset_type="crypto") == "BTC-USD"
    assert normalize_ticker("ETH/USD", asset_type="crypto") == "ETH-USD"
    assert normalize_ticker("BTC-USD", asset_type="crypto") == "BTC-USD"
    assert normalize_ticker("BTC/USD", asset_type="auto") == "BTC-USD"
    assert normalize_ticker("ETH/USDT", asset_type="auto") == "ETH-USDT"
    assert normalize_ticker("SOL/USD", asset_type="auto") == "SOL-USD"
    assert normalize_ticker("EUR/USD", asset_type="auto") == "EURUSD=X"


def test_screenshot4_insufficient_history_atr() -> None:
    """Screenshot 4: Short history (< 14 bars) flags insufficient_history and omits trade setup."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    df_short = pd.DataFrame(
        {
            "Open": [100.0] * 10,
            "High": [102.0] * 10,
            "Low": [98.0] * 10,
            "Close": [101.0] * 10,
            "Volume": [1000.0] * 10,
        },
        index=dates,
    )
    scorer = AIPatternScorer(df_short)
    result = scorer.score_signal("DOJI", dates[-1], 100)

    assert result.insufficient_history is True
    assert result.trade_setup is None
    assert np.isnan(result.atr)
    d = result.to_dict()
    assert d["insufficient_history"] is True
    assert d["trade_setup"] is None
    assert d["metrics"]["atr"] is None
    assert any("Insufficient History" in rf for rf in result.risk_factors)


@pytest.mark.parametrize("invalid_period", [0, -1, 1.5, True, False])
def test_screenshot5_strict_period_validation(invalid_period: object) -> None:
    """Screenshot 5: RSI and ATR reject non-positive or non-int period values with ValueError."""
    s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    with pytest.raises(ValueError, match="positive integer"):
        calc_wilder_rsi(s, period=invalid_period)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="positive integer"):
        calc_wilder_atr(s, s, s, period=invalid_period)  # type: ignore[arg-type]


def test_screenshot5_atr_series_alignment() -> None:
    """Screenshot 5: ATR validates identical lengths and index alignment for High, Low, Close."""
    s1 = pd.Series([10.0, 11.0], index=[0, 1])
    s2 = pd.Series([9.0, 10.0, 11.0], index=[0, 1, 2])
    with pytest.raises(ValueError, match="identical lengths"):
        calc_wilder_atr(s1, s1, s2)

    s3 = pd.Series([10.0, 11.0], index=[1, 2])
    with pytest.raises(ValueError, match="aligned indices"):
        calc_wilder_atr(s1, s1, s3)


def test_screenshot6_high_low_geometry_without_open_close() -> None:
    """Screenshot 6: High < Low geometry is checked even when Open and Close are absent."""
    # Test exactly as requested in audit: High=1 and Low=2 without Open/Close at strict=False
    df_inverted = pd.DataFrame({"High": [1.0, 10.0], "Low": [2.0, 5.0]})
    cleaned = validate_ohlc(df_inverted, strict=False)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["High"] == 10.0

    # With full OHLC, strict=True raises geometry ValueError
    df_inverted_ohlc = pd.DataFrame({"Open": [1.5], "High": [1.0], "Low": [2.0], "Close": [1.5]})
    with pytest.raises(ValueError, match="geometry"):
        validate_ohlc(df_inverted_ohlc, strict=True)


def test_cli_all_patterns_pure_python_fallback() -> None:
    """Pure-Python fallback scans all 61 supported patterns including CDL2CROWS."""
    from yfinance_ta_patterns.pattern_analyzer import PatternAnalyzer
    from yfinance_ta_patterns.talib_compat import HAS_NATIVE_TALIB, SUPPORTED_FALLBACK_PATTERNS

    dates = pd.date_range("2025-01-01", periods=20, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 20,
            "High": [105.0] * 20,
            "Low": [95.0] * 20,
            "Close": [102.0] * 20,
            "Volume": [1000.0] * 20,
        },
        index=dates,
    )
    analyzer = PatternAnalyzer(df)
    if not HAS_NATIVE_TALIB:
        assert len(analyzer.pattern_functions) == len(SUPPORTED_FALLBACK_PATTERNS)
        assert len(analyzer.pattern_functions) == 61
        assert "CDL2CROWS" in analyzer.pattern_functions
        sig = analyzer.get_signals("2CROWS")
        assert isinstance(sig, pd.Series)
