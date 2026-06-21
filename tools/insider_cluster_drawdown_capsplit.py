#!/usr/bin/env python3
"""
Insider-cluster drawdown × market-cap cross-tab.

Reproduces the cap-conditioning analysis behind the canonical rule
``deep_drawdown_insider_outperformance_microcap_only_survivorship_inflated_rule_2026_06_20``
and its 2026-06-21 20-day-drawdown confirmation (which drives the
DRAWDOWN_BOOST_MAX_CAP_M gate in insider_cluster_evaluator.py).

Finding: the prior-drawdown boost to insider-cluster 5-day abnormal returns is a
micro/small/mid-cap effect that vanishes (and slightly inverts) at large cap
(>=$10B). The original "deep drawdown = strongest bucket" result was measured on a
small/microcap-dominated, survivorship-inflated universe and must NOT be applied as
a tailwind to a large-cap cluster (e.g. FISV, $25.5B, -73% from 52w high).

Two drawdown dimensions are supported:
  --dim dd20   20-trading-day prior drawdown (peak-to-last) — the evaluator's 4c gate (default)
  --dim dd52w  distance from trailing-252d (52-week) high — the original discovery dimension

Inputs (all already in the repo's data/ dir, no network needed):
  data/clusters_with_roles_full.csv   1566 clusters 2021-2025 with abnormal_5d, roles
  data/ticker_prices_cache.pkl        {ticker: Close Series} daily 2021-2025
  data/ticker_cache/market_cap_cache.json  {ticker: market_cap_in_millions} (current cap proxy)

Caveat: market cap is the *current* yfinance cap used as a tier proxy; survivorship
applies to any ticker missing from the price cache (disproportionately delisted
deep-drawdown names — their exclusion inflates the deep-drawdown mean upward).

Usage:
  python3 tools/insider_cluster_drawdown_capsplit.py
  python3 tools/insider_cluster_drawdown_capsplit.py --dim dd52w --ceo-cfo-only
"""
import argparse
import json
import os
import pickle

import pandas as pd
from scipy import stats

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CLUSTERS = os.path.join(DATA, "clusters_with_roles_full.csv")
PRICES = os.path.join(DATA, "ticker_prices_cache.pkl")
CAPS = os.path.join(DATA, "ticker_cache", "market_cap_cache.json")


def cap_tier(c):
    if c is None or pd.isna(c):
        return "unknown"
    if c < 300:
        return "1_micro<300M"
    if c < 2000:
        return "2_small300M-2B"
    if c < 10000:
        return "3_mid2B-10B"
    return "4_large>=10B"


def compute_drawdown(series, cluster_date, dim):
    """Drawdown in % at cluster_date. dd20 = 20-day peak-to-last; dd52w = vs 252d high."""
    s = series[series.index <= cluster_date]
    if len(s) < 10:
        return None
    if dim == "dd20":
        w = s.iloc[-20:]
    else:  # dd52w
        w = s.iloc[-252:]
    peak = w.max()
    cur = w.iloc[-1]
    return (cur - peak) / peak * 100 if peak > 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dim", choices=["dd20", "dd52w"], default="dd20")
    ap.add_argument("--deep-threshold", type=float, default=-10.0,
                    help="drawdown %% at/below which a cluster is 'deep' (default -10)")
    ap.add_argument("--ceo-cfo-only", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(CLUSTERS, parse_dates=["cluster_date"])
    prices = pickle.load(open(PRICES, "rb"))
    caps = json.load(open(CAPS))

    if args.ceo_cfo_only and "has_c_suite" in df.columns:
        df = df[df["has_c_suite"] == True]  # noqa: E712

    def dd(row):
        s = prices.get(row["ticker"])
        return None if s is None else compute_drawdown(s, row["cluster_date"], args.dim)

    df["drawdown"] = df.apply(dd, axis=1)
    df["cap"] = df["ticker"].map(caps)
    n_total = len(df)
    df = df.dropna(subset=["drawdown", "abnormal_5d", "cap"])
    df["tier"] = df["cap"].map(cap_tier)
    df["deep"] = df["drawdown"] <= args.deep_threshold

    print(f"dim={args.dim} deep<={args.deep_threshold}% ceo_cfo_only={args.ceo_cfo_only}")
    print(f"resolved {len(df)}/{n_total} events (rest missing price/cap = survivorship)\n")

    print("=== deep-vs-shallow abnormal_5d by cap tier ===")
    for t in sorted(df["tier"].unique()):
        sub = df[df["tier"] == t]
        dp, sh = sub[sub["deep"]], sub[~sub["deep"]]
        line = f"{t:16s} n={len(sub):4d} | deep n={len(dp):3d} mean={dp.abnormal_5d.mean():6.2f} pos={(dp.abnormal_5d>0).mean()*100:5.1f}%"
        line += f" | shallow n={len(sh):3d} mean={sh.abnormal_5d.mean():6.2f} pos={(sh.abnormal_5d>0).mean()*100:5.1f}%"
        if len(dp) > 5 and len(sh) > 5:
            t_, p_ = stats.ttest_ind(dp.abnormal_5d, sh.abnormal_5d, equal_var=False)
            line += f" | Welch t={t_:.2f} p={p_:.3f}"
        print(line)


if __name__ == "__main__":
    main()
