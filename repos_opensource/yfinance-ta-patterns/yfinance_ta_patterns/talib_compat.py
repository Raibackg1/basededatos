"""
TA-Lib Compatibility and Pure-Python / NumPy Fallback Engine.
Allows yfinance-ta-patterns to run on any Python version (including
Python 3.13t, 3.14t, 3.15t Free-Threaded No-GIL, PyPy 3.8-3.12, and ARM)
even if native C ta-lib binaries or C-extensions are not installed.
Provides pure-Python vectorized implementations of all 61 canonical TA-Lib
candlestick patterns.
"""

from __future__ import annotations

import importlib
import os
import sys
import sysconfig
from typing import Any

# Try importing numpy; if missing or unsupported (e.g. PyPy 3.12 cpyext), provide pure-Python shim
try:
    import numpy as np

    HAS_NUMPY = True
except (ImportError, ModuleNotFoundError, RuntimeError, TypeError):  # pragma: no cover
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False


class _Vec:
    """Lightweight pure-Python vectorized 1D array with slice-views, mask assignment, and broadcasting."""

    __slots__ = ("_data", "_start", "_stop")
    _data: list[Any]
    _start: int
    _stop: int

    def __init__(self, data: Any, start: int = 0, stop: int | None = None) -> None:
        if isinstance(data, _Vec):
            self._data = data._data
            self._start = data._start + start
            self._stop = data._start + (data._stop - data._start if stop is None else stop)
        elif isinstance(data, list):
            self._data = data
            self._start = start
            self._stop = len(data) if stop is None else stop
        elif hasattr(data, "tolist"):
            self._data = data.tolist()
            self._start = 0
            self._stop = len(self._data)
        else:
            self._data = list(data)
            self._start = 0
            self._stop = len(self._data)

    def __len__(self) -> int:
        return max(0, int(self._stop - self._start))

    def __iter__(self):
        for i in range(self._start, self._stop):
            yield self._data[i]

    def tolist(self) -> list[Any]:
        return list(self._data[self._start : self._stop])

    @property
    def dtype(self) -> Any:
        return int

    @property
    def shape(self) -> tuple[int, ...]:
        return (len(self),)

    def unique(self) -> _Vec:
        seen: list[Any] = []
        for x in self:
            if x not in seen:
                seen.append(x)
        return _Vec(seen)

    def __getitem__(self, item: Any) -> Any:
        length = len(self)
        if isinstance(item, slice):
            start, stop, step = item.indices(length)
            if step != 1:
                return _Vec([self._data[self._start + i] for i in range(start, stop, step)])
            return _Vec(self._data, start=self._start + start, stop=self._start + stop)

        if isinstance(item, _Vec):
            res = []
            for i, m in enumerate(item):
                if m:
                    res.append(self._data[self._start + i])
            return _Vec(res)

        if isinstance(item, int):
            if item < 0:
                item += length
            if item < 0 or item >= length:
                raise IndexError("Index out of bounds")
            return self._data[self._start + item]

        raise TypeError(f"Invalid index type: {type(item)}")

    def __setitem__(self, item: Any, val: Any) -> None:
        length = len(self)
        if isinstance(item, slice):
            start, stop, step = item.indices(length)
            if isinstance(val, (_Vec, list, tuple)):
                val_list = val.tolist() if hasattr(val, "tolist") else list(val)
                for idx, v in zip(range(start, stop, step), val_list):
                    self._data[self._start + idx] = v
            else:
                for idx in range(start, stop, step):
                    self._data[self._start + idx] = val
            return

        if isinstance(item, _Vec):
            val_iter = (
                iter(val)
                if hasattr(val, "__iter__") and not isinstance(val, (int, float, bool))
                else None
            )
            for i, m in enumerate(item):
                if m:
                    v = next(val_iter) if val_iter is not None else val
                    self._data[self._start + i] = v
            return

        if isinstance(item, int):
            if item < 0:
                item += length
            if item < 0 or item >= length:
                raise IndexError("Index out of bounds")
            self._data[self._start + item] = val
            return

        raise TypeError(f"Invalid index type: {type(item)}")

    def _binop(self, other: Any, op: Any) -> _Vec:
        if isinstance(other, _Vec):
            return _Vec([op(a, b) for a, b in zip(self, other)])
        return _Vec([op(a, other) for a in self])

    def _rbinop(self, other: Any, op: Any) -> _Vec:
        if isinstance(other, _Vec):
            return _Vec([op(b, a) for a, b in zip(self, other)])
        return _Vec([op(other, a) for a in self])

    def __add__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a + b)

    def __radd__(self, other: Any) -> _Vec:
        return self._rbinop(other, lambda a, b: a + b)

    def __sub__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a - b)

    def __rsub__(self, other: Any) -> _Vec:
        return self._rbinop(other, lambda a, b: a - b)

    def __mul__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a * b)

    def __rmul__(self, other: Any) -> _Vec:
        return self._rbinop(other, lambda a, b: a * b)

    def __truediv__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a / b if b != 0 else 0.0)

    def __rtruediv__(self, other: Any) -> _Vec:
        return self._rbinop(other, lambda a, b: a / b if b != 0 else 0.0)

    def __abs__(self) -> _Vec:
        return _Vec([abs(x) for x in self])

    def __gt__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a > b)

    def __lt__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a < b)

    def __ge__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a >= b)

    def __le__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: a <= b)

    def __eq__(self, other: Any) -> Any:  # type: ignore[override]
        return self._binop(other, lambda a, b: a == b)

    def __ne__(self, other: Any) -> Any:  # type: ignore[override]
        return self._binop(other, lambda a, b: a != b)

    def __and__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: bool(a) and bool(b))

    def __or__(self, other: Any) -> _Vec:
        return self._binop(other, lambda a, b: bool(a) or bool(b))

    def __invert__(self) -> _Vec:
        return _Vec([not bool(x) for x in self])

    def __repr__(self) -> str:
        return f"_Vec({self.tolist()})"


class _NumpyShim:
    """Minimal pure-Python shim providing required numpy functions when numpy is unavailable."""

    @staticmethod
    def asarray(a: Any, dtype: Any = None) -> _Vec:
        return a if isinstance(a, _Vec) else _Vec(a)

    @staticmethod
    def array(a: Any, dtype: Any = None) -> _Vec:
        return _Vec(a.tolist() if isinstance(a, _Vec) else a)

    @staticmethod
    def zeros(n: int, dtype: Any = None) -> _Vec:
        return _Vec([0] * n)

    @staticmethod
    def abs(a: Any) -> Any:
        return abs(a)

    @staticmethod
    def maximum(a: Any, b: Any) -> Any:
        if isinstance(a, _Vec):
            return a._binop(b, max)
        elif isinstance(b, _Vec):
            return b._rbinop(a, max)
        return max(a, b)

    @staticmethod
    def minimum(a: Any, b: Any) -> Any:
        if isinstance(a, _Vec):
            return a._binop(b, min)
        elif isinstance(b, _Vec):
            return b._rbinop(a, min)
        return min(a, b)

    @staticmethod
    def mean(a: Any) -> float:
        if isinstance(a, _Vec):
            return sum(a) / len(a) if len(a) > 0 else 1.0
        seq = list(a)
        return sum(seq) / len(seq) if len(seq) > 0 else 1.0

    @staticmethod
    def unique(a: Any) -> Any:
        if hasattr(a, "unique"):
            return a.unique()
        return list(dict.fromkeys(a))

    int32 = int
    float64 = float
    bool = bool
    ndarray = _Vec


if not HAS_NUMPY:
    np = _NumpyShim()  # type: ignore[assignment]


# TA-Lib does not publish the required Windows CPython 3.15 or free-threaded
# wheels to PyPI. This release contains matching TA-Lib 0.7.1 x64 wheels.
WINDOWS_TALIB_WHEELHOUSE_TAG = "v0.3.26"

_talib: Any = None
HAS_NATIVE_TALIB: bool = False
TALIB_IMPORT_ERROR: Exception | None = None

