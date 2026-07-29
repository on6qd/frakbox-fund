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
# Plain-requests fallback for proxied / curl_cffi-hostile environments.
#
# yfinance fetches through curl_cffi's browser-TLS impersonation. Behind the
# agent egress proxy (which re-terminates TLS) that impersonated handshake is
# reset ("curl: (35) Recv failure: Connection reset by peer"), so yf.download()
# returns nothing on every fresh clone — the #1 recurring data_access friction.
# A plain requests.Session with a real browser User-Agent tunnels through the
# proxy cleanly and Yahoo's public v8 chart JSON API returns the same OHLCV.
# We build a MultiIndex frame shaped exactly like yf.download() output so the
# existing downstream flatten / xs logic is reused unchanged.
# ---------------------------------------------------------------------------

_YAHOO_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15"
)


def _tiingo_one(ticker: str, start: str, end: str, auto_adjust: bool = True):
    """Fetch daily OHLCV for one equity/ETF ticker from Tiingo's EOD API.

    Tiingo is authenticated (not IP-rate-limited like Yahoo) so it is the
    reliable primary source behind the shared egress proxy. It only covers
    US equities/ETFs — index (^VIX), futures (CL=F), FX and crypto symbols are
    not on the daily endpoint and return None here (caller falls back to Yahoo).
    Returns a DataFrame indexed by date with Open/High/Low/Close/Volume, or None.
    """
    import os
    import requests

    key = os.environ.get("TIINGO_API_KEY", "")
    if not key:
        return None
    # Tiingo daily only handles plain equity/ETF symbols.
    if any(ch in ticker for ch in ("^", "=", "-")) or "." in ticker:
        return None
    try:
        r = requests.get(
            f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
            params={"startDate": start, "endDate": end, "format": "json"},
            headers={"Content-Type": "application/json",
                     "Authorization": f"Token {key}"},
            timeout=25,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        rows = r.json()
    except Exception:
        return None
    if not rows:
        return None
    idx = pd.to_datetime([row["date"] for row in rows], utc=True).tz_convert(None).normalize()
    if auto_adjust:
        o = [row.get("adjOpen", row.get("open")) for row in rows]
        h = [row.get("adjHigh", row.get("high")) for row in rows]
        low = [row.get("adjLow", row.get("low")) for row in rows]
        c = [row.get("adjClose", row.get("close")) for row in rows]
        v = [row.get("adjVolume", row.get("volume")) for row in rows]
    else:
        o = [row.get("open") for row in rows]
        h = [row.get("high") for row in rows]
        low = [row.get("low") for row in rows]
        c = [row.get("close") for row in rows]
        v = [row.get("volume") for row in rows]
    df = pd.DataFrame(
        {"Open": o, "High": h, "Low": low, "Close": c, "Volume": v}, index=idx
    )
    return df[~df.index.duplicated(keep="last")].dropna(how="all")


def _requests_yahoo_multi(
    ticker_list: list[str],
    start: str,
    end: str,
    interval: str = "1d",
    auto_adjust: bool = True,
) -> pd.DataFrame:
    """Fetch OHLCV for one or more tickers via Yahoo's v8 chart JSON API using
    plain ``requests`` (works where curl_cffi TLS impersonation is proxy-reset).

    Returns a DataFrame whose columns are a ``(metric, ticker)`` MultiIndex —
    the same shape yf.download() produces for multiple tickers — or an empty
    DataFrame if every ticker fails.
    """
    import time
    import requests
    from datetime import datetime, timezone, timedelta

    def _to_epoch(d: str) -> int:
        return int(
            datetime.strptime(d, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )

    p1 = _to_epoch(start)
    # Yahoo period2 is exclusive-ish; pad a day so `end` is inclusive like yfinance.
    p2 = _to_epoch(end) + 86400

    sess = requests.Session()
    sess.headers.update({"User-Agent": _YAHOO_UA, "Accept": "application/json"})

    frames: dict[str, pd.DataFrame] = {}
    for ticker in ticker_list:
        # Reliable authenticated source first (equities/ETFs).
        df_t = _tiingo_one(ticker, start, end, auto_adjust=auto_adjust)
        if df_t is not None and not df_t.empty:
            frames[ticker] = df_t
            continue
        for host in ("query1", "query2"):
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}"
            params = {
                "period1": p1,
                "period2": p2,
                "interval": interval,
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
            for attempt in range(3):
                try:
                    r = sess.get(url, params=params, timeout=25)
                except Exception:
                    time.sleep(1.0)
                    continue
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    break
                try:
                    result = r.json()["chart"]["result"]
                except Exception:
                    break
                if not result:
                    break
                node = result[0]
                ts = node.get("timestamp")
                quote = node.get("indicators", {}).get("quote", [{}])[0]
                if not ts or not quote:
                    break
                idx = pd.to_datetime(ts, unit="s").normalize()
                df_t = pd.DataFrame(
                    {
                        "Open": quote.get("open"),
                        "High": quote.get("high"),
                        "Low": quote.get("low"),
                        "Close": quote.get("close"),
                        "Volume": quote.get("volume"),
                    },
                    index=idx,
                )
                if auto_adjust:
                    adj = node.get("indicators", {}).get("adjclose")
                    if adj and adj[0].get("adjclose"):
                        adjclose = pd.Series(adj[0]["adjclose"], index=idx)
                        ratio = adjclose / df_t["Close"]
                        for col in ("Open", "High", "Low", "Close"):
                            df_t[col] = df_t[col] * ratio
                df_t = df_t[~df_t.index.duplicated(keep="last")].dropna(how="all")
                break
            if df_t is not None and not df_t.empty:
                break
        if df_t is not None and not df_t.empty:
            frames[ticker] = df_t

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, axis=1)  # columns -> (ticker, metric)
    combined.columns = combined.columns.swaplevel(0, 1)  # -> (metric, ticker)
    combined = combined.sort_index(axis=1)
    combined.index.name = "Date"
    return combined


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
        # Fallback: plain-requests Yahoo chart API (proxy-safe).
        raw = _requests_yahoo_multi(
            ticker_list, start, end,
            interval=kwargs.get("interval", "1d"),
            auto_adjust=kwargs.get("auto_adjust", True),
        )

    if raw is None or raw.empty:
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
        # Fallback: plain-requests Yahoo chart API (proxy-safe).
        raw = _requests_yahoo_multi(
            ticker_list, start, end,
            interval=kwargs.get("interval", "1d"),
            auto_adjust=kwargs.get("auto_adjust", True),
        )

    if raw is None or raw.empty:
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
        # Second fallback: plain-requests Yahoo chart API (proxy-safe).
        try:
            raw = _requests_yahoo_multi(
                [ticker],
                start_dt.strftime("%Y-%m-%d"),
                end_dt.strftime("%Y-%m-%d"),
            )
            hist = flatten_yfinance_columns(raw, ticker=ticker)
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

    # Test 6: flatten_yfinance_columns on a MultiIndex frame (yfinance-shaped).
    # Built synthetically so the test does not depend on a live download (which
    # fails under the egress proxy where curl_cffi TLS impersonation is reset).
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    cols = pd.MultiIndex.from_tuples(
        [("Close", "AAPL"), ("Close", "MSFT"), ("Open", "AAPL"), ("Open", "MSFT")]
    )
    raw = pd.DataFrame([[1, 2, 3, 4], [5, 6, 7, 8]], index=idx, columns=cols)
    assert isinstance(raw.columns, pd.MultiIndex), "Raw download should be MultiIndex"
    flat_raw = flatten_yfinance_columns(raw)
    assert not isinstance(flat_raw.columns, pd.MultiIndex), "After flatten should be flat"
    print(f"  flatten_yfinance_columns multi: {list(flat_raw.columns)[:6]}")

    print("\nAll tests passed.")
