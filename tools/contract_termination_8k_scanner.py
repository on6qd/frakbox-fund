#!/usr/bin/env python3
"""8-K Item 1.02 (Termination of Material Definitive Agreement) scanner.

Companies must file an 8-K within 4 business days of terminating a material
contract.  These filings signal the sudden end of a supply deal, licensing
agreement, joint venture, or other significant arrangement.  The market may
under-react to the initial disclosure because the economic impact of losing a
key contract is hard to quantify quickly.

Hypothesis:
  Large-cap companies filing an 8-K with Item 1.02 produce negative abnormal
  returns of -1% to -4% over 3-10 days after the filing date.

Causal mechanism:
  1. Actors: Companies losing a material revenue stream, supplier, licensee, or
     strategic partner without warning.
  2. Transmission: Direct revenue loss + elevated uncertainty about replacement
     + potential signal that the underlying business relationship deteriorated.
  3. Market inefficiency: Filings often use boilerplate legal language that
     obscures economic magnitude; analyst models take days to update.

Usage:
    # Recent monitoring (last 14 days)
    python tools/contract_termination_8k_scanner.py --days 14

    # Historical scan
    python tools/contract_termination_8k_scanner.py --start-date 2022-01-01 --end-date 2025-12-31

    # Historical scan (alias flags)
    python tools/contract_termination_8k_scanner.py --start 2022-01-01 --end 2025-12-31

    # JSON events only (for data_tasks.py backtest)
    python tools/contract_termination_8k_scanner.py --start-date 2022-01-01 --end-date 2025-12-31 --json-events

    # Skip market cap filter (faster, more noise)
    python tools/contract_termination_8k_scanner.py --days 30 --no-filter
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
    import pandas as pd
except ImportError:
    pd = None

HEADERS = {"User-Agent": "financial-researcher research@frakbox.io"}
# 0.11 s between EDGAR calls → stays well under 10 req/sec hard limit
SEC_DELAY = 0.11
MIN_MARKET_CAP = 500_000_000  # $500M
EFTS_PAGE_SIZE = 100
# Deduplicate: same company within this many days → keep only the first filing
DEDUP_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# EDGAR search
# ---------------------------------------------------------------------------

def search_item_102(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings containing Item 1.02.

    Handles pagination transparently — fetches ALL matching filings regardless
    of total count.  Prints progress to stderr.

    Returns a list of raw filing dicts (not yet filtered for large-cap).
    """
    q = '%22Item+1.02%22'
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits: list[dict] = []

    # --- first page ---
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"EFTS error on first page: {resp.status_code}", file=sys.stderr)
        return []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(
        f"  8-K Item 1.02: {total} total filings found ({start_date} to {end_date})",
        file=sys.stderr,
    )

    # --- paginate ---
    fetched = len(hits)
    # EDGAR EFTS caps the addressable window at 10 000
    max_to_fetch = min(total, 10_000)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(
                f"  Pagination error at offset {fetched}: {resp.status_code}",
                file=sys.stderr,
            )
            break
        page_hits = resp.json().get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)
        if fetched % 500 == 0:
            print(f"  ... fetched {fetched}/{max_to_fetch}", file=sys.stderr)

    # --- parse hits ---
    results: list[dict] = []
    seen: set[tuple] = set()  # dedup identical (cik, file_date) pairs from EDGAR

    for h in all_hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])

        # Confirm Item 1.02 is present in the items metadata (when populated)
        has_102 = any("1.02" in str(it) for it in items)
        if items and not has_102:
            continue

        cik = ciks[0].lstrip("0") if ciks else ""
        dedup_key = (cik, file_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Extract ticker from display_name, e.g. "KROGER CO (KR)"
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


# ---------------------------------------------------------------------------
# Large-cap filter
# ---------------------------------------------------------------------------

def filter_largecap(events: list[dict]) -> list[dict]:
    """Filter to large-cap stocks (>$500M market cap).

    Prefers the cached `largecap_filter` module; falls back to inline yfinance
    if pandas/the module is unavailable.
    """
    if not events:
        return []

    # Preferred path: use largecap_filter (has caching + batch efficiency)
    if pd is not None:
        try:
            from tools.largecap_filter import filter_to_largecap as _filter_lc

            df = pd.DataFrame(events)
            df_with_tickers = df[df["ticker"].notna()].copy()
            if df_with_tickers.empty:
                return []

            df_filtered = _filter_lc(
                df_with_tickers,
                min_market_cap_m=500,
                ticker_col="ticker",
            )
            return df_filtered.to_dict("records")

        except Exception as ex:
            print(
                f"  largecap_filter failed ({ex}), falling back to inline yfinance check",
                file=sys.stderr,
            )

    # Fallback: inline yfinance
    try:
        import yfinance as yf
    except ImportError:
        print(
            "yfinance not available — skipping market cap filter (all tickers kept)",
            file=sys.stderr,
        )
        return [e for e in events if e.get("ticker")]

    filtered: list[dict] = []
    tickers = list({e["ticker"] for e in events if e.get("ticker")})

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
                print(
                    f"  Filtered out {tick}: market cap ${mcap/1e6:.0f}M < $500M",
                    file=sys.stderr,
                )
        except Exception as ex:
            print(f"  Error checking {tick}: {ex}", file=sys.stderr)

        if (i + 1) % 10 == 0:
            print(f"  Market cap check: {i+1}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.2)

    return filtered


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_by_window(events: list[dict], window_days: int = DEDUP_WINDOW_DAYS) -> tuple[list[dict], int]:
    """Keep only the FIRST filing per company within a rolling window.

    For contract termination, the initial filing is the signal.  Follow-up
    amendments or related filings within `window_days` are noise.

    Returns (deduped_events, n_removed).
    """
    # Sort ascending so we keep the earliest occurrence
    events_sorted = sorted(events, key=lambda e: e["file_date"])

    kept: list[dict] = []
    # Map ticker -> last kept date (as datetime)
    last_kept: dict[str, datetime] = {}

    for e in events_sorted:
        tick = e.get("ticker")
        if not tick:
            kept.append(e)
            continue

        file_dt = datetime.strptime(e["file_date"], "%Y-%m-%d")
        prev = last_kept.get(tick)

        if prev is None or (file_dt - prev).days > window_days:
            kept.append(e)
            last_kept[tick] = file_dt
        # else: within window → skip as duplicate

    n_removed = len(events_sorted) - len(kept)
    return kept, n_removed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scan EDGAR for 8-K Item 1.02 contract termination filings"
    )
    # Date range — support both naming conventions used by other scanners
    parser.add_argument("--start-date", "--start", dest="start_date",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", "--end", dest="end_date",
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int,
                        help="Look back N days from today (overrides --start-date/--end-date)")
    parser.add_argument("--historical", action="store_true",
                        help="Alias for using --start-date/--end-date range (no extra behaviour needed)")
    parser.add_argument("--json-events", action="store_true",
                        help="Output ONLY a JSON array of {symbol, date} events (for data_tasks.py)")
    parser.add_argument("--no-filter", action="store_true",
                        help="Skip large-cap market cap filter")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Keep all filings per ticker (not just first within window)")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")

    if args.days:
        start = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        end = today
    elif args.start_date:
        start = args.start_date
        end = args.end_date or today
    else:
        # Default: last 30 days
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        end = today

    days_scanned = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days

    print(f"Scanning EDGAR for 8-K Item 1.02 filings from {start} to {end}...", file=sys.stderr)

    # 1. Fetch from EDGAR
    events = search_item_102(start, end)
    total_found = len(events)
    print(f"Raw events found: {total_found}", file=sys.stderr)

    # 2. Keep only events where a ticker could be extracted
    events = [e for e in events if e.get("ticker")]
    print(f"Events with tickers: {len(events)}", file=sys.stderr)

    # 3. Large-cap filter
    if not args.no_filter and events:
        events = filter_largecap(events)
        print(f"Large-cap events (>$500M): {len(events)}", file=sys.stderr)

    # 4. Deduplication: keep first per company within rolling 30-day window
    if not args.no_dedup and events:
        events, n_removed = dedup_by_window(events)
        if n_removed:
            print(
                f"Deduplication: removed {n_removed} follow-up filings "
                f"(same company within {DEDUP_WINDOW_DAYS} days — keeping first)",
                file=sys.stderr,
            )

    large_cap_count = len(events)

    # --- Output ---

    if args.json_events:
        # Clean JSON array for direct use with data_tasks.py backtest
        json_events = [{"symbol": e["ticker"], "date": e["file_date"]} for e in events]
        print(json.dumps(json_events))
        return events

    # Human-readable summary
    print(f"\nFinal events: {large_cap_count}", file=sys.stderr)
    for e in events:
        mcap = e.get("market_cap")
        if mcap:
            if mcap >= 1e9:
                mcap_str = f" (${mcap/1e9:.1f}B)"
            else:
                mcap_str = f" (${mcap/1e6:.0f}M)"
        else:
            mcap_str = ""
        print(f"  {e['ticker']} {e['file_date']}{mcap_str}: {e['display_name'][:60]}")

    # Structured JSON output
    output = {
        "scan_time": datetime.now().isoformat(),
        "start_date": start,
        "end_date": end,
        "days_scanned": days_scanned,
        "total_found": total_found,
        "large_cap_events": large_cap_count,
        "events": [
            {
                "symbol": e["ticker"],
                "date": e["file_date"],
                "name": e["display_name"],
            }
            for e in events
        ],
    }
    print(json.dumps(output, indent=2))

    return events


if __name__ == "__main__":
    main()
