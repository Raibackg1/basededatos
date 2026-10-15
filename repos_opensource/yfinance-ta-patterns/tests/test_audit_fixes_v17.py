"""Regression tests for the v17 audit fixes.

Covers four independent defects:

1. `MarketDataLoader.process` kept pre-resampling calendar days in `orig_dates` and then
   indexed them with post-resampling positions, so the `closed_only` daily branch compared
   every bar against a date from the first hours of the download window.
2. `AIMarketAnalyst.generate_brief` raised `KeyError` on an empty dataset, because
   `get_market_regime_summary` returns only a `NO_DATA` status marker in that case.
3. `AIMarketAnalyst.to_json` serialized non-finite floats as bare `NaN` / `Infinity`
   tokens, producing JSON that strict parsers reject.
4. `PatternConfidenceResult` never derived `grade` from `confidence_score`, so constructing
   a result from a bare score left it labelled with the dataclass default `WEAK`.
"""

import json

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai import AIMarketAnalyst, PatternConfidenceResult, SignalGrade
from yfinance_ta_patterns.data import MarketDataLoader


def _make_1h_frame(start: str, hours: int) -> pd.DataFrame:
    """Hourly OHLCV frame with healthy candle bodies and a deterministic shape."""
    dates = pd.date_range(start, periods=hours, freq="1h", tz="UTC")
    base = 1.1000 + np.sin(np.linspace(0.0, 6.0, hours)) * 0.0050
    opens = base
    closes = base + 0.0005
    highs = np.maximum(opens, closes) + 0.0010
    lows = np.minimum(opens, closes) - 0.0010
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(hours, 1000.0),
        },
        index=dates,
    )


# --------------------------------------------------------------------------------------
# 1. orig_dates / closed_only alignment after resampling
# --------------------------------------------------------------------------------------


def test_closed_only_excludes_forming_day_after_resample():
    """A daily bar for the in-progress UTC day must not survive closed_only filtering.

    2025-01-06 is a completed UTC day; 2025-01-07 is still forming at 19:00 UTC. The second
    bucket already holds 19 hourly bars, so it passes the resampler's bar-count heuristic and
    only the closed-only checks can reject it.
    """
    frame = pd.concat(
        [_make_1h_frame("2025-01-06 00:00", 24), _make_1h_frame("2025-01-07 00:00", 19)]
    )

    loader = MarketDataLoader("EURUSD=X", interval="1d", clean_forex_daily=True)
    res = loader.process(frame, now_utc=pd.Timestamp("2025-01-07 19:00", tz="UTC"))

    assert len(res) == 1
    assert res.index[0].strftime("%Y-%m-%d") == "2025-01-06"


def test_closed_only_disabled_keeps_every_resampled_day():
    """`closed_only=False` must still return both buckets (the flag stays meaningful)."""
    frame = pd.concat(
        [_make_1h_frame("2025-01-06 00:00", 24), _make_1h_frame("2025-01-07 00:00", 19)]
    )

    loader = MarketDataLoader("EURUSD=X", interval="1d", clean_forex_daily=True, closed_only=False)
    res = loader.process(frame, now_utc=pd.Timestamp("2025-01-07 19:00", tz="UTC"))

    assert len(res) == 2


def test_resampling_keeps_completed_days_without_using_stale_dates():
    """Every completed UTC day survives; no bucket is dropped or kept by a stale date.

    With a multi-day download window the stale pre-resample list only ever held a couple of
    distinct days, so it could not discriminate between the six buckets. This pins the
    end-to-end resample + closed_only path.
    """
    frame = _make_1h_frame("2025-01-06 00:00", 24 * 6)  # 6 full UTC days
    loader = MarketDataLoader("EURUSD=X", interval="1d", clean_forex_daily=True)

    res = loader.process(frame, now_utc=pd.Timestamp("2025-01-12 00:30", tz="UTC"))

    assert list(res.index.strftime("%Y-%m-%d")) == [
        "2025-01-06",
        "2025-01-07",
        "2025-01-08",
        "2025-01-09",
        "2025-01-10",
        "2025-01-11",
    ]


# --------------------------------------------------------------------------------------
# 2. AIMarketAnalyst on an empty dataset
# --------------------------------------------------------------------------------------


def test_analyst_generate_brief_empty_data_does_not_raise():
    """`generate_brief` must survive the NO_DATA regime summary instead of raising KeyError."""
    analyst = AIMarketAnalyst(pd.DataFrame())

    brief = analyst.generate_brief()

    assert "unavailable" in brief
    assert "NO_DATA" not in brief


def test_analyst_to_llm_prompt_empty_data_does_not_raise():
    """`to_llm_prompt` delegates to `generate_brief`, so it must not raise either."""
    prompt = AIMarketAnalyst(pd.DataFrame()).to_llm_prompt()

    assert "Senior Quantitative Portfolio Manager" in prompt
    assert "unavailable" in prompt


def test_analyst_no_data_summary_contract_unchanged():
    """The public NO_DATA marker on the regime summary must be preserved."""
    assert AIMarketAnalyst(pd.DataFrame()).get_market_regime_summary() == {"status": "NO_DATA"}


# --------------------------------------------------------------------------------------
# 3. to_json must emit strict, NaN-free JSON
# --------------------------------------------------------------------------------------


