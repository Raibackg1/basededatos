"""Tests for Audit Fixes v15:
1. Subunit Quote Currencies (ILA, ZAc, GBp) in pattern_tester.py and data.py
2. Static FX Warning & Transparency in pattern_tester.py
3. Incomplete Crypto Ticker Normalization & 404s in data.py
4. Sharpe on Trading Calendar Boundary for Tokyo in pattern_tester.py
5. Market Regime Contract & AI Analyst Asset Type in ai/analyst.py
6. Zero Score Ambiguity in PatternConfidenceResult in ai/scorer.py
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst
from yfinance_ta_patterns.ai.scorer import PatternConfidenceResult
from yfinance_ta_patterns.data import MarketDataLoader, normalize_ticker, resolve_asset_currencies
from yfinance_ta_patterns.pattern_tester import (
    DEFAULT_FX_USD_RATES,
    PatternRankingTester,
    PatternResult,
    resolve_periods_per_year,
)

# ==============================================================================
# 1. Subunit Quote Currencies (ILA, ZAc, GBp)
# ==============================================================================


def test_subunit_currency_rates_resolution() -> None:
    """ILA and ZAc must scale by 0.01 against their base parent (ILS and ZAR)."""
    dates = pd.date_range("2025-01-01", periods=3, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [98.0, 99.0, 100.0],
            "Close": [103.0, 104.0, 105.0],
            "Volume": [1000, 1000, 1000],
        },
        index=dates,
    )

    tester = PatternRankingTester(df, symbol="TEVA.TA", account_currency="USD")

    # 1. Direct conversion to parent currency
    rate_ila_ils, src_ila_ils = tester._resolve_fx_rate_with_source("ILA", "ILS")
    assert rate_ila_ils == pytest.approx(0.01)
    assert src_ila_ils == "same_currency"

    rate_ils_ila, src_ils_ila = tester._resolve_fx_rate_with_source("ILS", "ILA")
    assert rate_ils_ila == pytest.approx(100.0)
    assert src_ils_ila == "same_currency"

    # 2. Subunit to subunit of same parent
    rate_same, src_same = tester._resolve_fx_rate_with_source("ILA", "ILA")
    assert rate_same == pytest.approx(1.0)
    assert src_same == "same_currency"

    # 3. ZAc to ZAR and vice versa
    rate_zac_zar, src_zac_zar = tester._resolve_fx_rate_with_source("ZAc", "ZAR")
    assert rate_zac_zar == pytest.approx(0.01)
    assert src_zac_zar == "same_currency"

    rate_zar_zac, src_zar_zac = tester._resolve_fx_rate_with_source("ZAR", "ZAc")
    assert rate_zar_zac == pytest.approx(100.0)
    assert src_zar_zac == "same_currency"

    # 4. Conversion to USD via DEFAULT_FX_USD_RATES
    # USDILS is 3.70 -> ILSUSD = 1 / 3.70 -> ILAUSD = 0.01 / 3.70
    rate_ila_usd, src_ila_usd = tester._resolve_fx_rate_with_source("ILA", "USD")
    expected_ila_usd = 0.01 * (1.0 / DEFAULT_FX_USD_RATES["USDILS"])
    assert rate_ila_usd == pytest.approx(expected_ila_usd)
    assert src_ila_usd == "default_static"

    # USDZAR is 18.0 -> ZARUSD = 1 / 18.0 -> ZAcUSD = 0.01 / 18.0
    rate_zac_usd, src_zac_usd = tester._resolve_fx_rate_with_source("ZAc", "USD")
    expected_zac_usd = 0.01 * (1.0 / DEFAULT_FX_USD_RATES["USDZAR"])
    assert rate_zac_usd == pytest.approx(expected_zac_usd)
    assert src_zac_usd == "default_static"


def test_subunit_currency_pnl_conversion() -> None:
    """PnL earned in ILA or ZAc must scale down by 100x when converted to parent currency."""
    dates = pd.date_range("2025-01-01", periods=3, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [98.0, 99.0, 100.0],
            "Close": [103.0, 104.0, 105.0],
            "Volume": [1000, 1000, 1000],
        },
        index=dates,
    )

    # Instrument in ILA with ILS account currency
    tester = PatternRankingTester(
        df,
        symbol="TEVA.TA",
        quote_currency="ILA",
        account_currency="ILS",
    )
    raw_pnl = 10000.0  # 10,000 Agorot
    conv_pnl = tester._convert_pnl_to_account_currency(raw_pnl, exit_price=105.0)
    assert conv_pnl == pytest.approx(100.0)  # Exactly 100 ILS


def test_resolve_asset_currencies_preserves_subunit_metadata() -> None:
    """resolve_asset_currencies must preserve metadata_currency for ILA and ZAc."""
    b, q = resolve_asset_currencies("TEVA.TA", metadata_currency="ILA")
    assert b == "TEVA.TA"
    assert q == "ILA"

    b2, q2 = resolve_asset_currencies("NPN.JO", metadata_currency="ZAc")
    assert b2 == "NPN.JO"
    assert q2 == "ZAc"


# ==============================================================================
# 2. Static FX Warning & Transparency
# ==============================================================================


def test_static_fx_warning_and_transparency(tmp_path) -> None:
    """Tester must record static FX usage and include fx_warning on report and exports."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 10,
            "High": [105.0] * 10,
            "Low": [95.0] * 10,
            "Close": [102.0] * 10,
            "Volume": [1000] * 10,
        },
        index=dates,
    )

    # EUR stock converted to USD without fx_history -> uses static FX
    tester = PatternRankingTester(df, symbol="SAP.DE", quote_currency="EUR", account_currency="USD")
    assert tester.used_approx_fx is False  # not used until conversion happens

    # Perform a conversion
    tester._convert_pnl_with_source(100.0, 105.0, exit_time=dates[0])
    assert tester.used_approx_fx is True
    assert tester.has_static_fx_conversion is True

    # Check comparison report attrs
    report = tester.get_comparison_report()
    assert report.attrs.get("fx_warning") is not None
    assert "Static/approximate FX rates were used" in report.attrs["fx_warning"]

    # Inject a dummy result to verify export row values
    dummy_res = PatternResult(
        pattern_name="Hammer",
        total_signals=5,
        winning_trades=4,
        losing_trades=1,
        win_rate=80.0,
        total_pnl=500.0,
        avg_pnl=100.0,
        max_profit=200.0,
        max_loss=-50.0,
        sharpe_ratio=2.1,
        max_drawdown=50.0,
        profit_factor=4.0,
        score=85.0,
        total_trades=5,
        fx_source="default_static",
    )
    tester._results.append(dummy_res)

    # Check export_results includes warning column
    export_file = tmp_path / "ranking.csv"
    with pytest.warns(UserWarning, match="Exporting results that used static/approximate FX rates"):
        tester.export_results(str(export_file))

    exported_df = pd.read_csv(export_file)
    assert "FX Warning" in exported_df.columns
    assert "Static/approximate FX rate used" in exported_df["FX Warning"].iloc[0]


