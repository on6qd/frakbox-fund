#!/usr/bin/env python3
"""Cybersecurity 8-K Item 1.05 first-time-filer backtest, bucketed by market-cap tier.

Purpose: test whether the validated large-cap (>$500M) cybersecurity 8-K short
signal extends to mid-cap and smaller tiers. Analogous to the NT 10-K mid-cap
extension test that yielded a strong (DISCOVERY_STRONG_OOS_SMALL) finding.

Uses current market cap (cache-only, fast) as a proxy for historical cap.
Same approach used for the validated NT 10-K finding.

Usage:
  python3 tools/cybersec_8k_cap_tier_backtest.py --start 2023-12-18 --end 2026-04-15
"""
import argparse
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.cybersecurity_8k_scanner import search_item_105


def bucket_by_cap(events: list[dict], cache: dict, bucket_edges_m: list[float]) -> dict:
    buckets = {}
    for i in range(len(bucket_edges_m) - 1):
        lo, hi = bucket_edges_m[i], bucket_edges_m[i+1]
        label = f"${lo:.0f}M-${hi:.0f}M" if hi != float('inf') else f">${lo:.0f}M"
        buckets[label] = []
    buckets["UNCACHED"] = []

    for ev in events:
        t = ev.get("ticker")
        if not t:
            buckets["UNCACHED"].append(ev)
            continue
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


def run_backtest(events: list[dict], label: str) -> dict:
    """Run abnormal return backtest on a list of events. Returns compact summary."""
    if not events:
        return {"N": 0, "label": label}
    import market_data
    from scipy import stats as _stats
    event_dates = [{"symbol": e["ticker"], "date": e["file_date"]} for e in events
                   if e.get("ticker") and e.get("file_date")]
    if not event_dates:
        return {"N": 0, "label": label}
    result = market_data.measure_event_impact(
        event_dates=event_dates,
        entry_price="open",
        benchmark="SPY",
    )
    n = result.get("events_measured", 0)
    summary = {"N": n, "label": label}
    impacts = result.get("individual_impacts", []) or []
    for h_key in ['1d', '3d', '5d', '10d']:
        mean = result.get(f"avg_abnormal_{h_key}")
        median = result.get(f"median_abnormal_{h_key}")
        # neg_rate = 100 - positive_rate_abnormal
        pos = result.get(f"positive_rate_abnormal_{h_key}")
        neg_rate = (100.0 - pos) / 100.0 if pos is not None else 0.0
        # Compute one-sample t-test p-value from impacts
        p_value = 1.0
        try:
            vals = [imp.get(f"abnormal_{h_key}") for imp in impacts
                    if imp.get(f"abnormal_{h_key}") is not None]
            if len(vals) >= 3:
                tstat, p_value = _stats.ttest_1samp(vals, 0.0)
        except Exception:
            pass
        summary[h_key] = {
            "mean_pct": round(mean if mean is not None else 0, 2),
            "median_pct": round(median if median is not None else 0, 2),
            "neg_rate": round(neg_rate, 3),
            "p_value": round(p_value, 4),
        }
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-12-18")
    ap.add_argument("--end", default="2026-04-15")
    ap.add_argument("--split-date", default="2025-01-01",
                    help="Discovery/OOS cutoff (events on/after = OOS)")
    ap.add_argument("--min-events", type=int, default=10,
                    help="Skip buckets with fewer events")
    args = ap.parse_args()

    print(f"Fetching cybersecurity 8-K Item 1.05 filings {args.start} -> {args.end}", file=sys.stderr)
    raw_events = search_item_105(args.start, args.end)
    print(f"  Raw events: {len(raw_events)}", file=sys.stderr)

    # Keep only events with a ticker
    events = [e for e in raw_events if e.get("ticker")]
    print(f"  With ticker: {len(events)}", file=sys.stderr)

    # Load cap cache
    with open("data/ticker_cache/market_cap_cache.json") as cf:
        cache = json.load(cf)

    # Bucket by cap
    edges = [0, 100, 250, 500, 2000, 10000, float('inf')]
    buckets = bucket_by_cap(events, cache, edges)

    print(f"\n  Bucket counts:", file=sys.stderr)
    for label, evs in buckets.items():
        print(f"    {label}: {len(evs)}", file=sys.stderr)

    # Discovery / OOS split
    split = datetime.strptime(args.split_date, "%Y-%m-%d")
    def before(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") < split]
    def after(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") >= split]

    results = {
        "params": vars(args),
        "edges_m": [e if e != float('inf') else 'inf' for e in edges],
        "bucket_counts": {k: len(v) for k, v in buckets.items()},
        "bucket_counts_discovery": {k: len(before(v)) for k, v in buckets.items()},
        "bucket_counts_oos": {k: len(after(v)) for k, v in buckets.items()},
        "backtests": {},
    }

    for label, evs in buckets.items():
        if label == "UNCACHED":
            continue
        if len(evs) < args.min_events:
            print(f"  SKIP bucket {label}: only {len(evs)} events (< {args.min_events})", file=sys.stderr)
            continue
        print(f"\n  Backtesting bucket {label} (N={len(evs)})...", file=sys.stderr)
        results["backtests"][label] = {
            "full": run_backtest(evs, f"{label}_full"),
            "discovery": run_backtest(before(evs), f"{label}_discovery"),
            "oos": run_backtest(after(evs), f"{label}_oos"),
        }

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
