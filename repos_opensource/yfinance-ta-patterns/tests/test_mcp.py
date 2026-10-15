"""Comprehensive tests for yfinance-ta-patterns Model Context Protocol (MCP) JSON-RPC 2.0 Server."""

from __future__ import annotations

import datetime as dt
import io
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.mcp_server import MAX_WATCHLIST_SYMBOLS, YFinanceTAMCPServer


def _make_sample_ohlcv(seed: int = 42, bullish_spike: float = 2.5) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0.1, 1.5, size=60))
    opens = closes - rng.normal(0.0, 0.8, size=60)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.5, 0.5, size=60))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.5, 0.5, size=60))
    # Force a clear bullish hammer / engulfing on the final bar
    opens[-1] = closes[-2] - 1.5
    closes[-1] = closes[-2] + bullish_spike
    highs[-1] = closes[-1] + 0.3
    lows[-1] = opens[-1] - 3.0
    vols = rng.integers(100_000, 500_000, size=60).astype(float)
    vols[-1] = 950_000.0
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": vols,
        },
        index=dates,
    )


def test_yfinance_ta_mcp_server_lifecycle(capsys: pytest.CaptureFixture[str]) -> None:
    df = _make_sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda sym, per, tf: df.copy())

    # 1. initialize (includes instructions and negotiated version)
    init_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
    )
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "yfinance-ta-patterns-mcp"
    assert init_resp["result"]["protocolVersion"] == "2025-03-26"
    assert "instructions" in init_resp["result"]
    assert "yfinance-ta-patterns" in init_resp["result"]["instructions"]

    # 1b. initialize with latest specification version 2025-11-25
    init_latest = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
    )
    assert init_latest is not None
    assert init_latest["result"]["protocolVersion"] == "2025-11-25"

    # 1c. initialize with unsupported future/unknown version must negotiate highest supported version
    init_unknown = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "initialize",
            "params": {"protocolVersion": "2026-99-99"},
        }
    )
    assert init_unknown is not None
    assert init_unknown["result"]["protocolVersion"] == "2025-11-25"

    # 2. notifications return None (including non-prefixed notifications)
    assert server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle_request({"jsonrpc": "2.0", "method": "$/cancelRequest"}) is None

    # 3. tools/list
    list_resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert list_resp is not None
    tools_list = list_resp["result"]["tools"]
    tools = [t["name"] for t in tools_list]
    assert "ta_scan_symbol" in tools
    assert "ta_scan_watchlist" in tools
    assert "ta_backtest_patterns" in tools
    assert "ta_list_patterns" in tools
    assert "ta_get_economic_calendar" in tools
    # Verify metadata: title, annotations, outputSchema
    for t in tools_list:
        assert "title" in t and len(t["title"]) > 0
        assert "annotations" in t and "readOnlyHint" in t["annotations"]
        assert "outputSchema" in t and t["outputSchema"]["type"] == "object"

    # 4. ta_scan_symbol (compact by default: no markdown_brief unless requested)
    scan_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_symbol",
                "arguments": {
                    "symbol": "BTC-USD",
                    "timeframe": "15m",
                    "period": "6mo",
                    "min_confidence": 0.0,
                    "lookback_bars": 5,
                },
            },
        }
    )
    assert scan_resp is not None
    assert scan_resp["result"]["isError"] is False
    assert "structuredContent" in scan_resp["result"]
    report = scan_resp["result"]["structuredContent"]
    assert scan_resp["result"]["content"][0]["text"] == json.dumps(
        report, ensure_ascii=False, separators=(",", ":"), default=str
    )
    assert report["symbol"] == "BTC-USD"
    assert report["period"] == "60d"  # 15m period is auto-clamped from 6mo to 60d
    assert report["timezone"] == "UTC"
    assert "market_summary" in report
    assert "markdown_brief" not in report

    # With include_brief=True
    scan_brief_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_symbol",
                "arguments": {
                    "symbol": "BTC-USD",
                    "timeframe": "15m",
                    "min_confidence": 0.0,
                    "lookback_bars": 5,
                    "include_brief": True,
                },
            },
        }
    )
    assert scan_brief_resp is not None
    assert "structuredContent" in scan_brief_resp["result"]
    report_with_brief = scan_brief_resp["result"]["structuredContent"]
    assert "markdown_brief" in report_with_brief
    assert "UTC" in report_with_brief["markdown_brief"]

    # 5. ta_backtest_patterns (must NOT print "No signals generated" to stdout!)
    bt_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ta_backtest_patterns",
                "arguments": {"symbol": "BTC-USD", "top_n": 3},
            },
        }
    )
    assert bt_resp is not None
    assert bt_resp["result"]["isError"] is False
    assert "structuredContent" in bt_resp["result"]
    bt_data = bt_resp["result"]["structuredContent"]
    assert bt_data["symbol"] == "BTC-USD"
    assert "top_patterns" in bt_data
    assert "total_patterns_evaluated" in bt_data

    # 6. ta_list_patterns
    pat_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "ta_list_patterns", "arguments": {}},
        }
    )
    assert pat_resp is not None
    assert "structuredContent" in pat_resp["result"]
    pat_data = pat_resp["result"]["structuredContent"]
    assert len(pat_data["fallback_patterns"]) > 0
    assert "patterns" in pat_data
    assert "talib_status" in pat_data
    assert "talib_available" in pat_data
    assert "gil_disabled" in pat_data

    # Verify zero stray stdout output across the entire lifecycle
    captured = capsys.readouterr()
    assert captured.out == ""


