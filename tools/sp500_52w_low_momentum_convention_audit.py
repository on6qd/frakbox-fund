#!/usr/bin/env python3
"""
Strict next-day-open convention audit for the `sp500_52w_low_momentum_short`
desk thesis (hypotheses 5b09b097 / cc88dd18, timing=intraday).

Canonical rule: event_signal_next_day_open_convention_canonical_rule_2026_08_24.
A signal confirmed at the CLOSE of day T cannot be entered on day T (you only
know it is a first-touch 52-week low once the close prints). The engine's
`event_timing="intraday"` convention enters at close(T-1) and its day-0 return
therefore captures the entire breach-day drop — pure look-ahead. The only
scanner-executable convention is entry = OPEN of the first trading day AFTER
confirmation (T+1), which the engine produces with
event_timing="after_hours" + entry_price="open".

This script rebuilds the S&P 500 first-touch 52-week-low event universe over
2021-2025 and recomputes SPY-abnormal returns under BOTH conventions, faithfully
replicating market_data.measure_event_impact's horizon indexing.

    B  (original, look-ahead):  entry=close(T-1), 5d exit=close(T+4)
    A  (strict, executable):    entry=open(T+1),  5d exit=close(T+5)

Universe = current S&P 500 membership (survivorship caveat: removed names are
missing; the RELATIVE comparison between conventions uses the same event set and
is unaffected). Deterministic given the Wikipedia membership on run date.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from tools.yfinance_utils import safe_download

WINDOW_START = "2021-01-01"
WINDOW_END = "2025-12-31"
RECENT_START = "2024-01-01"          # validation / recency subset
DEBOUNCE_DAYS = 45                    # cluster nearby crossings as one event
ROLL = 252                           # 52-week rolling min (trading days)
HORIZONS = {"1d": 0, "3d": 2, "5d": 4, "10d": 9, "20d": 19}


def get_sp500_tickers():
    from tools.build_sp500_universe import load_sp500_universe
    tk = load_sp500_universe()
    # normalize class-share dots to yfinance dashes
    return sorted({t.replace(".", "-") for t in tk})


def first_touch_dates(close: pd.Series) -> list:
    """Dates where close first drops below the prior 252-day rolling min,
    debounced by DEBOUNCE_DAYS, within [WINDOW_START, WINDOW_END]."""
    close = close.dropna()
    if len(close) < ROLL + 5:
        return []
    prior_min = close.shift(1).rolling(ROLL, min_periods=ROLL).min()
    breach = close < prior_min
    events = []
    last = None
    for dt, is_breach in breach.items():
        if not is_breach:
            continue
        d = pd.Timestamp(dt)
        if d < pd.Timestamp(WINDOW_START) or d > pd.Timestamp(WINDOW_END):
            continue
        if last is not None and (d - last).days < DEBOUNCE_DAYS:
            continue
        events.append(d)
        last = d
    return events


def abnormal(stock: pd.DataFrame, spy_close: pd.Series, tpos: int):
    """Return dict of B/A abnormal returns for the event at integer index tpos
    in the stock's own trading calendar. Faithful to measure_event_impact."""
    sc = stock["Close"]
    so = stock["Open"]
    idx = sc.index
    n = len(idx)
    out = {}
    # Align SPY on the stock's dates
    spy = spy_close.reindex(idx).ffill()

    # --- Convention B (intraday/close): entry close(T-1), post_dates[0]=T ---
    if tpos - 1 >= 0:
        entry_b = sc.iloc[tpos - 1]
        spy_pre_b = spy.iloc[tpos - 1]
        for lab, hidx in HORIZONS.items():
            j = tpos + hidx                       # post_dates[hidx], post_dates[0]=T
            if j < n and entry_b > 0 and spy_pre_b > 0:
                raw = (sc.iloc[j] / entry_b - 1) * 100
                bench = (spy.iloc[j] / spy_pre_b - 1) * 100
                out[f"B_{lab}"] = raw - bench

    # --- Convention A (after_hours/open): entry open(T+1), post_dates[0]=T+1 ---
    if tpos + 1 < n:
        entry_a = so.iloc[tpos + 1]
        spy_pre_a = spy.iloc[tpos]                # pre_dates[-1]=T
        for lab, hidx in HORIZONS.items():
            j = tpos + 1 + hidx                   # post_dates[hidx], post_dates[0]=T+1
            if j < n and entry_a > 0 and spy_pre_a > 0:
                raw = (sc.iloc[j] / entry_a - 1) * 100
                bench = (spy.iloc[j] / spy_pre_a - 1) * 100
                out[f"A_{lab}"] = raw - bench
    return out


def summarize(rows: pd.DataFrame, tag: str):
    from scipy import stats as st
    print(f"\n=== {tag}  (N={len(rows)}) ===")
    print(f"{'horizon':>7} | {'B close(T-1) LOOKAHEAD':>26} | {'A open(T+1) EXECUTABLE':>26}")
    for lab in HORIZONS:
        line = f"{lab:>7} |"
        for conv in ("B", "A"):
            col = f"{conv}_{lab}"
            v = rows[col].dropna()
            if len(v) < 5:
                line += f" {'n/a':>26} |"
                continue
            mean = v.mean()
            # short profitability: fraction of negative abnormal returns
            neg = (v < 0).mean() * 100
            t, p = st.ttest_1samp(v, 0)
            line += f" {mean:+6.2f}% p={p:5.3f} neg{neg:2.0f}% |"
        print(line)


def main():
    print("Fetching S&P 500 membership...", file=sys.stderr)
    tickers = get_sp500_tickers()
    print(f"{len(tickers)} tickers", file=sys.stderr)

    dl_start = "2019-06-01"   # >=252 trading days before WINDOW_START
    dl_end = "2026-03-01"     # >=20 trading days after WINDOW_END

    print("Downloading SPY...", file=sys.stderr)
    spy = safe_download("SPY", dl_start, dl_end)["Close"]

    all_events = []
    CHUNK = 60
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        print(f"Downloading {i}-{i+len(chunk)}...", file=sys.stderr)
        try:
            df = safe_download(chunk, dl_start, dl_end)
        except Exception as e:
            print(f"  chunk failed: {e}", file=sys.stderr)
            continue
        for tk in chunk:
            ccol, ocol = f"Close_{tk}", f"Open_{tk}"
            if ccol not in df.columns or ocol not in df.columns:
                continue
            stock = pd.DataFrame({"Close": df[ccol], "Open": df[ocol]}).dropna()
            if len(stock) < ROLL + 25:
                continue
            evs = first_touch_dates(stock["Close"])
            for d in evs:
                tpos = stock.index.get_loc(d)
                if isinstance(tpos, slice):
                    continue
                res = abnormal(stock, spy, tpos)
                if res:
                    res["symbol"] = tk
                    res["date"] = d.strftime("%Y-%m-%d")
                    all_events.append(res)

    rows = pd.DataFrame(all_events)
    if rows.empty:
        print("No events found.")
        return
    rows["date"] = pd.to_datetime(rows["date"])
    print(f"\nTotal first-touch events 2021-2025: {len(rows)}")
    summarize(rows, "POOLED 2021-2025")
    summarize(rows[rows["date"] >= RECENT_START], "RECENT 2024-2025")
    summarize(rows[rows["date"] < RECENT_START], "DISCOVERY 2021-2023")

    out_csv = os.path.join(os.path.dirname(__file__),
                           "sp500_52w_low_momentum_convention_audit_events.csv")
    rows.sort_values("date").to_csv(out_csv, index=False)
    print(f"\nSaved events -> {out_csv}")


if __name__ == "__main__":
    main()