def _reject_json_constant(token: str) -> None:
    """`json.loads(parse_constant=...)` hook: called for NaN / Infinity / -Infinity."""
    raise AssertionError(f"non-finite JSON constant emitted: {token}")


def test_to_json_sanitizes_nan_volume():
    """A NaN Volume must serialize as null rather than the invalid bare `NaN` token."""
    frame = _make_1h_frame("2025-01-06 00:00", 6)
    frame.loc[frame.index[-1], "Volume"] = np.nan

    payload = AIMarketAnalyst(frame).to_json()

    # Strict parse: any NaN / Infinity token would trip the hook.
    parsed = json.loads(payload, parse_constant=_reject_json_constant)
    assert parsed["market_summary"]["last_volume"] is None
    assert parsed["market_summary"]["current_price"] == pytest.approx(
        float(frame["Close"].iloc[-1])
    )


def test_to_json_sanitizes_nan_rvol_in_pattern_results():
    """NaN metric fields inside pattern results must also become null."""
    frame = _make_1h_frame("2025-01-06 00:00", 6)
    result = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=frame.index[-1],
        raw_signal=100,
        rvol=float("nan"),
        rsi=float("nan"),
        atr=float("nan"),
    )

    payload = AIMarketAnalyst(frame, [result]).to_json()

    parsed = json.loads(payload, parse_constant=_reject_json_constant)
    metrics = parsed["patterns"][0]["metrics"]
    assert metrics["rvol"] is None
    assert metrics["rsi"] is None
    assert metrics["atr"] is None


def test_to_json_normalizes_integer_scalars():
    """NumPy scalars must serialize as JSON numbers, not fall through to str()."""
    frame = _make_1h_frame("2025-01-06 00:00", 6)
    result = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=frame.index[-1],
        raw_signal=np.int64(100),
    )

    parsed = json.loads(AIMarketAnalyst(frame, [result]).to_json())

    assert parsed["patterns"][0]["raw_signal"] == 100


# --------------------------------------------------------------------------------------
# 4. grade derived from the confluence score
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.00, SignalGrade.EXCELLENT),
        (0.80, SignalGrade.EXCELLENT),
        (0.79, SignalGrade.STRONG),
        (0.70, SignalGrade.STRONG),
        (0.69, SignalGrade.MODERATE),
        (0.55, SignalGrade.MODERATE),
        (0.54, SignalGrade.WEAK),
        (0.40, SignalGrade.WEAK),
        (0.39, SignalGrade.FALSE_SIGNAL),
        (0.00, SignalGrade.FALSE_SIGNAL),
    ],
)
def test_signal_grade_from_score_boundaries(score, expected):
    """`from_score` is the single source of truth for the documented thresholds."""
    assert SignalGrade.from_score(score) is expected


def test_pattern_result_derives_grade_from_score():
    """A result built from a bare score must not keep the dataclass default WEAK grade."""
    ts = pd.Timestamp("2025-01-06 12:00", tz="UTC")

    assert PatternConfidenceResult("HAMMER", ts, 100, confidence_score=0.95).grade is (
        SignalGrade.EXCELLENT
    )
    assert PatternConfidenceResult("HAMMER", ts, 100, confidence_score=0.62).grade is (
        SignalGrade.MODERATE
    )
    assert PatternConfidenceResult("HAMMER", ts, 100, confidence_score=0.10).grade is (
        SignalGrade.FALSE_SIGNAL
    )


def test_pattern_result_to_dict_reports_derived_grade():
    """`to_dict` is the public surface, so it must expose the derived grade too."""
    ts = pd.Timestamp("2025-01-06 12:00", tz="UTC")

    payload = PatternConfidenceResult("HAMMER", ts, 100, confidence_score=0.95).to_dict()

    assert payload["grade"] == "EXCELLENT"
    assert payload["confidence_score"] == 0.95


def test_pattern_result_respects_explicit_grade():
    """An explicitly supplied non-default grade must win over the derived one."""
    ts = pd.Timestamp("2025-01-06 12:00", tz="UTC")
    result = PatternConfidenceResult(
        "HAMMER", ts, 100, confidence_score=0.20, grade=SignalGrade.EXCELLENT
    )

    assert result.grade is SignalGrade.EXCELLENT


def test_pattern_result_without_score_defaults_to_false_signal():
    """No score means no conviction, so the grade must not claim the WEAK default."""
    ts = pd.Timestamp("2025-01-06 12:00", tz="UTC")
    result = PatternConfidenceResult("HAMMER", ts, 100)

    assert result.confidence == 0.0
    assert result.grade is SignalGrade.FALSE_SIGNAL


def test_scorer_grade_matches_derived_grade():
    """`score_signal` must agree with `from_score` across a range of signal strengths."""
    from yfinance_ta_patterns.ai import AIPatternScorer

    frame = _make_1h_frame("2025-01-06 00:00", 60)
    scorer = AIPatternScorer(frame)

    for ts in (frame.index[25], frame.index[40], frame.index[-1]):
        for raw_signal in (100, -100):
            result = scorer.score_signal("HAMMER", ts, raw_signal=raw_signal)
            assert result.grade is SignalGrade.from_score(result.confidence)
            # to_dict() must not disagree with the attribute either.
            assert result.to_dict()["grade"] == result.grade.value
