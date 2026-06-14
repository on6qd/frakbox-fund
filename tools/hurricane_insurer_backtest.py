#!/usr/bin/env python3
"""Event study: major US hurricane landfall -> P&C insurer short-horizon abnormal returns.

Mechanism is theoretically ambiguous:
  (a) catastrophic insured losses are material to a P&C insurer's quarter -> price falls (SHORT)
  (b) large cat events harden the pricing market (future premium rates rise) -> relief rally (LONG)

We measure abnormal returns (stock minus benchmark) for a basket of hurricane-exposed
insurers around the landfall date, across several entry windows and horizons, with an
IS/OOS split. Pure price data (yfinance), no LLM.
"""
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from tools.yfinance_utils import get_close_prices

# Major US hurricane landfalls 2005-2024 with material insured losses.
# date = US landfall date (the loss-realization event). Significant Cat / insured-loss storms only.
HURRICANES = [
    ("Katrina",  "2005-08-29"),
    ("Rita",     "2005-09-24"),
    ("Wilma",    "2005-10-24"),
    ("Gustav",   "2008-09-01"),
    ("Ike",      "2008-09-13"),
    ("Irene",    "2011-08-28"),
    ("Sandy",    "2012-10-29"),
    ("Matthew",  "2016-10-08"),
    ("Harvey",   "2017-08-25"),
    ("Irma",     "2017-09-10"),
    ("Michael",  "2018-10-10"),
    ("Florence", "2018-09-14"),
    ("Dorian",   "2019-09-06"),
    ("Laura",    "2020-08-27"),
    ("Sally",    "2020-09-16"),
    ("Zeta",     "2020-10-28"),
    ("Ida",      "2021-08-29"),
    ("Ian",      "2022-09-28"),
    ("Nicole",   "2022-11-10"),
    ("Idalia",   "2023-08-30"),
    ("Beryl",    "2024-07-08"),
    ("Helene",   "2024-09-26"),
    ("Milton",   "2024-10-09"),
]

# Hurricane-exposed P&C insurers / reinsurers (large/mid cap, US-listed, history back to 2005).
INSURERS = ["ALL", "TRV", "CB", "PGR", "CINF", "HIG", "AIG", "RNR", "EG"]
BENCH = "SPY"
ALT_BENCH = "XLF"

OOS_START = "2018-01-01"   # IS: 2005-2017, OOS: 2018-2024


def trading_index(prices):
    return prices.index


def nearest_pos(idx, date):
    """Position of the first trading day on/after `date`."""
    date = pd.Timestamp(date)
    locs = np.where(idx >= date)[0]
    return int(locs[0]) if len(locs) else None


def abnormal_return(prices, bench, sym, idx, anchor_pos, entry_offset, hold):
    """Abnormal return of sym vs bench from close[anchor+entry_offset] to close[anchor+entry_offset+hold]."""
    e = anchor_pos + entry_offset
    x = e + hold
    if e < 0 or x >= len(idx):
        return None
    try:
        ps0, ps1 = prices[sym].iloc[e], prices[sym].iloc[x]
        pb0, pb1 = bench.iloc[e], bench.iloc[x]
    except KeyError:
        return None
    if any(pd.isna(v) for v in (ps0, ps1, pb0, pb1)) or ps0 <= 0 or pb0 <= 0:
        return None
    return (ps1 / ps0 - 1.0) - (pb1 / pb0 - 1.0)


def summarize(label, arr):
    arr = np.array([a for a in arr if a is not None])
    n = len(arr)
    if n < 5:
        print(f"  {label:42s} n={n:<4d} (insufficient)")
        return None
    mean = arr.mean() * 100
    sd = arr.std(ddof=1) * 100
    t = mean / (sd / np.sqrt(n)) if sd > 0 else 0.0
    dirpct = (arr < 0).mean() * 100  # % negative (short-favorable)
    print(f"  {label:42s} n={n:<4d} mean={mean:+6.2f}% t={t:+5.2f} dir_down={dirpct:4.0f}%")
    return dict(n=n, mean=mean, t=t, dir_down=dirpct)


def run():
    start = "2005-06-01"
    end = "2025-01-15"
    syms = INSURERS + [BENCH, ALT_BENCH]
    print(f"Fetching {len(syms)} symbols {start}..{end} ...")
    prices = get_close_prices(syms, start=start, end=end)
    prices = prices.dropna(how="all")
    have = [s for s in INSURERS if s in prices.columns and prices[s].notna().sum() > 200]
    print(f"Usable insurers: {have}")
    idx = prices.index

    # entry/horizon scenarios: (entry_offset_from_landfall, hold_days, description)
    scenarios = [
        (-3, 3, "pre: short T-3 -> landfall"),
        (-3, 5, "pre: short T-3 -> T+2"),
        (0, 3, "post: enter landfall -> +3"),
        (0, 5, "post: enter landfall -> +5"),
        (0, 10, "post: enter landfall -> +10"),
        (1, 5, "post: enter +1 -> +6"),
        (1, 10, "post: enter +1 -> +11"),
    ]

    for bench_name, bench_series in [("SPY", prices[BENCH]), ("XLF", prices[ALT_BENCH])]:
        print(f"\n===== Benchmark = {bench_name} =====")
        for eo, hold, desc in scenarios:
            allret, isret, oosret = [], [], []
            for name, dstr in HURRICANES:
                ap = nearest_pos(idx, dstr)
                if ap is None:
                    continue
                is_oos = pd.Timestamp(dstr) >= pd.Timestamp(OOS_START)
                for sym in have:
                    r = abnormal_return(prices, bench_series, sym, idx, ap, eo, hold)
                    if r is None:
                        continue
                    allret.append(r)
                    (oosret if is_oos else isret).append(r)
            print(f"\n[{desc}]  (entry_off={eo}, hold={hold})")
            summarize("ALL 2005-2024", allret)
            summarize("IS 2005-2017", isret)
            summarize("OOS 2018-2024", oosret)


if __name__ == "__main__":
    run()
