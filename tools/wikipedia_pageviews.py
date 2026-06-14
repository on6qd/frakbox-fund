#!/usr/bin/env python3
"""Wikipedia daily-pageviews fetcher + public-attention signal analysis.

Free Wikimedia REST API (no auth). Used to test whether abnormal public-attention
spikes (pageview z-scores) predict stock returns.

Large-cap universe was tested 2026-06-13 and was a DEAD END
(public_attention_proxy_largecap_non_tradeable_rule) — the effect, if any, should
live in SMALL/MID-cap names (limits to arbitrage). This module re-runs the same
methodology on a retail-heavy small/mid-cap universe benchmarked to IWM.

Usage:
    python3 tools/wikipedia_pageviews.py analyze \
        --start 2018-01-01 --end 2026-06-01 --oos-start 2024-01-01 --z 3.0
    python3 tools/wikipedia_pageviews.py fetch --article GameStop \
        --start 2024-01-01 --end 2024-02-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

import numpy as np
import pandas as pd

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download

WIKI_UA = "frakbox-research/1.0 (causal markets research; contact research@example.com)"
WIKI_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/all-agents/{article}/daily/{start}/{end}"
)

# Retail-heavy small/mid-cap universe: ticker -> en.wikipedia article title.
# Chosen for (a) plausible retail attention, (b) higher limits to arbitrage than
# mega-caps. Titles that 404 are skipped automatically at fetch time.
UNIVERSE = {
    "GME": "GameStop",
    "AMC": "AMC_Theatres",
    "KOSS": "Koss_Corporation",
    "BB": "BlackBerry_Limited",
    "SOFI": "SoFi",
    "CLOV": "Clover_Health",
    "SPCE": "Virgin_Galactic",
    "DKNG": "DraftKings",
    "RKT": "Rocket_Companies",
    "OPEN": "Opendoor",
    "BYND": "Beyond_Meat",
    "PTON": "Peloton_(company)",
    "CHWY": "Chewy_(company)",
    "CVNA": "Carvana",
    "AFRM": "Affirm_(company)",
    "UPST": "Upstart_(company)",
    "HOOD": "Robinhood_Markets",
    "LCID": "Lucid_Group",
    "RIVN": "Rivian",
    "NKLA": "Nikola_Corporation",
    "FUBO": "FuboTV",
    "DNA": "Ginkgo_Bioworks",
    "MARA": "Marathon_Digital_Holdings",
    "TLRY": "Tilray",
    "CGC": "Canopy_Growth",
    "ACB": "Aurora_Cannabis",
    "PLUG": "Plug_Power",
    "FCEL": "FuelCell_Energy",
    "WKHS": "Workhorse_Group",
    "RBLX": "Roblox_Corporation",
    "DASH": "DoorDash",
    "ABNB": "Airbnb",
    "COIN": "Coinbase",
    "PLTR": "Palantir_Technologies",
    "SNAP": "Snap_Inc.",
    "PINS": "Pinterest",
    "ROKU": "Roku,_Inc.",
}

BENCHMARK = "IWM"  # Russell 2000 small-cap ETF


def fetch_pageviews(article: str, start: str, end: str) -> pd.Series | None:
    """Return daily pageviews as a Series indexed by date, or None on 404/error."""
    s = start.replace("-", "")
    e = end.replace("-", "")
    url = WIKI_URL.format(article=urllib.parse.quote(article, safe=""), start=s, end=e)
    req = urllib.request.Request(url, headers={"User-Agent": WIKI_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None
        raise
    items = data.get("items", [])
    if not items:
        return None
    idx = [datetime.strptime(it["timestamp"][:8], "%Y%m%d") for it in items]
    vals = [it["views"] for it in items]
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name=article)


def build_spike_observations(start, end, z_thresh, baseline_win=60):
    """For each ticker, fetch pageviews + prices, find pageview z-spikes, and
    record forward abnormal returns vs benchmark. Lookahead-safe: a spike on
    calendar day t is only known after t closes (UTC), so entry is at the NEXT
    trading day's OPEN, exit at close of t+h."""
    horizons = [1, 5, 10]
    # Benchmark prices
    bench = safe_download(BENCHMARK, start=start, end=end, progress=False, auto_adjust=True)
    bench_open = bench["Open"]
    bench_close = bench["Close"]

    rows = []
    coverage = []
    for ticker, article in UNIVERSE.items():
        pv = fetch_pageviews(article, start, end)
        time.sleep(0.05)
        if pv is None or len(pv) < 200:
            coverage.append((ticker, article, "no_wiki", 0))
            continue
        try:
            px = safe_download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        except Exception:
            coverage.append((ticker, article, "no_price", 0))
            continue
        if px is None or len(px) < 200 or "Open" not in px:
            coverage.append((ticker, article, "no_price", 0))
            continue
        opens, closes = px["Open"], px["Close"]
        trading_days = closes.index

        # Align pageviews to trading days (sum views since previous trading day so
        # weekend attention is attributed to the next session — but keep simple:
        # reindex to trading days using last available daily value).
        pv_daily = pv.reindex(trading_days).ffill(limit=3)
        # rolling baseline z-score (trailing, shifted to exclude current day)
        roll_mean = pv_daily.rolling(baseline_win, min_periods=30).mean().shift(1)
        roll_std = pv_daily.rolling(baseline_win, min_periods=30).std().shift(1)
        z = (pv_daily - roll_mean) / roll_std

        spike_days = z[(z > z_thresh)].index
        n_spike = 0
        for t in spike_days:
            # entry: next trading day's open
            loc = trading_days.get_indexer([t])[0]
            if loc < 0 or loc + 1 >= len(trading_days):
                continue
            entry_i = loc + 1
            entry_day = trading_days[entry_i]
            entry_px = opens.get(entry_day)
            entry_bench = bench_open.get(entry_day)
            if entry_px is None or entry_bench is None or np.isnan(entry_px) or np.isnan(entry_bench):
                continue
            rec = {"ticker": ticker, "spike_date": t, "entry_date": entry_day, "z": float(z.get(t))}
            ok = False
            for h in horizons:
                exit_i = entry_i + h - 1
                if exit_i >= len(trading_days):
                    rec[f"ar_{h}"] = np.nan
                    continue
                exit_day = trading_days[exit_i]
                ex_px = closes.get(exit_day)
                ex_bench = bench_close.get(exit_day)
                if ex_px is None or ex_bench is None or np.isnan(ex_px) or np.isnan(ex_bench):
                    rec[f"ar_{h}"] = np.nan
                    continue
                stock_ret = ex_px / entry_px - 1.0
                bench_ret = ex_bench / entry_bench - 1.0
                rec[f"ar_{h}"] = (stock_ret - bench_ret) * 100.0  # abnormal %, vs IWM
                ok = True
            if ok:
                rows.append(rec)
                n_spike += 1
        coverage.append((ticker, article, "ok", n_spike))
    return pd.DataFrame(rows), coverage, horizons