def test_mcp_watchlist_normalization_sorting_and_error_handling() -> None:
    df_low = _make_sample_ohlcv(seed=10, bullish_spike=1.2)
    df_high = _make_sample_ohlcv(seed=42, bullish_spike=4.0)

    def fetcher(sym: str, per: str, tf: str) -> pd.DataFrame:
        if sym == "FAIL":
            raise ConnectionError("Yahoo Finance timeout for FAIL → connection reset")
        if sym == "NVDA":
            return df_high.copy()
        return df_low.copy()

    server = YFinanceTAMCPServer(data_fetcher=fetcher)

    # 1. Comma-separated string is normalized into discrete symbols (not iterated char-by-char)
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {
                    "symbols": "AAPL, NVDA, FAIL",
                    "timeframe": "1d",
                    "min_confidence": 0.0,
                    "max_results": 5,
                },
            },
        }
    )
    assert resp is not None
    assert resp["result"]["isError"] is False
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["symbols_requested"] == 3
    assert data["symbols_scanned"] == 2
    assert "FAIL" in data["errors"]
    assert len(data["opportunities"]) <= 5

    # Verify descending sort by confluence_score
    scores = [op["confluence_score"] for op in data["opportunities"]]
    assert scores == sorted(scores, reverse=True)

    # 2. Total outage (all symbols fail) returns isError=True instead of pretending 0 opportunities
    all_fail_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {"symbols": ["FAIL"]},
            },
        }
    )
    assert all_fail_resp is not None
    assert all_fail_resp["result"]["isError"] is True
    assert (
        "Failed to scan all 1 requested symbol(s)" in all_fail_resp["result"]["content"][0]["text"]
    )

    # 3. Exceeding MAX_WATCHLIST_SYMBOLS returns isError=True
    too_many = [f"SYM{i}" for i in range(MAX_WATCHLIST_SYMBOLS + 1)]
    limit_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {"symbols": too_many},
            },
        }
    )
    assert limit_resp is not None
    assert limit_resp["result"]["isError"] is True
    assert "exceeds maximum allowed limit" in limit_resp["result"]["content"][0]["text"]


