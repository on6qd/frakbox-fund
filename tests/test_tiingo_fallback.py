#!/usr/bin/env python3
"""
Unit test for the Tiingo historical fallback in tools/yfinance_utils.py.

yfinance depends on Yahoo endpoints (query*.finance.yahoo.com, fc.yahoo.com)
that periodically fail from this environment — every yf.download() then returns
an empty DataFrame and the whole toolchain (measure_event_impact, backtests,
regressions) breaks. safe_download() and get_close_prices() now retry US
equities/ETFs against Tiingo. This locks that behaviour in so a fresh clone
never silently loses it again.

Hermetic: monkeypatches yfinance and requests, so it needs no network and no
TIINGO_API_KEY.

    python tests/test_tiingo_fallback.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import yfinance_utils as yu


def test_tiingo_supported():
    assert yu._tiingo_supported("AAPL")
    assert yu._tiingo_supported("SPY")
    # futures, FX, indices and crypto are NOT on the Tiingo daily endpoint
    assert not yu._tiingo_supported("CL=F")
    assert not yu._tiingo_supported("EURUSD=X")
    assert not yu._tiingo_supported("^VIX")
    assert not yu._tiingo_supported("BTC-USD")
    assert not yu._tiingo_supported("")
    print("  test_tiingo_supported OK")


def _fake_tiingo_rows():
    return [
        {"date": "2026-08-03T00:00:00.000Z", "adjOpen": 100.0, "adjHigh": 101.0,
         "adjLow": 99.0, "adjClose": 100.5, "adjVolume": 1000},
        {"date": "2026-08-04T00:00:00.000Z", "adjOpen": 100.5, "adjHigh": 102.0,
         "adjLow": 100.0, "adjClose": 101.5, "adjVolume": 1200},
    ]


class _FakeResp:
    status_code = 200

    def json(self):
        return _fake_tiingo_rows()


def test_tiingo_history_shape(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "dummy")
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp())

    df = yu._tiingo_history(["AAPL"], "2026-08-01", "2026-08-15")
    # Must mirror yfinance's (metric, ticker) MultiIndex so downstream
    # flatten/extract logic works unchanged.
    assert isinstance(df.columns, pd.MultiIndex)
    assert ("Close", "AAPL") in df.columns
    assert ("Open", "AAPL") in df.columns
    assert list(df[("Close", "AAPL")]) == [100.5, 101.5]
    print("  test_tiingo_history_shape OK")


def test_fallback_when_yfinance_empty(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "dummy")
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp())
    # Simulate the Yahoo outage: yf.download returns empty.
    monkeypatch.setattr(yu.yf, "download", lambda *a, **k: pd.DataFrame())

    df = yu.safe_download("AAPL", "2026-08-01", "2026-08-15")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert round(float(df["Close"].iloc[-1]), 2) == 101.5

    closes = yu.get_close_prices(["AAPL"], "2026-08-01", "2026-08-15")
    assert list(closes.columns) == ["AAPL"]
    assert round(float(closes["AAPL"].iloc[-1]), 2) == 101.5
    print("  test_fallback_when_yfinance_empty OK")


def test_non_equity_still_raises(monkeypatch):
    # No Tiingo coverage for ^VIX -> must fall through to the normal error path.
    monkeypatch.setenv("TIINGO_API_KEY", "dummy")
    monkeypatch.setattr(yu.yf, "download", lambda *a, **k: pd.DataFrame())
    raised = False
    try:
        yu.get_close_prices("^VIX", "2026-08-01", "2026-08-15")
    except ValueError:
        raised = True
    assert raised, "expected ValueError for uncovered ^VIX ticker"
    print("  test_non_equity_still_raises OK")


# ---------------------------------------------------------------------------
# Minimal monkeypatch shim so this runs with or without pytest.
# ---------------------------------------------------------------------------
class _MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setenv(self, name, value):
        old = os.environ.get(name)
        self._undo.append(lambda: (os.environ.__setitem__(name, old)
                                   if old is not None
                                   else os.environ.pop(name, None)))
        os.environ[name] = value

    def setattr(self, target, name, value):
        old = getattr(target, name)
        self._undo.append(lambda: setattr(target, name, old))
        setattr(target, name, value)

    def undo(self):
        for fn in reversed(self._undo):
            fn()
        self._undo = []


if __name__ == "__main__":
    print("Testing Tiingo fallback...")
    test_tiingo_supported()
    for fn in (test_tiingo_history_shape,
               test_fallback_when_yfinance_empty,
               test_non_equity_still_raises):
        mp = _MonkeyPatch()
        try:
            fn(mp)
        finally:
            mp.undo()
    print("ALL TESTS PASSED")
