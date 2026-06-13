"""
Canonical tradeable test: HG=F -> XLB threshold backtest.

Scan hit claim: copper Granger-causes XLB materials ETF at lag 5 (IS p=0.0085, OOS confirmed lag 4).
Meta-rule warning: xlb_gold_lead_lag_tradeable_apr2026 DEAD_END — same pattern for GC=F found
Granger artifact with zero tradeable edge. Need to verify HG=F->XLB tradeably.

Design:
- When HG=F return on day t exceeds threshold X%, long (or short if neg) XLB at t+1 open.
- Hold h days, exit at close of t+h.
- Abnormal return = XLB return - SPY return.
- Discovery 2015..2023-06-30. OOS 2023-07-01..2026-04-18.
- Thresholds: 0.5%, 1.0%, 1.5%, 2.0%. Horizons: 1d, 3d, 5d (scan-claimed lag), 10d.
"""
import sys
import os
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_aligned_returns


def run_test(discovery_df, oos_df, threshold_pct, horizon_days, direction):
    """direction: 'long' if positive copper -> long XLB, 'short' if neg."""
    results = {}
    for name, df in [("discovery", discovery_df), ("oos", oos_df)]:
        # Returns are in PERCENT units (e.g., 1.0 = 1%), divide by 100 for decimal
        if direction == "long":
            mask = df["copper"] >= threshold_pct  # already in %
        else:
            mask = df["copper"] <= -threshold_pct
        trigger_dates = df.index[mask]
        if len(trigger_dates) < 5:
            results[name] = {"n": len(trigger_dates), "insufficient": True}
            continue

        abnormal_returns = []
        for t in trigger_dates:
            # Entry t+1, exit t+horizon_days
            loc = df.index.get_loc(t)
            entry_loc = loc + 1
            exit_loc = loc + horizon_days
            if exit_loc >= len(df):
                continue
            xlb_ret = np.prod(1 + df["xlb"].iloc[entry_loc : exit_loc + 1].values / 100) - 1
            spy_ret = np.prod(1 + df["spy"].iloc[entry_loc : exit_loc + 1].values / 100) - 1
            if direction == "short":
                abnormal_returns.append(-(xlb_ret - spy_ret))
            else:
                abnormal_returns.append(xlb_ret - spy_ret)

        arr = np.array(abnormal_returns)
        if len(arr) < 5:
            results[name] = {"n": len(arr), "insufficient": True}
            continue

        t_stat, p_val = stats.ttest_1samp(arr, 0)
        results[name] = {
            "n": len(arr),
            "mean_pct": float(arr.mean() * 100),
            "median_pct": float(np.median(arr) * 100),
            "t_stat": float(t_stat),
            "p_val": float(p_val),
            "pos_rate": float((arr > 0).mean()),
        }
    return results


def main():
    # Get returns
    ret = get_aligned_returns(["HG=F", "XLB", "SPY"], start="2015-01-01", end="2026-04-18")
    ret.columns = ["copper", "xlb", "spy"]
    ret = ret.dropna()
    print(f"Aligned rows: {len(ret)}")
    print(f"Date range: {ret.index[0]} to {ret.index[-1]}")

    # Split
    split = "2023-06-30"
    discovery = ret.loc[:split]
    oos = ret.loc[split:]
    print(f"Discovery n={len(discovery)}, OOS n={len(oos)}")

    # Test grid
    thresholds = [0.5, 1.0, 1.5, 2.0]
    horizons = [1, 3, 5, 10]
    directions = ["long", "short"]

    print("\n" + "=" * 100)
    print(f"{'dir':5} {'thr%':5} {'hor':4} | {'disc n':6} {'disc mean%':10} {'disc p':8} {'disc pos':8} | {'oos n':5} {'oos mean%':9} {'oos p':7} {'oos pos':7}")
    print("=" * 100)

    passes = []
    for d in directions:
        for thr in thresholds:
            for h in horizons:
                r = run_test(discovery, oos, thr, h, d)
                disc = r.get("discovery", {})
                oos_r = r.get("oos", {})
                if disc.get("insufficient") or oos_r.get("insufficient"):
                    continue
                print(f"{d:5} {thr:5.1f} {h:4d} | "
                      f"{disc['n']:6d} {disc['mean_pct']:+10.3f} {disc['p_val']:8.4f} {disc['pos_rate']:8.2%} | "
                      f"{oos_r['n']:5d} {oos_r['mean_pct']:+9.3f} {oos_r['p_val']:7.4f} {oos_r['pos_rate']:7.2%}")
                # Canonical pass criteria
                if (disc['p_val'] < 0.05 and abs(disc['mean_pct']) >= 1.0 and disc['pos_rate'] >= 0.6
                        and oos_r['p_val'] < 0.05 and abs(oos_r['mean_pct']) >= 1.0 and oos_r['pos_rate'] >= 0.6
                        and np.sign(disc['mean_pct']) == np.sign(oos_r['mean_pct'])):
                    passes.append((d, thr, h, disc, oos_r))

    print("\n" + "=" * 100)
    if passes:
        print(f"CANONICAL PASSES: {len(passes)}")
        for p in passes:
            print(f"  {p[0]} thr={p[1]}% hor={p[2]}d: disc mean={p[3]['mean_pct']:+.2f}% oos mean={p[4]['mean_pct']:+.2f}%")
    else:
        print("NO CANONICAL PASSES. Signal is not tradeable. Closing as DEAD END.")
    return passes


if __name__ == "__main__":
    main()
