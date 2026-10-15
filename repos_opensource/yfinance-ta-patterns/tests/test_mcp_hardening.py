"""Regression tests for MCP hardening: real tool timeout, schema/clamp parity, news status, calendar cache."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from yfinance_ta_patterns import economic_calendar as ec
from yfinance_ta_patterns.mcp_server import MCP_TOOLS_SCHEMA, YFinanceTAMCPServer


def _call(server: YFinanceTAMCPServer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    )
    assert resp is not None and "result" in resp
    return resp["result"]


def _synthetic_ohlcv(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": np.full(n, 1e6)}, index=idx
    )


def test_tool_timeout_returns_promptly_even_if_fetch_hangs() -> None:
    def hung_fetcher(symbol: str, period: str, interval: str) -> pd.DataFrame:
        time.sleep(3.0)
        return _synthetic_ohlcv()

    server = YFinanceTAMCPServer(data_fetcher=hung_fetcher, tool_timeout=0.4)
    t0 = time.monotonic()
    result = _call(server, "ta_scan_symbol", {"symbol": "AAPL"})
    elapsed = time.monotonic() - t0

    assert result["isError"] is True
    assert "timed out" in result["content"][0]["text"]
    assert elapsed < 1.5, f"timeout response took {elapsed:.2f}s - worker was joined"


def test_backtest_top_n_clamp_matches_advertised_schema(monkeypatch: Any) -> None:
    from yfinance_ta_patterns import mcp_server as ms

    advertised = next(t for t in MCP_TOOLS_SCHEMA if t["name"] == "ta_backtest_patterns")[
        "inputSchema"
    ]["properties"]["top_n"]["maximum"]

    seen: dict[str, int] = {}

    def fake_top(self: Any, n: int = 10) -> list[Any]:
        seen["n"] = n
        return []

    monkeypatch.setattr(ms.PatternRankingTester, "get_top_patterns", fake_top)
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: _synthetic_ohlcv(), tool_timeout=None)
    _call(server, "ta_backtest_patterns", {"symbol": "AAPL", "top_n": advertised})
    assert seen["n"] == advertised


def test_news_empty_result_is_not_reported_as_live_success() -> None:
    server = YFinanceTAMCPServer(news_fetcher=lambda symbol, limit: [])
    out = _call(server, "ta_get_news", {"symbol": "AAPL"})["structuredContent"]
    assert out["total_news"] == 0
    assert out["status"] == "empty"
    assert "message" in out


def test_news_with_articles_still_reports_live() -> None:
    article = {"content": {"title": "Headline", "provider": {"displayName": "Wire"}, "pubDate": "2026-10-07T08:00:00Z"}}
    server = YFinanceTAMCPServer(news_fetcher=lambda symbol, limit: [article])
    out = _call(server, "ta_get_news", {"symbol": "AAPL"})["structuredContent"]
    assert out["status"] == "live"
    assert out["total_news"] == 1


class _FakeResponse:
    status_code = 200
    text = "<html>" + "x" * 800 + "</html>"


class _FakeSession:
    def get(self, url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        return None


def test_negative_cache_from_short_probe_does_not_block_longer_timeout_caller() -> None:
    url = "http://calendar.invalid/feed"
    ec._CALENDAR_CACHE[url] = (time.monotonic(), None)  # failure recorded by a 2s probe
    ec._CALENDAR_FAIL_TIMEOUT[url] = 2.0
    try:
        with ec.InvestingCalendar(timeout=3.5) as cal:
            cal._curl_session = _FakeSession()
            assert cal._download_feed_html(url) is not None  # longer timeout => retried, not cached-failure

        ec._CALENDAR_CACHE[url] = (time.monotonic(), None)
        ec._CALENDAR_FAIL_TIMEOUT[url] = 2.0
        with ec.InvestingCalendar(timeout=2.0) as cal:
            cal._curl_session = _FakeSession()
            assert cal._download_feed_html(url) is None  # same timeout => negative cache still honoured
    finally:
        ec._CALENDAR_CACHE.pop(url, None)
        ec._CALENDAR_FAIL_TIMEOUT.pop(url, None)