def test_historical_fx_no_static_warning() -> None:
    """When accurate historical FX is provided, used_approx_fx remains False and no warning in attrs."""
    dates = pd.date_range("2025-01-01", periods=5, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 5,
            "High": [105.0] * 5,
            "Low": [95.0] * 5,
            "Close": [102.0] * 5,
            "Volume": [1000] * 5,
        },
        index=dates,
    )
    fx_series = pd.Series([1.08, 1.09, 1.08, 1.10, 1.09], index=dates, name="EURUSD")

    tester = PatternRankingTester(
        df,
        symbol="SAP.DE",
        quote_currency="EUR",
        account_currency="USD",
        fx_history={"EURUSD": fx_series},
    )
    tester._convert_pnl_with_source(100.0, 105.0, exit_time=dates[0])
    assert tester.used_approx_fx is False
    report = tester.get_comparison_report()
    assert report.attrs.get("fx_warning") is None


# ==============================================================================
# 3. Incomplete Crypto Ticker Normalization & 404s
# ==============================================================================


def test_crypto_ticker_normalization() -> None:
    """Single crypto tickers must normalize to {SYMBOL}-USD in crypto and auto modes."""
    assert normalize_ticker("BTC", asset_type="crypto") == "BTC-USD"
    assert normalize_ticker("ETH", asset_type="crypto") == "ETH-USD"
    assert normalize_ticker("SOL", asset_type="crypto") == "SOL-USD"
    assert normalize_ticker("DOGE", asset_type="crypto") == "DOGE-USD"
    assert normalize_ticker("UNKNOWNCOIN", asset_type="crypto") == "UNKNOWNCOIN-USD"

    # Pairs should keep their delimiter
    assert normalize_ticker("BTC/USDT", asset_type="crypto") == "BTC-USDT"
    assert normalize_ticker("BTCUSDT", asset_type="crypto") == "BTC-USDT"

    # Multi-currency crypto pairs in auto and crypto modes
    assert normalize_ticker("BTC-USD", asset_type="auto") == "BTC-USD"
    assert normalize_ticker("BTC/EUR", asset_type="auto") == "BTC-EUR"
    assert normalize_ticker("BTCUSDT", asset_type="auto") == "BTC-USDT"


def test_market_data_loader_fetch_empty_data_raises_value_error() -> None:
    """fetch() must raise a descriptive ValueError when yfinance returns empty data."""
    pytest.importorskip("yfinance")
    loader = MarketDataLoader("INVALID_SYMBOL_12345_XYZ")
    with patch("yfinance.download", return_value=pd.DataFrame()), pytest.raises(
        ValueError, match="No market data found on Yahoo Finance for ticker"
    ):
        loader.fetch()


# ==============================================================================
# 4. Sharpe on Trading Calendar Boundary for Tokyo (.T)
# ==============================================================================