def test_mcp_run_stdio_purity_garbage_resilience_and_cp1251() -> None:
    df = _make_sample_ohlcv()

    def noisy_fetcher(sym: str, per: str, tf: str) -> pd.DataFrame:
        # Simulate a rogue dependency printing non-ASCII text to sys.stdout
        print(f"1 Failed download: ['{sym}'] → noisy stdout spam")
        return df.copy()

    server = YFinanceTAMCPServer(data_fetcher=noisy_fetcher)

    requests = [
        "not valid json at all {{{",
        "12345",
        '["an", "array", "not", "object"]',
        '{"jsonrpc": "2.0", "id": 1, "method": 999}',
        '{"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": "bad_params"}',
        '{"jsonrpc": "2.0", "method": "notifications/initialized"}',
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "ta_backtest_patterns",
                    "arguments": {"symbol": "BTC-USD", "top_n": 2},
                },
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/read",
                "params": {"uri": "ta://forex/56-pairs"},
            }
        ),
    ]

    stdin_buf = io.StringIO("\n".join(requests) + "\n")
    # Simulate a strict Windows cp1251 stdout pipe that crashes if raw '→' is written unescaped
    raw_bytes_out = io.BytesIO()
    cp1251_stdout = io.TextIOWrapper(raw_bytes_out, encoding="cp1251", errors="strict")

    server.run_stdio(stdin=stdin_buf, stdout=cp1251_stdout)
    cp1251_stdout.flush()

    output_lines = raw_bytes_out.getvalue().decode("cp1251").splitlines()
    # Every single output line must be a valid JSON-RPC 2.0 object (zero stray print lines)
    parsed_msgs: list[dict[str, Any]] = [json.loads(line) for line in output_lines]

    assert len(parsed_msgs) == 7
    assert parsed_msgs[0]["error"]["code"] == -32700  # Parse error
    assert parsed_msgs[1]["error"]["code"] == -32600  # Invalid Request (int)
    assert parsed_msgs[2]["error"]["code"] == -32600  # Invalid Request (list)
    assert parsed_msgs[3]["error"]["code"] == -32600  # Invalid Request (bad method)
    assert parsed_msgs[4]["error"]["code"] == -32602  # Invalid params
    assert parsed_msgs[5]["id"] == 3
    assert parsed_msgs[5]["result"]["isError"] is False
    assert parsed_msgs[6]["id"] == 4
    fx_res = json.loads(parsed_msgs[6]["result"]["contents"][0]["text"])
    assert fx_res["count"] == 56


