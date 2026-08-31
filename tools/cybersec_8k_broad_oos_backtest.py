#!/usr/bin/env python3
"""Cybersecurity 8-K Item 1.05 short — BROAD >=$500M reformulation, strict next-day-open, OOS split.

Reformulates the sole surviving desk thesis as a single tradeable unit:
  universe = all first-time Item 1.05 filers with current mkt cap >= $500M
  entry    = OPEN of the first trading day STRICTLY AFTER the filing date
             (event_timing='after_hours' => look-ahead-free)
  hold     = 3 and 5 trading days, short, SPY-benchmarked abnormal return
Discovery (Dec 2023 - Dec 2024) vs OOS (Jan 2025 - Aug 2026).
"""
import json, sys, os
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.cybersecurity_8k_scanner import search_item_105
import market_data
from scipy import stats as st

START, END, SPLIT = "2023-12-18", "2026-08-31", datetime(2025, 1, 1)
CAP_FLOOR_M = 500.0

cache = json.load(open("data/ticker_cache/market_cap_cache.json"))
raw = search_item_105(START, END)
evs = [e for e in raw if e.get("ticker") and e.get("file_date")]

# First-time filer: earliest filing per ticker
first = {}
for e in sorted(evs, key=lambda x: x["file_date"]):
    first.setdefault(e["ticker"], e)
first_evs = list(first.values())
print(f"raw={len(raw)} with_ticker={len(evs)} first_time_filers={len(first_evs)}", file=sys.stderr)

# Cap filter
kept, unknown = [], []
for e in first_evs:
    cap = cache.get(e["ticker"])
    if cap is None:
        unknown.append(e["ticker"])
    elif cap >= CAP_FLOOR_M:
        kept.append(e)
print(f">=${CAP_FLOOR_M:.0f}M largecap dedup N={len(kept)} | uncached={len(unknown)}: {unknown}", file=sys.stderr)


def backtest(events, label):
    ed = [{"symbol": e["ticker"], "date": e["file_date"]} for e in events]
    if not ed:
        return {"N": 0, "label": label}
    r = market_data.measure_event_impact(
        event_dates=ed, entry_price="open", benchmark="SPY",
        event_timing="after_hours",  # entry = OPEN of first day STRICTLY AFTER filing
    )
    imp = r.get("individual_impacts", []) or []
    out = {"label": label, "N": r.get("events_measured", 0)}
    for h in ["1d", "3d", "5d", "10d"]:
        vals = [i.get(f"abnormal_{h}") for i in imp if i.get(f"abnormal_{h}") is not None]
        p = 1.0
        if len(vals) >= 3:
            _, p = st.ttest_1samp(vals, 0.0)
        mean = sum(vals) / len(vals) if vals else 0.0
        neg = sum(1 for v in vals if v < 0) / len(vals) if vals else 0.0
        out[h] = {"n": len(vals), "mean_pct": round(mean, 2),
                  "neg_rate": round(neg, 3), "p": round(p, 4)}
    return out


disc = [e for e in kept if datetime.strptime(e["file_date"], "%Y-%m-%d") < SPLIT]
oos = [e for e in kept if datetime.strptime(e["file_date"], "%Y-%m-%d") >= SPLIT]
print(f"discovery N={len(disc)}  oos N={len(oos)}", file=sys.stderr)

result = {
    "params": {"cap_floor_m": CAP_FLOOR_M, "convention": "next_day_open_after_hours",
               "split": SPLIT.strftime("%Y-%m-%d")},
    "full": backtest(kept, "full"),
    "discovery": backtest(disc, "discovery"),
    "oos": backtest(oos, "oos"),
    "oos_tickers": [(e["ticker"], e["file_date"]) for e in oos],
}
print(json.dumps(result, indent=2, default=str))
