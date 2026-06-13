#!/usr/bin/env python3
"""8-K Item 5.02 (new CEO appointment) scanner and backtester.

Item 5.02 covers "Departure of Directors or Certain Officers; Election of
Directors; Appointment of Certain Officers". This scanner targets the
APPOINTMENT subset: a company naming a NEW chief executive.

Hypothesis (exploratory, LONG bias):
  A large-cap company filing an 8-K announcing the appointment of a new CEO
  produces positive abnormal returns over the following 1-10 days, driven by
  turnaround / fresh-strategy optimism, analyst re-rating, and "new broom"
  expectations. Direction is measured empirically (could be short if the
  market reads the change as instability/distress).

Causal mechanism:
  1. Actors: boards installing a new chief executive (planned succession,
     forced turnaround, or outsider hire).
  2. Transmission: leadership change reshapes strategy, capital allocation,
     guidance; outsider hires especially carry restructuring optionality.
  3. Market inefficiency tested: does the multi-day drift after the filing
     carry tradeable alpha, or is the leadership change priced same-day?

Distinct from existing signals:
  - ceo_sudden_departure_short / ceo_performance_failure_departure: the
    DEPARTURE side (negative). This tests the APPOINTMENT side (positive bias).
  - Merger/take-private family (defm14a_*, merger_arbitrage_*): all DEAD; this
    is NOT a deal-premium/arb mechanism.

Usage:
    # Historical scan + full backtest (discovery window)
    python tools/ceo_appointment_8k_scanner.py --start 2021-01-01 --end 2024-12-31 --backtest

    # Out-of-sample window
    python tools/ceo_appointment_8k_scanner.py --start 2025-01-01 --end 2026-06-13 --backtest

    # Recent monitoring / JSON events for data_tasks.py
    python tools/ceo_appointment_8k_scanner.py --days 30 --json-events
"""
import argparse
import json
import re
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.edgar_efts import efts_get_json, EFTSFetchError

try:
    import yfinance as yf
except ImportError:
    yf = None

HEADERS = {"User-Agent": "frakbox-research bart.de.lepeleer@gmail.com"}
SEC_DELAY = 0.15
MIN_MARKET_CAP = 500_000_000  # $500M
EFTS_PAGE_SIZE = 100
# High-precision phrase: captures genuine appointment language, filters out
# pure-departure and director-only Item 5.02 filings.
SEARCH_PHRASE = '"appointed as Chief Executive Officer"'


