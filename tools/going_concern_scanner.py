#!/usr/bin/env python3
"""Going-concern disclosure scanner.

When an auditor expresses "substantial doubt about the company's ability to
continue as a going concern", it is disclosed in the 10-K (or 10-Q) and the
auditor's report. This is a strong distress signal that typically precedes
further price declines, covenant breaches, and in some cases Chapter 11.

This scanner uses EDGAR EFTS full-text search to find filings that contain
the exact phrase pair "substantial doubt" + "going concern" in 10-K / 10-Q
filings, dedups by (CIK, filing-date) so an auditor's consent exhibit does
not double-count, and (optionally) filters to large-cap issuers so we match
the current research universe (>500M market cap).

Usage:
    # Recent scan for weekly monitoring
    python tools/going_concern_scanner.py --days 14

    # Historical backtest fetch
    python tools/going_concern_scanner.py --start 2022-01-01 --end 2024-12-31 --json-events

    # Skip market-cap filter (for discovery work)
    python tools/going_concern_scanner.py --days 90 --no-filter

    # Only first-time disclosures (attempts to reject repeat filers)
    python tools/going_concern_scanner.py --days 90 --first-time-only
"""
import argparse
import json
import re
import sys
import os
import time
import urllib.parse
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
EFTS_MAX_RESULTS = 10000

# The phrase pair used in all auditor going-concern disclosures. EFTS treats
# quoted strings as exact phrases and combines multiple quoted strings with AND.
GC_QUERY = '"substantial doubt" "going concern"'


def search_going_concern(start_date: str, end_date: str, form_type: str = "10-K") -> list[dict]:
    """EFTS full-text search for going-concern phrases in a given form type."""
    encoded_q = urllib.parse.quote_plus(GC_QUERY)
    encoded_form = urllib.parse.quote(form_type)
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={encoded_q}"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
        f"&forms={encoded_form}"
    )

    all_hits: list[dict] = []

    def _get_with_retry(u: str, attempts: int = 4) -> requests.Response | None:
        backoff = 1.0
        for i in range(attempts):
            try:
                r = requests.get(u, headers=HEADERS, timeout=30)
                if r.status_code == 200:
                    return r
                # 500 is EFTS throttling -> wait and retry
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                return r
            except Exception:
                time.sleep(backoff)
                backoff *= 2
        return None

    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = _get_with_retry(url)
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "no-response"
        print(f"EFTS error for {form_type} ({start_date}..{end_date}): {code}", file=sys.stderr)
        return []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)

    print(
        f"  {form_type} going-concern: {total} raw hits ({start_date} -> {end_date})",
        file=sys.stderr,
    )

    fetched = len(hits)
    max_to_fetch = min(total, EFTS_MAX_RESULTS)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        resp = _get_with_retry(url)
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "no-response"
            print(f"  Pagination error at offset {fetched}: {code}", file=sys.stderr)
            break
        page_hits = resp.json().get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)

    results: list[dict] = []
    for h in all_hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        file_type = src.get("file_type", "")
        root_forms = src.get("root_forms", [])
        adsh = src.get("adsh", "")

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
            "file_type": file_type,
            "root_forms": root_forms,
            "adsh": adsh,
            "form_type_queried": form_type,
        })

    return results


def deduplicate_by_cik_date(filings: list[dict]) -> list[dict]:
    """Auditor consents (EX-23.1) and exhibits (EX-99.1) are indexed separately
    from the main 10-K body. Collapse (cik, file_date) duplicates by keeping the
    first occurrence that has a ticker.
    """
    filings.sort(key=lambda x: (x.get("cik", ""), x.get("file_date", ""), 0 if x.get("ticker") else 1))
    seen = set()
    deduped: list[dict] = []
    for f in filings:
        key = (f.get("cik", ""), f.get("file_date", ""))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped


def filter_first_time(filings: list[dict], lookback_days: int = 400) -> list[dict]:
    """Best-effort first-time filter: for each ticker, only keep the EARLIEST
    going-concern filing within the fetched window. This is a proxy: true
    first-time disclosure requires checking all prior 10-Ks, which is beyond
    EFTS. Use --days with a long window to make this meaningful.
    """
    earliest: dict[str, dict] = {}
    for f in filings:
        t = f.get("ticker") or f.get("cik")
        if not t:
            continue
        existing = earliest.get(t)
        if existing is None or f["file_date"] < existing["file_date"]:
            earliest[t] = f
    return list(earliest.values())


