#!/usr/bin/env python3
"""NT 10-K short: strict next-day-open convention audit.

Motivated by starboard_13d_null_under_strict_next_open_convention_2026_08_24 +
event_signal_next_day_open_convention_canonical_rule_2026_08_24.

The NT 10-K validation (incl. the 2026-08 moderate-drawdown refresh) called
measure_event_impact(entry_price="open") with DEFAULT event_timing. With default
timing + entry_price="open", the entry is the FILING-DAY open (post_dates[0] ==
event_date), which double-counts the filing-day move a daily scanner cannot
capture. The scanner-executable convention is entry = the open the trading day
AFTER the filing (measure_event_impact enters T+1 open only when
event_timing="after_hours").

This script rebuilds the SAME first-time large-cap NT 10-K universe and its
moderate-drawdown subgroup, then recomputes 3/5/10d abnormal returns under BOTH:
  B_filing_day_open  (current/artifact): default timing,     entry_price="open"
  A_next_day_open    (strict tradeable): timing="after_hours", entry_price="open"
"""
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.nt_10k_first_vs_repeat import fetch_nt10k_range, classify
from tools.nt_10k_pre_event_drawdown import get_pre_event_return, bucket_by_pre_return
import market_data

try:
    import scipy.stats as sps
except ImportError:
    sps = None

START = "2022-01-01"
END = "2026-08-25"
LOOKBACK = 30


def backtest(events, timing):
    """events: list of {'ticker','file_date'}. timing: None (filing-day open) or 'after_hours' (T+1 open)."""
    if not events:
        return {"N": 0}
    bt = []
    for e in events:
        d = {"symbol": e["ticker"], "date": e["file_date"], "entry_price": "open"}
        if timing:
            d["timing"] = timing
        bt.append(d)
    res = market_data.measure_event_impact(
        event_dates=bt, benchmark="SPY", entry_price="open",
        event_timing=timing, check_factors=False, check_seasonal=False,
    )
    impacts = res.get("individual_impacts", []) or []
    out = {"N_measured": res.get("events_measured", 0)}
    for h in (3, 5, 10):
        vals = [i.get(f"abnormal_{h}d") for i in impacts if i.get(f"abnormal_{h}d") is not None]
        if not vals:
            out[f"{h}d"] = {"n": 0}
            continue
        n = len(vals)
        mean = sum(vals) / n
        neg = sum(1 for v in vals if v < 0) / n
        p = None
        if sps and n >= 6:
            try:
                p = float(sps.wilcoxon(vals).pvalue)
            except Exception:
                p = None
        pt = None
        if sps and n >= 3:
            try:
                pt = float(sps.ttest_1samp(vals, 0).pvalue)
            except Exception:
                pt = None
        out[f"{h}d"] = {"n": n, "mean_pct": round(mean, 3),
                        "median_pct": round(sorted(vals)[n // 2], 3),
                        "neg_rate": round(neg, 3),
                        "p_wilcoxon": round(p, 4) if p is not None else None,
                        "p_ttest": round(pt, 4) if pt is not None else None}
    return out


def main():
    print(f"Fetching NT 10-K {START} -> {END}", file=sys.stderr)
    filings = fetch_nt10k_range(START, END)
    print(f"  Raw (dedup): {len(filings)}", file=sys.stderr)

    with open("data/ticker_cache/market_cap_cache.json") as cf:
        cap_cache = json.load(cf)

    first, _ = classify(filings, lookback_days=730)
    print(f"  First-time: {len(first)}", file=sys.stderr)

    # large-cap only (>=500M)
    large = [e for e in first if (cap_cache.get(e["ticker"]) or 0) >= 500]
    print(f"  Large-cap first-time: {len(large)}", file=sys.stderr)

    # pre-event return + bucket
    for i, ev in enumerate(large):
        if i % 25 == 0:
            print(f"    pre-return {i}/{len(large)}", file=sys.stderr)
        r = get_pre_event_return(ev["ticker"], ev["file_date"], LOOKBACK)
        ev["pre_return_pct"] = r
        ev["pre_bucket"] = bucket_by_pre_return(r)

    moderate = [e for e in large if e.get("pre_bucket") == "moderate_drawdown_neg20_to_neg5"]
    print(f"  Moderate-drawdown subgroup: {len(moderate)}", file=sys.stderr)

    SPLIT = datetime.strptime("2024-07-01", "%Y-%m-%d")

    def disc(evs):
        return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") < SPLIT]

    def oos(evs):
        return [e for e in evs if datetime.strptime(e["file_date"], "%Y-%m-%d") >= SPLIT]

    subgroups = {
        "ALL_large_firsttime": large,
        "moderate_drawdown_large_firsttime": moderate,
    }

    result = {"params": {"start": START, "end": END, "lookback": LOOKBACK, "split": "2024-07-01"},
              "counts": {k: len(v) for k, v in subgroups.items()},
              "audit": {}}

    for name, evs in subgroups.items():
        print(f"  Backtesting {name} (N={len(evs)}) under both conventions...", file=sys.stderr)
        result["audit"][name] = {
            "full_B_filing_day_open_ARTIFACT": backtest(evs, None),
            "full_A_next_day_open_TRADEABLE": backtest(evs, "after_hours"),
            "discovery_A_next_day_open": backtest(disc(evs), "after_hours"),
            "oos_A_next_day_open": backtest(oos(evs), "after_hours"),
            "oos_B_filing_day_open": backtest(oos(evs), None),
        }

    # Per-event detail for the moderate subgroup under strict convention (10d)
    bt = [{"symbol": e["ticker"], "date": e["file_date"], "entry_price": "open", "timing": "after_hours"}
          for e in moderate]
    res = market_data.measure_event_impact(event_dates=bt, benchmark="SPY", entry_price="open",
                                           event_timing="after_hours", check_factors=False, check_seasonal=False)
    rows = []
    for i in res.get("individual_impacts", []) or []:
        rows.append({"symbol": i.get("symbol"), "date": i.get("event_date"),
                     "abn_10d": i.get("abnormal_10d"), "abn_3d": i.get("abnormal_3d")})
    result["moderate_per_event_strict"] = sorted(rows, key=lambda r: r["date"])

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