def search_ceo_appointments(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings announcing a new CEO appointment."""
    import urllib.parse
    q = urllib.parse.quote(SEARCH_PHRASE)
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits = []
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    try:
        data = efts_get_json(url, headers=HEADERS, label="ceo-appt-8k")
    except EFTSFetchError as e:
        print(f"EFTS error (data unavailable, NOT a clean scan): {e}", file=sys.stderr)
        raise

    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(f"  8-K CEO appointment: {total} total filings ({start_date} to {end_date})", file=sys.stderr)

    fetched = len(hits)
    max_to_fetch = min(total, 10000)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        try:
            page_data = efts_get_json(url, headers=HEADERS, label="ceo-appt-8k-page")
        except EFTSFetchError as e:
            print(f"  Pagination error at offset {fetched}: {e}", file=sys.stderr)
            break
        page_hits = page_data.get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)

    results = []
    seen = set()  # Dedup by (cik, file_date)
    for h in all_hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])

        # Confirm Item 5.02 is present in the items list (when populated)
        has_502 = any("5.02" in str(it) for it in items)
        if items and not has_502:
            continue

        cik = ciks[0].lstrip("0") if ciks else ""
        dedup_key = (cik, file_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        ticker = None
        if names:
            m = re.search(r'\(([A-Z]{1,5})\)', names[0])
            if m:
                ticker = m.group(1)

        results.append({
            "cik": cik,
            "display_name": names[0] if names else "",
            "ticker": ticker,
            "file_date": file_date,
            "items": items,
            "accession": h.get("_id", ""),
        })

    return results


def filter_largecap(events: list[dict]) -> list[dict]:
    """Filter to large-cap stocks (>$500M market cap)."""
    if yf is None:
        print("yfinance not available, skipping market cap filter", file=sys.stderr)
        return [e for e in events if e.get("ticker")]

    filtered = []
    tickers = list(set(e["ticker"] for e in events if e.get("ticker")))

    for i, tick in enumerate(tickers):
        try:
            info = yf.Ticker(tick).info
            mcap = info.get("marketCap", 0) or 0
            if mcap >= MIN_MARKET_CAP:
                for e in events:
                    if e.get("ticker") == tick:
                        e["market_cap"] = mcap
                        filtered.append(e)
        except Exception as ex:
            print(f"  Error checking {tick}: {ex}", file=sys.stderr)
        if (i + 1) % 25 == 0:
            print(f"  Market cap check: {i+1}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.2)

    return filtered


def _summarize(result: dict) -> dict:
    """Compact per-horizon summary from a measure_event_impact result.

    market_data.measure_event_impact returns FLAT keys (avg_abnormal_5d,
    positive_rate_abnormal_5d, p_value_abnormal_5d, events_measured). Read those
    directly — the older result["5d"]["abnormal_mean"] nested schema is stale.
    """
    s = {
        "events_measured": result.get("events_measured", 0),
        "passes_mt": result.get("passes_multiple_testing", False),
    }
    for h in ["1d", "3d", "5d", "10d", "20d"]:
        if f"avg_abnormal_{h}" in result:
            s[h] = {
                "avg": result.get(f"avg_abnormal_{h}"),
                "median": result.get(f"median_abnormal_{h}"),
                "pos_rate": result.get(f"positive_rate_abnormal_{h}"),
                "p_value": result.get(f"p_value_abnormal_{h}"),
            }
    return s


def run_backtest(events: list[dict]) -> dict:
    """Run abnormal return backtest on CEO appointment events (LONG bias)."""
    import market_data
    import db

    db.init_db()

    event_dates = [
        {"symbol": e["ticker"], "date": e["file_date"]}
        for e in events if e.get("ticker") and e.get("file_date")
    ]
    if not event_dates:
        print("No events to backtest", file=sys.stderr)
        return {}

    print(f"\nMeasuring abnormal returns for {len(event_dates)} events...", file=sys.stderr)
    result = market_data.measure_event_impact(
        event_dates=event_dates, entry_price="open", benchmark="SPY",
    )
    summ = _summarize(result)

    print(f"\nEvents measured: {summ['events_measured']}")
    print(f"\n--- ABNORMAL RETURN RESULTS (LONG bias) ---")
    print(f"{'Horizon':<10} {'Avg Abn':>12} {'Median':>10} {'Pos%':>8} {'p-value':>10}")
    print("-" * 54)
    for h_key in ['1d', '3d', '5d', '10d', '20d']:
        h = summ.get(h_key)
        if not h:
            continue
        print(f"{h_key:<10} {h['avg']:>+10.3f}% {h['median']:>+8.2f}% "
              f"{h['pos_rate']:>7.1f}% {h['p_value']:>10.4f}")

    # OOS split: discovery = first 60% by date, validation = last 40%
    sorted_events = sorted(event_dates, key=lambda e: e["date"])
    split_idx = int(len(sorted_events) * 0.6)
    discovery, validation = sorted_events[:split_idx], sorted_events[split_idx:]

    print(f"\n--- IN-SAMPLE OOS SPLIT ---")
    for label, subset in [("DISCOVERY", discovery), ("VALIDATION", validation)]:
        if len(subset) >= 3:
            rs = _summarize(market_data.measure_event_impact(
                event_dates=subset, entry_price="open", benchmark="SPY"))
            for h_key in ['3d', '5d', '10d', '20d']:
                h = rs.get(h_key)
                if not h:
                    continue
                print(f"  {label} n={rs['events_measured']} | {h_key}: "
                      f"avg={h['avg']:+.2f}% median={h['median']:+.2f}% "
                      f"pos_rate={h['pos_rate']:.1f}% p={h['p_value']:.3f}")

    passes_mt = summ["passes_mt"]
    print(f"\nPasses multiple testing correction: {passes_mt}")

    # Pick best horizon by |abnormal mean|
    best_horizon, best_abs = None, 0
    for h_key in ['1d', '3d', '5d', '10d', '20d']:
        h = summ.get(h_key)
        if h and abs(h['avg']) > best_abs:
            best_abs, best_horizon = abs(h['avg']), h_key

    h = summ.get(best_horizon, {})
    avg_abn = h.get('avg', 0)
    pos_rate = h.get('pos_rate', 0)
    p = h.get('p_value', 1.0)

    n_meas = summ['events_measured']
    checks = {
        "n_sufficient": n_meas >= 20,
        "passes_mt": passes_mt,
        "direction_long": pos_rate > 50,
        "abnormal_above_threshold": abs(avg_abn) > 0.5,
        "return_after_costs": abs(avg_abn) > 0.416,
    }
    failed = [k for k, v in checks.items() if not v]
    if not failed:
        status = "VALIDATED"
    elif len(failed) <= 2 and checks["direction_long"] and checks["n_sufficient"]:
        status = "PRELIMINARY_NEEDS_MORE_DATA"
    else:
        status = "DEAD_END"

    assessment = {
        "status": status,
        "hypothesis_class": "event",
        "expected_direction": "long",
        "universe": f"EDGAR 8-K Item 5.02 CEO appointment, large-cap >$500M, "
                    f"{events[0]['file_date']} to {events[-1]['file_date']}",
        "n_events": n_meas,
        "best_horizon": best_horizon,
        "avg_abnormal": avg_abn,
        "p_value": p,
        "pos_rate": pos_rate,
        "passes_mt": passes_mt,
        "discovery_n": len(discovery),
        "validation_n": len(validation),
        "failed_checks": failed,
        "sample_events": [f"{e['ticker']} {e['file_date']}" for e in events[:10]],
        "full_result": result,
    }
    return assessment


def main():
    parser = argparse.ArgumentParser(description="Scan EDGAR for 8-K Item 5.02 CEO appointments")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--days", type=int)
    parser.add_argument("--json-events", action="store_true")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--no-filter", action="store_true")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    if args.days:
        start = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        end = today
    elif args.start:
        start, end = args.start, args.end or today
    else:
        start, end = "2021-01-01", today

    events = search_ceo_appointments(start, end)
    print(f"\nRaw events found: {len(events)}", file=sys.stderr)
    events = [e for e in events if e.get("ticker")]
    print(f"Events with tickers: {len(events)}", file=sys.stderr)

    if not args.no_filter and events:
        events = filter_largecap(events)
        print(f"Large-cap events (>$500M): {len(events)}", file=sys.stderr)

    # Keep only FIRST appointment filing per ticker
    events.sort(key=lambda e: e["file_date"])
    seen, deduped = set(), []
    for e in events:
        if e["ticker"] not in seen:
            seen.add(e["ticker"])
            deduped.append(e)
    events = deduped

    print(f"\nFinal events: {len(events)}")
    for e in events:
        mcap = f" (${e.get('market_cap',0)/1e9:.1f}B)" if e.get('market_cap') else ""
        print(f"  {e['ticker']} {e['file_date']}{mcap}: {e['display_name'][:55]}")

    if args.json_events:
        print(json.dumps([{"symbol": e["ticker"], "date": e["file_date"]} for e in events]))

    if args.backtest and events:
        print("\n" + "=" * 60 + "\nRUNNING BACKTEST\n" + "=" * 60)
        result = run_backtest(events)
        print(f"\nStatus: {result.get('status', 'N/A')}  failed={result.get('failed_checks')}")

    return events


if __name__ == "__main__":
    main()
