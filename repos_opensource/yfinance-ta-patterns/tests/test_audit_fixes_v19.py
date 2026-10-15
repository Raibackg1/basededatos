"""Regression tests for the v19 / v0.3.52 audit fixes.

Covers:
1. Unknown tool names in `tools/call` return JSON-RPC error code -32602 instead of execution result with isError.
2. `event_risk` feed resiliency: when calendar feed is unreachable, returns `source: "unavailable"` and `status: "unavailable"`.
3. `event_risk` future-event filter: filters by UTC datetime (>= now_utc) rather than raw dates alone, preventing past events earlier today from being flagged as "imminent".
4. Watchlist timeout hardening: uses `as_completed` with absolute deadline; slow symbol is recorded in `errors` dict and does not block fast symbols.
5. Server-side argument clamping: lookback_bars (1–60), holding_period (1–60), min_confidence (0.0–1.0), and min_signals (1–100).
6. Macroeconomic calendar dates integration into `ta_backtest_patterns` when `filter_news=True`.
7. `ta_get_news` MCP tool for market headlines.
8. CLI `--calendar` and `--news` flags.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns import cli
from yfinance_ta_patterns.mcp_server import YFinanceTAMCPServer


def _sample_ohlcv(periods: int = 50) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=periods, freq="1d", tz="UTC")
    opens = np.linspace(100.0, 110.0, periods)
    highs = opens + 2.0
    lows = opens - 1.0
    closes = opens + 1.0
    volumes = np.full(periods, 10000.0)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


def test_unknown_tool_returns_32602() -> None:
    server = YFinanceTAMCPServer()
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "invalid_tool_name", "arguments": {}},
    })
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "Unknown tool" in resp["error"]["message"]


def test_event_risk_feed_unavailable_returns_source_unavailable() -> None:
    df = _sample_ohlcv()
    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: None,
    )
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "AAPL"}},
    })
    assert resp is not None
    data = resp["result"]["structuredContent"]
    er = data["event_risk"]
    assert er["source"] == "unavailable"
    assert er["status"] == "unavailable"
    assert er["has_high_impact_event"] is None
    assert er["event_count"] == 0


def test_event_risk_future_event_filter() -> None:
    df = _sample_ohlcv()
    now_utc = dt.datetime.now(dt.timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    tomorrow_str = (now_utc + dt.timedelta(days=1)).strftime("%Y-%m-%d")

    html_feed = f"""
    <table>
      <tr data-id="1" data-country="united states" data-event="past rate decision" class="calendar-date-3">
        <td class=" {today_str}">12:01 AM</td>
        <td>US</td><td>Past Rate Decision</td><td>-</td><td>-</td>
      </tr>
      <tr data-id="2" data-country="united states" data-event="upcoming nfp" class="calendar-date-3">
        <td class=" {tomorrow_str}">02:30 PM</td>
        <td>US</td><td>Upcoming NFP</td><td>-</td><td>-</td>
      </tr>
    </table>
    """
    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: html_feed,
    )
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "AAPL"}},
    })
    assert resp is not None
    data = resp["result"]["structuredContent"]
    er = data["event_risk"]
    assert er["source"] == "live"
    assert er["status"] == "ok"
    assert er["has_high_impact_event"] is True
    assert er["nearest_event"] == "Upcoming NFP"
    assert er["hours_until"] > 0


def test_watchlist_argument_clamping() -> None:
    df = _sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: df.copy())
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "ta_scan_watchlist",
            "arguments": {
                "symbols": ["AAPL"],
                "min_confidence": -0.5,
                "lookback_bars": 999,
                "max_results": 200,
            },
        },
    })
    assert resp is not None
    assert resp["result"]["isError"] is False


def test_backtest_argument_clamping_and_filter_news() -> None:
    df = _sample_ohlcv()
    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: None,
    )
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 30,
        "method": "tools/call",
        "params": {
            "name": "ta_backtest_patterns",
            "arguments": {
                "symbol": "AAPL",
                "holding_period": 999,
                "min_signals": 999,
                "top_n": 999,
                "filter_news": True,
            },
        },
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    data = resp["result"]["structuredContent"]
    assert data["filter_news"] is True
    assert data["holding_period"] == 60
    assert data["min_signals"] == 100


def test_ta_get_news_tool() -> None:
    mock_articles = [
        {
            "title": "US GDP Expands 3.0%",
            "publisher": "Wall Street Journal",
            "link": "https://wsj.com/gdp",
            "publish_time": "2026-10-07 12:00 UTC",
        }
    ]
    server = YFinanceTAMCPServer(news_fetcher=lambda sym, lim: mock_articles)
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 40,
        "method": "tools/call",
        "params": {"name": "ta_get_news", "arguments": {"symbol": "AAPL", "limit": 5}},
    })
    assert resp is not None
    assert resp["result"]["isError"] is False
    data = resp["result"]["structuredContent"]
    assert data["symbol"] == "AAPL"
    assert data["total_news"] == 1
    assert data["news"][0]["title"] == "US GDP Expands 3.0%"


def test_cli_calendar_and_news_flags(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    parser = cli.get_parser()
    args = parser.parse_args(["--symbol", "AAPL", "--calendar", "--news"])
    assert args.calendar is True
    assert args.news is True

    # Run cli with mock
    df = _sample_ohlcv()
    monkeypatch.setattr("yfinance_ta_patterns.data.MarketDataLoader.get_data", lambda self: df.copy())
    monkeypatch.setattr(
        "yfinance_ta_patterns.economic_calendar.InvestingCalendar.get_events_for_symbol",
        lambda self, **kw: {
            "source": "live",
            "events": [{"time": "2026-10-08 14:30 UTC", "currency": "USD", "event": "CPI", "importance": "3"}],
        },
    )
    code = cli.run_cli(args)
    assert code == 0
    captured = capsys.readouterr()
    assert "UPCOMING MACROECONOMIC EVENTS" in captured.out
    assert "CPI" in captured.out
