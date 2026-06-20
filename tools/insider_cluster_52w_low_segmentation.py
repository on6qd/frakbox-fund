#!/usr/bin/env python3
"""Segment historical insider-cluster events by 52-week drawdown AND market-cap tier.

Rebuilds and extends the lost 2026-06-19 analysis. Adds two things the original
flagged as open caveats:
  1. MARKET-CAP SPLIT — isolates whether the deep-drawdown (<=-50% from 52w high)
     outperformance survives in large caps (FISV is a $25.5B large cap). Large caps
     essentially never delist to zero inside the 5-day forward window, so a large-cap
     deep-drawdown effect is survivorship-bias-free by construction.
  2. SURVIVORSHIP DIAGNOSTIC — the tickers whose market-cap fetch FAILS are the
     delisted / reused-ticker names (the survivorship dropouts). We report their
     drawdown distribution to measure the direction of the bias directly.

Forward returns (abnormal_5d) are precomputed in data/clusters_with_roles_full.csv.
Usage: python3 tools/insider_cluster_52w_low_segmentation.py
"""
import sys, os, json, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yfinance as yf
from tools.yfinance_utils import safe_download

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "clusters_with_roles_full.csv")

DD_BUCKETS = [
    ("le_minus_50pct_near_52w_low", -1.01, -0.50),
    ("minus50_to_minus30",          -0.50, -0.30),
    ("minus30_to_minus15",          -0.30, -0.15),
    ("minus15_to_minus5",           -0.15, -0.05),
    ("near_highs_gt_minus5",        -0.05,  1.00),
]

CAP_TIERS = [
    ("micro_lt_300M",   0,       300e6),
    ("small_300M_2B",   300e6,   2e9),
    ("mid_2B_10B",      2e9,     10e9),
    ("large_gt_10B",    10e9,    1e15),
]


def bucket(val, buckets):
    for name, lo, hi in buckets:
        if lo <= val < hi:
            return name
    return None


