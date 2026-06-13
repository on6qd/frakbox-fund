"""
Pre-registered robustness test for VIX>30 -> TLT short 5d signal.

Goal: check whether the canonical-passing signal is an artifact of the 2022
Fed-hike bond bear market, or survives exclusion of that regime.

Success criterion (locked BEFORE running):
  Non-2022 subset (n=11): 5d abnormal return (TLT - SPY) mean < -1.0%
  with two-sided p < 0.10 (relaxed from 0.05 due to smaller n).
"""
import sys
import os
import numpy as np
import pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download

# Event dates from the canonical retest (first-close cluster-buffered, 30-day)
EVENT_DATES = [
    "2015-08-24",
    "2018-02-05",
    "2018-12-21",
    "2020-02-27",
    "2020-09-03",
    "2020-10-26",
    "2021-01-27",
    "2021-12-01",
    "2022-01-25",
    "2022-04-26",
    "2022-09-26",
    "2024-08-05",
    "2025-04-03",
    "2026-03-27",
]

HORIZON = 5  # trading days


def abnormal_return(event_date, horizon, tlt, spy):
    """Entry at next open after event; exit at close after horizon trading days."""
    event_ts = pd.Timestamp(event_date)
    # Find entry trading day (first day after event)
    later = tlt.index[tlt.index > event_ts]
    if len(later) < horizon + 1:
        return None, None
    entry_day = later[0]
    exit_day = later[horizon] if len(later) > horizon else None
    if exit_day is None:
        return None, None
    # Entry at open, exit at close
    tlt_ret = (tlt.loc[exit_day, "Close"] / tlt.loc[entry_day, "Open"] - 1) * 100
    spy_ret = (spy.loc[exit_day, "Close"] / spy.loc[entry_day, "Open"] - 1) * 100
    return tlt_ret, spy_ret


def main():
    tlt = safe_download("TLT", "2015-01-01", "2026-04-24")
    spy = safe_download("SPY", "2015-01-01", "2026-04-24")

    # Flatten multi-index if present
    if isinstance(tlt.columns, pd.MultiIndex):
        tlt.columns = tlt.columns.get_level_values(0)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)

    print(f"TLT data: {tlt.index[0].date()} to {tlt.index[-1].date()}, n={len(tlt)}")
    print(f"SPY data: {spy.index[0].date()} to {spy.index[-1].date()}, n={len(spy)}")
    print()

    records = []
    for d in EVENT_DATES:
        tlt_ret, spy_ret = abnormal_return(d, HORIZON, tlt, spy)
        if tlt_ret is None:
            print(f"  {d}: SKIP (insufficient data)")
            continue
        abn = tlt_ret - spy_ret
        records.append({
            "date": d,
            "year": int(d[:4]),
            "tlt_5d": tlt_ret,
            "spy_5d": spy_ret,
            "abn_5d": abn,
        })

    df = pd.DataFrame(records)
    print(df.to_string(index=False))
    print()

    # Summary stats
    def stats_block(label, sub):
        if len(sub) < 2:
            print(f"{label}: n={len(sub)} (too few for t-test)")
            return
        mean = sub["abn_5d"].mean()
        median = sub["abn_5d"].median()
        t, p = stats.ttest_1samp(sub["abn_5d"], 0)
        pos = (sub["abn_5d"] > 0).mean() * 100
        print(f"{label}: n={len(sub)}, mean={mean:.2f}%, median={median:.2f}%, "
              f"t={t:.2f}, p={p:.4f}, positive_rate={pos:.1f}%")

    print("--- Summary by subset ---")
    stats_block("ALL (pooled 2015-2026)", df)
    stats_block("2020+ only", df[df.year >= 2020])
    stats_block("EXCLUDING 2022", df[df.year != 2022])
    stats_block("ONLY 2022", df[df.year == 2022])
    stats_block("Pre-2020 (n=3)", df[df.year < 2020])

    # Robustness verdict
    non2022 = df[df.year != 2022]
    if len(non2022) >= 2:
        mean = non2022["abn_5d"].mean()
        t, p = stats.ttest_1samp(non2022["abn_5d"], 0)
        pass_robust = (mean < -1.0) and (p < 0.10)
        print()
        print(f"ROBUSTNESS (locked criterion): non-2022 mean<-1% AND p<0.10")
        print(f"  mean={mean:.2f}%, p={p:.4f} -> {'PASS' if pass_robust else 'FAIL'}")


if __name__ == "__main__":
    main()
