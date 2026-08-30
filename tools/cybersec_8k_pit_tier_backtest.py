#!/usr/bin/env python3
"""Cybersecurity 8-K Item 1.05 short — POINT-IN-TIME market-cap tier backtest.

Improves on cybersec_8k_cap_tier_backtest.py (which tiers by CURRENT market cap,
a survivorship-caveated proxy) in two ways:

  1. Point-in-time tiering: each event is bucketed by its market cap AT the filing
     date, approximated as
         pit_cap = current_cap * (close_at_filing / recent_close)
     a price-ratio proxy (yfinance get_shares_full is unavailable through the
     egress proxy). Assumes ~stable share count over the window — the dominant
     driver of cap change for these names is price, so the proxy recovers the
     survivorship cases (a stock now sub-$500M that was mid-cap at filing gets its
     filing-date price ratio applied back to a non-stale reference).

  2. Strict next-day-open (T+1 open) entry convention, per
     event_signal_next_day_open_convention_canonical_rule_2026_08_24 — the only
     scanner-executable convention for an intraday EDGAR filing.

Finding (2026-08-30, cybersec_8k_item_105_point_in_time_tiering_survives_midcap_underpowered):
  Pooled >=$500M dedup 3d -1.76% p=0.012 neg79% — signal is realizable and NOT a
  survivorship artifact. Mid-cap $2-10B remains directionally strongest
  (3d -2.12% neg89% N=9) but underpowered and no longer shows the survivorship-
  inflated -3.6% seen under current-cap tiering.

Usage:
  python3 tools/cybersec_8k_pit_tier_backtest.py --start 2023-12-18 --end 2026-08-30
"""
import argparse
import json
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats as _stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_data
from tools.cybersecurity_8k_scanner import search_item_105
from tools.yfinance_utils import safe_download, get_close_prices

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP_PATH = os.path.join(REPO, "data", "ticker_cache", "market_cap_cache.json")
# Tier edges in $M: sub-500M excluded from tradeable tiers.
EDGES = [("sub500M", 0, 500), ("low_0.5_2B", 500, 2000),
         ("mid_2_10B", 2000, 10000), ("mega_>10B", 10000, float("inf"))]
RECENT_START, RECENT_END = "2026-08-10", "2026-08-30"


def _first_px(ticker, start, end):
    try:
        df = get_close_prices(ticker, start, end)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    try:
        v = df.iloc[0, 0] if hasattr(df, "columns") else df.iloc[0]
        return float(v)
    except Exception:
        return None


def _last_px(ticker, start=RECENT_START, end=RECENT_END):
    try:
        df = get_close_prices(ticker, start, end)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    try:
        s = df.iloc[:, 0] if hasattr(df, "columns") else df
        return float(s.dropna().iloc[-1])
    except Exception:
        return None


def pit_cap(ticker, file_date, cap_cache):
    """Point-in-time market cap ($M) via price-ratio proxy. None if uncached."""
    cur = cap_cache.get(ticker)
    if not cur or cur <= 0:
        return None
    end6 = (pd.Timestamp(file_date) + timedelta(days=6)).strftime("%Y-%m-%d")
    fpx = _first_px(ticker, file_date, end6)
    lpx = _last_px(ticker)
    if fpx is None or lpx is None or lpx <= 0:
        return cur  # delisted / no recent price: no PIT adjustment
    return cur * (fpx / lpx)


def tier_of(cap):
    if cap is None:
        return "UNKNOWN"
    for name, lo, hi in EDGES:
        if lo <= cap < hi:
            return name
    return "UNKNOWN"


def _next_trading_day(d, cal):
    d = pd.Timestamp(d).normalize()
    idx = cal.searchsorted(d, side="right")
    return None if idx >= len(cal) else cal[idx].strftime("%Y-%m-%d")


def backtest(events, label):
    ed = [{"symbol": e["ticker"], "date": e["t1"]} for e in events if e.get("t1")]
    if len(ed) < 3:
        return {"N": len(ed), "label": label, "note": "too_few"}
    r = market_data.measure_event_impact(event_dates=ed, entry_price="open", benchmark="SPY")
    imp = r.get("individual_impacts", []) or []
    out = {"N": r.get("events_measured", 0), "label": label}
    for h in ["1d", "3d", "5d", "10d"]:
        vals = [i.get(f"abnormal_{h}") for i in imp if i.get(f"abnormal_{h}") is not None]
        if len(vals) >= 3:
            _, p = _stats.ttest_1samp(vals, 0.0)
            out[h] = {"mean": round(float(np.mean(vals)), 2),
                      "med": round(float(np.median(vals)), 2),
                      "neg": round(float(np.mean([v < 0 for v in vals])), 2),
                      "p": round(float(p), 4), "n": len(vals)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-12-18")
    ap.add_argument("--end", default="2026-08-30")
    ap.add_argument("--dedup", action="store_true",
                    help="keep only first filing per symbol (first-time-filer mechanism)")
    args = ap.parse_args()

    with open(CAP_PATH) as f:
        cap_cache = json.load(f)

    print(f"Fetching Item 1.05 filings {args.start}..{args.end}", file=sys.stderr)
    raw = search_item_105(args.start, args.end)
    evs = [{"ticker": e.get("ticker"), "file_date": e.get("file_date")}
           for e in raw if e.get("ticker") and e.get("file_date")]

    spy = safe_download("SPY", "2023-12-01", "2026-12-31")
    cal = pd.DatetimeIndex(sorted(set(pd.to_datetime(spy.index).normalize())))

    for e in evs:
        e["t1"] = _next_trading_day(e["file_date"], cal)
        e["cap_m"] = pit_cap(e["ticker"], e["file_date"], cap_cache)

    evs = [e for e in evs if e["t1"]]
    evs.sort(key=lambda x: x["file_date"])
    if args.dedup:
        seen, ded = set(), []
        for e in evs:
            if e["ticker"] in seen:
                continue
            seen.add(e["ticker"]); ded.append(e)
        evs = ded

    tiers = {}
    for e in evs:
        tiers.setdefault(tier_of(e["cap_m"]), []).append(e)

    out = {"params": vars(args), "convention": "strict_T+1_open",
           "tier_counts": {k: len(v) for k, v in tiers.items()}, "backtests": {}}
    for name in ["low_0.5_2B", "mid_2_10B", "mega_>10B"]:
        if len(tiers.get(name, [])) >= 3:
            out["backtests"][name] = backtest(tiers[name], name)
    pooled = [e for e in evs if e["cap_m"] and e["cap_m"] >= 500]
    out["backtests"]["pooled_ge500M"] = backtest(pooled, "pooled_ge500M")
    out["uncached_unknown"] = sorted({e["ticker"] for e in tiers.get("UNKNOWN", [])})
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