def stats(arr):
    a = np.asarray(arr, dtype=float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return dict(n=0, mean=None, median=None, pos_rate=None)
    return dict(n=int(len(a)), mean=round(float(a.mean()), 2),
                median=round(float(np.median(a)), 2),
                pos_rate=round(float((a > 0).mean() * 100), 1))


def main():
    df = pd.read_csv(CSV, parse_dates=["cluster_date"])
    df = df.dropna(subset=["abnormal_5d"]).copy()
    tickers = sorted(df["ticker"].unique())
    print(f"Events: {len(df)} | unique tickers: {len(tickers)}", flush=True)

    # ---- 1. trailing price history -> 52w drawdown ----
    hist = {}
    CH = 80
    for i in range(0, len(tickers), CH):
        chunk = tickers[i:i+CH]
        try:
            d = safe_download(chunk, start="2020-01-01", end="2026-01-15",
                              progress=False, auto_adjust=True)
        except Exception as e:
            print(f"  chunk {i} fail: {e}", flush=True)
            continue
        # safe_download flattens to 'Close_<TICKER>' columns (single ticker -> 'Close')
        if len(chunk) == 1 and "Close" in d.columns:
            s = d["Close"].dropna()
            if len(s) > 60:
                hist[chunk[0]] = s
        else:
            for t in chunk:
                col = f"Close_{t}"
                if col in d.columns:
                    s = d[col].dropna()
                    if len(s) > 60:
                        hist[t] = s
        print(f"  history {i+len(chunk)}/{len(tickers)} resolved={len(hist)}", flush=True)

    def drawdown(row):
        s = hist.get(row["ticker"])
        if s is None:
            return np.nan
        cd = pd.Timestamp(row["cluster_date"])
        window = s[s.index <= cd]
        if len(window) < 60:
            return np.nan
        trailing = window.iloc[-252:]
        cur = window.iloc[-1]
        peak = trailing.max()
        if peak <= 0:
            return np.nan
        return cur / peak - 1.0

    df["dd"] = df.apply(drawdown, axis=1)
    resolved_dd = df["dd"].notna().sum()
    print(f"52w drawdown resolved: {resolved_dd}/{len(df)} "
          f"(dropped {len(df)-resolved_dd} = survivorship suspects)", flush=True)

    # ---- 2. market cap per ticker (current proxy via fast_info) ----
    caps = {}
    fail_cap = []
    for j, t in enumerate(tickers):
        try:
            fi = yf.Ticker(t).fast_info
            mc = getattr(fi, "market_cap", None)
            if mc and mc > 0:
                caps[t] = float(mc)
            else:
                fail_cap.append(t)
        except Exception:
            fail_cap.append(t)
        if (j + 1) % 100 == 0:
            print(f"  caps {j+1}/{len(tickers)} ok={len(caps)} fail={len(fail_cap)}", flush=True)
    print(f"Market caps resolved: {len(caps)}/{len(tickers)} (fail={len(fail_cap)})", flush=True)

    df["cap"] = df["ticker"].map(caps)
    df["cap_tier"] = df["cap"].apply(lambda c: bucket(c, CAP_TIERS) if pd.notna(c) else None)
    df["dd_bucket"] = df["dd"].apply(lambda v: bucket(v, DD_BUCKETS) if pd.notna(v) else None)

    out = {}

    # baseline DD buckets (replicates prior finding)
    out["dd_buckets_all"] = {b: stats(df[df.dd_bucket == b]["abnormal_5d"])
                             for b, _, _ in DD_BUCKETS}

    # SURVIVORSHIP DIAGNOSTIC: drawdown of cap-fail (delisted) vs cap-ok names
    fail_set = set(fail_cap)
    df["cap_failed"] = df["ticker"].isin(fail_set)
    out["survivorship_diagnostic"] = {
        "cap_fetch_failed_events": int(df["cap_failed"].sum()),
        "dd_dist_of_cap_failed": stats(df[df.cap_failed]["dd"]),
        "dd_dist_of_cap_ok": stats(df[~df.cap_failed]["dd"]),
        "abn5d_of_cap_failed": stats(df[df.cap_failed]["abnormal_5d"]),
        "abn5d_of_cap_ok": stats(df[~df.cap_failed]["abnormal_5d"]),
        "frac_cap_failed_in_deepDD": round(float(
            (df[df.dd_bucket == "le_minus_50pct_near_52w_low"]["cap_failed"]).mean() * 100), 1)
            if (df.dd_bucket == "le_minus_50pct_near_52w_low").any() else None,
        "frac_cap_failed_in_shallow": round(float(
            (df[df.dd_bucket == "minus15_to_minus5"]["cap_failed"]).mean() * 100), 1)
            if (df.dd_bucket == "minus15_to_minus5").any() else None,
    }

    # CAP x DD cross-tab on abnormal_5d  (the decisive table)
    crosstab = {}
    for ct, _, _ in CAP_TIERS:
        row = {}
        for db, _, _ in DD_BUCKETS:
            sub = df[(df.cap_tier == ct) & (df.dd_bucket == db)]
            row[db] = stats(sub["abnormal_5d"])
        # deep-DD vs rest within this cap tier
        deep = df[(df.cap_tier == ct) & (df.dd_bucket == "le_minus_50pct_near_52w_low")]["abnormal_5d"]
        rest = df[(df.cap_tier == ct) & (df.dd_bucket.notna()) &
                  (df.dd_bucket != "le_minus_50pct_near_52w_low")]["abnormal_5d"]
        row["_deepDD_stats"] = stats(deep)
        row["_rest_stats"] = stats(rest)
        crosstab[ct] = row
    out["cap_x_dd"] = crosstab

    # large+mid combined deep-DD (FISV-relevant, survivorship-robust)
    bigcap = df[df.cap.notna() & (df.cap >= 2e9)]
    out["bigcap_2B_plus"] = {
        "deepDD_le_minus50": stats(bigcap[bigcap.dd_bucket == "le_minus_50pct_near_52w_low"]["abnormal_5d"]),
        "shallow_or_up_gt_minus30": stats(bigcap[bigcap.dd.notna() & (bigcap.dd > -0.30)]["abnormal_5d"]),
        "all_resolved": stats(bigcap[bigcap.dd.notna()]["abnormal_5d"]),
    }
    largecap = df[df.cap.notna() & (df.cap >= 10e9)]
    out["largecap_10B_plus"] = {
        "deepDD_le_minus50": stats(largecap[largecap.dd_bucket == "le_minus_50pct_near_52w_low"]["abnormal_5d"]),
        "le_minus30": stats(largecap[largecap.dd.notna() & (largecap.dd <= -0.30)]["abnormal_5d"]),
        "all_resolved": stats(largecap[largecap.dd.notna()]["abnormal_5d"]),
    }

    print(json.dumps(out, indent=2))
    with open("/tmp/insider_dd_capsplit.json", "w") as f:
        json.dump(out, f, indent=2)
    df.to_csv("/tmp/insider_dd_capsplit_events.csv", index=False)
    print("\nSaved /tmp/insider_dd_capsplit.json and events csv")


if __name__ == "__main__":
    main()
