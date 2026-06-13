#!/usr/bin/env python3
"""Targeted NT 10-K mid-cap (250M-500M and 500M-2000M) OOS extension.

Skips the slow micro-cap bucket. Uses the same pipeline but only backtests
the buckets we actually care about.
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.nt_filing_scanner import search_nt_filings, deduplicate_events  # noqa
from tools.nt_10k_first_vs_repeat import fetch_nt10k_range, classify, run_backtest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-04-18")
    ap.add_argument("--lookback", type=int, default=730)
    ap.add_argument("--split-date", default="2026-01-01")
    args = ap.parse_args()

    from datetime import datetime
    split = datetime.strptime(args.split_date, "%Y-%m-%d")

    print(f"Fetching NT 10-K filings {args.start} -> {args.end}", file=sys.stderr)
    filings = fetch_nt10k_range(args.start, args.end)
    print(f"  Raw + dedup: {len(filings)}", file=sys.stderr)

    with open("data/ticker_cache/market_cap_cache.json") as cf:
        cache = json.load(cf)

    first, rep = classify(filings, lookback_days=args.lookback)
    print(f"  First-time: {len(first)} | Repeat: {len(rep)}", file=sys.stderr)

    # Only keep buckets we care about
    buckets = {
        "$250M-$500M": [],
        "$500M-$2000M": [],
        "$2000M-$10000M": [],
        ">$10000M": [],
    }
    uncached = 0
    for ev in first:
        cap = cache.get(ev["ticker"])
        if cap is None:
            uncached += 1
            continue
        if 250 <= cap < 500:
            buckets["$250M-$500M"].append(ev)
        elif 500 <= cap < 2000:
            buckets["$500M-$2000M"].append(ev)
        elif 2000 <= cap < 10000:
            buckets["$2000M-$10000M"].append(ev)
        elif cap >= 10000:
            buckets[">$10000M"].append(ev)

    def before(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") < split]
    def after(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") >= split]

    print(f"  Uncached: {uncached}", file=sys.stderr)
    for k, v in buckets.items():
        print(f"  {k}: total={len(v)}  disc={len(before(v))}  oos={len(after(v))}", file=sys.stderr)

    results = {
        "params": vars(args),
        "bucket_counts": {k: {"total": len(v), "discovery": len(before(v)), "oos": len(after(v))} for k, v in buckets.items()},
        "backtests": {},
    }

    for label, evs in buckets.items():
        if not evs:
            continue
        print(f"  Backtesting {label} N={len(evs)}...", file=sys.stderr)
        results["backtests"][label] = {
            "full": run_backtest(evs, f"ft_{label}_full"),
            "discovery": run_backtest(before(evs), f"ft_{label}_discovery"),
            "oos": run_backtest(after(evs), f"ft_{label}_oos"),
        }

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
