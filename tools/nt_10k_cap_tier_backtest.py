#!/usr/bin/env python3
"""NT 10-K first-time filer backtest, bucketed by market-cap tier.

Purpose: test whether the validated large-cap (>$500M) NT 10-K short signal
extends to mid-cap (~$250M-$500M) and/or smaller tiers. If it does, the
signal frequency roughly doubles.

Uses current market cap (cache-only, fast) as a proxy for historical cap.
This is the same approach used for the validated large-cap finding.

Usage:
  python3 tools/nt_10k_cap_tier_backtest.py --start 2022-01-01 --end 2025-12-31
"""
import argparse
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.nt_filing_scanner import search_nt_filings, deduplicate_events
from tools.nt_10k_first_vs_repeat import fetch_nt10k_range, classify, run_backtest


def bucket_by_cap(events: list[dict], cache: dict, bucket_edges_m: list[float]) -> dict:
    """Bucket events by current market cap (millions).

    bucket_edges_m: e.g. [0, 100, 250, 500, 2000, 10000, float('inf')]
    """
    buckets = {}
    for i in range(len(bucket_edges_m) - 1):
        lo, hi = bucket_edges_m[i], bucket_edges_m[i+1]
        label = f"${lo:.0f}M-${hi:.0f}M" if hi != float('inf') else f">${lo:.0f}M"
        buckets[label] = []
    buckets["UNCACHED"] = []

    for ev in events:
        t = ev["ticker"]
        cap = cache.get(t)
        if cap is None:
            buckets["UNCACHED"].append(ev)
            continue
        for i in range(len(bucket_edges_m) - 1):
            lo, hi = bucket_edges_m[i], bucket_edges_m[i+1]
            if lo <= cap < hi:
                label = f"${lo:.0f}M-${hi:.0f}M" if hi != float('inf') else f">${lo:.0f}M"
                buckets[label].append(ev)
                break
    return buckets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--lookback", type=int, default=730)
    ap.add_argument("--split-date", default="2024-07-01",
                    help="Split discovery/OOS at this date (YYYY-MM-DD)")
    ap.add_argument("--min-first-time", type=int, default=15,
                    help="Only run backtest for buckets with >= this many first-time filers")
    args = ap.parse_args()

    print(f"Fetching NT 10-K filings {args.start} -> {args.end}", file=sys.stderr)
    filings = fetch_nt10k_range(args.start, args.end)
    print(f"  Raw + dedup: {len(filings)}", file=sys.stderr)

    # Load cap cache
    with open("data/ticker_cache/market_cap_cache.json") as cf:
        cache = json.load(cf)

    # Classify first-time vs repeat (globally, across all events)
    first, rep = classify(filings, lookback_days=args.lookback)
    print(f"  First-time: {len(first)} | Repeat: {len(rep)}", file=sys.stderr)

    # Bucket the first-time filers by cap tier
    edges = [0, 100, 250, 500, 2000, 10000, float('inf')]
    buckets_ft = bucket_by_cap(first, cache, edges)

    print(f"\n  First-time filer bucket counts:", file=sys.stderr)
    for label, evs in buckets_ft.items():
        print(f"    {label}: {len(evs)}", file=sys.stderr)

    # Backtest each bucket (first-time only) with enough sample
    split = datetime.strptime(args.split_date, "%Y-%m-%d")
    def before(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") < split]
    def after(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") >= split]

    results = {
        "params": vars(args),
        "edges_m": [e if e != float('inf') else 'inf' for e in edges],
        "bucket_counts_first_time": {k: len(v) for k, v in buckets_ft.items()},
        "bucket_counts_first_time_discovery": {k: len(before(v)) for k, v in buckets_ft.items()},
        "bucket_counts_first_time_oos": {k: len(after(v)) for k, v in buckets_ft.items()},
        "backtests": {},
    }

    for label, evs in buckets_ft.items():
        if label == "UNCACHED":
            continue
        if len(evs) < args.min_first_time:
            print(f"  SKIP bucket {label}: only {len(evs)} events (< {args.min_first_time})", file=sys.stderr)
            continue
        print(f"  Backtesting bucket {label} (N={len(evs)})...", file=sys.stderr)
        results["backtests"][label] = {
            "full": run_backtest(evs, f"ft_{label}_full"),
            "discovery": run_backtest(before(evs), f"ft_{label}_discovery"),
            "oos": run_backtest(after(evs), f"ft_{label}_oos"),
        }

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
