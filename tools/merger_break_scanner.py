#!/usr/bin/env python3
"""M&A Deal Break Scanner — detects merger/acquisition agreement terminations via EDGAR EFTS.

Unlike the generic Item 1.02 scanner (which catches ALL contract terminations),
this scanner specifically targets MERGER/ACQUISITION agreement terminations
by using full-text search for merger-specific language.

When a merger deal breaks, the target stock typically drops (loses acquisition premium).
The acquirer stock may rise (avoided overpaying).

Usage:
    # Historical scan (for backtesting)
    python tools/merger_break_scanner.py --start 2020-01-01 --end 2025-12-31

    # Recent monitoring
    python tools/merger_break_scanner.py --days 30

    # JSON events only (for data_tasks.py backtest)
    python tools/merger_break_scanner.py --start 2020-01-01 --end 2025-12-31 --json-events
"""

import argparse
import json
import re
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

HEADERS = {"User-Agent": "financial-researcher research@frakbox.io"}
SEC_DELAY = 0.12  # Stay well under 10 req/sec
EFTS_PAGE_SIZE = 40  # Smaller pages — these are targeted queries
MIN_MARKET_CAP = 500_000_000  # $500M

# Multiple search queries to catch different phrasings of deal termination
MERGER_BREAK_QUERIES = [
    '"merger agreement" "terminated"',
    '"merger agreement" "termination"',
    '"acquisition agreement" "terminated"',
    '"merger" "mutual termination"',
    '"merger agreement" "break-up fee"',
    '"merger agreement" "failed to close"',
]


def search_efts(query: str, start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS with a text query, paginating through all results."""
    import urllib.parse
    encoded_q = urllib.parse.quote(query)
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={encoded_q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits = []
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"  EFTS error: {resp.status_code} for query: {query}", file=sys.stderr)
        return []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(f"  Query '{query}': {total} total hits", file=sys.stderr)

    # Paginate (cap at 500 for safety)
    fetched = len(hits)
    max_fetch = min(total, 500)
    while fetched < max_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            break
        page_hits = resp.json().get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)

    return all_hits


def parse_hits(hits: list[dict]) -> list[dict]:
    """Parse EFTS hits into structured events, deduplicated by (CIK, date)."""
    results = []
    seen = set()

    for h in hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])
        accession = h.get("_id", "")

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

        # Check if Item 1.02 is present (strong signal it's a termination)
        has_102 = any("1.02" in str(it) for it in items) if items else False

        results.append({
            "cik": cik,
            "display_name": names[0] if names else "",
            "ticker": ticker,
            "file_date": file_date,
            "items": items,
            "has_item_102": has_102,
            "accession": accession,
        })

    return results


def filter_largecap(events: list[dict]) -> list[dict]:
    """Filter to large-cap stocks (>$500M market cap)."""
    if not events:
        return []

    try:
        from tools.largecap_filter import filter_to_largecap
        tickers = list(set(e["ticker"] for e in events if e.get("ticker")))
        if not tickers:
            return []
        lc_set = set(filter_to_largecap(tickers, min_cap=MIN_MARKET_CAP / 1e6))
        return [e for e in events if e.get("ticker") in lc_set]
    except Exception as ex:
        print(f"  Large-cap filter failed: {ex}", file=sys.stderr)
        return [e for e in events if e.get("ticker")]


def scan_merger_breaks(start_date: str, end_date: str) -> list[dict]:
    """Run all merger-break queries and merge/deduplicate results."""
    all_hits = []
    for query in MERGER_BREAK_QUERIES:
        hits = search_efts(query, start_date, end_date)
        all_hits.extend(hits)
        time.sleep(SEC_DELAY * 3)  # Extra delay between queries

    events = parse_hits(all_hits)
    print(f"  Total unique filings after dedup: {len(events)}", file=sys.stderr)

    # Filter to those with tickers
    with_ticker = [e for e in events if e.get("ticker")]
    print(f"  With ticker: {len(with_ticker)}", file=sys.stderr)

    # Filter to large-cap
    largecap = filter_largecap(with_ticker)
    print(f"  Large-cap (>{MIN_MARKET_CAP/1e6:.0f}M): {len(largecap)}", file=sys.stderr)

    return largecap


def main():
    parser = argparse.ArgumentParser(description="M&A Deal Break Scanner")
    parser.add_argument("--start", "--start-date", type=str)
    parser.add_argument("--end", "--end-date", type=str)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json-events", action="store_true",
                        help="Print only JSON events list (for backtesting)")
    args = parser.parse_args()

    if args.days:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    elif args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        parser.error("Provide --days or --start/--end")

    events = scan_merger_breaks(start_date, end_date)

    if args.json_events:
        # Output for data_tasks.py backtest
        backtest_events = [
            {"symbol": e["ticker"], "date": e["file_date"]}
            for e in events if e.get("ticker")
        ]
        print(json.dumps(backtest_events))
    else:
        print(f"\n=== M&A Deal Break Events ({start_date} to {end_date}) ===")
        print(f"Total large-cap events: {len(events)}\n")
        for e in sorted(events, key=lambda x: x["file_date"]):
            item_flag = " [Item 1.02]" if e.get("has_item_102") else ""
            print(f"  {e['file_date']}  {e.get('ticker','?'):6s}  {e['display_name']}{item_flag}")
        print()

        # Also output JSON for convenience
        backtest_events = [
            {"symbol": e["ticker"], "date": e["file_date"]}
            for e in events if e.get("ticker")
        ]
        print("JSON events for backtesting:")
        print(json.dumps(backtest_events, indent=2))


if __name__ == "__main__":
    main()