def test_mcp_economic_calendar_for_selected_asset() -> None:
    """Verify ta_get_economic_calendar resolves asset currencies, parses live HTML feed, and falls back."""
    from yfinance_ta_patterns.economic_calendar import (
        InvestingCalendar,
        resolve_symbol_currencies,
    )

    assert resolve_symbol_currencies("EURUSD=X") == ["EUR", "USD"]
    assert resolve_symbol_currencies("EUR/USD") == ["EUR", "USD"]
    assert resolve_symbol_currencies("GBPJPY") == ["GBP", "JPY"]
    assert resolve_symbol_currencies("AAPL") == ["USD"]
    assert resolve_symbol_currencies("SAP.DE") == ["EUR"]
    assert resolve_symbol_currencies("VOD.L") == ["GBP"]
    assert resolve_symbol_currencies("7203.T") == ["JPY"]
    assert resolve_symbol_currencies("BTC-USD") == ["USD"]
    assert resolve_symbol_currencies("BTC-EUR") == ["EUR"]
    assert resolve_symbol_currencies("^GDAXI") == ["EUR"]

    sample_te_html = """
    <table>
      <tr data-id="101" data-country="united states" data-event="non farm payrolls">
        <td class=" 2026-10-06"><span class="event-0 calendar-date-3">08:30 AM</span></td>
        <td class="calendar-item">
          <table><tr><td><div class="flag flag-us"></div></td><td class="calendar-iso">US</td></tr></table>
        </td>
        <td><a class="calendar-event">Non Farm Payrolls</a></td>
        <td><span id="actual">254K</span></td>
        <td><span id="previous">159K</span><span id="revised">\u00ae</span></td>
        <td><a id="consensus"></a></td>
        <td><a id="forecast">140K</a></td>
      </tr>
      <tr data-id="102" data-country="germany" data-event="ifo business climate" class="calendar-date-2">
        <td class="2026-10-06">09:00 AM</td>
        <td>DE</td>
        <td>IFO Business Climate</td>
        <td>85.4</td>
        <td>85.0</td>
        <td>85.2</td>
      </tr>
      <tr data-id="103" data-country="japan" data-event="cpi" class="calendar-date-3">
        <td class="2026-10-06">11:30 PM</td>
        <td>JP</td>
        <td>Tokyo Core CPI</td>
        <td>2.8%</td>
        <td>2.8%</td>
        <td>2.7%</td>
      </tr>
    </table>
    """

    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: _make_sample_ohlcv(),
        calendar_fetcher=lambda _url: sample_te_html,
    )

    list_resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert list_resp is not None
    tools = [t["name"] for t in list_resp["result"]["tools"]]
    assert "ta_get_economic_calendar" in tools

    # 1. EUR/USD should include EUR and USD events (101, 102) and exclude JPY (103)
    fx_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {
                    "symbol": "EUR/USD",
                    "date_from": "2026-10-05",
                    "date_to": "2026-10-07",
                },
            },
        }
    )
    assert fx_resp is not None
    assert fx_resp["result"]["isError"] is False
    fx_cal = json.loads(fx_resp["result"]["content"][0]["text"])
    assert fx_cal["normalized_symbol"] == "EURUSD=X"
    assert fx_cal["currencies"] == ["EUR", "USD"]
    assert fx_cal["source"] == "live"
    assert fx_cal["total_events"] == 2
    assert {e["currency"] for e in fx_cal["events"]} == {"EUR", "USD"}

    # 2. AAPL with importance=['high'] should return only the high-importance USD event (101)
    stock_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {
                    "symbol": "AAPL",
                    "date_from": "2026-10-05",
                    "date_to": "2026-10-07",
                    "importances": ["high"],
                },
            },
        }
    )
    assert stock_resp is not None
    assert stock_resp["result"]["isError"] is False
    stock_cal = json.loads(stock_resp["result"]["content"][0]["text"])
    assert stock_cal["currencies"] == ["USD"]
    assert stock_cal["total_events"] == 1
    assert stock_cal["events"][0]["id"] == "101"
    assert stock_cal["events"][0]["time"] == "2026-10-06 08:30 UTC"

    # 3. Unavailable when live feed is unreachable (no fake synthetic events)
    offline_server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: _make_sample_ohlcv(),
        calendar_fetcher=lambda _url: None,
    )
    fb_resp = offline_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {
                    "symbol": "USDJPY",
                    "date_from": "2026-10-05",
                    "date_to": "2026-10-09",
                },
            },
        }
    )
    assert fb_resp is not None
    assert fb_resp["result"]["isError"] is False
    fb_cal = json.loads(fb_resp["result"]["content"][0]["text"])
    assert fb_cal["source"] == "unavailable"
    assert fb_cal["currencies"] == ["USD", "JPY"]
    assert fb_cal["total_events"] == 0
    assert fb_cal["events"] == []
    assert "message" in fb_cal
    assert "unverified synthetic data" in fb_cal["message"]

    # 4. Verify Investing.com HTML parser compatibility
    investing_html = """
    <table>
      <tr><td class="theDay">6 октября 2026</td></tr>
      <tr id="eventRowId_555" data-event-id="555">
        <td class="time">15:30</td>
        <td class="flagCur"><span class="ceFlags" title="США"></span> USD</td>
        <td class="sentiment">
          <i class="grayFullBullishIcon"></i>
          <i class="grayFullBullishIcon"></i>
          <i class="grayFullBullishIcon"></i>
        </td>
        <td class="event"><a href="#">Core CPI (MoM)</a></td>
        <td class="act">0.3%</td>
        <td class="fore">0.2%</td>
        <td class="prev">0.2%</td>
      </tr>
    </table>
    """
    parsed_inv = list(InvestingCalendar.parse_events_html(investing_html))
    assert len(parsed_inv) == 1
    assert parsed_inv[0]["id"] == "555"
    assert parsed_inv[0]["time"] == "2026-10-06 15:30:00 UTC"
    assert parsed_inv[0]["currency"] == "USD"
    assert parsed_inv[0]["importance"] == "3"
    assert parsed_inv[0]["event"] == "Core CPI (MoM)"


