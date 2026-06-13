#!/usr/bin/env python3
"""Post-options-expiration (OpEx) week effect on equity index ETFs.

Monthly options expire on the 3rd Friday. Documented effects (dealer gamma
hedging / pinning unwind): equities tend to drift up into OpEx and reverse
after. Quad-witching (Mar/Jun/Sep/Dec) carries the largest open interest.

This screens the effect cleanly from price data: no event-date ambiguity
(3rd Friday is deterministic). IS = 2010-2019, OOS = 2020-2026, plus a
recent-only (2022+) recency check, per the system's calendar-anomaly rules
(effect-size floor + recency-subgroup).
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats
from tools.yfinance_utils import get_close_prices

TICKERS = ["SPY", "QQQ", "IWM"]
START = "2010-01-01"
END = "2026-06-12"


def third_friday(year, month):
    # first day of month weekday; find first Friday then +14
    d = pd.Timestamp(year, month, 1)
    # weekday(): Mon=0..Sun=6 ; Friday=4
    offset = (4 - d.weekday()) % 7
    first_fri = d + pd.Timedelta(days=offset)
    return first_fri + pd.Timedelta(days=14)


def trading_day_on_or_before(idx, ts):
    """Return position in idx of last trading day <= ts, or None."""
    pos = idx.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    return pos


def analyze(ticker, px):
    """px: Series of close prices indexed by date. Returns dataframe of weekly windows."""
    idx = px.index
    rows = []
    for year in range(2010, 2027):
        for month in range(1, 13):
            tf = third_friday(year, month)
            if tf < idx[0] or tf > idx[-1]:
                continue
            is_quad = month in (3, 6, 9, 12)
            # OpEx Friday trading position (on or before 3rd Friday in case holiday)
            p_fri = trading_day_on_or_before(idx, tf)
            if p_fri is None or p_fri < 5 or p_fri + 6 >= len(idx):
                continue
            # Run-up: close 5 trading days before OpEx Fri -> OpEx Fri close
            runup = px.iloc[p_fri] / px.iloc[p_fri - 5] - 1.0
            # Post-OpEx week: OpEx Fri close -> +5 trading days close
            post = px.iloc[p_fri + 5] / px.iloc[p_fri] - 1.0
            # Just the Monday after OpEx (1 day)
            mon = px.iloc[p_fri + 1] / px.iloc[p_fri] - 1.0
            rows.append(dict(date=tf, year=year, month=month, is_quad=is_quad,
                             runup=runup, post=post, mon=mon))
    return pd.DataFrame(rows)


def baseline_weekly(px):
    """Mean/std of all overlapping 5-trading-day close-to-close returns (annualization-free)."""
    r5 = px / px.shift(5) - 1.0
    r5 = r5.dropna()
    return r5.mean(), r5.std()


def summ(name, x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return f"{name}: n={len(x)} (too small)"
    t, p = stats.ttest_1samp(x, 0.0)
    pos = (x > 0).mean()
    return (f"{name}: n={len(x)} mean={x.mean()*100:+.3f}% median={np.median(x)*100:+.3f}% "
            f"pos_rate={pos*100:.0f}% t={t:.2f} p={p:.3f}")


def main():
    px_all = get_close_prices(TICKERS, START, END)
    for tk in TICKERS:
        s = px_all[tk].dropna() if isinstance(px_all, pd.DataFrame) else px_all.dropna()
        df = analyze(tk, s)
        bmean, bstd = baseline_weekly(s)
        print(f"\n========== {tk} ==========")
        print(f"baseline 5d return: mean={bmean*100:+.3f}% std={bstd*100:.3f}% (all overlapping windows)")
        print(f"n OpEx months: {len(df)}")
        for window in ["runup", "post", "mon"]:
            print(f"-- {window} --")
            print("  ALL    ", summ("all", df[window]))
            print("  IS10-19", summ("is", df[df.year <= 2019][window]))
            print("  OOS20+ ", summ("oos", df[df.year >= 2020][window]))
            print("  REC22+ ", summ("rec", df[df.year >= 2022][window]))
            print("  QUAD   ", summ("quad", df[df.is_quad][window]))
            print("  NONQUAD", summ("nonq", df[~df.is_quad][window]))


if __name__ == "__main__":
    main()
