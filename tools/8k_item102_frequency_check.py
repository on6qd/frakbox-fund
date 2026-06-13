#!/usr/bin/env python3
"""Frequency analysis for 8-K Item 1.02 (Termination of a Material Definitive Agreement).

Queries SEC EDGAR EFTS over the last 2 years to estimate how often large-cap
companies file an Item 1.02 disclosure — determines whether this event type is
worth backtesting as a trading signal.

Viability thresholds:
  - >= 10 large-cap events/year  -> "viable"
  - 5-10 large-cap events/year   -> "marginal"
  - < 5 large-cap events/year    -> "too rare"

Usage:
    python tools/8k_item102_frequency_check.py
    python tools/8k_item102_frequency_check.py --start 2022-01-01 --end 2026-04-14
    python tools/8k_item102_frequency_check.py --sample 30
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
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

HEADERS = {"User-Agent": "FrakboxResearch research@frakbox.io"}
SEC_DELAY = 0.15
MIN_MARKET_CAP = 500_000_000  # $500M
EFTS_PAGE_SIZE = 100


def search_item_102(start_date: str, end_date: str) -> tuple[int, list[dict]]:
    """Search EDGAR EFTS for 8-K filings containing Item 1.02.

    Returns (total_count, sample_hits) where sample_hits are the first
    page of raw results (up to EFTS_PAGE_SIZE).
    """
    q = '%22Item+1.02%22'
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"EFTS error: {resp.status_code} — {resp.text[:200]}", file=sys.stderr)
        return 0, []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    print(
        f"  8-K Item 1.02: {total} total filings found ({start_date} to {end_date})",
        file=sys.stderr,
    )
    return total, hits


def parse_hits(hits: list[dict]) -> list[dict]:
    """Parse EFTS hit records into clean event dicts."""
    results = []
    seen = set()  # dedup by (cik, file_date)

    for h in hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])

        # Confirm Item 1.02 is present in items metadata (if populated)
        has_102 = any("1.02" in str(it) for it in items)
        if items and not has_102:
            continue

        cik = ciks[0].lstrip("0") if ciks else ""
        dedup_key = (cik, file_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Extract ticker from display_name e.g. "ACME Corp (ACME)"
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
    """Filter events to large-cap stocks (>$500M market cap).

    Uses tools/largecap_filter.py (with caching) when pandas is available,
    otherwise falls back to inline yfinance check.
    """
    events_with_tickers = [e for e in events if e.get("ticker")]
    if not events_with_tickers:
        return []

    if HAS_PANDAS:
        try:
            from tools.largecap_filter import filter_to_largecap as _filter_lc

            df = pd.DataFrame(events_with_tickers)
            df_filtered = _filter_lc(df, min_market_cap_m=500, ticker_col="ticker")
            return df_filtered.to_dict("records")
        except Exception as ex:
            print(
                f"  largecap_filter failed ({ex}), falling back to inline yfinance check",
                file=sys.stderr,
            )

    # Inline fallback using yfinance directly
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not available — cannot filter by market cap", file=sys.stderr)
        return events_with_tickers

    filtered = []
    tickers = list(set(e["ticker"] for e in events_with_tickers))
    print(f"  Checking market cap for {len(tickers)} unique tickers...", file=sys.stderr)

    for i, tick in enumerate(tickers):
        try:
            info = yf.Ticker(tick).info
            mcap = info.get("marketCap", 0) or 0
            if mcap >= MIN_MARKET_CAP:
                for e in events_with_tickers:
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


def main():
    parser = argparse.ArgumentParser(
        description="Frequency analysis for 8-K Item 1.02 (Termination of Material Definitive Agreement)"
    )
    parser.add_argument("--start", default="2024-04-14", help="Start date (YYYY-MM-DD), default 2 years ago")
    parser.add_argument("--end", default="2026-04-14", help="End date (YYYY-MM-DD), default today")
    parser.add_argument(
        "--sample", type=int, default=20,
        help="Max large-cap sample events to include in output (default 20)"
    )
    parser.add_argument(
        "--no-filter", action="store_true",
        help="Skip market cap filter (report raw ticker counts only)"
    )
    args = parser.parse_args()

    start_date = args.start
    end_date = args.end

    # Parse date range for per-year estimation
    try:
        dt_start = datetime.strptime(start_date, "%Y-%m-%d")
        dt_end = datetime.strptime(end_date, "%Y-%m-%d")
        years_covered = max((dt_end - dt_start).days / 365.25, 0.01)
    except ValueError:
        years_covered = 2.0

    print(f"\nScanning EDGAR for 8-K Item 1.02 filings ({start_date} to {end_date})...", file=sys.stderr)

    # --- Step 1: Get total count and a first-page sample ---
    total_filings, raw_hits = search_item_102(start_date, end_date)

    if total_filings == 0:
        summary = {
            "total_filings_2yr": 0,
            "total_with_tickers": 0,
            "sample_with_tickers_n": 0,
            "largecap_in_sample": 0,
            "estimated_largecap_per_year": 0,
            "assessment": "too rare",
            "note": "No filings found — EDGAR EFTS returned 0 results.",
            "sample_events": [],
        }
        print(json.dumps(summary, indent=2))
        return summary

    # --- Step 2: Parse first page for ticker extraction ---
    sample_events = parse_hits(raw_hits)
    with_tickers = [e for e in sample_events if e.get("ticker")]
    print(f"  First-page events parsed: {len(sample_events)} ({len(with_tickers)} with tickers)", file=sys.stderr)

    # Estimate overall ticker-extraction rate
    ticker_extraction_rate = len(with_tickers) / max(len(sample_events), 1)
    estimated_total_with_tickers = int(total_filings * ticker_extraction_rate)

    # --- Step 3: Large-cap filter on the sample ---
    if args.no_filter:
        largecap_sample = with_tickers[: args.sample]
        print(f"  Skipping market cap filter (--no-filter)", file=sys.stderr)
    else:
        print(f"\nApplying large-cap filter (>$500M) to {len(with_tickers)} sample events...", file=sys.stderr)
        largecap_sample = filter_largecap(with_tickers)
        print(f"  Large-cap events in sample: {len(largecap_sample)}", file=sys.stderr)

    largecap_in_sample = len(largecap_sample)
    sample_size = max(len(with_tickers), 1)

    # Estimate large-cap rate and project to full 2-year window
    largecap_rate = largecap_in_sample / sample_size
    estimated_largecap_total = int(estimated_total_with_tickers * largecap_rate)
    estimated_largecap_per_year = estimated_largecap_total / years_covered

    # --- Step 4: Viability assessment ---
    if estimated_largecap_per_year >= 10:
        assessment = "viable"
    elif estimated_largecap_per_year >= 5:
        assessment = "marginal"
    else:
        assessment = "too rare"

    # --- Step 5: Build sample event list (capped at --sample) ---
    sample_output = [
        {"ticker": e["ticker"], "date": e["file_date"], "name": e["display_name"][:60]}
        for e in largecap_sample[: args.sample]
        if e.get("ticker")
    ]

    summary = {
        "date_range": f"{start_date} to {end_date}",
        "years_covered": round(years_covered, 2),
        "total_filings_found": total_filings,
        "estimated_filings_with_tickers": estimated_total_with_tickers,
        "ticker_extraction_rate_pct": round(ticker_extraction_rate * 100, 1),
        "sample_size_for_largecap_filter": sample_size,
        "largecap_in_sample": largecap_in_sample,
        "largecap_rate_pct": round(largecap_rate * 100, 1),
        "estimated_largecap_total_2yr": estimated_largecap_total,
        "estimated_largecap_per_year": round(estimated_largecap_per_year, 1),
        "assessment": assessment,
        "sample_largecap_events": sample_output,
    }

    print("\n" + "=" * 60)
    print("8-K ITEM 1.02 FREQUENCY ANALYSIS RESULTS")
    print("=" * 60)
    print(json.dumps(summary, indent=2))

    return summary


if __name__ == "__main__":
    main()
