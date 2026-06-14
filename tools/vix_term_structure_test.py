#!/usr/bin/env python3
"""
VIX term-structure (slope) backwardation -> SPY forward-return test.

Distinct from the validated VIX>30 *level* signal: this tests the *slope* of the
VIX curve. Backwardation (front-month ^VIX > 3-month ^VIX3M, ratio > 1) marks acute
near-term fear and historically mean-reverts. Key question: does it add anything
beyond VIX>30, or is it redundant (fires on the same fear days)?

Design:
  - Signal onset = first day the VIX/VIX3M ratio closes above `thr` after >= `gap`
    trading days below it (first-touch de-clustering).
  - Entry at onset close; SPY close-to-close forward returns at 5/10/20d.
  - IS: pre-2020. OOS: 2020+. Reports base-rate-adjusted abnormal return.
  - Orthogonality: splits onsets into VIX>=30 vs VIX<30 to isolate the
    independent (low-level) cases that VIX>30 would miss.

Usage: python3 tools/vix_term_structure_test.py [--thr 1.0] [--gap 10]
"""
import argparse
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import get_close_prices  # noqa: E402


def fwd_return(spy, idx, h):
    """SPY close-to-close return from position idx to idx+h, or nan if out of range."""
    if idx + h >= len(spy):
        return np.nan
    return spy.iloc[idx + h] / spy.iloc[idx] - 1.0


def summarize(label, rets, base):
    rets = np.asarray([r for r in rets if not np.isnan(r)])
    n = len(rets)
    if n == 0:
        print(f"  {label}: n=0")
        return
    mean = rets.mean() * 100
    abn = mean - base * 100
    direction = (rets > 0).mean() * 100
    # one-sample t vs 0
    t = rets.mean() / (rets.std(ddof=1) / np.sqrt(n)) if n > 1 and rets.std() > 0 else float("nan")
    print(f"  {label}: n={n:3d} | mean={mean:+.2f}% | abnormal(vs base)={abn:+.2f}% | "
          f"dir={direction:.0f}% | t={t:+.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=1.0, help="VIX/VIX3M ratio threshold for backwardation")
    ap.add_argument("--gap", type=int, default=10, help="min trading days between onsets (de-cluster)")
    ap.add_argument("--start", default="2008-01-01")
    ap.add_argument("--end", default="2026-06-13")
    args = ap.parse_args()

    px = get_close_prices(["^VIX", "^VIX3M", "SPY"], start=args.start, end=args.end)
    px = px.dropna()
    ratio = px["^VIX"] / px["^VIX3M"]
    vix = px["^VIX"]
    spy = px["SPY"].reset_index(drop=True)
    dates = px.index

    above = (ratio > args.thr).values
    n_days = len(above)

    # First-touch onsets with de-clustering gap.
    onsets = []  # (idx, date, vix_level)
    last = -10 ** 9
    for i in range(1, n_days):
        if above[i] and not above[i - 1] and (i - last) >= args.gap:
            onsets.append((i, dates[i], float(vix.iloc[i])))
            last = i

    print(f"=== VIX term-structure backwardation (ratio>{args.thr}, gap>={args.gap}d) ===")
    print(f"Sample {args.start}..{args.end} | trading days={n_days} | "
          f"days in backwardation={above.sum()} ({100*above.mean():.1f}%) | onsets={len(onsets)}")

    horizons = [5, 10, 20]
    # Unconditional base rates per horizon.
    base = {}
    for h in horizons:
        allr = [fwd_return(spy, i, h) for i in range(n_days)]
        allr = np.asarray([r for r in allr if not np.isnan(r)])
        base[h] = allr.mean()
        print(f"  base-rate SPY {h}d fwd: {allr.mean()*100:+.2f}% (dir {100*(allr>0).mean():.0f}%)")

    def split(predicate):
        return [(i, d, v) for (i, d, v) in onsets if predicate(v)]

    groups = {
        "ALL onsets": onsets,
        "IS (pre-2020)": [o for o in onsets if o[1].year < 2020],
        "OOS (2020+)": [o for o in onsets if o[1].year >= 2020],
        "VIX>=30 (redundant w/ level signal)": split(lambda v: v >= 30),
        "VIX<30 (INDEPENDENT of VIX>30)": split(lambda v: v < 30),
    }

    for h in horizons:
        print(f"\n--- SPY {h}d forward return ---")
        for name, grp in groups.items():
            rets = [fwd_return(spy, i, h) for (i, d, v) in grp]
            summarize(name, rets, base[h])


if __name__ == "__main__":
    main()