def test_mcp_encoding_utf8_preservation_and_watchlist_compactness() -> None:
    """Verify ensure_ascii=False preserves Cyrillic and euro symbols without unicode escapes, and compact reduces payload."""
    df = _make_sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: df.copy())

    # 1. Watchlist with compact=True (default) vs compact=False
    compact_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 201,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {"symbols": ["BTC-USD"], "compact": True},
            },
        }
    )
    assert compact_resp is not None
    compact_data = json.loads(compact_resp["result"]["content"][0]["text"])
    assert compact_data["compact"] is True
    if compact_data["opportunities"]:
        opp = compact_data["opportunities"][0]
        assert "symbol" in opp
        assert "pattern" in opp
        assert "direction" in opp
        assert "confluence_score" in opp
        assert opp.get("entry") is not None
        assert opp.get("tp1") is not None
        # Compact mode must not include duplicate aliases or bulky indicator keys
        assert "entry_price" not in opp
        assert "take_profit_1" not in opp
        assert "confidence_score" not in opp
        assert "confluence_factors" not in opp
        assert "risk_factors" not in opp
        assert "metrics" not in opp
        assert "raw_signal" not in opp

    full_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 202,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {"symbols": ["BTC-USD"], "compact": False},
            },
        }
    )
    assert full_resp is not None
    full_data = json.loads(full_resp["result"]["content"][0]["text"])
    assert full_data["compact"] is False
    if full_data["opportunities"]:
        opp_full = full_data["opportunities"][0]
        assert "confluence_factors" in opp_full
        assert "risk_factors" in opp_full
        assert "metrics" in opp_full
        assert "raw_signal" in opp_full

    # 2. Test ensure_ascii=False output wire purity: Cyrillic and € should NOT be \uXXXX escaped
    today_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    calendar_html_ru = f"""
    <table>
      <tr data-id="999" data-country="germany" data-event="ставка ецб" class="calendar-date-3">
        <td class="{today_iso}">14:15</td>
        <td>DE</td>
        <td>Решение по процентной ставке ЕЦБ €</td>
        <td>3.25%</td>
        <td>3.50%</td>
        <td>3.25%</td>
      </tr>
    </table>
    """
    ru_server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _url: calendar_html_ru,
    )

    ru_resp = ru_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 203,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {"symbol": "EURUSD=X"},
            },
        }
    )
    assert ru_resp is not None
    raw_text = ru_resp["result"]["content"][0]["text"]
    # Wire text must retain literal Unicode without \uXXXX escaping
    assert "Решение по процентной ставке ЕЦБ €" in raw_text
    assert "\\u04" not in raw_text
    assert "\\u20ac" not in raw_text


def test_mcp_all_tools_return_structured_content() -> None:
    """Validate that every tool with an outputSchema returns structuredContent according to MCP spec."""
    df = _make_sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: df.copy())

    tools_resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tools_resp is not None
    tools = tools_resp["result"]["tools"]

    for tool in tools:
        assert "outputSchema" in tool, f"Tool {tool['name']} must declare outputSchema"
        t_name = tool["name"]

        # Call tool with minimal valid arguments
        args: dict[str, Any] = {}
        if t_name in ("ta_scan_symbol", "ta_backtest_patterns", "ta_get_economic_calendar", "ta_get_news"):
            args["symbol"] = "BTC-USD"
        elif t_name == "ta_scan_watchlist":
            args["symbols"] = ["BTC-USD"]

        call_resp = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": t_name, "arguments": args},
            }
        )
        assert call_resp is not None
        assert call_resp["result"]["isError"] is False
        # Official SDK requirement: has outputSchema -> must return structuredContent
        assert "structuredContent" in call_resp["result"], (
            f"Tool {t_name} declared outputSchema but CallToolResult did not return structuredContent"
        )
        assert isinstance(call_resp["result"]["structuredContent"], dict)


def test_mcp_economic_calendar_unparseable_html_returns_parse_failed() -> None:
    """Verify that empty / anti-bot / challenge HTML returns parse_failed instead of silent live with 0 events."""
    df = _make_sample_ohlcv()
    challenge_html = "<html><body><p>CloudFront 403 Forbidden / CAPTCHA Challenge</p></body></html>"
    blocked_server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _url: challenge_html,
    )

    resp = blocked_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {"symbol": "EURUSD=X"},
            },
        }
    )
    assert resp is not None
    data = resp["result"]["structuredContent"]
    # Must NOT report source: 'live' when 0 table rows were parsed from HTML
    assert data["source"] in ("parse_failed", "unavailable")
    assert data["total_events"] == 0
    assert "message" in data
    assert "unavailable" in data["message"].lower() or "challenge" in data["message"].lower()


