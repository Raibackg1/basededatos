from unittest.mock import patch

import pandas as pd
import pytest

from yfinance_ta_patterns.cli import normalize_timeframe, parse_args, run_cli


def test_normalize_timeframe_aliases():
    assert normalize_timeframe("M1") == "1m"
    assert normalize_timeframe("M5") == "5m"
    assert normalize_timeframe("M15") == "15m"
    assert normalize_timeframe("M30") == "30m"
    assert normalize_timeframe("h1") == "1h"
    assert normalize_timeframe("h4") == "4h"
    assert normalize_timeframe("D1") == "1d"
    assert normalize_timeframe("1wk") == "1wk"


def test_normalize_timeframe_invalid():
    with pytest.raises(ValueError, match="Unsupported timeframe 'invalid'"):
        normalize_timeframe("invalid")


def test_parse_args_pattern():
    args = parse_args(["--pattern", "HAMMER", "--symbol", "GBPUSD", "--timeframe", "1h"])
    assert args.pattern == "HAMMER"
    assert args.symbol == "GBPUSD"
    assert args.timeframe == "1h"
    assert not args.all_patterns
    assert not args.ai


def test_parse_args_all_patterns_with_ai():
    args = parse_args(["--all-patterns", "--ai", "--min-confidence", "0.65"])
    assert args.all_patterns is True
    assert args.ai is True
    assert args.min_confidence == 0.65


def test_parse_args_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--pattern", "HAMMER", "--all-patterns"])


def test_run_cli_date_conflict():
    args = parse_args(["--pattern", "HAMMER", "--date", "2025-01-01", "--start-date", "2025-01-01"])
    with pytest.raises(ValueError, match="Use either --date or --start-date/--end-date"):
        run_cli(args)


@pytest.fixture
def mock_ohlcv_df():
    idx = pd.date_range("2025-01-01 00:00", periods=20, freq="1d", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(20)],
            "High": [102.0 + i for i in range(20)],
            "Low": [99.0 + i for i in range(20)],
            "Close": [101.0 + i for i in range(20)],
            "Volume": [50000.0] * 20,
        },
        index=idx,
    )


def test_run_cli_pattern_flow(capsys, mock_ohlcv_df):
    with patch("yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_df):
        args = parse_args(["--pattern", "DOJI", "--symbol", "EURUSD"])
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "DOJI" in captured.out


def test_run_cli_ai_flow(capsys, mock_ohlcv_df):
    with patch("yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_df):
        args = parse_args(["--pattern", "DOJI", "--symbol", "AAPL", "--ai"])
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        # Either found AI score or cleanly reported no signals
        assert "AI" in captured.out or "No" in captured.out


def test_run_cli_ai_analyst_flow(capsys, mock_ohlcv_df):
    with patch("yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_df):
        args = parse_args(["--pattern", "HAMMER", "--symbol", "BTC-USD", "--ai-analyst"])
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "AI Technical Intelligence Brief" in captured.out


def test_resolve_symbols():
    from yfinance_ta_patterns.cli import resolve_symbols
    from yfinance_ta_patterns.forex_data_loader import FOREX_56_PAIRS

    assert len(resolve_symbols(None)) == 56
    assert len(resolve_symbols("ALL")) == 56
    assert len(resolve_symbols("FOREX")) == 56
    assert resolve_symbols("EURUSD") == ["EURUSD"]
    assert resolve_symbols("EURUSD,GBPUSD,USDJPY") == ["EURUSD", "GBPUSD", "USDJPY"]
    assert resolve_symbols(None, all_pairs_flag=True) == list(FOREX_56_PAIRS)


def test_run_cli_multi_symbol_flow(capsys, mock_ohlcv_df):
    with patch("yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_df):
        args = parse_args(["--symbol", "EURUSD,GBPUSD", "--timeframe", "1h", "--ai"])
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "SCANNING 2 FOREX CURRENCY PAIRS" in captured.out
        assert "Scanned: 2 active pairs" in captured.out


def test_run_cli_single_symbol_lookback_and_formats(capsys, mock_ohlcv_df):
    with patch("yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_df):
        # Test markdown format with lookback
        args = parse_args(
            ["--symbol", "EURUSD", "--ai-analyst", "--lookback-bars", "5", "--format", "markdown"]
        )
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "# AI Technical Intelligence Brief: EURUSD" in captured.out

        # Test json format
        args_json = parse_args(
            ["--symbol", "EURUSD", "--ai-analyst", "--lookback-bars", "5", "--format", "json"]
        )
        exit_code_json = run_cli(args_json)
        assert exit_code_json == 0
        captured_json = capsys.readouterr()
        assert '"symbol": "EURUSD"' in captured_json.out


def test_format_price_and_timestamp():
    from yfinance_ta_patterns.data import format_price, format_timestamp

    assert format_price(None) == "-"
    assert format_price("") == "-"
    assert format_price(0.0) == "0.00"
    assert format_price(1.1420739889) == "1.14207"
    assert format_price(125.0) == "125.00"
    assert format_price(45123.456) == "45123.46"
    assert format_price(0.0002234) == "0.000223"

    assert format_timestamp(None) == "-"
    assert format_timestamp(pd.Timestamp("2026-09-26 15:30:00+03:00"), "5m") == "2026-09-26 15:30"
    assert format_timestamp(pd.Timestamp("2026-09-26 00:00:00"), "1d") == "2026-09-26"
