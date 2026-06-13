#!/usr/bin/env python3
"""NT 10-K first-time vs repeat filer subgroup analysis.

Splits NT 10-K filings into two subgroups based on the SAME ticker having
a prior NT 10-K filing within `lookback_days` before the current filing:
  - first-time: no prior NT 10-K within lookback window
  - repeat: at least one prior NT 10-K within lookback window

Computes abnormal returns at 1d/3d/5d/10d for each subgroup.

Hypothesis (from nt_10k_late_filing_short validation):
  Signal may be concentrated in first-time filers — repeat filers may already
  have the news priced in (market knows they file late every year).

Usage:
  python3 tools/nt_10k_first_vs_repeat.py --start 2020-01-01 --end 2025-12-31 --largecap
"""
import argparse
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.nt_filing_scanner import search_nt_filings, deduplicate_events
from tools.largecap_filter import filter_to_largecap as lc_filter
import market_data


def fetch_nt10k_range(start: str, end: str) -> list[dict]:
    """Fetch NT 10-K filings in range, chunking by quarter (EFTS 1000 cap)."""
    from datetime import timedelta
    out = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while cur < end_dt:
        chunk_end = min(cur + timedelta(days=90), end_dt)
        s = cur.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")
        res = search_nt_filings(s, e, "NT 10-K")
        out.extend(res)
        cur = chunk_end + timedelta(days=1)
    # Keep only with ticker
    out = [f for f in out if f.get("ticker")]
    # Dedup (30-day window per ticker)
    out = deduplicate_events(out)
    return out


def classify(events: list[dict], lookback_days: int = 730) -> tuple[list[dict], list[dict]]:
    events = sorted(events, key=lambda e: e["file_date"])
    seen: dict[str, list[datetime]] = {}
    first_time: list[dict] = []
    repeat: list[dict] = []
    for ev in events:
        t = ev["ticker"]
        d = datetime.strptime(ev["file_date"], "%Y-%m-%d")
        prior = seen.get(t, [])
        has_recent = any(0 < (d - p).days <= lookback_days for p in prior)
        (repeat if has_recent else first_time).append(ev)
        prior.append(d)
        seen[t] = prior
    return first_time, repeat


def run_backtest(events: list[dict], label: str) -> dict:
    if not events:
        return {"label": label, "N": 0}
    bt_events = [{"symbol": e["ticker"], "date": e["file_date"]} for e in events]
    res = market_data.measure_event_impact(
        event_dates=bt_events,
        benchmark="SPY",
        entry_price="open",
        check_factors=False,
        check_seasonal=False,
    )
    # Compute wilcoxon p-values from individual_impacts
    try:
        import scipy.stats as sps
    except ImportError:
        sps = None

    impacts = res.get("individual_impacts", []) or []
    out = {
        "label": label,
        "N_input": len(events),
        "N_measured": res.get("events_measured", 0),
        "N_failed": res.get("events_failed", 0),
    }
    for h in (1, 3, 5, 10):
        vals = [i.get(f"abnormal_{h}d") for i in impacts if i.get(f"abnormal_{h}d") is not None]
        if not vals:
            continue
        neg_rate = sum(1 for v in vals if v < 0) / len(vals)
        med = sorted(vals)[len(vals)//2] if vals else 0
        wp = None
        if sps and len(vals) >= 6:
            try:
                wp = float(sps.wilcoxon(vals).pvalue)
            except Exception:
                pass
        out[f"{h}d"] = {
            "n": len(vals),
            "mean": round(res.get(f"avg_abnormal_{h}d") or 0, 3),
            "median": round(med, 3),
            "neg_rate": round(neg_rate, 3),
            "wilcoxon_p": round(wp, 4) if wp is not None else None,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--lookback", type=int, default=730)
    ap.add_argument("--largecap", action="store_true", help="Filter to >500M market cap")
    ap.add_argument("--split-date", default=None, help="Optional: split discovery/OOS at this date (YYYY-MM-DD)")
    args = ap.parse_args()

    print(f"Fetching NT 10-K filings {args.start} -> {args.end}", file=sys.stderr)
    filings = fetch_nt10k_range(args.start, args.end)
    print(f"  Raw + dedup: {len(filings)}", file=sys.stderr)

    if args.largecap:
        # Fast cache-only filter: use existing market_cap_cache.json and
        # skip any uncached tickers. Avoids yfinance rate-limit hell.
        print("  Applying large-cap filter (cache-only, fast)...", file=sys.stderr)
        import json as _json
        with open("data/ticker_cache/market_cap_cache.json") as cf:
            mc_cache = _json.load(cf)
        unique_tickers = {f["ticker"] for f in filings}
        passing = {t for t in unique_tickers
                   if mc_cache.get(t) and mc_cache[t] >= 500}  # cap is in millions
        skipped_unknown = unique_tickers - passing - {t for t in unique_tickers if mc_cache.get(t)}
        filings = [f for f in filings if f["ticker"] in passing]
        print(f"  After large-cap (cache hit): {len(filings)} events, {len(passing)} unique tickers "
              f"(skipped {len(skipped_unknown)} uncached)", file=sys.stderr)

    first, rep = classify(filings, lookback_days=args.lookback)
    print(f"First-time: {len(first)} | Repeat: {len(rep)}", file=sys.stderr)

    result = {
        "params": {
            "start": args.start, "end": args.end,
            "lookback_days": args.lookback, "largecap": args.largecap,
        },
        "counts": {"first_time": len(first), "repeat": len(rep)},
        "first_time": run_backtest(first, "first_time"),
        "repeat": run_backtest(rep, "repeat"),
    }

    # Optional OOS split
    if args.split_date:
        split = datetime.strptime(args.split_date, "%Y-%m-%d")
        def before(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") < split]
        def atfter(evs): return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") >= split]
        result["split"] = {
            "first_time_discovery": run_backtest(before(first), "first_time_discovery"),
            "first_time_oos": run_backtest(atfter(first), "first_time_oos"),
            "repeat_discovery": run_backtest(before(rep), "repeat_discovery"),
            "repeat_oos": run_backtest(atfter(rep), "repeat_oos"),
        }

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