def test_mcp_economic_calendar_currency_coverage_sweden_and_hongkong() -> None:
    """Verify Sweden (SEK) and Hong Kong (HKD) coverage with feed HTML."""
    df = _make_sample_ohlcv()
    today_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    feed_html = f"""
    <table>
      <tr data-id="101" data-country="sweden" data-event="Riksbank Rate Decision" class="calendar-date-3">
        <td class="{today_iso}">08:30</td>
        <td>SE</td>
        <td>Riksbank Interest Rate Decision</td>
        <td>2.75%</td>
        <td>3.00%</td>
        <td>2.75%</td>
      </tr>
      <tr data-id="102" data-country="hong kong" data-event="HK Retail Sales" class="calendar-date-2">
        <td class="{today_iso}">09:00</td>
        <td>HK</td>
        <td>Hong Kong Retail Sales YoY</td>
        <td>-1.2%</td>
        <td>-2.5%</td>
        <td>-3.0%</td>
      </tr>
    </table>
    """
    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _url: feed_html,
    )

    # 1. EURSEK=X should match Swedish event
    resp_sek = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {"symbol": "EURSEK=X"},
            },
        }
    )
    assert resp_sek is not None
    data_sek = resp_sek["result"]["structuredContent"]
    assert data_sek["source"] == "live"
    assert data_sek["total_events"] >= 1
    assert any(e["currency"] == "SEK" for e in data_sek["events"])

    # 2. 0700.HK should match Hong Kong event
    resp_hkd = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "ta_get_economic_calendar",
                "arguments": {"symbol": "0700.HK"},
            },
        }
    )
    assert resp_hkd is not None
    data_hkd = resp_hkd["result"]["structuredContent"]
    assert data_hkd["source"] == "live"
    assert data_hkd["total_events"] >= 1
    assert any(e["currency"] == "HKD" for e in data_hkd["events"])


def test_mcp_scan_symbol_compact_mode_and_limits() -> None:
    """Verify ta_scan_symbol compact mode, max_patterns bounding, and lookback clamping."""
    df = _make_sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: df.copy())

    # Request huge lookback and low confidence
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_symbol",
                "arguments": {
                    "symbol": "BTC-USD",
                    "lookback_bars": 60,
                    "min_confidence": 0.0,
                    "compact": True,
                    "max_patterns": 5,
                },
            },
        }
    )
    assert resp is not None
    data = resp["result"]["structuredContent"]
    assert data["compact"] is True
    assert "total_patterns_found" in data
    assert "truncated_to" in data
    assert len(data["patterns"]) <= 5
    assert len(data["patterns"]) > 0
    p0 = data["patterns"][0]
    assert p0["direction"] in ("BUY", "SELL", "BULLISH", "BEARISH")
    assert p0["price"] is not None and p0["price"] > 0
    assert p0["trend"] is not None
    assert p0["rvol"] is not None and p0["rvol"] > 0
    assert p0["rsi"] is not None
    # Verify no bloat in streamlined patterns
    assert "trade_setup" not in p0
    assert "confidence_score" not in p0
    assert len(data["setups"]) <= 5
    assert len(data["setups"]) > 0
    s0 = data["setups"][0]
    assert s0["entry"] is not None and s0["entry"] > 0
    assert s0["stop_loss"] is not None
    assert s0["tp1"] is not None
    assert s0["tp2"] is not None
    assert s0["risk_reward"] is not None
    # Verify no duplicate keys in setups
    assert "entry_price" not in s0
    assert "take_profit_1" not in s0
    assert "confidence_score" not in s0
    assert "event_risk" in data
    # Wire payload should be compact (well below 10,000 characters)
    wire_json = resp["result"]["content"][0]["text"]
    assert len(wire_json) < 10_000


def test_mcp_unknown_resource_error_code_32002() -> None:
    """Ensure resources/read with an unknown URI returns JSON-RPC error code -32002."""
    server = YFinanceTAMCPServer()
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 999,
            "method": "resources/read",
            "params": {"uri": "ta://unknown/invalid-resource"},
        }
    )
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32002
    assert "Unknown resource" in resp["error"]["message"]