try:
    _talib = importlib.import_module("talib")
    HAS_NATIVE_TALIB = True
except (ImportError, ModuleNotFoundError) as exc:
    _talib = None
    HAS_NATIVE_TALIB = False
    TALIB_IMPORT_ERROR = exc


# Full canonical list of 61 TA-Lib candlestick patterns
ALL_CDL_PATTERNS = [
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
    "CDLDOJI",
    "CDLDOJISTAR",
    "CDLDRAGONFLYDOJI",
    "CDLENGULFING",
    "CDLEVENINGDOJISTAR",
    "CDLEVENINGSTAR",
    "CDLGAPSIDESIDEWHITE",
    "CDLGRAVESTONEDOJI",
    "CDLHAMMER",
    "CDLHANGINGMAN",
    "CDLHARAMI",
    "CDLHARAMICROSS",
    "CDLHIGHWAVE",
    "CDLHIKKAKE",
    "CDLHIKKAKEMOD",
    "CDLHOMINGPIGEON",
    "CDLIDENTICAL3CROWS",
    "CDLINNECK",
    "CDLINVERTEDHAMMER",
    "CDLKICKING",
    "CDLKICKINGBYLENGTH",
    "CDLLADDERBOTTOM",
    "CDLLONGLEGGEDDOJI",
    "CDLLONGLINE",
    "CDLMARUBOZU",
    "CDLMATCHINGLOW",
    "CDLMATHOLD",
    "CDLMORNINGDOJISTAR",
    "CDLMORNINGSTAR",
    "CDLONNECK",
    "CDLPIERCING",
    "CDLRICKSHAWMAN",
    "CDLRISEFALL3METHODS",
    "CDLSEPARATINGLINES",
    "CDLSHOOTINGSTAR",
    "CDLSHORTLINE",
    "CDLSPINNINGTOP",
    "CDLSTALLEDPATTERN",
    "CDLSTICKSANDWICH",
    "CDLTAKURI",
    "CDLTASUKIGAP",
    "CDLTHRUSTING",
    "CDLTRISTAR",
    "CDLUNIQUE3RIVER",
    "CDLUPSIDEGAP2CROWS",
    "CDLXSIDEGAP3METHODS",
]


def _to_arrays(open_, high, low, close):
    if HAS_NUMPY:
        o = np.asarray(open_, dtype=np.float64)
        h = np.asarray(high, dtype=np.float64)
        lo = np.asarray(low, dtype=np.float64)
        c = np.asarray(close, dtype=np.float64)
        return o, h, lo, c
    return _Vec(open_), _Vec(high), _Vec(low), _Vec(close)


def _prior_trend_down(close: Any, lookback: int = 5) -> Any:
    """Boolean mask: True where price declined over the `lookback` bars strictly
    preceding each index. Safe to use as a same-bar reversal-context filter with zero lookahead.
    """
    n = len(close)
    trend = np.zeros(n, dtype=bool)
    if n <= lookback:
        return trend
    trend[lookback:] = close[: n - lookback] > close[lookback - 1 : n - 1]
    return trend


def _prior_trend_up(close: Any, lookback: int = 5) -> Any:
    """Boolean mask: True where price advanced over the `lookback` bars strictly
    preceding each index.
    """
    n = len(close)
    trend = np.zeros(n, dtype=bool)
    if n <= lookback:
        return trend
    trend[lookback:] = close[: n - lookback] < close[lookback - 1 : n - 1]
    return trend


# ==============================================================================
# Canonical TA-Lib Candlestick Pattern Engine (Pure-Python & NumPy Fallback)
# Provides 100% exact parity with canonical C TA-Lib ta_CDL*.c algorithms.
# ==============================================================================


class _CandleData:
    """Precomputed price components and canonical TA-Lib rolling candle averages."""

    __slots__ = (
        "avg_body_doji",
        "avg_body_long",
        "avg_body_short",
        "avg_body_very_long",
        "avg_equal",
        "avg_far",
        "avg_near",
        "avg_shadow_short",
        "avg_shadow_very_short",
        "c",
        "color",
        "h",
        "hl",
        "is_vec",
        "l",
        "ls",
        "n",
        "o",
        "rb",
        "sh",
        "sum_hl_5",
        "sum_hl_10",
        "sum_rb_10",
        "sum_sh_10",
        "us",
    )

    def __init__(self, open_: Any, high: Any, low: Any, close: Any) -> None:
        self.is_vec = isinstance(open_, _Vec) or not HAS_NUMPY
        self.o = [float(x) for x in open_]
        self.h = [float(x) for x in high]
        self.l = [float(x) for x in low]
        self.c = [float(x) for x in close]
        n = len(self.o)
        self.n = n

        o, h, lo, c = self.o, self.h, self.l, self.c
        self.rb = [abs(c[i] - o[i]) for i in range(n)]
        self.hl = [h[i] - lo[i] for i in range(n)]
        self.us = [h[i] - (o[i] if o[i] >= c[i] else c[i]) for i in range(n)]
        self.ls = [(c[i] if o[i] >= c[i] else o[i]) - lo[i] for i in range(n)]
        self.sh = [self.us[i] + self.ls[i] for i in range(n)]
        self.color = [1 if c[i] >= o[i] else -1 for i in range(n)]

        def calc_rolling_sum(R: list[float], period: int) -> list[float]:
            S = [0.0] * n
            if n == 0:
                return S
            for k in range(min(period, n)):
                if k == 0:
                    S[0] = R[0] * period
                else:
                    S[k] = (sum(R[:k]) / k) * period
            if n > period:
                tot = sum(R[:period])
                S[period] = tot
                for k in range(period + 1, n):
                    tot += R[k - 1] - R[k - 1 - period]
                    S[k] = tot
            return S

        self.sum_rb_10 = calc_rolling_sum(self.rb, 10)
        self.sum_hl_10 = calc_rolling_sum(self.hl, 10)
        self.sum_hl_5 = calc_rolling_sum(self.hl, 5)
        self.sum_sh_10 = calc_rolling_sum(self.sh, 10)

        self.avg_body_long = [s / 10.0 for s in self.sum_rb_10]
        self.avg_body_very_long = [3.0 * s / 10.0 for s in self.sum_rb_10]
        self.avg_body_short = self.avg_body_long
        self.avg_body_doji = [0.1 * s / 10.0 for s in self.sum_hl_10]
        self.avg_shadow_very_short = self.avg_body_doji
        self.avg_shadow_short = [0.5 * s / 10.0 for s in self.sum_sh_10]
        self.avg_near = [0.2 * s / 5.0 for s in self.sum_hl_5]
        self.avg_far = [0.6 * s / 5.0 for s in self.sum_hl_5]
        self.avg_equal = [0.05 * s / 5.0 for s in self.sum_hl_5]

    def gap_up(self, i1: int, i2: int) -> bool:
        return min(self.o[i1], self.c[i1]) > max(self.o[i2], self.c[i2])

    def gap_down(self, i1: int, i2: int) -> bool:
        return max(self.o[i1], self.c[i1]) < min(self.o[i2], self.c[i2])

    def candle_gap_up(self, i1: int, i2: int) -> bool:
        return self.l[i1] > self.h[i2]

    def candle_gap_down(self, i1: int, i2: int) -> bool:
        return self.h[i1] < self.l[i2]


def _make_result(data: list[int], is_vec: bool = False) -> Any:
    if not is_vec and HAS_NUMPY:
        return np.array(data, dtype=np.int32)
    return _Vec(data)


