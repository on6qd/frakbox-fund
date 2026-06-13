#!/usr/bin/env python3
"""Share buyback announcement scanner.

Searches EDGAR EFTS for 8-K filings announcing NEW share repurchase programs.
Academic basis: Ikenberry, Lakonishok & Vermaelen (1995) — documented +2-3%
short-term announcement effect and +12% long-term abnormal returns.

This scanner:
1. Searches EDGAR EFTS full-text for "new share repurchase program" in 8-K filings
2. Also searches "new stock repurchase program" and "authorized a new" + "repurchase"
3. Deduplicates (same CIK within 30 days)
4. Maps CIKs to tickers via display_name
5. Optionally filters to large-cap (>$500M)

Usage:
    # Historical backtest: fetch all buyback announcements in date range
    python tools/buyback_scanner.py --start 2020-01-01 --end 2025-12-31

    # Recent scan
    python tools/buyback_scanner.py --days 7

    # Output as JSON events for data_tasks.py backtest
    python tools/buyback_scanner.py --start 2024-01-01 --end 2024-12-31 --json-events

    # With large-cap filtering
    python tools/buyback_scanner.py --start 2024-01-01 --end 2024-06-30 --filter-largecap
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

try:
    import yfinance as yf
except ImportError:
    yf = None

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
SEC_DELAY = 0.15
MIN_MARKET_CAP = 500_000_000
EFTS_PAGE_SIZE = 100

# Search queries that target NEW buyback announcements (not existing program mentions)
SEARCH_QUERIES = [
    "%22new%20share%20repurchase%20program%22",      # "new share repurchase program"
    "%22new%20stock%20repurchase%20program%22",       # "new stock repurchase program"
    "%22authorized%20a%20new%22%20%22repurchase%22",  # "authorized a new" + "repurchase"
]


def search_buyback_filings(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings announcing new buyback programs.

    Runs multiple queries and deduplicates by accession number.
    Chunks date ranges into 6-month windows (EFTS boundary issue with year-end dates).
    """
    all_hits = {}  # accession_number -> hit dict (dedup across queries)

    # Chunk into 6-month windows
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=180), end)
        s = current.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")

        for query in SEARCH_QUERIES:
            offset = 0
            while True:
                url = (
                    f"https://efts.sec.gov/LATEST/search-index"
                    f"?q={query}"
                    f"&forms=8-K"
                    f"&dateRange=custom&startdt={s}&enddt={e}"
                    f"&from={offset}&size={EFTS_PAGE_SIZE}"
                )
                resp = requests.get(url, headers=HEADERS, timeout=30)
                if resp.status_code != 200:
                    print(f"  EFTS error: {resp.status_code} at offset {offset}", file=sys.stderr)
                    break

                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                total = data.get("hits", {}).get("total", {}).get("value", 0)

                for h in hits:
                    acc = h.get("_id", "")
                    if acc not in all_hits:
                        all_hits[acc] = h

                offset += len(hits)
                if not hits or offset >= total or offset >= 500:
                    break
                time.sleep(SEC_DELAY)

            time.sleep(SEC_DELAY)

        current = chunk_end + timedelta(days=1)

    # Convert to structured results
    results = []
    for acc, h in all_hits.items():
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")

        ticker = None
        if names:
            m = re.search(r'\(([A-Z]{1,5})\)', names[0])
            if m:
                ticker = m.group(1)

        results.append({
            "cik": ciks[0].lstrip("0") if ciks else "",
            "display_name": names[0] if names else "",
            "ticker": ticker,
            "file_date": file_date,
            "accession": acc,
        })

    # Sort by date
    results.sort(key=lambda x: x.get("file_date", ""))
    return results


def deduplicate_events(filings: list[dict], window_days: int = 30) -> list[dict]:
    """Same ticker within window_days = one event (keep earliest)."""
    filings.sort(key=lambda x: (x.get("ticker", ""), x.get("file_date", "")))

    seen = {}  # ticker -> last filing date
    deduped = []
    for f in filings:
        t = f.get("ticker")
        d = f.get("file_date", "")
        if not t or not d:
            continue

        if t in seen:
            last_date = datetime.strptime(seen[t], "%Y-%m-%d")
            this_date = datetime.strptime(d, "%Y-%m-%d")
            if (this_date - last_date).days < window_days:
                continue

        seen[t] = d
        deduped.append(f)

    return deduped