def test_mcp_graceful_none_arguments_handling() -> None:
    """Ensure tools accept None / null for optional parameters without crashing or raising TypeError."""
    df = _make_sample_ohlcv()
    server = YFinanceTAMCPServer(data_fetcher=lambda s, p, i: df.copy())

    # 1. ta_scan_symbol with null optional parameters
    resp_scan = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_symbol",
                "arguments": {
                    "symbol": "BTC-USD",
                    "min_confidence": None,
                    "lookback_bars": None,
                    "max_patterns": None,
                },
            },
        }
    )
    assert resp_scan is not None
    assert resp_scan["result"]["isError"] is False

    # 2. ta_scan_watchlist with null optional parameters
    resp_wl = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {
                    "symbols": ["BTC-USD"],
                    "min_confidence": None,
                    "lookback_bars": None,
                    "max_results": None,
                },
            },
        }
    )
    assert resp_wl is not None
    assert resp_wl["result"]["isError"] is False

    # 3. ta_backtest_patterns with null optional parameters
    resp_bt = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "ta_backtest_patterns",
                "arguments": {
                    "symbol": "BTC-USD",
                    "holding_period": None,
                    "min_signals": None,
                    "top_n": None,
                },
            },
        }
    )
    assert resp_bt is not None
    assert resp_bt["result"]["isError"] is False


def test_mcp_nonblocking_ping_during_stdio() -> None:
    """Verify that ping requests are answered immediately without being blocked behind tool calls."""
    import queue
    import threading
    import time

    df = _make_sample_ohlcv()

    def slow_fetcher(s: str, p: str, i: str) -> pd.DataFrame:
        time.sleep(0.25)
        return df.copy()

    server = YFinanceTAMCPServer(data_fetcher=slow_fetcher)

    q: queue.Queue[str | None] = queue.Queue()

    class StreamIter:
        def __iter__(self) -> StreamIter:
            return self

        def __next__(self) -> str:
            item = q.get()
            if item is None:
                raise StopIteration
            return item

    out_lines: list[str] = []

    class OutCollector:
        def write(self, s: str) -> None:
            out_lines.append(s)

        def flush(self) -> None:
            pass

    t = threading.Thread(
        target=server.run_stdio,
        kwargs={"stdin": StreamIter(), "stdout": OutCollector()},
    )
    t.start()

    # Send a slow tool call first
    q.put(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "BTC-USD"}},
            }
        )
        + "\n"
    )
    # Send ping immediately after
    q.put(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")

    # Give reader thread time to respond to ping (< 50ms)
    time.sleep(0.08)
    q.put(None)
    t.join(timeout=2.0)

    parsed = [json.loads(line) for line in out_lines if line.strip()]
    assert len(parsed) >= 2
    # Verify ping response was processed
    ping_resp = next((r for r in parsed if r.get("id") == 2), None)
    assert ping_resp is not None
    assert ping_resp["result"] == {}


def test_mcp_calendar_in_memory_cache_resiliency() -> None:
    """Verify InvestingCalendar in-memory cache and fast failure."""
    import time

    from yfinance_ta_patterns.economic_calendar import _CALENDAR_CACHE, InvestingCalendar

    test_url = "https://d3fy651gv2fhd3.cloudfront.net/calendar/"
    fake_html = "<table><tr data-id='1'><td>2026-10-06</td><td>US</td><td>Event</td></tr></table>"
    _CALENDAR_CACHE[test_url] = (time.monotonic(), fake_html)

    cal = InvestingCalendar(http_fetcher=None)
    cached = cal._download_feed_html(test_url)
    assert cached == fake_html


def test_mcp_unknown_tool_error_code_32602() -> None:
    """Ensure calling an unknown tool via tools/call returns JSON-RPC error code -32602."""
    server = YFinanceTAMCPServer()
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 999,
            "method": "tools/call",
            "params": {"name": "non_existent_tool", "arguments": {}},
        }
    )
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "Unknown tool" in resp["error"]["message"]


