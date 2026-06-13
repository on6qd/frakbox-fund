#!/usr/bin/env python3
"""8-K Item 2.06 (Material Impairments) scanner.

Item 2.06 of Form 8-K is triggered when a company concludes a material impairment
is required under GAAP — most commonly goodwill write-downs. These disclosures
signal deteriorating acquired-business fundamentals and typically shock prices.

Hypothesis (rank 2 new signal from strategic review 2026-04-10):
  Large-cap companies filing an 8-K with Item 2.06 produce negative abnormal returns
  of -2% to -5% over 3-5 days.

This scanner queries EDGAR EFTS full-text search for `forms=8-K` and
`q="Item 2.06"` to harvest candidate filings, then extracts tickers from
display_names and (optionally) filters to large-cap.

Usage:
    # Historical backtest
    python tools/goodwill_impairment_scanner.py --start 2022-01-01 --end 2025-12-31

    # Recent scan
    python tools/goodwill_impairment_scanner.py --days 30

    # Output JSON events for data_tasks.py backtest
    python tools/goodwill_impairment_scanner.py --start 2022-01-01 --end 2025-12-31 --json-events
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
MIN_MARKET_CAP = 500_000_000  # $500M
EFTS_PAGE_SIZE = 100


def search_item_206(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings containing 'Item 2.06'."""
    # EFTS wants form=8-K and a quoted phrase query
    # Quoted string gets URL-encoded as %22...%22
    q = '%22Item+2.06%22'
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits = []
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"EFTS error: {resp.status_code}", file=sys.stderr)
        return []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(f"  8-K Item 2.06: {total} total filings found ({start_date} to {end_date})", file=sys.stderr)

    fetched = len(hits)
    max_to_fetch = min(total, 10000)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"  Pagination error at offset {fetched}: {resp.status_code}", file=sys.stderr)
            break
        page_hits = resp.json().get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)

    results = []
    for h in all_hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])  # EFTS returns item numbers for 8-Ks

        # Confirm Item 2.06 is actually present (q= is loose substring)
        has_206 = any("2.06" in str(it) for it in items)
        if items and not has_206:
            # If items list exists but 2.06 absent, skip
            continue

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
            "form_type": "8-K",
            "items": items,
        })

    return results


def get_market_cap(ticker: str) -> float | None:
    if not yf:
        return None
    try:
        info = yf.Ticker(ticker).info
        return info.get("marketCap")
    except Exception:
        return None


def filter_to_largecap(filings: list[dict], min_cap: float = MIN_MARKET_CAP) -> list[dict]:
    tickers = set(f["ticker"] for f in filings if f["ticker"])
    print(f"  Checking market caps for {len(tickers)} unique tickers...", file=sys.stderr)

    cap_cache = {}
    checked = 0
    for t in sorted(tickers):
        cap = get_market_cap(t)
        cap_cache[t] = cap
        checked += 1
        if checked % 20 == 0:
            print(f"    Checked {checked}/{len(tickers)} tickers", file=sys.stderr)
        time.sleep(0.1)

    filtered = []
    for f in filings:
        t = f["ticker"]
        if not t:
            continue
        cap = cap_cache.get(t)
        if cap and cap >= min_cap:
            f["market_cap"] = cap
            filtered.append(f)
    return filtered


def deduplicate_events(filings: list[dict]) -> list[dict]:
    """One impairment per ticker per 180 days (don't double-count follow-up filings)."""
    filings.sort(key=lambda x: (x.get("ticker", ""), x.get("file_date", "")))
    seen = {}
    deduped = []
    for f in filings:
        t = f["ticker"]
        d = f["file_date"]
        if not t or not d:
            continue
        if t in seen:
            last = datetime.strptime(seen[t], "%Y-%m-%d")
            cur = datetime.strptime(d, "%Y-%m-%d")
            if (cur - last).days < 180:
                continue
        seen[t] = d
        deduped.append(f)
    return deduped


def scan(start_date: str, end_date: str, filter_largecap: bool = True, min_cap: float = MIN_MARKET_CAP) -> list[dict]:
    all_filings = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # Chunk into 6-month windows (2.06 is rare enough that 6mo stays under the 1000 EFTS limit)
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=180), end)
        s = current.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")
        filings = search_item_206(s, e)
        all_filings.extend(filings)
        time.sleep(SEC_DELAY)
        current = chunk_end + timedelta(days=1)

    print(f"\n  Total 8-K Item 2.06 filings: {len(all_filings)}", file=sys.stderr)
    with_ticker = [f for f in all_filings if f["ticker"]]
    print(f"  With tickers: {len(with_ticker)}", file=sys.stderr)

    deduped = deduplicate_events(with_ticker)
    print(f"  After dedup (180-day window): {len(deduped)}", file=sys.stderr)

    if filter_largecap:
        filtered = filter_to_largecap(deduped, min_cap=min_cap)
        print(f"  Large-cap (>{min_cap/1e6:.0f}M): {len(filtered)}", file=sys.stderr)
        return filtered
    return deduped


def to_backtest_events(filings: list[dict]) -> list[dict]:
    return [
        {"symbol": f["ticker"], "date": f["file_date"]}
        for f in filings if f["ticker"] and f["file_date"]
    ]


def main():
    parser = argparse.ArgumentParser(description="8-K Item 2.06 Material Impairment Scanner")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=30, help="Days to look back if no --start/--end")
    parser.add_argument("--no-filter", action="store_true", help="Skip large-cap filter")
    parser.add_argument("--json-events", action="store_true", help="Output JSON events for data_tasks.py backtest")
    parser.add_argument("--min-cap", type=float, default=MIN_MARKET_CAP, help="Min market cap")
    args = parser.parse_args()

    if args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    filings = scan(start_date, end_date, filter_largecap=not args.no_filter, min_cap=args.min_cap)

    if args.json_events:
        print(json.dumps(to_backtest_events(filings)))
        return

    print(f"\n{'='*70}")
    print(f"8-K ITEM 2.06 EVENTS: {len(filings)} found ({start_date} to {end_date})")
    print(f"{'='*70}")
    for f in filings:
        cap_str = f"${f.get('market_cap', 0)/1e9:.1f}B" if f.get('market_cap') else "?"
        print(f"  {f['file_date']}  {f['ticker']:6s}  {cap_str:>8s}  {f['display_name'][:50]}")
    print(f"\nSummary: {len(filings)} events")


if __name__ == "__main__":
    main()
