#!/usr/bin/env python3
"""
FDA Class I recall -> short backtest (NOVEL CATALYST, 0 prior coverage).

Hypothesis: A Class I FDA recall ("reasonable probability of serious adverse
health consequences or death") is a hard negative catalyst — product liability +
lost revenue. Short the publicly-traded manufacturer after the recall becomes
known and capture abnormal (SPY-adjusted) drift.

Data: openFDA drug + device enforcement endpoints (free, clean JSON).
Mapping: openFDA `recalling_firm` substrings -> US-listed tickers. Only
confident, currently-liquid mappings are used so SPY-adjustment is valid.

Event date choice:
  --date-field initiation : recall_initiation_date (when the firm started the
                            recall; press releases often coincide). Closest to
                            first public knowledge but can precede broad coverage.
  --date-field report     : report_date (when FDA published the enforcement
                            report). Unambiguously public, but lags initiation.

Events are clustered per ticker: recalls within --cluster-days of each other are
collapsed to a single event (first date) so overlapping windows don't double-count.

Usage:
  python3 tools/recall_short_backtest.py --date-field initiation --cluster-days 10
"""
import argparse
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_data

# openFDA recalling_firm substring (lowercase) -> US-listed ticker.
# Conservative: only confident mappings to currently-liquid common stock / clean ADRs.
FIRM_MAP = [
    ("baxter", "BAX"),
    ("pfizer", "PFE"),
    ("cardinal health", "CAH"),
    ("procter & gamble", "PG"),
    ("novo nordisk", "NVO"),
    ("medtronic", "MDT"),
    ("boston scientific", "BSX"),
    ("teleflex", "TFX"),
    ("arrow international", "TFX"),
    ("philips respironics", "PHG"),
    ("philips medical", "PHG"),
    ("edwards lifesciences", "EW"),
    ("abbott", "ABT"),
    ("ethicon", "JNJ"),
    ("lemaitre", "LMAT"),
    ("becton", "BDX"),
    ("stryker", "SYK"),
    ("fresenius medical", "FMS"),
    ("abiomed", "ABMD"),
]

ENDPOINTS = ["drug", "device"]


def fetch_recalls(endpoint, firm_query, date_field):
    """Return list of (yyyy-mm-dd) Class I recall dates for a firm substring."""
    field = "recall_initiation_date" if date_field == "initiation" else "report_date"
    # search firm name AND class I; pull up to 1000 records
    q = (
        f'https://api.fda.gov/{endpoint}/enforcement.json?'
        f'search=recalling_firm:"{urllib.parse.quote(firm_query)}"'
        f'+AND+classification:"Class+I"&limit=1000'
    )
    try:
        with urllib.request.urlopen(q, timeout=40) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # openFDA returns 404 when zero matches
            return []
        raise
    dates = []
    for rec in d.get("results", []):
        raw = rec.get(field)
        if not raw or len(raw) != 8:
            continue
        try:
            dt = datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            continue
        dates.append(dt)
    return dates


def cluster(dates, cluster_days):
    """Collapse dates within cluster_days of the previous kept date."""
    if not dates:
        return []
    dates = sorted(set(dates))
    kept = [dates[0]]
    for dt in dates[1:]:
        if (dt - kept[-1]).days > cluster_days:
            kept.append(dt)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date-field", choices=["initiation", "report"], default="initiation")
    ap.add_argument("--cluster-days", type=int, default=10)
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--oos-start", default="2021-01-01")
    ap.add_argument("--entry-price", default="open")
    ap.add_argument("--dump-events", action="store_true")
    args = ap.parse_args()

    # Build per-ticker date sets
    by_ticker = {}
    for sub, ticker in FIRM_MAP:
        all_dates = []
        for ep in ENDPOINTS:
            all_dates += fetch_recalls(ep, sub, args.date_field)
        by_ticker.setdefault(ticker, [])
        by_ticker[ticker] += all_dates

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    events = []
    per_ticker_counts = {}
    for ticker, dates in by_ticker.items():
        dates = [d for d in dates if d >= start_dt]
        clustered = cluster(dates, args.cluster_days)
        per_ticker_counts[ticker] = len(clustered)
        for d in clustered:
            events.append({"symbol": ticker, "date": d.strftime("%Y-%m-%d"),
                           "timing": "after_hours"})

    print(f"=== RECALL SHORT BACKTEST ({args.date_field} dates) ===")
    print(f"Total clustered events: {len(events)}")
    print("Per-ticker:", {k: v for k, v in sorted(per_ticker_counts.items(),
                                                   key=lambda x: -x[1]) if v})
    if args.dump_events:
        print(json.dumps(events, indent=2))

    def run(evts, label):
        if len(evts) < 3:
            print(f"\n[{label}] n={len(evts)} too few")
            return
        res = market_data.measure_event_impact(
            event_dates=evts, entry_price=args.entry_price,
            event_type="fda_class1_recall_short", check_factors=False,
        )
        n = res.get("events_measured")
        print(f"\n[{label}] n={n} (attempted {res.get('events_attempted')}, "
              f"failed {res.get('events_failed')})")
        for h in ["1d", "3d", "5d", "10d", "20d"]:
            avg = res.get(f"avg_abnormal_{h}")
            if avg is None:
                continue
            pos = res.get(f"positive_rate_abnormal_{h}")
            t = res.get(f"t_stat_abnormal_{h}")
            p = res.get(f"p_value_abnormal_{h}")
            w = res.get(f"wilcoxon_p_abnormal_{h}")
            # short direction-correct = abnormal return < 0
            short_dir = round(100 - pos, 1) if pos is not None else None
            print(f"  {h}: avg_abn={avg:+.2f}%  short_dir={short_dir}%  "
                  f"t={t}  p={p}  wilcoxon={w}")

    run(events, "ALL")
    oos_dt = datetime.strptime(args.oos_start, "%Y-%m-%d")
    is_ev = [e for e in events if datetime.strptime(e["date"], "%Y-%m-%d") < oos_dt]
    oos_ev = [e for e in events if datetime.strptime(e["date"], "%Y-%m-%d") >= oos_dt]
    run(is_ev, f"IN-SAMPLE <{args.oos_start}")
    run(oos_ev, f"OOS >={args.oos_start}")


if __name__ == "__main__":
    main()