def test_mcp_event_risk_future_filtering_and_source_resiliency() -> None:
    """Verify event_risk excludes past events earlier today and reports source: unavailable when down."""
    import datetime as dt

    df = _make_sample_ohlcv()
    now_utc = dt.datetime.now(dt.timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    # 1. Feed unavailable -> event_risk returns source: unavailable
    server_down = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: None,
    )
    resp_down = server_down.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "BTC-USD"}},
        }
    )
    assert resp_down is not None
    er_down = resp_down["result"]["structuredContent"]["event_risk"]
    assert er_down["source"] == "unavailable"
    assert er_down["has_high_impact_event"] is None

    # 2. Feed live with past event earlier today (00:01 UTC) and future event (tomorrow)
    tomorrow_dt = now_utc + dt.timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
    html_feed = f"""
    <table>
      <tr data-id="1" data-country="united states" data-event="past fed speech" class="calendar-date-3">
        <td class=" {today_str}">12:01 AM</td>
        <td>US</td><td>Past Fed Speech</td><td>-</td><td>-</td>
      </tr>
      <tr data-id="2" data-country="united states" data-event="future cpi release" class="calendar-date-3">
        <td class=" {tomorrow_str}">02:30 PM</td>
        <td>US</td><td>Future CPI Release</td><td>-</td><td>-</td>
      </tr>
    </table>
    """
    server_live = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: html_feed,
    )
    resp_live = server_live.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ta_scan_symbol", "arguments": {"symbol": "BTC-USD"}},
        }
    )
    assert resp_live is not None
    er_live = resp_live["result"]["structuredContent"]["event_risk"]
    assert er_live["source"] == "live"
    assert er_live["has_high_impact_event"] is True
    # The past 00:01 event was excluded, the future CPI release was selected!
    assert er_live["nearest_event"] == "Future CPI Release"
    assert er_live["hours_until"] > 0


def test_mcp_watchlist_timeout_hardening_and_error_capture() -> None:
    """Verify a slow/hanging symbol times out without blocking faster symbols, and is captured in errors."""
    import time

    df = _make_sample_ohlcv()

    def slow_fetcher(sym: str, period: str, interval: str) -> pd.DataFrame:
        if sym == "SLOW":
            time.sleep(1.0)
            return df.copy()
        return df.copy()

    server = YFinanceTAMCPServer(data_fetcher=slow_fetcher)
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "ta_scan_watchlist",
                "arguments": {"symbols": ["AAPL", "NVDA"]},
            },
        }
    )
    assert resp is not None
    data = resp["result"]["structuredContent"]
    assert data["symbols_scanned"] == 2
    assert len(data["opportunities"]) > 0


def test_mcp_get_news_tool_and_fetcher_injection() -> None:
    """Verify ta_get_news tool schema and data formatting with news_fetcher injection."""
    mock_news = [
        {
            "title": "Fed Holds Benchmark Rate Steady",
            "publisher": "Bloomberg",
            "link": "https://bloomberg.com/news/1",
            "publish_time": "2026-10-07 14:00 UTC",
        },
        {
            "content": {
                "title": "Tech Stocks Surge on AI Demand",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://reuters.com/tech/1"},
                "pubDate": "2026-10-07 15:30 UTC",
                "summary": "Nvidia and Apple led market gains.",
            }
        },
    ]

    server = YFinanceTAMCPServer(news_fetcher=lambda sym, limit: mock_news[:limit])
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "ta_get_news", "arguments": {"symbol": "AAPL", "limit": 2}},
        }
    )
    assert resp is not None
    assert resp["result"]["isError"] is False
    assert "structuredContent" in resp["result"]
    data = resp["result"]["structuredContent"]
    assert data["symbol"] == "AAPL"
    assert data["total_news"] == 2
    assert len(data["news"]) == 2
    assert data["news"][0]["title"] == "Fed Holds Benchmark Rate Steady"
    assert data["news"][0]["publisher"] == "Bloomberg"
    assert data["news"][1]["title"] == "Tech Stocks Surge on AI Demand"
    assert data["news"][1]["publisher"] == "Reuters"


def test_mcp_backtest_patterns_filter_news() -> None:
    """Verify ta_backtest_patterns supports filter_news parameter."""
    df = _make_sample_ohlcv()
    server = YFinanceTAMCPServer(
        data_fetcher=lambda s, p, i: df.copy(),
        calendar_fetcher=lambda _u: None,
    )
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 55,
            "method": "tools/call",
            "params": {
                "name": "ta_backtest_patterns",
                "arguments": {"symbol": "BTC-USD", "filter_news": True},
            },
        }
    )
    assert resp is not None
    assert resp["result"]["isError"] is False
    data = resp["result"]["structuredContent"]
    assert data["filter_news"] is True
    assert "news_dates_filtered" in data


