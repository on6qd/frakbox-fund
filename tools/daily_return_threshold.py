#!/usr/bin/env python3
"""
daily_return_threshold.py — Generic: identify days where a driver series' daily
return exceeds a threshold (absolute, up, or down), then measure target abnormal
return vs a benchmark over multiple horizons, with IS/OOS split.

Used to convert validated exposure relationships into concrete event-class signals.

Examples:
  # XLU reacts to big TLT moves
  python tools/daily_return_threshold.py --driver TLT --target XLU --benchmark SPY --threshold 1.5

  # IBB reacts to big DGS10 moves (FRED series — use absolute bps, not pct)
  python tools/daily_return_threshold.py --driver FRED:DGS10 --target IBB --benchmark SPY --threshold 15 --driver-unit bps_level
"""
import sys, json, warnings, argparse
import os
import numpy as np
import pandas as pd
from datetime import datetime

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_series


def run_test(
    driver: str,
    target: str,
    benchmark: str,
    threshold: float,
    driver_unit: str = "pct_return",  # or "bps_level" for rates
    start: str = "2018-01-01",
    oos_start: str = "2024-01-01",
    horizons: list = None,
):
    """
    driver_unit:
      - "pct_return": detect days where |pct_change(driver)| > threshold_pct
      - "bps_level":  detect days where |diff(driver)| > threshold_bps/100 (for rate levels in %)
    """
    if horizons is None:
        horizons = [0, 1, 2, 3, 5]
    end = datetime.now().strftime("%Y-%m-%d")

    # Fetch all three series via the unified fetcher (handles FRED, yfinance, etc.)
    driver_s = get_series(driver, start, end).rename("driver")
    target_s = get_series(target, start, end).rename("target")
    bench_s = get_series(benchmark, start, end).rename("bench")

    df = pd.concat([driver_s, target_s, bench_s], axis=1).dropna()
    if len(df) < 100:
        return {"error": f"Insufficient aligned data: {len(df)} rows"}

    # Target/bench returns (always pct_change)
    df["tgt_ret"] = df["target"].pct_change()
    df["bench_ret"] = df["bench"].pct_change()
    df["abn"] = df["tgt_ret"] - df["bench_ret"]

    # Driver move
    if driver_unit == "pct_return":
        df["driver_move"] = df["driver"].pct_change() * 100  # in percent
    elif driver_unit == "bps_level":
        df["driver_move"] = df["driver"].diff() * 100  # diff in percent * 100 = bps
    else:
        raise ValueError(f"Unknown driver_unit: {driver_unit}")

    df = df.dropna()
    oos_dt = pd.Timestamp(oos_start)

    # Identify event days
    up_days = df.index[df["driver_move"] > threshold]
    down_days = df.index[df["driver_move"] < -threshold]

    results = {
        "params": {
            "driver": driver, "target": target, "benchmark": benchmark,
            "threshold": threshold, "driver_unit": driver_unit,
            "start": start, "oos_start": oos_start, "end": end,
            "total_days_aligned": len(df),
        },
        "events": {},
    }

    for direction, event_dates in [("driver_up", up_days), ("driver_down", down_days)]:
        is_dates = [d for d in event_dates if d < oos_dt]
        oos_dates = [d for d in event_dates if d >= oos_dt]

        horizon_stats = {}
        for h in horizons:
            is_abns = []
            oos_abns = []
            for d in event_dates:
                loc = df.index.get_loc(d)
                if h == 0:
                    ar = df["abn"].iloc[loc]
                else:
                    end_loc = loc + h
                    if end_loc >= len(df):
                        continue
                    cum_tgt = (1 + df["tgt_ret"].iloc[loc + 1 : end_loc + 1]).prod() - 1
                    cum_bench = (1 + df["bench_ret"].iloc[loc + 1 : end_loc + 1]).prod() - 1
                    ar = cum_tgt - cum_bench
                if d < oos_dt:
                    is_abns.append(ar)
                else:
                    oos_abns.append(ar)

            all_abns = is_abns + oos_abns
            stats_row = {
                "is_n": len(is_abns),
                "is_mean_abn_pct": round(np.mean(is_abns) * 100, 3) if is_abns else None,
                "oos_n": len(oos_abns),
                "oos_mean_abn_pct": round(np.mean(oos_abns) * 100, 3) if oos_abns else None,
                "total_n": len(all_abns),
                "total_mean_abn_pct": round(np.mean(all_abns) * 100, 3) if all_abns else None,
            }
            if all_abns:
                pos = sum(1 for r in all_abns if r > 0)
                stats_row["direction_pos_pct"] = round(pos / len(all_abns) * 100, 1)
            if len(is_abns) >= 5:
                from scipy import stats as scipy_stats
                t, p = scipy_stats.ttest_1samp(is_abns, 0)
                stats_row["is_t"] = round(t, 2)
                stats_row["is_p"] = round(p, 4)
            if len(oos_abns) >= 5:
                from scipy import stats as scipy_stats
                t, p = scipy_stats.ttest_1samp(oos_abns, 0)
                stats_row["oos_t"] = round(t, 2)
                stats_row["oos_p"] = round(p, 4)
            horizon_stats[f"day_{h}"] = stats_row

        results["events"][direction] = {
            "n_is": len(is_dates),
            "n_oos": len(oos_dates),
            "horizons": horizon_stats,
        }

    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--driver", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--threshold", type=float, required=True)
    p.add_argument("--driver-unit", default="pct_return", choices=["pct_return", "bps_level"])
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--oos-start", default="2024-01-01")
    p.add_argument("--horizons", default="0,1,2,3,5")
    args = p.parse_args()

    horizons = [int(h) for h in args.horizons.split(",")]
    r = run_test(
        driver=args.driver, target=args.target, benchmark=args.benchmark,
        threshold=args.threshold, driver_unit=args.driver_unit,
        start=args.start, oos_start=args.oos_start, horizons=horizons,
    )
    print(json.dumps(r, indent=2, default=str))
