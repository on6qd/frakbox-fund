"""
yfinance_utils.py - Shared utility wrappers for yfinance downloads.

Solves the recurring MultiIndex column-handling bug documented in friction_log.
In yfinance >= 0.2.x (and confirmed in 1.2.0), BOTH single-ticker and multi-ticker
yf.download() calls return a MultiIndex column structure like:

    ('Close', 'AAPL'), ('High', 'AAPL'), ...           # single ticker
    ('Close', 'AAPL'), ('Close', 'MSFT'), ...           # multi ticker

This trips up any code that assumes flat column names like df['Close'].

All functions in this module guarantee a clean, flat-column DataFrame on return.
They are drop-in safe: callers never need to inspect or flatten columns themselves.

Usage examples:
    from tools.yfinance_utils import safe_download, get_close_prices, get_current_price

    # Single ticker — returns DataFrame with columns: Open, High, Low, Close, Volume
    df = safe_download("AAPL", start="2024-01-01", end="2024-06-01")
    print(df["Close"])

    # Multi-ticker — returns DataFrame with columns: Open_AAPL, Close_AAPL, Open_MSFT, ...
    df = safe_download(["AAPL", "MSFT"], start="2024-01-01", end="2024-06-01")
    print(df["Close_AAPL"])

    # Just close prices, one column per ticker
    closes = get_close_prices(["AAPL", "MSFT"], start="2024-01-01", end="2024-06-01")
    print(closes)  # columns: AAPL, MSFT

    # Latest price for a single ticker
    price = get_current_price("SPY")
    print(price)   # e.g. 512.34

    # Standalone flattener — for DataFrames from yf.Ticker().history() or other paths
    flat = flatten_yfinance_columns(df, ticker="AAPL")
"""

from __future__ import annotations

import sys
from typing import Union

import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def flatten_yfinance_columns(df: pd.DataFrame, ticker: str | None = None) -> pd.DataFrame:
    """
    Flatten a yfinance DataFrame that may have MultiIndex columns.

    Handles three cases produced by different yfinance versions / call patterns:
      1. Flat columns already (e.g. ['Open', 'Close', ...]) — returned as-is.
      2. Single-ticker MultiIndex: ('Close', 'AAPL') -> 'Close'
         (when ticker is provided or there is only one ticker level value)
      3. Multi-ticker MultiIndex: ('Close', 'AAPL') -> 'Close_AAPL'

    Args:
        df:     DataFrame from yf.download() or yf.Ticker().history().
        ticker: Optional ticker hint. When provided and columns are a single-ticker
                MultiIndex, the ticker suffix is dropped (keeps column names clean).
                When None and multiple tickers are detected, uses "metric_TICKER" format.

    Returns:
        DataFrame with single-level string columns.

    Examples:
        df = yf.download("AAPL", start="2024-01-01", end="2024-06-01")
        df = flatten_yfinance_columns(df, ticker="AAPL")
        # df.columns -> ['Close', 'High', 'Low', 'Open', 'Volume']

        df = yf.download(["AAPL", "MSFT"], start="2024-01-01", end="2024-06-01")
        df = flatten_yfinance_columns(df)
        # df.columns -> ['Close_AAPL', 'Close_MSFT', 'High_AAPL', ...]
    """
    if df is None or df.empty:
        return df

    if not isinstance(df.columns, pd.MultiIndex):
        # Already flat — nothing to do
        return df

    # Determine whether this is single-ticker or multi-ticker
    # Level 0 = metric name, Level 1 = ticker symbol
    level1_values = df.columns.get_level_values(1).unique().tolist()

    is_single_ticker = (
        len(level1_values) == 1
        or (ticker is not None and all(v == ticker for v in level1_values))
    )

    if is_single_ticker:
        # Drop the ticker level — keep only the metric name
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    else:
        # Multi-ticker: "metric_TICKER" format
        df = df.copy()
        df.columns = [f"{metric}_{sym}" for metric, sym in df.columns]

    return df


# ---------------------------------------------------------------------------
# Tiingo historical fallback
# ---------------------------------------------------------------------------
# yfinance's egress often gets TLS-reset by the agent proxy on fresh clones
# (recurring #1 data_access friction, 139x). Tiingo (authenticated EOD) is a
# reliable daily-bar fallback for equities/ETFs. These helpers mirror the
# yfinance output shapes so callers need no special-casing.