def filter_to_largecap(filings: list[dict], min_cap: float = MIN_MARKET_CAP) -> list[dict]:
    """Filter to large-cap companies using yfinance market cap."""
    if not yf:
        print("yfinance not available", file=sys.stderr)
        return filings

    tickers = set(f["ticker"] for f in filings if f.get("ticker"))
    print(f"  Checking market caps for {len(tickers)} unique tickers...", file=sys.stderr)

    cap_cache = {}
    for i, t in enumerate(sorted(tickers)):
        try:
            info = yf.Ticker(t).info
            cap_cache[t] = info.get("marketCap")
        except Exception:
            cap_cache[t] = None
        if (i + 1) % 20 == 0:
            print(f"    Checked {i+1}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.1)

    filtered = []
    for f in filings:
        t = f.get("ticker")
        if not t:
            continue
        cap = cap_cache.get(t)
        if cap and cap >= min_cap:
            f["market_cap"] = cap
            filtered.append(f)

    return filtered


def to_backtest_events(filings: list[dict]) -> list[dict]:
    """Convert to backtest event format."""
    return [
        {"symbol": f["ticker"], "date": f["file_date"]}
        for f in filings
        if f.get("ticker") and f.get("file_date")
    ]


def main():
    parser = argparse.ArgumentParser(description="Share Buyback Announcement Scanner")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default 7)")
    parser.add_argument("--json-events", action="store_true", help="Output JSON events")
    parser.add_argument("--filter-largecap", action="store_true", help="Filter to >$500M cap")
    parser.add_argument("--min-cap", type=float, default=MIN_MARKET_CAP, help="Min market cap")
    args = parser.parse_args()

    if args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"Scanning EDGAR for buyback announcements: {start_date} to {end_date}", file=sys.stderr)

    filings = search_buyback_filings(start_date, end_date)
    print(f"  Raw filings found: {len(filings)}", file=sys.stderr)

    # Filter to those with tickers
    with_ticker = [f for f in filings if f.get("ticker")]
    print(f"  With tickers: {len(with_ticker)}", file=sys.stderr)

    # Deduplicate
    deduped = deduplicate_events(with_ticker)
    print(f"  After dedup (30-day window): {len(deduped)}", file=sys.stderr)

    # Optional large-cap filter
    if args.filter_largecap:
        deduped = filter_to_largecap(deduped, min_cap=args.min_cap)
        print(f"  Large-cap (>{args.min_cap/1e6:.0f}M): {len(deduped)}", file=sys.stderr)

    if args.json_events:
        events = to_backtest_events(deduped)
        print(json.dumps(events))
    else:
        print(f"\n{'='*70}")
        print(f"BUYBACK ANNOUNCEMENTS: {len(deduped)} events ({start_date} to {end_date})")
        print(f"{'='*70}")
        for f in deduped:
            cap_str = f"${f.get('market_cap', 0)/1e9:.1f}B" if f.get('market_cap') else "?"
            print(f"  {f['file_date']}  {f['ticker']:6s}  {cap_str:>8s}  {f['display_name'][:55]}")

        # Stats
        print(f"\nSummary: {len(deduped)} unique events")
        tickers = set(f["ticker"] for f in deduped if f.get("ticker"))
        print(f"  Unique tickers: {len(tickers)}")
        dates = [f["file_date"] for f in deduped if f.get("file_date")]
        if len(dates) >= 2:
            first = datetime.strptime(min(dates), "%Y-%m-%d")
            last = datetime.strptime(max(dates), "%Y-%m-%d")
            years = max((last - first).days / 365.25, 0.1)
            print(f"  Frequency: {len(deduped)/years:.0f}/year")


if __name__ == "__main__":
    main()
