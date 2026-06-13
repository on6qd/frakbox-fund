#!/usr/bin/env python3
"""NT 10-K pre-event drawdown analysis.

Hypothesis test: does the NT 10-K short signal differ by pre-event state?
  - Deep pre-drawdown (<-20%) — already broken, short may be exhausted
  - Moderate pre-drawdown (-20% to -5%) — distress worsens post-filing
  - Flat (-5% to +5%) — surprise signal, room to drop
  - Up (>+5%) — contrarian / clerical delay at healthy firm

Test design:
  - First-time NT 10-K filers 2022-01-01 to 2025-12-31
  - Split large-cap (>$500M) and mid-cap ($250M-$500M)
  - For each event: compute 30-day pre-event ticker RAW return (not abnormal,
    to identify distress regimes rather than factor exposure)
  - Bucket by pre-event return
  - Run backtest per bucket for 3d/5d/10d abnormal returns
  - OOS split at 2024-07-01 (same as discovery)

Success criterion: identify a pre-event bucket where the short signal is
stronger AND OOS-confirmed.
"""
import argparse
import json
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.nt_10k_first_vs_repeat import fetch_nt10k_range, classify
from tools.yfinance_utils import get_close_prices
import market_data


def get_pre_event_return(ticker: str, file_date: str, lookback_days: int = 30) -> float | None:
    """Raw total return of ticker in the N days preceding file_date (not including)."""
    end = datetime.strptime(file_date, "%Y-%m-%d")
    start = end - timedelta(days=lookback_days + 10)  # buffer for weekends
    try:
        prices = get_close_prices(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if prices is None or len(prices) < 2:
            return None
        # get_close_prices returns a DataFrame with ticker column
        if hasattr(prices, "columns") and len(prices.columns) > 0:
            series = prices.iloc[:, 0]
        else:
            series = prices
        # Strictly before file_date
        series = series[series.index < end]
        if len(series) < 2:
            return None
        first = float(series.iloc[0])
        last = float(series.iloc[-1])
        if first == 0 or first != first:  # NaN check
            return None
        return (last - first) / first * 100.0
    except Exception as exc:
        return None


def bucket_by_pre_return(ret_pct: float) -> str:
    if ret_pct is None:
        return "UNKNOWN"
    if ret_pct < -20:
        return "deep_drawdown_lt_neg20"
    if ret_pct < -5:
        return "moderate_drawdown_neg20_to_neg5"
    if ret_pct < 5:
        return "flat_neg5_to_5"
    return "up_gt_5"


def run_backtest_simple(events: list[dict], label: str) -> dict:
    if not events:
        return {"label": label, "N": 0}
    bt = [{"symbol": e["ticker"], "date": e["file_date"]} for e in events]
    try:
        res = market_data.measure_event_impact(
            event_dates=bt, benchmark="SPY", entry_price="open",
            check_factors=False, check_seasonal=False,
        )
    except Exception as exc:
        return {"label": label, "N": len(events), "error": str(exc)}

    impacts = res.get("individual_impacts", []) or []
    out = {"label": label, "N_input": len(events), "N_measured": res.get("events_measured", 0)}
    try:
        import scipy.stats as sps
    except ImportError:
        sps = None

    for h in (3, 5, 10):
        vals = [i.get(f"abnormal_{h}d") for i in impacts if i.get(f"abnormal_{h}d") is not None]
        if not vals:
            continue
        neg = sum(1 for v in vals if v < 0) / len(vals)
        wp = None
        if sps and len(vals) >= 6:
            try:
                wp = float(sps.wilcoxon(vals).pvalue)
            except Exception:
                pass
        out[f"{h}d"] = {
            "n": len(vals),
            "mean": round(res.get(f"avg_abnormal_{h}d") or 0, 3),
            "median": round(sorted(vals)[len(vals)//2], 3),
            "neg_rate": round(neg, 3),
            "p": round(wp, 4) if wp is not None else None,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--split-date", default="2024-07-01")
    ap.add_argument("--lookback-days", type=int, default=30)
    ap.add_argument("--cap-tier", choices=["large", "mid", "both"], default="both")
    args = ap.parse_args()

    print(f"Fetching NT 10-K {args.start} -> {args.end}", file=sys.stderr)
    filings = fetch_nt10k_range(args.start, args.end)
    print(f"  Raw (dedup): {len(filings)}", file=sys.stderr)

    # Load market cap cache
    with open("data/ticker_cache/market_cap_cache.json") as cf:
        cap_cache = json.load(cf)

    first, _ = classify(filings, lookback_days=730)
    print(f"  First-time: {len(first)}", file=sys.stderr)

    # Filter by cap tier
    def cap_bucket(t):
        m = cap_cache.get(t)
        if m is None:
            return None
        if m >= 500:
            return "large"
        if 250 <= m < 500:
            return "mid"
        return None

    first_with_tier = []
    for ev in first:
        tier = cap_bucket(ev["ticker"])
        if tier is None:
            continue
        if args.cap_tier != "both" and tier != args.cap_tier:
            continue
        ev["tier"] = tier
        first_with_tier.append(ev)

    print(f"  In-tier: {len(first_with_tier)}", file=sys.stderr)

    # Compute pre-event return for each
    print("  Computing pre-event returns...", file=sys.stderr)
    for i, ev in enumerate(first_with_tier):
        if i % 25 == 0:
            print(f"    {i}/{len(first_with_tier)}", file=sys.stderr)
        r = get_pre_event_return(ev["ticker"], ev["file_date"], args.lookback_days)
        ev["pre_return_pct"] = r
        ev["pre_bucket"] = bucket_by_pre_return(r)

    # Split by tier x pre-bucket
    split_dt = datetime.strptime(args.split_date, "%Y-%m-%d")

    def is_disc(ev):
        return datetime.strptime(ev["file_date"], "%Y-%m-%d") < split_dt

    tiers = ["large", "mid"] if args.cap_tier == "both" else [args.cap_tier]
    buckets = ["deep_drawdown_lt_neg20", "moderate_drawdown_neg20_to_neg5",
               "flat_neg5_to_5", "up_gt_5"]

    result = {
        "params": vars(args),
        "total_in_tier": len(first_with_tier),
        "unknown_pre_return": sum(1 for e in first_with_tier if e.get("pre_bucket") == "UNKNOWN"),
        "by_tier_bucket": {},
    }

    for tier in tiers:
        for bucket in buckets:
            evs = [e for e in first_with_tier
                   if e["tier"] == tier and e.get("pre_bucket") == bucket]
            disc = [e for e in evs if is_disc(e)]
            oos = [e for e in evs if not is_disc(e)]
            key = f"{tier}__{bucket}"
            result["by_tier_bucket"][key] = {
                "N_total": len(evs),
                "N_discovery": len(disc),
                "N_oos": len(oos),
                "discovery_stats": run_backtest_simple(disc, f"{key}_disc") if len(disc) >= 6 else {"N": len(disc), "note": "too_small"},
                "oos_stats": run_backtest_simple(oos, f"{key}_oos") if len(oos) >= 5 else {"N": len(oos), "note": "too_small"},
                "oos_tickers": [(e["ticker"], e["file_date"], round(e.get("pre_return_pct") or 0, 1)) for e in oos[:15]],
            }

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
