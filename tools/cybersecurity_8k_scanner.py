#!/usr/bin/env python3
"""8-K Item 1.05 (Material Cybersecurity Incidents) scanner and backtester.

Since December 18, 2023, public companies must file an 8-K within 4 business
days of determining a cybersecurity incident is "material." This is a NEW
disclosure requirement — the market may not yet be efficient at pricing these.

Hypothesis:
  Large-cap companies filing an 8-K with Item 1.05 produce negative abnormal
  returns of -2% to -5% over 3-10 days after the filing date.

Causal mechanism:
  1. Actors: Companies suffering data breaches, ransomware, or system compromises
  2. Transmission: Direct costs (remediation, legal, regulatory fines) +
     reputational damage + customer churn + potential future litigation
  3. Market inefficiency: New disclosure requirement (Dec 2023), incident severity
     often unclear at initial filing, follow-up disclosures reveal worse-than-expected
     damage (progressive revelation bias)

Usage:
    # Historical scan
    python tools/cybersecurity_8k_scanner.py --start 2023-12-18 --end 2026-04-13

    # Recent monitoring
    python tools/cybersecurity_8k_scanner.py --days 30

    # Full backtest with abnormal return measurement
    python tools/cybersecurity_8k_scanner.py --backtest --start 2023-12-18 --end 2025-12-31

    # JSON events for data_tasks.py
    python tools/cybersecurity_8k_scanner.py --start 2023-12-18 --end 2026-04-13 --json-events
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, '/Users/frakbox/Bots/financial_researcher')

import requests

from tools.edgar_efts import efts_get_json, EFTSFetchError

try:
    import yfinance as yf
except ImportError:
    yf = None

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
SEC_DELAY = 0.15
MIN_MARKET_CAP = 500_000_000  # $500M
EFTS_PAGE_SIZE = 100


def search_item_105(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings containing Item 1.05."""
    q = '%22Item+1.05%22'
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits = []
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    try:
        data = efts_get_json(url, headers=HEADERS, label="cybersec-8k")
    except EFTSFetchError as e:
        print(f"EFTS error (data unavailable, NOT a clean scan): {e}", file=sys.stderr)
        raise

    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(f"  8-K Item 1.05: {total} total filings found ({start_date} to {end_date})", file=sys.stderr)

    fetched = len(hits)
    max_to_fetch = min(total, 10000)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        try:
            page_data = efts_get_json(url, headers=HEADERS, label="cybersec-8k-page")
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

        # Confirm Item 1.05 is present in items list
        has_105 = any("1.05" in str(it) for it in items)
        if items and not has_105:
            continue

        cik = ciks[0].lstrip("0") if ciks else ""
        dedup_key = (cik, file_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Extract ticker from display_name
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
            else:
                print(f"  Filtered out {tick}: market cap ${mcap/1e6:.0f}M < $500M", file=sys.stderr)
        except Exception as ex:
            print(f"  Error checking {tick}: {ex}", file=sys.stderr)

        if (i + 1) % 10 == 0:
            print(f"  Market cap check: {i+1}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.2)

    return filtered


def run_backtest(events: list[dict]) -> dict:
    """Run abnormal return backtest on cybersecurity incident events."""
    import market_data
    import db
    from tools.yfinance_utils import safe_download

    db.init_db()

    # Build event list for measure_event_impact
    event_dates = []
    for e in events:
        if e.get("ticker") and e.get("file_date"):
            event_dates.append({
                "symbol": e["ticker"],
                "date": e["file_date"]
            })

    if not event_dates:
        print("No events to backtest", file=sys.stderr)
        return {}

    print(f"\nMeasuring abnormal returns for {len(event_dates)} events...", file=sys.stderr)

    result = market_data.measure_event_impact(
        event_dates=event_dates,
        entry_price="open",
        benchmark="SPY",
    )

    # Print results
    print(f"\nEvents measured: {result.get('n_events', 0)}")
    print(f"\n--- ABNORMAL RETURN RESULTS ---")
    print(f"{'Horizon':<12} {'Avg Abn Return':>16} {'Dir% (short)':>14} {'p-value':>10}")
    print("-" * 52)

    for h_key in ['1d', '3d', '5d', '10d']:
        h_data = result.get(h_key, {})
        avg = h_data.get('abnormal_mean', 0)
        neg_rate = h_data.get('negative_rate', 0)
        p = h_data.get('p_value', 1.0)
        print(f"{h_key:<12} {avg:>+14.3f}% {neg_rate:>13.1f}% {p:>10.4f}")

    # OOS split: discovery = first 60% of events by date, validation = last 40%
    sorted_events = sorted(event_dates, key=lambda e: e["date"])
    split_idx = int(len(sorted_events) * 0.6)
    discovery = sorted_events[:split_idx]
    validation = sorted_events[split_idx:]

    print(f"\n--- OUT-OF-SAMPLE ANALYSIS ---")
    if len(discovery) >= 5:
        disc_result = market_data.measure_event_impact(
            event_dates=discovery, entry_price="open",
            benchmark="SPY",
        )
        for h_key in ['1d', '3d', '5d', '10d']:
            h_data = disc_result.get(h_key, {})
            avg = h_data.get('abnormal_mean', 0)
            neg_rate = h_data.get('negative_rate', 0)
            print(f"  DISCOVERY n={disc_result.get('n_events',0)} | {h_key}: avg={avg:+.2f}% neg_rate={neg_rate:.1f}%")

    if len(validation) >= 3:
        val_result = market_data.measure_event_impact(
            event_dates=validation, entry_price="open",
            benchmark="SPY",
        )
        for h_key in ['1d', '3d', '5d', '10d']:
            h_data = val_result.get(h_key, {})
            avg = h_data.get('abnormal_mean', 0)
            neg_rate = h_data.get('negative_rate', 0)
            print(f"  VALIDATION n={val_result.get('n_events',0)} | {h_key}: avg={avg:+.2f}% neg_rate={neg_rate:.1f}%")

    # Assessment
    passes_mt = result.get('passes_multiple_testing', False)
    print(f"\nPasses multiple testing correction: {passes_mt}")

    # Record result
    best_horizon = None
    best_abs = 0
    for h_key in ['1d', '3d', '5d', '10d']:
        h_data = result.get(h_key, {})
        avg = abs(h_data.get('abnormal_mean', 0))
        if avg > best_abs:
            best_abs = avg
            best_horizon = h_key

    h_data = result.get(best_horizon, {})
    assessment = {
        "status": "",
        "hypothesis_class": "event",
        "expected_direction": "short",
        "universe": f"EDGAR 8-K Item 1.05, large-cap >$500M, {events[0]['file_date']} to {events[-1]['file_date']}",
        "n_events": result.get('n_events', 0),
        "best_horizon": best_horizon,
        "avg_abnormal": h_data.get('abnormal_mean', 0),
        "p_value": h_data.get('p_value', 1.0),
        "neg_rate": h_data.get('negative_rate', 0),
        "passes_mt": passes_mt,
        "discovery_n": len(discovery),
        "validation_n": len(validation),
        "sample_events": [f"{e['ticker']} {e['file_date']}" for e in events[:10]],
        "full_result": result,
    }

    # Evaluate
    neg_rate = h_data.get('negative_rate', 0)
    p = h_data.get('p_value', 1.0)
    avg_abn = h_data.get('abnormal_mean', 0)

    checks = {
        "n_sufficient": result.get('n_events', 0) >= 20,
        "passes_mt": passes_mt,
        "direction_correct": neg_rate > 50,
        "abnormal_above_threshold": abs(avg_abn) > 0.5,
        "return_after_costs": abs(avg_abn) > 0.416,
    }

    failed = [k for k, v in checks.items() if not v]

    if not failed:
        assessment["status"] = "VALIDATED"
    elif len(failed) <= 2 and checks["direction_correct"] and checks["n_sufficient"]:
        assessment["status"] = "PRELIMINARY_NEEDS_MORE_DATA"
    else:
        assessment["status"] = f"DEAD_END"

    # Record
    from research import record_known_effect, record_dead_end
    if "DEAD_END" in assessment["status"]:
        record_dead_end(
            "cybersecurity_8k_item_105_short",
            f"Signal failed check(s): {', '.join(failed)}. n={result.get('n_events',0)}, "
            f"best_horizon={best_horizon}, avg_abnormal={avg_abn:.3f}%, "
            f"p={p:.4f}, neg_rate={neg_rate:.1f}%, passes_mt={passes_mt}."
        )

    record_known_effect(
        "cybersecurity_8k_item_105_short",
        assessment
    )

    return assessment


def main():
    parser = argparse.ArgumentParser(description="Scan EDGAR for 8-K Item 1.05 cybersecurity incidents")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Look back N days from today")
    parser.add_argument("--json-events", action="store_true", help="Output as JSON events")
    parser.add_argument("--backtest", action="store_true", help="Run full backtest with abnormal returns")
    parser.add_argument("--no-filter", action="store_true", help="Skip market cap filter")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    if args.days:
        start = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        end = today
    elif args.start:
        start = args.start
        end = args.end or today
    else:
        start = "2023-12-18"  # Item 1.05 effective date
        end = today

    # Search EDGAR
    events = search_item_105(start, end)
    print(f"\nRaw events found: {len(events)}", file=sys.stderr)

    # Filter to those with tickers
    events = [e for e in events if e.get("ticker")]
    print(f"Events with tickers: {len(events)}", file=sys.stderr)

    # Filter to large-cap
    if not args.no_filter and events:
        events = filter_largecap(events)
        print(f"Large-cap events (>$500M): {len(events)}", file=sys.stderr)

    # Dedup: keep only FIRST filing per ticker (initial disclosure is the signal)
    # Sort by date first to ensure we get the earliest
    events.sort(key=lambda e: e["file_date"])
    seen_tickers = set()
    deduped = []
    followups = []
    for e in events:
        if e["ticker"] not in seen_tickers:
            seen_tickers.add(e["ticker"])
            deduped.append(e)
        else:
            followups.append(e)
    if followups:
        print(f"  Removed {len(followups)} follow-up filings (keeping first per ticker)", file=sys.stderr)
    events = deduped

    print(f"\nFinal events: {len(events)}")
    for e in events:
        mcap_str = f" (${e.get('market_cap',0)/1e9:.1f}B)" if e.get('market_cap') else ""
        print(f"  {e['ticker']} {e['file_date']}{mcap_str}: {e['display_name'][:60]}")

    if args.json_events:
        json_events = [{"symbol": e["ticker"], "date": e["file_date"]} for e in events]
        print(json.dumps(json_events))

    if args.backtest and events:
        print("\n" + "=" * 70)
        print("RUNNING BACKTEST")
        print("=" * 70)
        result = run_backtest(events)
        print(f"\nStatus: {result.get('status', 'N/A')}")

    return events


if __name__ == "__main__":
    main()
