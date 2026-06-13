#!/usr/bin/env python3
"""
Test FOMC announcement day returns on SPY.
Hypothesis: SPY shows positive abnormal return on FOMC announcement days.
Based on Lucca & Moench (2015) "pre-FOMC announcement drift."

Discovery: 2015-2022, OOS: 2023-2026
"""

import json
import sys
import os
import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download

# FOMC announcement dates (2015-2026)
# These are the dates of the FOMC statement release (typically ~2:00 PM ET)
FOMC_DATES = [
    # 2015
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17",
    "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    # 2016
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    # 2017
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    # 2018
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    # 2019
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29",  # Note: Mar 3 and Mar 15 were emergency
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023 (OOS)
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024 (OOS)
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025 (OOS)
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
    # 2026 (OOS - partial)
    "2026-01-28", "2026-03-18",
]

# Split into discovery and OOS
DISCOVERY_END = "2022-12-31"
OOS_START = "2023-01-01"


def main():
    import pandas as pd

    # Fetch SPY data
    print("Fetching SPY data 2015-2026...", file=sys.stderr)
    df = safe_download("SPY", start="2014-12-01", end="2026-04-15")
    if df is None or df.empty:
        print(json.dumps({"status": "error", "error": "Failed to download SPY data"}))
        return

    # Compute daily returns
    df["return"] = df["Close"].pct_change() * 100
    df = df.dropna(subset=["return"])

    # Map FOMC dates to actual trading days
    fomc_dates = pd.to_datetime(FOMC_DATES)

    # For each FOMC date, find the matching trading day (or next trading day if weekend/holiday)
    fomc_returns = []
    for fdate in fomc_dates:
        # Look for exact date or next trading day
        mask = df.index >= fdate
        if mask.any():
            actual_date = df.index[mask][0]
            # Only use if within 3 days (to handle weekends)
            if (actual_date - fdate).days <= 3:
                ret = df.loc[actual_date, "return"]
                fomc_returns.append({
                    "fomc_date": str(fdate.date()),
                    "actual_date": str(actual_date.date()),
                    "return": round(float(ret), 4),
                    "period": "discovery" if fdate <= pd.Timestamp(DISCOVERY_END) else "oos"
                })

    # Split into discovery and OOS
    discovery = [r for r in fomc_returns if r["period"] == "discovery"]
    oos = [r for r in fomc_returns if r["period"] == "oos"]

    disc_rets = [r["return"] for r in discovery]
    oos_rets = [r["return"] for r in oos]

    # Non-FOMC day returns
    fomc_actual_dates = set(r["actual_date"] for r in fomc_returns)
    non_fomc_df = df[~df.index.isin(pd.to_datetime(list(fomc_actual_dates)))]

    # Split non-FOMC by period too
    non_fomc_disc = non_fomc_df[non_fomc_df.index <= DISCOVERY_END]["return"]
    non_fomc_oos = non_fomc_df[non_fomc_df.index > DISCOVERY_END]["return"]

    # Statistics
    disc_mean = np.mean(disc_rets)
    disc_std = np.std(disc_rets, ddof=1)
    disc_median = np.median(disc_rets)
    disc_pos_rate = sum(1 for r in disc_rets if r > 0) / len(disc_rets) * 100

    oos_mean = np.mean(oos_rets) if oos_rets else 0
    oos_std = np.std(oos_rets, ddof=1) if len(oos_rets) > 1 else 0
    oos_median = np.median(oos_rets) if oos_rets else 0
    oos_pos_rate = sum(1 for r in oos_rets if r > 0) / len(oos_rets) * 100 if oos_rets else 0

    non_fomc_disc_mean = float(non_fomc_disc.mean())
    non_fomc_oos_mean = float(non_fomc_oos.mean())

    # Abnormal returns
    disc_abnormal = disc_mean - non_fomc_disc_mean
    oos_abnormal = oos_mean - non_fomc_oos_mean if oos_rets else 0

    # T-tests: FOMC days vs non-FOMC days
    t_disc, p_disc = stats.ttest_ind(disc_rets, non_fomc_disc.values)
    t_oos, p_oos = stats.ttest_ind(oos_rets, non_fomc_oos.values) if oos_rets else (0, 1)

    # One-sample t-test: are FOMC returns > 0?
    t_disc_onesided, p_disc_onesided = stats.ttest_1samp(disc_rets, 0)
    t_oos_onesided, p_oos_onesided = stats.ttest_1samp(oos_rets, 0) if oos_rets else (0, 1)

    # Also test pre-FOMC day (day before announcement)
    pre_fomc_disc_rets = []
    pre_fomc_oos_rets = []
    for r in fomc_returns:
        actual_idx = df.index.get_loc(pd.Timestamp(r["actual_date"]))
        if actual_idx > 0:
            pre_ret = float(df.iloc[actual_idx - 1]["return"])
            if r["period"] == "discovery":
                pre_fomc_disc_rets.append(pre_ret)
            else:
                pre_fomc_oos_rets.append(pre_ret)

    pre_disc_mean = np.mean(pre_fomc_disc_rets) if pre_fomc_disc_rets else 0
    pre_oos_mean = np.mean(pre_fomc_oos_rets) if pre_fomc_oos_rets else 0
    pre_disc_abnormal = pre_disc_mean - non_fomc_disc_mean
    pre_oos_abnormal = pre_oos_mean - non_fomc_oos_mean

    t_pre_disc, p_pre_disc = stats.ttest_ind(pre_fomc_disc_rets, non_fomc_disc.values) if pre_fomc_disc_rets else (0, 1)
    t_pre_oos, p_pre_oos = stats.ttest_ind(pre_fomc_oos_rets, non_fomc_oos.values) if pre_fomc_oos_rets else (0, 1)

    result = {
        "status": "ok",
        "hypothesis": "SPY shows positive abnormal return on FOMC announcement days",
        "discovery_period": "2015-2022",
        "oos_period": "2023-2026",
        "fomc_day_returns": {
            "discovery": {
                "n": len(disc_rets),
                "mean": round(disc_mean, 4),
                "median": round(disc_median, 4),
                "std": round(disc_std, 4),
                "pos_rate": round(disc_pos_rate, 1),
                "abnormal_vs_avg_day": round(disc_abnormal, 4),
                "p_vs_non_fomc": round(p_disc, 4),
                "p_vs_zero": round(p_disc_onesided, 4),
            },
            "oos": {
                "n": len(oos_rets),
                "mean": round(oos_mean, 4),
                "median": round(oos_median, 4),
                "std": round(oos_std, 4),
                "pos_rate": round(oos_pos_rate, 1),
                "abnormal_vs_avg_day": round(oos_abnormal, 4),
                "p_vs_non_fomc": round(p_oos, 4),
                "p_vs_zero": round(p_oos_onesided, 4),
            },
        },
        "pre_fomc_day_returns": {
            "discovery": {
                "n": len(pre_fomc_disc_rets),
                "mean": round(pre_disc_mean, 4),
                "abnormal_vs_avg_day": round(pre_disc_abnormal, 4),
                "p_vs_non_fomc": round(p_pre_disc, 4),
            },
            "oos": {
                "n": len(pre_fomc_oos_rets),
                "mean": round(pre_oos_mean, 4),
                "abnormal_vs_avg_day": round(pre_oos_abnormal, 4),
                "p_vs_non_fomc": round(p_pre_oos, 4),
            },
        },
        "non_fomc_day_returns": {
            "discovery_mean": round(non_fomc_disc_mean, 4),
            "oos_mean": round(non_fomc_oos_mean, 4),
        },
        "success_criteria": {
            "abnormal_gt_0.1pct": disc_abnormal > 0.1,
            "direction_gt_55pct": disc_pos_rate > 55,
            "p_lt_0.05": p_disc < 0.05,
            "oos_same_sign": (oos_mean > 0) == (disc_mean > 0) if oos_rets else False,
        },
    }

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
