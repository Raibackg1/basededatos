"""Regression and hardening tests for v20 / v0.3.53 audit fixes.

Covers:
1. `ta_get_news` error handling: returns source="unavailable", status="unavailable", and message on failure.
2. `ta_get_news` success: returns source="live", status="live".
3. `event_risk.has_high_impact_event`: returns None (JSON null) when calendar feed is unavailable.
4. Engine indicator: `ta_scan_symbol` and `ta_scan_watchlist` report engine ("TA-Lib" or "NumPy-fallback").
5. Input type validation: invalid non-numeric inputs like days="abc" return clean schema errors instead of raw tracebacks.
6. Single tool call execution timeout: server returns isError=True on slow tool calls and does not hang.
7. Cyrillic country mapping: countries filter with Cyrillic (e.g. "сша") expands and matches live feed data.
8. TradingEconomicsCalendar alias availability.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from yfinance_ta_patterns.economic_calendar import InvestingCalendar, TradingEconomicsCalendar
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


def test_trading_economics_calendar_alias() -> None:
    assert TradingEconomicsCalendar is InvestingCalendar


def test_ta_get_news_failure_reports_unavailable_source_and_status() -> None:
    def failing_fetcher(symbol: str, limit: int) -> list[dict]:
        raise RuntimeError("Yahoo Finance network 403 Forbidden")

    server = YFinanceTAMCPServer(news_fetcher=failing_fetcher)
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {"name": "ta_get_news", "arguments": {"symbol": "AAPL"}},
    })
    assert resp is not None
    data = resp["result"]["structuredContent"]
    assert data["symbol"] == "AAPL"
    assert data["source"] == "unavailable"
    assert data["status"] == "unavailable"
    assert data["total_news"] == 0
    assert data["news"] == []
    assert "message" in data
    assert "Yahoo Finance network 403 Forbidden" in data["message"]


def test_ta_get_news_success_reports_live_source_and_status() -> None:
    mock_articles = [
        {
            "title": "Fed Signals Steady Rates",
            "publisher": "Reuters",
            "link": "https://reuters.com/fed",
            "publish_time": "2026-10-07 14:00 UTC",
        }
    ]
    server = YFinanceTAMCPServer(news_fetcher=lambda _sym, _lim: mock_articles)
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {"name": "ta_get_news", "arguments": {"symbol": "AAPL"}},
    })
    assert resp is not None
    data = resp["result"]["structuredContent"]
    assert data["symbol"] == "AAPL"
    assert data["source"] == "live"
    assert data["status"] == "live"
    assert data["total_news"] == 1
    assert len(data["news"]) == 1
    assert data["news"][0]["title"] == "Fed Signals Steady Rates"


def test_event_risk_has_high_impact_event_is_none_when_unavailable() -> None:
    df = _sample_ohlcv()
    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: None,
    )
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tools/call",
        "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "BTC-USD"}},
    })
    assert resp is not None
    data = resp["result"]["structuredContent"]
    er = data["event_risk"]
    assert er["source"] == "unavailable"
    assert er["status"] == "unavailable"
    assert er["has_high_impact_event"] is None
    assert er["event_count"] == 0


def test_scan_symbol_and_watchlist_report_engine() -> None:
    df = _sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: df.copy())

    # 1. ta_scan_symbol
    resp_symbol = server.handle_request({
        "jsonrpc": "2.0",
        "id": 104,
        "method": "tools/call",
        "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "AAPL"}},
    })
    assert resp_symbol is not None
    symbol_data = resp_symbol["result"]["structuredContent"]
    assert "engine" in symbol_data
    assert symbol_data["engine"] in ("TA-Lib", "NumPy-fallback")

    # 2. ta_scan_watchlist
    resp_watchlist = server.handle_request({
        "jsonrpc": "2.0",
        "id": 105,
        "method": "tools/call",
        "params": {"name": "ta_scan_watchlist", "arguments": {"symbols": ["AAPL"]}},
    })
    assert resp_watchlist is not None
    watchlist_data = resp_watchlist["result"]["structuredContent"]
    assert "engine" in watchlist_data
    assert watchlist_data["engine"] in ("TA-Lib", "NumPy-fallback")


def test_input_schema_validation_type_errors() -> None:
    server = YFinanceTAMCPServer()

    # 1. days="abc" in ta_get_economic_calendar
    resp_days_str = server.handle_request({
        "jsonrpc": "2.0",
        "id": 106,
        "method": "tools/call",
        "params": {
            "name": "ta_get_economic_calendar",
            "arguments": {"symbol": "AAPL", "days": "abc"},
        },
    })
    assert resp_days_str is not None
    assert resp_days_str["result"]["isError"] is True
    err_text = resp_days_str["result"]["content"][0]["text"]
    assert "Invalid parameter 'days'" in err_text
    assert "expected integer" in err_text

    # 2. days=True (boolean instead of integer)
    resp_days_bool = server.handle_request({
        "jsonrpc": "2.0",
        "id": 107,
        "method": "tools/call",
        "params": {
            "name": "ta_get_economic_calendar",
            "arguments": {"symbol": "AAPL", "days": True},
        },
    })
    assert resp_days_bool is not None
    assert resp_days_bool["result"]["isError"] is True
    err_text = resp_days_bool["result"]["content"][0]["text"]
    assert "Invalid parameter 'days'" in err_text
    assert "boolean" in err_text

    # 3. min_confidence="invalid_num"
    resp_conf = server.handle_request({
        "jsonrpc": "2.0",
        "id": 108,
        "method": "tools/call",
        "params": {
            "name": "ta_scan_symbol",
            "arguments": {"symbol": "AAPL", "min_confidence": "invalid_num"},
        },
    })
    assert resp_conf is not None
    assert resp_conf["result"]["isError"] is True
    err_text = resp_conf["result"]["content"][0]["text"]
    assert "Invalid parameter 'min_confidence'" in err_text
    assert "expected number" in err_text


def test_single_tool_timeout_resiliency() -> None:
    def hanging_data_fetcher(symbol: str, period: str, interval: str) -> pd.DataFrame:
        time.sleep(0.5)
        return _sample_ohlcv()

    # Server configured with very short timeout (0.1s)
    server = YFinanceTAMCPServer(
        data_fetcher=hanging_data_fetcher,
        tool_timeout=0.1,
    )
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 109,
        "method": "tools/call",
        "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "AAPL"}},
    })
    assert resp is not None
    assert resp["result"]["isError"] is True
    err_text = resp["result"]["content"][0]["text"]
    assert "timed out after 0.1s" in err_text


def test_cyrillic_country_expansion_in_calendar() -> None:
    html_feed = """
    <table>
      <tr data-id="10" data-country="united states" data-event="fed rate decision" class="calendar-date-3">
        <td class=" 2026-10-15">02:00 PM</td>
        <td>US</td><td>Fed Rate Decision</td><td>-</td><td>-</td>
      </tr>
      <tr data-id="11" data-country="germany" data-event="ifo business climate" class="calendar-date-2">
        <td class=" 2026-10-15">08:00 AM</td>
        <td>DE</td><td>Ifo Business Climate</td><td>-</td><td>-</td>
      </tr>
    </table>
    """
    cal = InvestingCalendar(http_fetcher=lambda _u: html_feed)
    events, source, _msg = cal._fetch_from_feed(
        date_from="2026-10-14",
        date_to="2026-10-16",
        countries=["сша"],
    )
    assert source == "live"
    assert len(events) == 1
    assert events[0]["event"] == "Fed Rate Decision"
    assert events[0]["currency"] == "USD"