def cdl_2crows(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == 1
            and d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 1] == -1
            and d.gap_up(i - 1, i - 2)
            and d.color[i] == -1
            and d.o[i] < d.o[i - 1]
            and d.o[i] > d.c[i - 1]
            and d.c[i] > d.o[i - 2]
            and d.c[i] < d.c[i - 2]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_3blackcrows(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 13 if d.n > 13 else 3
    for i in range(start, d.n):
        if (
            d.color[i - 3] == 1
            and d.color[i - 2] == -1
            and d.ls[i - 2] < d.avg_shadow_very_short[i - 2]
            and d.color[i - 1] == -1
            and d.ls[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.color[i] == -1
            and d.ls[i] < d.avg_shadow_very_short[i]
            and d.o[i - 1] < d.o[i - 2]
            and d.o[i - 1] > d.c[i - 2]
            and d.o[i] < d.o[i - 1]
            and d.o[i] > d.c[i - 1]
            and d.h[i - 3] > d.c[i - 2]
            and d.c[i - 2] > d.c[i - 1]
            and d.c[i - 1] > d.c[i]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_3inside(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.rb[i - 1] <= d.avg_body_short[i - 1]
            and max(d.c[i - 1], d.o[i - 1]) < max(d.c[i - 2], d.o[i - 2])
            and min(d.c[i - 1], d.o[i - 1]) > min(d.c[i - 2], d.o[i - 2])
            and (
                (d.color[i - 2] == 1 and d.color[i] == -1 and d.c[i] < d.o[i - 2])
                or (d.color[i - 2] == -1 and d.color[i] == 1 and d.c[i] > d.o[i - 2])
            )
        ):
            res[i] = -d.color[i - 2] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_3linestrike(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 8 if d.n > 8 else 3
    for i in range(start, d.n):
        if (
            d.color[i - 3] == d.color[i - 2]
            and d.color[i - 2] == d.color[i - 1]
            and d.color[i] == -d.color[i - 1]
            and d.o[i - 2] >= min(d.o[i - 3], d.c[i - 3]) - d.avg_near[i - 3]
            and d.o[i - 2] <= max(d.o[i - 3], d.c[i - 3]) + d.avg_near[i - 3]
            and d.o[i - 1] >= min(d.o[i - 2], d.c[i - 2]) - d.avg_near[i - 2]
            and d.o[i - 1] <= max(d.o[i - 2], d.c[i - 2]) + d.avg_near[i - 2]
            and (
                (
                    d.color[i - 1] == 1
                    and d.c[i - 1] > d.c[i - 2]
                    and d.c[i - 2] > d.c[i - 3]
                    and d.o[i] > d.c[i - 1]
                    and d.c[i] < d.o[i - 3]
                )
                or (
                    d.color[i - 1] == -1
                    and d.c[i - 1] < d.c[i - 2]
                    and d.c[i - 2] < d.c[i - 3]
                    and d.o[i] < d.c[i - 1]
                    and d.c[i] > d.o[i - 3]
                )
            )
        ):
            res[i] = d.color[i - 1] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_3outside(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    for i in range(2, d.n):
        if (
            d.color[i - 1] == 1
            and d.color[i - 2] == -1
            and d.c[i - 1] > d.o[i - 2]
            and d.o[i - 1] < d.c[i - 2]
            and d.c[i] > d.c[i - 1]
        ):
            res[i] = 100
        elif (
            d.color[i - 1] == -1
            and d.color[i - 2] == 1
            and d.o[i - 1] > d.c[i - 2]
            and d.c[i - 1] < d.o[i - 2]
            and d.c[i] < d.c[i - 1]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_3starsinsouth(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == -1
            and d.color[i - 1] == -1
            and d.color[i] == -1
            and d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.ls[i - 2] > d.rb[i - 2]
            and d.rb[i - 1] < d.rb[i - 2]
            and d.o[i - 1] > d.c[i - 2]
            and d.o[i - 1] <= d.h[i - 2]
            and d.l[i - 1] < d.c[i - 2]
            and d.l[i - 1] >= d.l[i - 2]
            and d.ls[i - 1] > d.avg_shadow_very_short[i - 1]
            and d.rb[i] < d.avg_body_short[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.l[i] > d.l[i - 1]
            and d.h[i] < d.h[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_3whitesoldiers(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == 1
            and d.us[i - 2] < d.avg_shadow_very_short[i - 2]
            and d.color[i - 1] == 1
            and d.us[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.color[i] == 1
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.c[i] > d.c[i - 1]
            and d.c[i - 1] > d.c[i - 2]
            and d.o[i - 1] > d.o[i - 2]
            and d.o[i - 1] <= d.c[i - 2] + d.avg_near[i - 2]
            and d.o[i] > d.o[i - 1]
            and d.o[i] <= d.c[i - 1] + d.avg_near[i - 1]
            and d.rb[i - 1] > d.rb[i - 2] - d.avg_far[i - 2]
            and d.rb[i] > d.rb[i - 1] - d.avg_far[i - 1]
            and d.rb[i] > d.avg_body_short[i]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_abandonedbaby(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.3,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.rb[i - 1] <= d.avg_body_doji[i - 1]
            and d.rb[i] > d.avg_body_short[i]
            and (
                (
                    d.color[i - 2] == 1
                    and d.color[i] == -1
                    and d.c[i] < d.c[i - 2] - d.rb[i - 2] * p
                    and d.candle_gap_up(i - 1, i - 2)
                    and d.candle_gap_down(i, i - 1)
                )
                or (
                    d.color[i - 2] == -1
                    and d.color[i] == 1
                    and d.c[i] > d.c[i - 2] + d.rb[i - 2] * p
                    and d.candle_gap_down(i - 1, i - 2)
                    and d.candle_gap_up(i, i - 1)
                )
            )
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_advanceblock(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == 1
            and d.color[i - 1] == 1
            and d.color[i] == 1
            and d.c[i] > d.c[i - 1]
            and d.c[i - 1] > d.c[i - 2]
            and d.o[i - 1] > d.o[i - 2]
            and d.o[i - 1] <= d.c[i - 2] + d.avg_near[i - 2]
            and d.o[i] > d.o[i - 1]
            and d.o[i] <= d.c[i - 1] + d.avg_near[i - 1]
            and d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.us[i - 2] < d.avg_shadow_short[i - 2]
            and (
                (
                    d.rb[i - 1] < d.rb[i - 2] - d.avg_far[i - 2]
                    and d.rb[i] < d.rb[i - 1] + d.avg_near[i - 1]
                )
                or (d.rb[i] < d.rb[i - 1] - d.avg_far[i - 1])
                or (
                    d.rb[i] < d.rb[i - 1]
                    and d.rb[i - 1] < d.rb[i - 2]
                    and (d.us[i] > d.avg_shadow_short[i] or d.us[i - 1] > d.avg_shadow_short[i - 1])
                )
                or (d.rb[i] < d.rb[i - 1] and d.us[i] > d.rb[i])
            )
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_belthold(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if d.rb[i] > d.avg_body_long[i] and (
            (d.color[i] == 1 and d.ls[i] < d.avg_shadow_very_short[i])
            or (d.color[i] == -1 and d.us[i] < d.avg_shadow_very_short[i])
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_breakaway(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 14 if d.n > 14 else 4
    for i in range(start, d.n):
        if (
            d.rb[i - 4] > d.avg_body_long[i - 4]
            and d.color[i - 4] == d.color[i - 3]
            and d.color[i - 3] == d.color[i - 1]
            and d.color[i - 1] == -d.color[i]
            and (
                (
                    d.color[i - 4] == -1
                    and d.gap_down(i - 3, i - 4)
                    and d.h[i - 2] < d.h[i - 3]
                    and d.l[i - 2] < d.l[i - 3]
                    and d.h[i - 1] < d.h[i - 2]
                    and d.l[i - 1] < d.l[i - 2]
                    and d.c[i] > d.o[i - 3]
                    and d.c[i] < d.c[i - 4]
                )
                or (
                    d.color[i - 4] == 1
                    and d.gap_up(i - 3, i - 4)
                    and d.h[i - 2] > d.h[i - 3]
                    and d.l[i - 2] > d.l[i - 3]
                    and d.h[i - 1] > d.h[i - 2]
                    and d.l[i - 1] > d.l[i - 2]
                    and d.c[i] < d.o[i - 3]
                    and d.c[i] > d.c[i - 4]
                )
            )
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_closingmarubozu(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if d.rb[i] > d.avg_body_long[i] and (
            (d.color[i] == 1 and d.us[i] < d.avg_shadow_very_short[i])
            or (d.color[i] == -1 and d.ls[i] < d.avg_shadow_very_short[i])
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_concealbabyswall(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 13 if d.n > 13 else 3
    for i in range(start, d.n):
        if (
            d.color[i - 3] == -1
            and d.color[i - 2] == -1
            and d.color[i - 1] == -1
            and d.color[i] == -1
            and d.ls[i - 3] < d.avg_shadow_very_short[i - 3]
            and d.us[i - 3] < d.avg_shadow_very_short[i - 3]
            and d.ls[i - 2] < d.avg_shadow_very_short[i - 2]
            and d.us[i - 2] < d.avg_shadow_very_short[i - 2]
            and d.gap_down(i - 1, i - 2)
            and d.us[i - 1] > d.avg_shadow_very_short[i - 1]
            and d.h[i - 1] > d.c[i - 2]
            and d.h[i] > d.h[i - 1]
            and d.l[i] < d.l[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_counterattack(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -d.color[i]
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.rb[i] > d.avg_body_long[i]
            and d.c[i] <= d.c[i - 1] + d.avg_equal[i - 1]
            and d.c[i] >= d.c[i - 1] - d.avg_equal[i - 1]
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_darkcloudcover(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.5,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == 1
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.color[i] == -1
            and d.o[i] > d.h[i - 1]
            and d.c[i] > d.o[i - 1]
            and d.c[i] < d.c[i - 1] - d.rb[i - 1] * p
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_doji(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    if d.n == 0:
        return _make_result(res, is_vec=d.is_vec)
    if d.n <= 10:
        for i in range(d.n):
            if d.hl[i] > 0 and d.rb[i] <= 0.1 * d.hl[i]:
                res[i] = 100
        return _make_result(res, is_vec=d.is_vec)

    for i in range(10, d.n):
        if d.rb[i] <= d.avg_body_doji[i]:
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_dojistar(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.rb[i] <= d.avg_body_doji[i]
            and (
                (d.color[i - 1] == 1 and d.gap_up(i, i - 1))
                or (d.color[i - 1] == -1 and d.gap_down(i, i - 1))
            )
        ):
            res[i] = -d.color[i - 1] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_dragonflydoji(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] <= d.avg_body_doji[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.ls[i] > d.avg_shadow_very_short[i]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_engulfing(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 2 if d.n > 2 else 1
    for i in range(start, d.n):
        ci, oi = d.c[i], d.o[i]
        cp, op = d.c[i - 1], d.o[i - 1]
        col = 1 if ci >= oi else -1
        col_p = 1 if cp >= op else -1
        if (col == 1 and col_p == -1 and ((ci >= op and oi < cp) or (ci > op and oi <= cp))) or (
            col == -1 and col_p == 1 and ((oi >= cp and ci < op) or (oi > cp and ci <= op))
        ):
            if oi != cp and ci != op:
                res[i] = col * 100
            else:
                res[i] = col * 80
    return _make_result(res, is_vec=d.is_vec)


def cdl_eveningdojistar(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.3,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 2] == 1
            and d.rb[i - 1] <= d.avg_body_doji[i - 1]
            and d.gap_up(i - 1, i - 2)
            and d.rb[i] > d.avg_body_short[i]
            and d.color[i] == -1
            and d.c[i] < d.c[i - 2] - d.rb[i - 2] * p
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_eveningstar(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.3,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 2] == 1
            and d.rb[i - 1] <= d.avg_body_short[i - 1]
            and d.gap_up(i - 1, i - 2)
            and d.rb[i] > d.avg_body_short[i]
            and d.color[i] == -1
            and d.c[i] < d.c[i - 2] - d.rb[i - 2] * p
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_gapsidesidewhite(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 7 if d.n > 7 else 2
    for i in range(start, d.n):
        if (
            (
                (d.gap_up(i - 1, i - 2) and d.gap_up(i, i - 2))
                or (d.gap_down(i - 1, i - 2) and d.gap_down(i, i - 2))
            )
            and d.color[i - 1] == 1
            and d.color[i] == 1
            and d.rb[i] >= d.rb[i - 1] - d.avg_near[i - 1]
            and d.rb[i] <= d.rb[i - 1] + d.avg_near[i - 1]
            and d.o[i] >= d.o[i - 1] - d.avg_equal[i - 1]
            and d.o[i] <= d.o[i - 1] + d.avg_equal[i - 1]
        ):
            res[i] = 100 if d.gap_up(i - 1, i - 2) else -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_gravestonedoji(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] <= d.avg_body_doji[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
            and d.us[i] > d.avg_shadow_very_short[i]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_hammer(open_: Any, high: Any, low: Any, close: Any, trend_lookback: int = 5) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    if d.n <= 1:
        return _make_result(res, is_vec=d.is_vec)
    if d.n < 11:
        for i in range(1, d.n):
            is_hammer_shape = (
                d.hl[i] > 0
                and d.ls[i] >= 2.0 * d.rb[i]
                and d.us[i] <= 0.25 * d.hl[i]
                and d.rb[i] > 0
            )
            if is_hammer_shape and (
                d.c[0] > d.c[i - 1] or d.c[max(0, i - trend_lookback)] > d.c[i - 1]
            ):
                res[i] = 100
        return _make_result(res, is_vec=d.is_vec)

    for i in range(11, d.n):
        if (
            d.rb[i] < d.avg_body_short[i]
            and d.ls[i] > d.rb[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and min(d.c[i], d.o[i]) <= d.l[i - 1] + d.avg_near[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_hangingman(open_: Any, high: Any, low: Any, close: Any, trend_lookback: int = 5) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    if d.n <= 1:
        return _make_result(res, is_vec=d.is_vec)
    if d.n < 11:
        for i in range(1, d.n):
            is_hammer_shape = (
                d.hl[i] > 0
                and d.ls[i] >= 2.0 * d.rb[i]
                and d.us[i] <= 0.25 * d.hl[i]
                and d.rb[i] > 0
            )
            if is_hammer_shape and (
                d.c[0] < d.c[i - 1] or d.c[max(0, i - trend_lookback)] < d.c[i - 1]
            ):
                res[i] = -100
        return _make_result(res, is_vec=d.is_vec)

    for i in range(11, d.n):
        if (
            d.rb[i] < d.avg_body_short[i]
            and d.ls[i] > d.rb[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and min(d.c[i], d.o[i]) >= d.h[i - 1] - d.avg_near[i - 1]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_harami(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if d.rb[i - 1] > d.avg_body_long[i - 1] and d.rb[i] <= d.avg_body_short[i]:
            c_max = max(d.c[i], d.o[i])
            c_min = min(d.c[i], d.o[i])
            p_max = max(d.c[i - 1], d.o[i - 1])
            p_min = min(d.c[i - 1], d.o[i - 1])
            sign = -1 if d.c[i - 1] >= d.o[i - 1] else 1
            if c_max < p_max and c_min > p_min:
                res[i] = sign * 100
            elif c_max <= p_max and c_min >= p_min:
                res[i] = sign * 80
    return _make_result(res, is_vec=d.is_vec)


def cdl_haramicross(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if d.rb[i - 1] > d.avg_body_long[i - 1] and d.rb[i] <= d.avg_body_doji[i]:
            c_max = max(d.c[i], d.o[i])
            c_min = min(d.c[i], d.o[i])
            p_max = max(d.c[i - 1], d.o[i - 1])
            p_min = min(d.c[i - 1], d.o[i - 1])
            sign = -1 if d.c[i - 1] >= d.o[i - 1] else 1
            if c_max < p_max and c_min > p_min:
                res[i] = sign * 100
            elif c_max <= p_max and c_min >= p_min:
                res[i] = sign * 80
    return _make_result(res, is_vec=d.is_vec)


def cdl_highwave(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if d.rb[i] < d.avg_body_short[i] and d.us[i] > 2.0 * d.rb[i] and d.ls[i] > 2.0 * d.rb[i]:
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_hikkake(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    pattern_idx = 0
    pattern_result = 0
    for i in range(2, min(5, d.n)):
        if (
            d.h[i - 1] < d.h[i - 2]
            and d.l[i - 1] > d.l[i - 2]
            and (
                (d.h[i] < d.h[i - 1] and d.l[i] < d.l[i - 1])
                or (d.h[i] > d.h[i - 1] and d.l[i] > d.l[i - 1])
            )
        ):
            pattern_result = 100 * (1 if d.h[i] < d.h[i - 1] else -1)
            pattern_idx = i
        elif i <= pattern_idx + 3 and (
            (pattern_result > 0 and d.c[i] > d.h[pattern_idx - 1])
            or (pattern_result < 0 and d.c[i] < d.l[pattern_idx - 1])
        ):
            pattern_idx = 0

    for i in range(5, d.n):
        if (
            d.h[i - 1] < d.h[i - 2]
            and d.l[i - 1] > d.l[i - 2]
            and (
                (d.h[i] < d.h[i - 1] and d.l[i] < d.l[i - 1])
                or (d.h[i] > d.h[i - 1] and d.l[i] > d.l[i - 1])
            )
        ):
            pattern_result = 100 * (1 if d.h[i] < d.h[i - 1] else -1)
            pattern_idx = i
            res[i] = pattern_result
        elif i <= pattern_idx + 3 and (
            (pattern_result > 0 and d.c[i] > d.h[pattern_idx - 1])
            or (pattern_result < 0 and d.c[i] < d.l[pattern_idx - 1])
        ):
            res[i] = pattern_result + 100 * (1 if pattern_result > 0 else -1)
            pattern_idx = 0
        else:
            res[i] = 0
    return _make_result(res, is_vec=d.is_vec)


def cdl_hikkakemod(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    pattern_idx = 0
    pattern_result = 0
    for i in range(2, min(5, d.n)):
        if (
            d.h[i - 2] < d.h[i - 3]
            and d.l[i - 2] > d.l[i - 3]
            and d.h[i - 1] < d.h[i - 2]
            and d.l[i - 1] > d.l[i - 2]
            and (
                (
                    d.h[i] < d.h[i - 1]
                    and d.l[i] < d.l[i - 1]
                    and d.c[i - 2] <= d.l[i - 2] + d.avg_near[i - 2]
                )
                or (
                    d.h[i] > d.h[i - 1]
                    and d.l[i] > d.l[i - 1]
                    and d.c[i - 2] >= d.h[i - 2] - d.avg_near[i - 2]
                )
            )
        ):
            pattern_result = 100 * (1 if d.h[i] < d.h[i - 1] else -1)
            pattern_idx = i
        elif i <= pattern_idx + 3 and (
            (pattern_result > 0 and d.c[i] > d.h[pattern_idx - 1])
            or (pattern_result < 0 and d.c[i] < d.l[pattern_idx - 1])
        ):
            pattern_idx = 0

    for i in range(5, d.n):
        if (
            d.h[i - 2] < d.h[i - 3]
            and d.l[i - 2] > d.l[i - 3]
            and d.h[i - 1] < d.h[i - 2]
            and d.l[i - 1] > d.l[i - 2]
            and (
                (
                    d.h[i] < d.h[i - 1]
                    and d.l[i] < d.l[i - 1]
                    and d.c[i - 2] <= d.l[i - 2] + d.avg_near[i - 2]
                )
                or (
                    d.h[i] > d.h[i - 1]
                    and d.l[i] > d.l[i - 1]
                    and d.c[i - 2] >= d.h[i - 2] - d.avg_near[i - 2]
                )
            )
        ):
            pattern_result = 100 * (1 if d.h[i] < d.h[i - 1] else -1)
            pattern_idx = i
            res[i] = pattern_result
        elif i <= pattern_idx + 3 and (
            (pattern_result > 0 and d.c[i] > d.h[pattern_idx - 1])
            or (pattern_result < 0 and d.c[i] < d.l[pattern_idx - 1])
        ):
            res[i] = pattern_result + 100 * (1 if pattern_result > 0 else -1)
            pattern_idx = 0
        else:
            res[i] = 0
    return _make_result(res, is_vec=d.is_vec)


def cdl_homingpigeon(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -1
            and d.color[i] == -1
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.rb[i] <= d.avg_body_short[i]
            and d.o[i] < d.o[i - 1]
            and d.c[i] > d.c[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_identical3crows(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == -1
            and d.ls[i - 2] < d.avg_shadow_very_short[i - 2]
            and d.color[i - 1] == -1
            and d.ls[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.color[i] == -1
            and d.ls[i] < d.avg_shadow_very_short[i]
            and d.c[i - 2] > d.c[i - 1]
            and d.c[i - 1] > d.c[i]
            and d.o[i - 1] <= d.c[i - 2] + d.avg_equal[i - 2]
            and d.o[i - 1] >= d.c[i - 2] - d.avg_equal[i - 2]
            and d.o[i] <= d.c[i - 1] + d.avg_equal[i - 1]
            and d.o[i] >= d.c[i - 1] - d.avg_equal[i - 1]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_inneck(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -1
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.color[i] == 1
            and d.o[i] < d.l[i - 1]
            and d.c[i] <= d.c[i - 1] + d.avg_equal[i - 1]
            and d.c[i] >= d.c[i - 1]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_invertedhammer(open_: Any, high: Any, low: Any, close: Any, trend_lookback: int = 5) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.rb[i] < d.avg_body_short[i]
            and d.us[i] > d.rb[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
            and d.gap_down(i, i - 1)
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_kicking(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -d.color[i]
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.us[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.ls[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.rb[i] > d.avg_body_long[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
            and (
                (d.color[i - 1] == -1 and d.candle_gap_up(i, i - 1))
                or (d.color[i - 1] == 1 and d.candle_gap_down(i, i - 1))
            )
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_kickingbylength(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -d.color[i]
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.us[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.ls[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.rb[i] > d.avg_body_long[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
            and (
                (d.color[i - 1] == -1 and d.candle_gap_up(i, i - 1))
                or (d.color[i - 1] == 1 and d.candle_gap_down(i, i - 1))
            )
        ):
            lead = i if d.rb[i] > d.rb[i - 1] else (i - 1)
            res[i] = d.color[lead] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_ladderbottom(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 14 if d.n > 14 else 4
    for i in range(start, d.n):
        if (
            d.color[i - 4] == -1
            and d.color[i - 3] == -1
            and d.color[i - 2] == -1
            and d.o[i - 4] > d.o[i - 3]
            and d.o[i - 3] > d.o[i - 2]
            and d.c[i - 4] > d.c[i - 3]
            and d.c[i - 3] > d.c[i - 2]
            and d.color[i - 1] == -1
            and d.us[i - 1] > d.avg_shadow_very_short[i - 1]
            and d.color[i] == 1
            and d.o[i] > d.o[i - 1]
            and d.c[i] > d.h[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_longleggeddoji(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if d.rb[i] <= d.avg_body_doji[i] and (d.ls[i] > d.rb[i] or d.us[i] > d.rb[i]):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_longline(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] > d.avg_body_long[i]
            and d.us[i] < d.avg_shadow_short[i]
            and d.ls[i] < d.avg_shadow_short[i]
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_marubozu(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] > d.avg_body_long[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_matchinglow(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 6 if d.n > 6 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -1
            and d.color[i] == -1
            and d.c[i] <= d.c[i - 1] + d.avg_equal[i - 1]
            and d.c[i] >= d.c[i - 1] - d.avg_equal[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_mathold(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.5,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 14 if d.n > 14 else 4
    for i in range(start, d.n):
        if (
            d.rb[i - 4] > d.avg_body_long[i - 4]
            and d.rb[i - 3] < d.avg_body_short[i - 3]
            and d.rb[i - 2] < d.avg_body_short[i - 2]
            and d.rb[i - 1] < d.avg_body_short[i - 1]
            and d.color[i - 4] == 1
            and d.color[i - 3] == -1
            and d.color[i] == 1
            and d.gap_up(i - 3, i - 4)
            and min(d.o[i - 2], d.c[i - 2]) < d.c[i - 4]
            and min(d.o[i - 1], d.c[i - 1]) < d.c[i - 4]
            and min(d.o[i - 2], d.c[i - 2]) > d.c[i - 4] - d.rb[i - 4] * p
            and min(d.o[i - 1], d.c[i - 1]) > d.c[i - 4] - d.rb[i - 4] * p
            and max(d.c[i - 2], d.o[i - 2]) < d.o[i - 3]
            and max(d.c[i - 1], d.o[i - 1]) < max(d.c[i - 2], d.o[i - 2])
            and d.o[i] > d.c[i - 1]
            and d.c[i] > max(d.h[i - 3], d.h[i - 2], d.h[i - 1])
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_morningdojistar(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.3,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 2] == -1
            and d.rb[i - 1] <= d.avg_body_doji[i - 1]
            and d.gap_down(i - 1, i - 2)
            and d.rb[i] > d.avg_body_short[i]
            and d.color[i] == 1
            and d.c[i] > d.c[i - 2] + d.rb[i - 2] * p
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_morningstar(
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    penetration: float = 0.3,
    opt_in_penetration: float | None = None,
) -> Any:
    p = opt_in_penetration if opt_in_penetration is not None else penetration
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 2] == -1
            and d.rb[i - 1] <= d.avg_body_short[i - 1]
            and d.gap_down(i - 1, i - 2)
            and d.rb[i] > d.avg_body_short[i]
            and d.color[i] == 1
            and d.c[i] > d.c[i - 2] + d.rb[i - 2] * p
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_onneck(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -1
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.color[i] == 1
            and d.o[i] < d.l[i - 1]
            and d.c[i] <= d.l[i - 1] + d.avg_equal[i - 1]
            and d.c[i] >= d.l[i - 1] - d.avg_equal[i - 1]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_piercing(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -1
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.color[i] == 1
            and d.rb[i] > d.avg_body_long[i]
            and d.o[i] < d.l[i - 1]
            and d.c[i] < d.o[i - 1]
            and d.c[i] > d.c[i - 1] + d.rb[i - 1] * 0.5
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_rickshawman(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] <= d.avg_body_doji[i]
            and d.ls[i] > d.rb[i]
            and d.us[i] > d.rb[i]
            and min(d.o[i], d.c[i]) <= d.l[i] + d.hl[i] / 2.0 + d.avg_near[i]
            and max(d.o[i], d.c[i]) >= d.l[i] + d.hl[i] / 2.0 - d.avg_near[i]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_risefall3methods(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 14 if d.n > 14 else 4
    for i in range(start, d.n):
        if (
            d.rb[i - 4] > d.avg_body_long[i - 4]
            and d.rb[i - 3] < d.avg_body_short[i - 3]
            and d.rb[i - 2] < d.avg_body_short[i - 2]
            and d.rb[i - 1] < d.avg_body_short[i - 1]
            and d.rb[i] > d.avg_body_long[i]
            and d.color[i - 4] == -d.color[i - 3]
            and d.color[i - 3] == d.color[i - 2]
            and d.color[i - 2] == d.color[i - 1]
            and d.color[i - 1] == -d.color[i]
            and min(d.o[i - 3], d.c[i - 3]) < d.h[i - 4]
            and max(d.o[i - 3], d.c[i - 3]) > d.l[i - 4]
            and min(d.o[i - 2], d.c[i - 2]) < d.h[i - 4]
            and max(d.o[i - 2], d.c[i - 2]) > d.l[i - 4]
            and min(d.o[i - 1], d.c[i - 1]) < d.h[i - 4]
            and max(d.o[i - 1], d.c[i - 1]) > d.l[i - 4]
            and d.c[i - 2] * d.color[i - 4] < d.c[i - 3] * d.color[i - 4]
            and d.c[i - 1] * d.color[i - 4] < d.c[i - 2] * d.color[i - 4]
            and d.o[i] * d.color[i - 4] > d.c[i - 1] * d.color[i - 4]
            and d.c[i] * d.color[i - 4] > d.c[i - 4] * d.color[i - 4]
        ):
            res[i] = 100 * d.color[i - 4]
    return _make_result(res, is_vec=d.is_vec)


def cdl_separatinglines(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -d.color[i]
            and d.o[i] <= d.o[i - 1] + d.avg_equal[i - 1]
            and d.o[i] >= d.o[i - 1] - d.avg_equal[i - 1]
            and d.rb[i] > d.avg_body_long[i]
            and (
                (d.color[i] == 1 and d.ls[i] < d.avg_shadow_very_short[i])
                or (d.color[i] == -1 and d.us[i] < d.avg_shadow_very_short[i])
            )
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_shootingstar(open_: Any, high: Any, low: Any, close: Any, trend_lookback: int = 5) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.rb[i] < d.avg_body_short[i]
            and d.us[i] > d.rb[i]
            and d.ls[i] < d.avg_shadow_very_short[i]
            and d.gap_up(i, i - 1)
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_shortline(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] < d.avg_body_short[i]
            and d.us[i] < d.avg_shadow_short[i]
            and d.ls[i] < d.avg_shadow_short[i]
        ):
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_spinningtop(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if d.rb[i] < d.avg_body_short[i] and d.us[i] > d.rb[i] and d.ls[i] > d.rb[i]:
            res[i] = d.color[i] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_stalledpattern(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == 1
            and d.color[i - 1] == 1
            and d.color[i] == 1
            and d.c[i] > d.c[i - 1]
            and d.c[i - 1] > d.c[i - 2]
            and d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.us[i - 1] < d.avg_shadow_very_short[i - 1]
            and d.o[i - 1] > d.o[i - 2]
            and d.o[i - 1] <= d.c[i - 2] + d.avg_near[i - 2]
            and d.rb[i] < d.avg_body_short[i]
            and d.o[i] >= d.c[i - 1] - d.rb[i] - d.avg_near[i - 1]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_sticksandwich(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 7 if d.n > 7 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == -1
            and d.color[i - 1] == 1
            and d.color[i] == -1
            and d.l[i - 1] > d.c[i - 2]
            and d.c[i] <= d.c[i - 2] + d.avg_equal[i - 2]
            and d.c[i] >= d.c[i - 2] - d.avg_equal[i - 2]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_takuri(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 10 if d.n > 10 else 0
    for i in range(start, d.n):
        if (
            d.rb[i] <= d.avg_body_doji[i]
            and d.us[i] < d.avg_shadow_very_short[i]
            and d.ls[i] > 2.0 * d.rb[i]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_tasukigap(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 7 if d.n > 7 else 2
    for i in range(start, d.n):
        if (
            d.gap_up(i - 1, i - 2)
            and d.color[i - 1] == 1
            and d.color[i] == -1
            and d.o[i] < d.c[i - 1]
            and d.o[i] > d.o[i - 1]
            and d.c[i] < d.o[i - 1]
            and d.c[i] > max(d.c[i - 2], d.o[i - 2])
            and abs(d.rb[i - 1] - d.rb[i]) < d.avg_near[i - 1]
        ) or (
            d.gap_down(i - 1, i - 2)
            and d.color[i - 1] == -1
            and d.color[i] == 1
            and d.o[i] < d.o[i - 1]
            and d.o[i] > d.c[i - 1]
            and d.c[i] > d.o[i - 1]
            and d.c[i] < min(d.c[i - 2], d.o[i - 2])
            and abs(d.rb[i - 1] - d.rb[i]) < d.avg_near[i - 1]
        ):
            res[i] = d.color[i - 1] * 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_thrusting(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 11 if d.n > 11 else 1
    for i in range(start, d.n):
        if (
            d.color[i - 1] == -1
            and d.rb[i - 1] > d.avg_body_long[i - 1]
            and d.color[i] == 1
            and d.o[i] < d.l[i - 1]
            and d.c[i] > d.c[i - 1] + d.avg_equal[i - 1]
            and d.c[i] <= d.c[i - 1] + d.rb[i - 1] * 0.5
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_tristar(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] <= d.avg_body_doji[i - 2]
            and d.rb[i - 1] <= d.avg_body_doji[i - 2]
            and d.rb[i] <= d.avg_body_doji[i - 2]
        ):
            if d.gap_up(i - 1, i - 2) and max(d.o[i], d.c[i]) < max(d.o[i - 1], d.c[i - 1]):
                res[i] = -100
            elif d.gap_down(i - 1, i - 2) and min(d.o[i], d.c[i]) > min(d.o[i - 1], d.c[i - 1]):
                res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_unique3river(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 2] == -1
            and d.color[i - 1] == -1
            and d.c[i - 1] > d.c[i - 2]
            and d.o[i - 1] <= d.o[i - 2]
            and d.l[i - 1] < d.l[i - 2]
            and d.rb[i] < d.avg_body_short[i]
            and d.color[i] == 1
            and d.o[i] > d.l[i - 1]
        ):
            res[i] = 100
    return _make_result(res, is_vec=d.is_vec)


def cdl_upsidegap2crows(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    start = 12 if d.n > 12 else 2
    for i in range(start, d.n):
        if (
            d.color[i - 2] == 1
            and d.rb[i - 2] > d.avg_body_long[i - 2]
            and d.color[i - 1] == -1
            and d.rb[i - 1] <= d.avg_body_short[i - 1]
            and d.gap_up(i - 1, i - 2)
            and d.color[i] == -1
            and d.o[i] > d.o[i - 1]
            and d.c[i] < d.c[i - 1]
            and d.c[i] > d.c[i - 2]
        ):
            res[i] = -100
    return _make_result(res, is_vec=d.is_vec)


def cdl_xsidegap3methods(open_: Any, high: Any, low: Any, close: Any) -> Any:
    d = _CandleData(open_, high, low, close)
    res = [0] * d.n
    for i in range(2, d.n):
        if (
            d.color[i - 2] == d.color[i - 1]
            and d.color[i - 1] == -d.color[i]
            and d.o[i] < max(d.c[i - 1], d.o[i - 1])
            and d.o[i] > min(d.c[i - 1], d.o[i - 1])
            and d.c[i] < max(d.c[i - 2], d.o[i - 2])
            and d.c[i] > min(d.c[i - 2], d.o[i - 2])
            and (
                (d.color[i - 2] == 1 and d.gap_up(i - 1, i - 2))
                or (d.color[i - 2] == -1 and d.gap_down(i - 1, i - 2))
            )
        ):
            res[i] = d.color[i - 2] * 100
    return _make_result(res, is_vec=d.is_vec)


# All 61 canonical TA-Lib patterns registered
CUSTOM_PATTERNS = {
    "CDL2CROWS": cdl_2crows,
    "CDL3BLACKCROWS": cdl_3blackcrows,
    "CDL3INSIDE": cdl_3inside,
    "CDL3LINESTRIKE": cdl_3linestrike,
    "CDL3OUTSIDE": cdl_3outside,
    "CDL3STARSINSOUTH": cdl_3starsinsouth,
    "CDL3WHITESOLDIERS": cdl_3whitesoldiers,
    "CDLABANDONEDBABY": cdl_abandonedbaby,
    "CDLADVANCEBLOCK": cdl_advanceblock,
    "CDLBELTHOLD": cdl_belthold,
    "CDLBREAKAWAY": cdl_breakaway,
    "CDLCLOSINGMARUBOZU": cdl_closingmarubozu,
    "CDLCONCEALBABYSWALL": cdl_concealbabyswall,
    "CDLCOUNTERATTACK": cdl_counterattack,
    "CDLDARKCLOUDCOVER": cdl_darkcloudcover,
    "CDLDOJI": cdl_doji,
    "CDLDOJISTAR": cdl_dojistar,
    "CDLDRAGONFLYDOJI": cdl_dragonflydoji,
    "CDLENGULFING": cdl_engulfing,
    "CDLEVENINGDOJISTAR": cdl_eveningdojistar,
    "CDLEVENINGSTAR": cdl_eveningstar,
    "CDLGAPSIDESIDEWHITE": cdl_gapsidesidewhite,
    "CDLGRAVESTONEDOJI": cdl_gravestonedoji,
    "CDLHAMMER": cdl_hammer,
    "CDLHANGINGMAN": cdl_hangingman,
    "CDLHARAMI": cdl_harami,
    "CDLHARAMICROSS": cdl_haramicross,
    "CDLHIGHWAVE": cdl_highwave,
    "CDLHIKKAKE": cdl_hikkake,
    "CDLHIKKAKEMOD": cdl_hikkakemod,
    "CDLHOMINGPIGEON": cdl_homingpigeon,
    "CDLIDENTICAL3CROWS": cdl_identical3crows,
    "CDLINNECK": cdl_inneck,
    "CDLINVERTEDHAMMER": cdl_invertedhammer,
    "CDLKICKING": cdl_kicking,
    "CDLKICKINGBYLENGTH": cdl_kickingbylength,
    "CDLLADDERBOTTOM": cdl_ladderbottom,
    "CDLLONGLEGGEDDOJI": cdl_longleggeddoji,
    "CDLLONGLINE": cdl_longline,
    "CDLMARUBOZU": cdl_marubozu,
    "CDLMATCHINGLOW": cdl_matchinglow,
    "CDLMATHOLD": cdl_mathold,
    "CDLMORNINGDOJISTAR": cdl_morningdojistar,
    "CDLMORNINGSTAR": cdl_morningstar,
    "CDLONNECK": cdl_onneck,
    "CDLPIERCING": cdl_piercing,
    "CDLRICKSHAWMAN": cdl_rickshawman,
    "CDLRISEFALL3METHODS": cdl_risefall3methods,
    "CDLSEPARATINGLINES": cdl_separatinglines,
    "CDLSHOOTINGSTAR": cdl_shootingstar,
    "CDLSHORTLINE": cdl_shortline,
    "CDLSPINNINGTOP": cdl_spinningtop,
    "CDLSTALLEDPATTERN": cdl_stalledpattern,
    "CDLSTICKSANDWICH": cdl_sticksandwich,
    "CDLTAKURI": cdl_takuri,
    "CDLTASUKIGAP": cdl_tasukigap,
    "CDLTHRUSTING": cdl_thrusting,
    "CDLTRISTAR": cdl_tristar,
    "CDLUNIQUE3RIVER": cdl_unique3river,
    "CDLUPSIDEGAP2CROWS": cdl_upsidegap2crows,
    "CDLXSIDEGAP3METHODS": cdl_xsidegap3methods,
}

SUPPORTED_FALLBACK_PATTERNS: frozenset[str] = frozenset(CUSTOM_PATTERNS.keys())
UNSUPPORTED_FALLBACK_PATTERNS: frozenset[str] = frozenset()


def _make_unsupported_pattern(name: str):
    def _unsupported(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"Candlestick pattern '{name}' is not recognized. "
            f"Pure-Python fallback engine implements all {len(CUSTOM_PATTERNS)} TA-Lib patterns: "
            f"{', '.join(sorted(CUSTOM_PATTERNS.keys()))}."
        )

    return _unsupported


class TALibWrapper:
    """Wrapper that routes to native TA-Lib if available, or pure-Python / NumPy fallback engine."""

    def __init__(self, force_fallback: bool = False) -> None:
        self._has_native = HAS_NATIVE_TALIB and not force_fallback

    def __getattr__(self, name: str) -> Any:
        if self._has_native and hasattr(_talib, name):
            return getattr(_talib, name)

        upper = name.upper().replace("_", "")
        if not upper.startswith("CDL") and f"CDL{upper}" in CUSTOM_PATTERNS:
            upper = f"CDL{upper}"

        if upper in CUSTOM_PATTERNS:
            return CUSTOM_PATTERNS[upper]
        if upper.startswith("CDL") and upper in ALL_CDL_PATTERNS:
            return _make_unsupported_pattern(upper)

        if name == "HAS_NATIVE_TALIB":
            return False

        raise AttributeError(f"Module 'talib' has no attribute '{name}'")

    def __dir__(self) -> list[str]:
        if self._has_native:
            return dir(_talib)
        return [*ALL_CDL_PATTERNS, "HAS_NATIVE_TALIB"]


talib: Any = _talib if HAS_NATIVE_TALIB else TALibWrapper()


def is_freethreaded() -> bool:
    """Return True if running under a free-threaded (No-GIL, PEP 703) CPython build."""
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED") == 1)


def is_gil_enabled() -> bool:
    """Return True if the Global Interpreter Lock (GIL) is currently active.

    On free-threaded CPython (3.13t+), queries sys._is_gil_enabled().
    On standard CPython and PyPy, returns True.
    """
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    if callable(gil_probe):
        try:
            return bool(gil_probe())
        except Exception:  # pragma: no cover
            return True
    return True


def get_talib_status() -> dict[str, Any]:
    """Return diagnostic details regarding native TA-Lib availability and system environment."""
    is_pypy = sys.implementation.name == "pypy"
    is_windows = sys.platform.startswith("win")
    is_macos = sys.platform == "darwin"
    is_linux = sys.platform.startswith("linux")
    is_free_threaded = is_freethreaded()
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    try:
        gil_enabled = bool(gil_probe()) if callable(gil_probe) else None
    except Exception:  # pragma: no cover - interpreter-specific diagnostic API
        gil_enabled = None

    gil_cause: str | None = None
    gil_recommendation: str | None = None

    if is_free_threaded:
        if gil_enabled is True:
            env_gil = os.environ.get("PYTHON_GIL")
            if env_gil == "1":
                gil_cause = "Forced enabled by PYTHON_GIL=1 environment variable."
            elif HAS_NATIVE_TALIB:
                gil_cause = (
                    "Automatically re-enabled by CPython PEP 703 runtime protection upon importing "
                    "native 'talib._ta_lib' (which lacks Py_MOD_GIL_NOT_USED declaration)."
                )
            else:
                gil_cause = (
                    "Re-enabled by non-free-threaded C extension or default interpreter policy."
                )
            gil_recommendation = "Run with 'python -X gil=0 <script>' or set PYTHON_GIL=0 in environment to maintain No-GIL execution."
        elif gil_enabled is False:
            gil_cause = "GIL is disabled (running in true multi-core No-GIL mode)."
            gil_recommendation = None

    err_str = str(TALIB_IMPORT_ERROR) if TALIB_IMPORT_ERROR is not None else None
    if HAS_NATIVE_TALIB:
        reason = "Native C TA-Lib successfully loaded."
    elif is_windows and is_pypy:
        reason = (
            "Native TA-Lib binary not found for Windows PyPy. PyPI does not host "
            "precompiled PyPy Windows wheels for TA-Lib. Use --find-links with GitHub Releases "
            "to install prebuilt wheels."
        )
    elif TALIB_IMPORT_ERROR is not None:
        reason = f"Native TA-Lib import failed: {err_str}"
    else:
        reason = "Native TA-Lib package is not installed."

    return {
        "has_native_talib": HAS_NATIVE_TALIB,
        "import_error": err_str,
        "is_pypy": is_pypy,
        "is_windows": is_windows,
        "is_macos": is_macos,
        "is_linux": is_linux,
        "is_free_threaded": is_free_threaded,
        "gil_enabled": gil_enabled,
        "gil_cause": gil_cause,
        "gil_recommendation": gil_recommendation,
        "python_version": sys.version.split()[0],
        "implementation": sys.implementation.name,
        "reason": reason,
    }


def get_talib_install_hint(release_tag: str = "v0.3.30") -> str:
    """Return actionable platform-specific instructions to install or repair native TA-Lib."""
    if HAS_NATIVE_TALIB:
        return "Native TA-Lib is already installed and available."

    is_pypy = sys.implementation.name == "pypy"
    is_windows = sys.platform.startswith("win")
    is_macos = sys.platform == "darwin"
    is_linux = sys.platform.startswith("linux")

    find_links_url = (
        f"https://github.com/eminsk/yfinance-ta-patterns/releases/expanded_assets/{release_tag}"
    )
    is_free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    windows_wheelhouse_url = (
        "https://github.com/eminsk/yfinance-ta-patterns/releases/expanded_assets/"
        f"{WINDOWS_TALIB_WHEELHOUSE_TAG}"
    )
    needs_windows_wheelhouse = (
        is_windows and not is_pypy and (is_free_threaded or sys.version_info >= (3, 15))
    )

    lines: list[str] = []
    if is_windows and is_free_threaded and sys.version_info[:2] == (3, 13):
        lines.append("Note on Windows Free-Threaded Python 3.13t:")
        lines.append(
            "  PyPI does not host precompiled cp313t-win_amd64 wheels for core dependencies "
            "(lxml, numpy, scipy). Building from source requires MSVC and libxml2 development headers."
        )
        lines.append(
            "  Recommended action: Upgrade to Python 3.14t or 3.15t where official prebuilt wheels "
            "are available on PyPI, or install yfinance-ta-patterns without the [all] extra:"
        )
        lines.append("    uv add yfinance-ta-patterns")
        lines.append("  If native TA-Lib is desired on Windows, use Python 3.14t or 3.15t:")

    if needs_windows_wheelhouse:
        runtime = "Windows free-threaded CPython" if is_free_threaded else "Windows CPython 3.15+"
        lines.append(f"To enable native TA-Lib on {runtime}:")
        lines.append("  [PowerShell + uv]:")
        if is_free_threaded:
            lines.append('    $env:PYTHON_GIL = "0"')
        lines.append(
            f'    uv add "yfinance-ta-patterns[all]" --find-links {windows_wheelhouse_url}'
        )
        lines.append("  [PowerShell + pip]:")
        if is_free_threaded:
            lines.append('    $env:PYTHON_GIL = "0"')
        lines.append(
            f'    pip install "yfinance-ta-patterns[all]" --find-links {windows_wheelhouse_url}'
        )
        if is_free_threaded:
            lines.append(
                "  The wheelhouse supplies matching pandas, curl_cffi, and TA-Lib wheels "
                "for CPython 3.14t and 3.15t."
            )
        else:
            lines.append(
                "  The wheelhouse supplies a TA-Lib cp315 wheel, but PyPI does not yet provide "
                "a Windows pandas cp315 wheel; pandas must be built from source."
            )
    elif is_windows and is_pypy:
        lines.append("To enable all 61 native TA-Lib candlestick patterns on Windows PyPy:")
        lines.append("  [uv]:")
        lines.append(f'    uv add "yfinance-ta-patterns[all]" --find-links {find_links_url}')
        lines.append("  [pip]:")
        lines.append(f'    pip install "ta-lib>=0.4.19,<=0.7.1" --find-links {find_links_url}')
        lines.append("  [pyproject.toml]:")
        lines.append("    [tool.uv]")
        lines.append(f'    find-links = ["{find_links_url}"]')
    elif is_macos:
        lines.append("To install native TA-Lib on macOS:")
        lines.append("  brew install ta-lib")
        lines.append("  pip install ta-lib")
    elif is_linux:
        lines.append("To install native TA-Lib on Linux (Ubuntu/Debian):")
        lines.append("  sudo apt-get install -y libta-lib0 libta-lib-dev")
        lines.append("  pip install ta-lib")
    else:
        lines.append("To install native TA-Lib on Windows (CPython):")
        lines.append("  pip install ta-lib")
        lines.append(
            f"  or install prebuilt wheels via: pip install ta-lib --find-links {find_links_url}"
        )

    return "\n".join(lines)
