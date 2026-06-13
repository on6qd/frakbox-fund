"""
Canonical tradeable test: ZS=F -> MOO threshold backtest.

Scan hit claim: soybean futures Granger-cause MOO agribusiness ETF at lag 1 (IS p=0.030, OOS confirmed lag 5).
Prior: meta-rule exposure_to_event_conversion_meta_finding — 5 prior confirmations that commodity-sector
Granger lead-lag is artifact, not tradeable.

Design: same as HG=F/XLB test but for ZS=F/MOO.
"""
import sys
import os
import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_aligned_returns


def run_test(df, threshold_pct, horizon_days, direction):
    if direction == "long":
        mask = df["factor"] >= threshold_pct
    else:
        mask = df["factor"] <= -threshold_pct
    trigger_dates = df.index[mask]
    if len(trigger_dates) < 5:
        return {"n": len(trigger_dates), "insufficient": True}

    abnormal_returns = []
    for t in trigger_dates:
        loc = df.index.get_loc(t)
        entry_loc = loc + 1
        exit_loc = loc + horizon_days
        if exit_loc >= len(df):
            continue
        tgt_ret = np.prod(1 + df["target"].iloc[entry_loc : exit_loc + 1].values / 100) - 1
        bmk_ret = np.prod(1 + df["bench"].iloc[entry_loc : exit_loc + 1].values / 100) - 1
        if direction == "short":
            abnormal_returns.append(-(tgt_ret - bmk_ret))
        else:
            abnormal_returns.append(tgt_ret - bmk_ret)

    arr = np.array(abnormal_returns)
    if len(arr) < 5:
        return {"n": len(arr), "insufficient": True}
    t_stat, p_val = stats.ttest_1samp(arr, 0)
    return {
        "n": len(arr),
        "mean_pct": float(arr.mean() * 100),
        "t_stat": float(t_stat),
        "p_val": float(p_val),
        "pos_rate": float((arr > 0).mean()),
    }


def main():
    ret = get_aligned_returns(["ZS=F", "MOO", "SPY"], start="2015-01-01", end="2026-04-18")
    ret.columns = ["factor", "target", "bench"]
    ret = ret.dropna()
    print(f"Aligned rows: {len(ret)}")

    split = "2023-06-30"
    discovery = ret.loc[:split]
    oos = ret.loc[split:]

    print(f"\n{'dir':5} {'thr%':5} {'hor':4} | {'disc n':6} {'disc mean%':10} {'disc p':8} {'disc pos':8} | {'oos n':5} {'oos mean%':9} {'oos p':7} {'oos pos':7}")
    print("=" * 100)
    passes = []
    for d in ["long", "short"]:
        for thr in [0.5, 1.0, 1.5, 2.0]:
            for h in [1, 3, 5, 10]:
                dr = run_test(discovery, thr, h, d)
                orr = run_test(oos, thr, h, d)
                if dr.get("insufficient") or orr.get("insufficient"):
                    continue
                print(f"{d:5} {thr:5.1f} {h:4d} | "
                      f"{dr['n']:6d} {dr['mean_pct']:+10.3f} {dr['p_val']:8.4f} {dr['pos_rate']:8.2%} | "
                      f"{orr['n']:5d} {orr['mean_pct']:+9.3f} {orr['p_val']:7.4f} {orr['pos_rate']:7.2%}")
                if (dr['p_val'] < 0.05 and abs(dr['mean_pct']) >= 1.0 and dr['pos_rate'] >= 0.6
                        and orr['p_val'] < 0.05 and abs(orr['mean_pct']) >= 1.0 and orr['pos_rate'] >= 0.6
                        and np.sign(dr['mean_pct']) == np.sign(orr['mean_pct'])):
                    passes.append((d, thr, h, dr, orr))

    if passes:
        print(f"\nCANONICAL PASSES: {len(passes)}")
        for p in passes:
            print(f"  {p[0]} thr={p[1]}% hor={p[2]}d: disc {p[3]['mean_pct']:+.2f}% oos {p[4]['mean_pct']:+.2f}%")
    else:
        print("\nNO CANONICAL PASSES — DEAD END.")


if __name__ == "__main__":
    main()