def get_market_cap(ticker: str) -> float | None:
    if not yf or not ticker:
        return None
    try:
        info = yf.Ticker(ticker).info
        return info.get("marketCap")
    except Exception:
        return None


def filter_to_largecap(filings: list[dict], min_cap: float = MIN_MARKET_CAP) -> list[dict]:
    tickers = sorted({f["ticker"] for f in filings if f.get("ticker")})
    print(f"  Checking market caps for {len(tickers)} unique tickers...", file=sys.stderr)
    cap_cache: dict[str, float | None] = {}
    for i, t in enumerate(tickers, 1):
        cap_cache[t] = get_market_cap(t)
        if i % 20 == 0:
            print(f"    Checked {i}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.1)

    filtered: list[dict] = []
    for f in filings:
        t = f.get("ticker")
        if not t:
            continue
        cap = cap_cache.get(t)
        if cap and cap >= min_cap:
            f["market_cap"] = cap
            filtered.append(f)
    return filtered


def scan(start_date: str, end_date: str, filter_largecap: bool, min_cap: float,
         first_time_only: bool) -> list[dict]:
    all_filings: list[dict] = []

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=90), end)
        s = current.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")
        for form_type in ["10-K", "10-Q"]:
            hits = search_going_concern(s, e, form_type)
            all_filings.extend(hits)
            time.sleep(SEC_DELAY)
        current = chunk_end + timedelta(days=1)

    print(f"\n  Raw hits: {len(all_filings)}", file=sys.stderr)

    with_ticker_or_cik = [f for f in all_filings if f.get("cik")]
    deduped = deduplicate_by_cik_date(with_ticker_or_cik)
    print(f"  After (cik,date) dedup: {len(deduped)}", file=sys.stderr)

    if first_time_only:
        deduped = filter_first_time(deduped)
        print(f"  After first-time filter: {len(deduped)}", file=sys.stderr)

    if filter_largecap:
        deduped = filter_to_largecap(deduped, min_cap=min_cap)
        print(f"  Large-cap (>{min_cap/1e6:.0f}M): {len(deduped)}", file=sys.stderr)

    return deduped


def to_backtest_events(filings: list[dict]) -> list[dict]:
    return [
        {"symbol": f["ticker"], "date": f["file_date"]}
        for f in filings
        if f.get("ticker") and f.get("file_date")
    ]


def main():
    parser = argparse.ArgumentParser(description="Going-concern disclosure scanner")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=14, help="Look-back days (default 14)")
    parser.add_argument("--no-filter", action="store_true", help="Skip large-cap filter")
    parser.add_argument("--first-time-only", action="store_true",
                        help="Keep only earliest hit per ticker in the window")
    parser.add_argument("--min-cap", type=float, default=MIN_MARKET_CAP,
                        help="Min market cap (default 500M)")
    parser.add_argument("--json-events", action="store_true",
                        help="Output JSON event list for data_tasks.py backtest")
    args = parser.parse_args()

    if args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    filings = scan(
        start_date, end_date,
        filter_largecap=not args.no_filter,
        min_cap=args.min_cap,
        first_time_only=args.first_time_only,
    )

    if args.json_events:
        print(json.dumps(to_backtest_events(filings)))
        return

    print(f"\n{'=' * 70}")
    print(f"GOING-CONCERN DISCLOSURE EVENTS: {len(filings)} ({start_date} -> {end_date})")
    print(f"{'=' * 70}")
    for f in filings:
        cap_str = f"${f.get('market_cap', 0)/1e9:.2f}B" if f.get("market_cap") else "?"
        print(f"  {f['file_date']}  {f.get('ticker') or '-':6s}  {f.get('file_type') or '-':10s}  {cap_str:>8s}  {f['display_name'][:60]}")
    if filings:
        tickers = {f.get("ticker") for f in filings if f.get("ticker")}
        print(f"\n  Unique tickers: {len(tickers)}")


if __name__ == "__main__":
    main()