def _tiingo_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily adjusted OHLCV from Tiingo for one ticker.

    Returns a DataFrame indexed by tz-naive normalized date with columns
    Open, High, Low, Close, Volume (split/dividend-adjusted). Returns an empty
    DataFrame on any failure (no key, network error, unknown ticker, index).
    """
    import os
    import requests

    key = os.environ.get("TIINGO_API_KEY", "")
    if not key:
        return pd.DataFrame()
    try:
        url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Token {key}"}
        r = requests.get(url, params={"startDate": start, "endDate": end},
                         headers=headers, timeout=15)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        if not data:
            return pd.DataFrame()
        raw = pd.DataFrame(data)
        idx = pd.to_datetime(raw["date"]).dt.tz_localize(None).dt.normalize()
        out = pd.DataFrame({
            "Open": raw.get("adjOpen", raw.get("open")).to_numpy(),
            "High": raw.get("adjHigh", raw.get("high")).to_numpy(),
            "Low": raw.get("adjLow", raw.get("low")).to_numpy(),
            "Close": raw.get("adjClose", raw.get("close")).to_numpy(),
            "Volume": raw.get("adjVolume", raw.get("volume")).to_numpy(),
        }, index=idx)
        out.index.name = None
        return out.sort_index()
    except Exception:
        return pd.DataFrame()


def _tiingo_safe_download(ticker_list: list[str], start: str, end: str,
                          single: bool) -> pd.DataFrame:
    """Tiingo fallback shaped like safe_download() output (flat columns)."""
    frames = {}
    for t in ticker_list:
        h = _tiingo_history(t, start, end)
        if not h.empty:
            frames[t] = h
    if not frames:
        return pd.DataFrame()
    if single:
        return frames[ticker_list[0]]
    cols = {}
    for t, h in frames.items():
        for metric in ("Open", "High", "Low", "Close", "Volume"):
            cols[f"{metric}_{t}"] = h[metric]
    return pd.DataFrame(cols).sort_index()


def _tiingo_closes(ticker_list: list[str], start: str, end: str) -> pd.DataFrame:
    """Tiingo fallback shaped like get_close_prices() output (ticker columns)."""
    cols = {}
    for t in ticker_list:
        h = _tiingo_history(t, start, end)
        if not h.empty:
            cols[t] = h["Close"]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().dropna(how="all")


def safe_download(
    tickers: Union[str, list[str]],
    start: str,
    end: str,
    **kwargs,
) -> pd.DataFrame:
    """
    Download OHLCV data from yfinance with automatic MultiIndex flattening.

    Wraps yf.download() and guarantees a clean, flat-column DataFrame regardless
    of yfinance version or number of tickers.

    For a single ticker, returned columns are: Open, High, Low, Close, Volume
    For multiple tickers, returned columns are: Open_AAPL, Close_AAPL, Open_MSFT, ...

    Args:
        tickers: Single ticker string or list of ticker strings.
        start:   Start date string, e.g. "2024-01-01".
        end:     End date string, e.g. "2024-06-01".
        **kwargs: Passed directly to yf.download() (e.g. auto_adjust=True,
                  progress=False, interval="1d").

    Returns:
        DataFrame with DatetimeIndex and flat string column names.

    Raises:
        ValueError: If the download returns an empty DataFrame (no data at all).

    Examples:
        # Single ticker
        df = safe_download("AAPL", start="2024-01-01", end="2024-06-01")
        price = df["Close"].iloc[-1]

        # Multiple tickers with extra kwargs
        df = safe_download(["AAPL", "MSFT"], "2024-01-01", "2024-06-01",
                           auto_adjust=True, progress=False)
        aapl_close = df["Close_AAPL"]
    """
    kwargs.setdefault("progress", False)
    kwargs.setdefault("auto_adjust", True)

    single = isinstance(tickers, str)
    ticker_list = [tickers] if single else list(tickers)
    try:
        raw = yf.download(tickers, start=start, end=end, **kwargs)
    except Exception:
        raw = pd.DataFrame()

    if raw is None or raw.empty:
        # yfinance failed (e.g. proxy TLS reset on a fresh clone). Fall back to
        # Tiingo for daily equity/ETF bars. See recurring data_access friction.
        if str(kwargs.get("interval", "1d")) in ("1d", "None"):
            fb = _tiingo_safe_download(ticker_list, start, end, single)
            if not fb.empty:
                return fb
        ticker_str = tickers if single else ", ".join(tickers)
        raise ValueError(
            f"yfinance returned no data for {ticker_str!r} "
            f"from {start} to {end}. Ticker may be delisted or invalid."
        )

    ticker_hint = tickers if single else None
    return flatten_yfinance_columns(raw, ticker=ticker_hint)


def get_close_prices(
    tickers: Union[str, list[str]],
    start: str,
    end: str,
    **kwargs,
) -> pd.DataFrame:
    """
    Return a DataFrame of Close prices with one column per ticker.

    This is the most common use case for multi-asset comparison or benchmark math.
    Columns are always the ticker symbols themselves (not "Close_AAPL").

    Args:
        tickers: Single ticker string or list of ticker strings.
        start:   Start date string, e.g. "2024-01-01".
        end:     End date string, e.g. "2024-06-01".
        **kwargs: Passed to yf.download().

    Returns:
        DataFrame with DatetimeIndex and ticker-named columns.
        If a single ticker is provided, returns a one-column DataFrame
        with the ticker name as the column.

    Raises:
        ValueError: If the download returns no data.

    Examples:
        # Multi-ticker comparison
        closes = get_close_prices(["AAPL", "MSFT", "SPY"],
                                  start="2024-01-01", end="2024-06-01")
        # closes.columns -> ['AAPL', 'MSFT', 'SPY']

        # Single ticker — still returns a DataFrame (not a Series)
        spy = get_close_prices("SPY", start="2024-01-01", end="2024-06-01")
        # spy.columns -> ['SPY']
    """
    kwargs.setdefault("progress", False)
    kwargs.setdefault("auto_adjust", True)

    single = isinstance(tickers, str)
    ticker_list = [tickers] if single else list(tickers)

    try:
        raw = yf.download(ticker_list, start=start, end=end, **kwargs)
    except Exception:
        raw = pd.DataFrame()

    if raw is None or raw.empty:
        # yfinance failed (e.g. proxy TLS reset on a fresh clone). Fall back to
        # Tiingo for daily equity/ETF closes. See recurring data_access friction.
        if str(kwargs.get("interval", "1d")) in ("1d", "None"):
            fb = _tiingo_closes(ticker_list, start, end)
            if not fb.empty:
                return fb[[c for c in ticker_list if c in fb.columns]]
        raise ValueError(
            f"yfinance returned no data for {ticker_list} "
            f"from {start} to {end}."
        )

    if isinstance(raw.columns, pd.MultiIndex):
        # Extract only the Close row from MultiIndex (level 0 = metric)
        # Works for both single-ticker and multi-ticker in yfinance 1.x
        try:
            close_df = raw.xs("Close", level=0, axis=1)
        except KeyError:
            # Fallback: try level 1 ordering (older yfinance)
            level0 = raw.columns.get_level_values(0)
            level1 = raw.columns.get_level_values(1)
            if "Close" in level0.unique():
                mask = level0 == "Close"
                close_df = raw.loc[:, mask]
                close_df.columns = level1[mask]
            else:
                raise ValueError(
                    "Cannot find 'Close' column in yfinance output. "
                    f"Available level-0 values: {list(level0.unique())}"
                )
    else:
        # Flat columns — just grab Close
        if "Close" not in raw.columns:
            raise ValueError(
                f"'Close' column missing. Available columns: {list(raw.columns)}"
            )
        close_df = raw[["Close"]].copy()
        close_df.columns = ticker_list

    # Ensure column names match the requested tickers
    # xs() preserves ticker names from level 1, which is what we want
    return close_df.dropna(how="all")


def get_current_price(ticker: str) -> float:
    """
    Return the latest available Close price for a single ticker.

    Uses yf.Ticker().history() (not yf.download()) which avoids MultiIndex
    entirely for single-ticker lookups and is faster for real-time queries.

    Args:
        ticker: Ticker symbol string, e.g. "SPY" or "^VIX".

    Returns:
        Latest close price as a float.

    Raises:
        ValueError: If no price data is available (delisted or bad ticker).

    Examples:
        price = get_current_price("SPY")    # e.g. 512.34
        vix   = get_current_price("^VIX")   # e.g. 18.7
    """
    # Use yf.download() (Ticker.history can hang indefinitely)
    from datetime import datetime, timedelta
    hist = pd.DataFrame()
    end_dt = datetime.now() + timedelta(days=1)
    start_dt = end_dt - timedelta(days=10)
    try:
        hist = yf.download(
            ticker,
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        hist = flatten_yfinance_columns(hist, ticker=ticker)
    except Exception:
        hist = pd.DataFrame()

    if hist is None or hist.empty:
        # Third fallback: try Tiingo
        import os, requests
        tiingo_key = os.environ.get("TIINGO_API_KEY", "")
        if tiingo_key:
            try:
                from datetime import datetime, timedelta
                end_d = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                start_d = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
                headers = {"Content-Type": "application/json",
                           "Authorization": f"Token {tiingo_key}"}
                url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
                r = requests.get(url, params={"startDate": start_d, "endDate": end_d},
                                 headers=headers, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if data:
                        return float(data[-1].get("adjClose", data[-1].get("close", 0)))
            except Exception:
                pass

        raise ValueError(
            f"No price data available for {ticker!r}. "
            "Ticker may be delisted, invalid, or the market may be closed."
        )

    # yf.Ticker().history() returns flat columns — no MultiIndex
    # But guard defensively in case a future yfinance version changes this
    if isinstance(hist.columns, pd.MultiIndex):
        hist = flatten_yfinance_columns(hist, ticker=ticker)

    return float(hist["Close"].iloc[-1])


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Testing yfinance_utils...")

    # Test 1: get_current_price
    spy_price = get_current_price("SPY")
    print(f"  SPY current price: ${spy_price:.2f}")
    assert isinstance(spy_price, float), "Expected float"

    # Test 2: safe_download single ticker
    df_single = safe_download("AAPL", start="2024-01-02", end="2024-01-10")
    assert not isinstance(df_single.columns, pd.MultiIndex), "Columns should be flat"
    assert "Close" in df_single.columns, f"Missing Close. Got: {list(df_single.columns)}"
    print(f"  AAPL single-ticker columns: {list(df_single.columns)}")

    # Test 3: safe_download multi-ticker
    df_multi = safe_download(["AAPL", "MSFT"], start="2024-01-02", end="2024-01-10")
    assert not isinstance(df_multi.columns, pd.MultiIndex), "Columns should be flat"
    assert "Close_AAPL" in df_multi.columns, f"Missing Close_AAPL. Got: {list(df_multi.columns)}"
    assert "Close_MSFT" in df_multi.columns, f"Missing Close_MSFT. Got: {list(df_multi.columns)}"
    print(f"  Multi-ticker columns (first 6): {list(df_multi.columns)[:6]}")

    # Test 4: get_close_prices
    closes = get_close_prices(["AAPL", "MSFT", "SPY"], start="2024-01-02", end="2024-01-10")
    assert list(closes.columns) == ["AAPL", "MSFT", "SPY"], f"Wrong columns: {list(closes.columns)}"
    print(f"  Close prices shape: {closes.shape}, columns: {list(closes.columns)}")

    # Test 5: flatten_yfinance_columns on already-flat DataFrame (no-op)
    flat = pd.DataFrame({"Close": [1.0], "Open": [2.0]})
    result = flatten_yfinance_columns(flat)
    assert list(result.columns) == ["Close", "Open"], "Should be unchanged"

    # Test 6: flatten_yfinance_columns on raw multi-ticker download
    import yfinance as yf
    raw = yf.download(["AAPL", "MSFT"], start="2024-01-02", end="2024-01-05",
                      progress=False, auto_adjust=True)
    assert isinstance(raw.columns, pd.MultiIndex), "Raw download should be MultiIndex"
    flat_raw = flatten_yfinance_columns(raw)
    assert not isinstance(flat_raw.columns, pd.MultiIndex), "After flatten should be flat"
    print(f"  flatten_yfinance_columns multi: {list(flat_raw.columns)[:6]}")

    print("\nAll tests passed.")