def test_tokyo_sharpe_calendar_boundary_deterministic() -> None:
    """Tokyo annualization factor must resolve deterministically via date_range without relying on today's date."""
    tf = "1m"
    pre_factor = 73500.0  # 245 days * 300 min/day
    post_factor = 80850.0  # 245 days * 330 min/day

    # 1. Pure pre-2024 range
    r_pre = resolve_periods_per_year(
        tf,
        "7203.T",
        date_range=(datetime.date(2023, 1, 1), datetime.date(2023, 12, 31)),
    )
    assert r_pre == pytest.approx(pre_factor)

    # 2. Pure post-2024 range
    r_post = resolve_periods_per_year(
        tf,
        "7203.T",
        date_range=(datetime.date(2024, 11, 10), datetime.date(2025, 3, 1)),
    )
    assert r_post == pytest.approx(post_factor)

    # 3. Mixed range spanning 2024-11-05
    start_d = datetime.date(2024, 10, 1)
    end_d = datetime.date(2024, 11, 30)
    r_mixed = resolve_periods_per_year(
        tf,
        "7203.T",
        date_range=(start_d, end_d),
    )
    # Must be strictly between pre and post factors
    assert pre_factor < r_mixed < post_factor

    # 4. As-of-date support
    assert resolve_periods_per_year(
        tf, "7203.T", as_of_date=datetime.date(2023, 5, 1)
    ) == pytest.approx(pre_factor)
    assert resolve_periods_per_year(
        tf, "7203.T", as_of_date=datetime.date(2025, 5, 1)
    ) == pytest.approx(post_factor)


# ==============================================================================
# 5. Market Regime Contract & AI Analyst Asset Type
# ==============================================================================


def test_ai_analyst_asset_type() -> None:
    """AIMarketAnalyst must accept asset_type, validate it, and include it in to_dict."""
    dates = pd.date_range("2025-01-01", periods=5, freq="1D")
    dummy_data = pd.DataFrame(
        {
            "Open": [100.0] * 5,
            "High": [105.0] * 5,
            "Low": [95.0] * 5,
            "Close": [102.0] * 5,
            "Volume": [1000] * 5,
        },
        index=dates,
    )
    analyst_stock = AIMarketAnalyst(dummy_data, asset_type="stock")
    assert analyst_stock.asset_type == "stock"
    assert analyst_stock.to_dict()["asset_type"] == "stock"

    analyst_forex = AIMarketAnalyst(dummy_data, asset_type="forex")
    assert analyst_forex.asset_type == "forex"
    assert analyst_forex.to_dict()["asset_type"] == "forex"

    analyst_crypto = AIMarketAnalyst(dummy_data, asset_type="crypto")
    assert analyst_crypto.asset_type == "crypto"
    assert analyst_crypto.to_dict()["asset_type"] == "crypto"

    analyst_auto = AIMarketAnalyst(dummy_data)
    assert analyst_auto.asset_type == "auto"
    assert analyst_auto.to_dict()["asset_type"] == "auto"

    # Invalid asset type raises ValueError
    with pytest.raises(ValueError, match="Unsupported asset_type"):
        AIMarketAnalyst(dummy_data, asset_type="commodities_metal")


# ==============================================================================
# 6. Zero Score Ambiguity in PatternConfidenceResult
# ==============================================================================


def test_pattern_confidence_result_zero_score_handling() -> None:
    """PatternConfidenceResult must distinguish 0.0 from None and detect conflicting scores."""
    ts = pd.Timestamp("2025-01-01")
    # 1. Conflicting scores: one 0.0, one non-zero
    with pytest.raises(ValueError, match="Conflicting scores provided"):
        PatternConfidenceResult(
            pattern_name="Hammer",
            timestamp=ts,
            raw_signal=100,
            confidence_score=0.0,
            confluence_score=0.8,
        )

    with pytest.raises(ValueError, match="Conflicting scores provided"):
        PatternConfidenceResult(
            pattern_name="Hammer",
            timestamp=ts,
            raw_signal=100,
            confidence_score=0.8,
            confluence_score=0.0,
        )

    # 2. Both explicitly 0.0
    res_zero = PatternConfidenceResult(
        pattern_name="Hammer",
        timestamp=ts,
        raw_signal=100,
        confidence_score=0.0,
        confluence_score=0.0,
    )
    assert res_zero.confidence_score == 0.0
    assert res_zero.confluence_score == 0.0

    # 3. Only confidence_score provided
    res_conf = PatternConfidenceResult(
        pattern_name="Hammer",
        timestamp=ts,
        raw_signal=100,
        confidence_score=0.75,
    )
    assert res_conf.confidence_score == 0.75
    assert res_conf.confluence_score == 0.75

    # 4. Only confluence_score provided
    res_confl = PatternConfidenceResult(
        pattern_name="Hammer",
        timestamp=ts,
        raw_signal=100,
        confluence_score=0.65,
    )
    assert res_confl.confidence_score == 0.65
    assert res_confl.confluence_score == 0.65

    # 5. Neither provided: defaults to 0.0
    res_default = PatternConfidenceResult(
        pattern_name="Hammer",
        timestamp=ts,
        raw_signal=100,
    )
    assert res_default.confidence_score == 0.0
    assert res_default.confluence_score == 0.0