def _ttest_summary(arr):
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    if n < 5:
        return {"n": n, "mean": None, "t": None, "p": None, "dir_neg": None}
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    se = sd / np.sqrt(n)
    t = mean / se if se > 0 else 0.0
    # two-sided p via survival of |t| under normal approx (n large enough)
    from scipy import stats
    p = float(2 * stats.t.sf(abs(t), df=n - 1))
    return {
        "n": n,
        "mean": round(mean, 4),
        "t": round(t, 3),
        "p": round(p, 5),
        "pct_negative": round(float(np.mean(arr < 0)) * 100, 1),
    }


def analyze(start, end, oos_start, z_thresh):
    df, coverage, horizons = build_spike_observations(start, end, z_thresh)
    valid = [c for c in coverage if c[2] == "ok"]
    print("=== COVERAGE ===")
    for tkr, art, status, n in coverage:
        if status != "ok":
            print(f"  SKIP {tkr:5s} ({art}): {status}")
    print(f"  Valid tickers: {len(valid)} / {len(UNIVERSE)} | total spike-obs: {len(df)}")

    if df.empty:
        print("No observations.")
        return

    df["spike_date"] = pd.to_datetime(df["spike_date"])
    is_mask = df["spike_date"] < pd.Timestamp(oos_start)
    oos_mask = ~is_mask

    print(f"\n=== ATTENTION-SPIKE -> ABNORMAL RETURN vs {BENCHMARK} (z>{z_thresh}) ===")
    print(f"Sample {start}..{end} | OOS split {oos_start} | "
          f"IS n={int(is_mask.sum())} OOS n={int(oos_mask.sum())}")
    print("Convention: spike day t -> enter OPEN t+1 -> exit CLOSE t+h (lookahead-safe)")
    print("Barber-Odean predicts NEGATIVE abnormal return (attention-overreaction reversal).\n")

    for h in horizons:
        col = f"ar_{h}"
        full = _ttest_summary(df[col].values)
        iss = _ttest_summary(df.loc[is_mask, col].values)
        oos = _ttest_summary(df.loc[oos_mask, col].values)
        print(f"  h={h}d  FULL: mean={full['mean']}% t={full['t']} p={full['p']} "
              f"n={full['n']} neg%={full.get('pct_negative')}")
        print(f"        IS:  mean={iss['mean']}% t={iss['t']} p={iss['p']} n={iss['n']}")
        print(f"        OOS: mean={oos['mean']}% t={oos['t']} p={oos['p']} n={oos['n']}")
        # sign stability
        if iss["mean"] is not None and oos["mean"] is not None:
            flip = (iss["mean"] > 0) != (oos["mean"] > 0)
            print(f"        sign_flip_IS_to_OOS={flip}")
        print()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--article", required=True)
    f.add_argument("--start", required=True)
    f.add_argument("--end", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--start", default="2018-01-01")
    a.add_argument("--end", default="2026-06-01")
    a.add_argument("--oos-start", default="2024-01-01")
    a.add_argument("--z", type=float, default=3.0)
    args = ap.parse_args()

    if args.cmd == "fetch":
        s = fetch_pageviews(args.article, args.start, args.end)
        if s is None:
            print("404 / no data")
        else:
            print(s.to_string())
    elif args.cmd == "analyze":
        analyze(args.start, args.end, args.oos_start, args.z)


if __name__ == "__main__":
    main()
