#!/usr/bin/env python3
"""Backtest individual activist 13D filings.

Searches EDGAR for SC 13D filings by specific activists, extracts target
tickers, and runs price impact analysis.

Usage:
    python3 tools/activist_individual_backtest.py --activist "Elliott Investment Management" --years 2020-2024
    python3 tools/activist_individual_backtest.py --activist "Carl Icahn" --years 2020-2024
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

ACTIVISTS = {
    "Elliott Investment Management": {"cik": "1791786", "search_terms": ["Elliott Investment Management", "Elliott Associates", "Elliott International"]},
    "Carl Icahn": {"cik": "921669", "search_terms": ["Icahn", "Carl C. Icahn"]},
    "Starboard Value": {"cik": "1517137", "search_terms": ["Starboard Value"]},
    "Third Point": {"cik": "1040273", "search_terms": ["Third Point"]},
    "JANA Partners": {"cik": "1998597", "search_terms": ["JANA Partners"]},
    "Trian Fund Management": {"cik": "1345471", "search_terms": ["Trian Fund"]},
    "Pershing Square": {"cik": "1336528", "search_terms": ["Pershing Square"]},
}


def search_activist_13d(activist_name: str, start_year: int, end_year: int) -> list[dict]:
    """Search EDGAR EFTS for SC 13D filings by a specific activist."""
    if activist_name not in ACTIVISTS:
        print(f"Unknown activist: {activist_name}")
        print(f"Known: {list(ACTIVISTS.keys())}")
        return []

    info = ACTIVISTS[activist_name]
    all_events = []
    seen_targets = set()

    for search_term in info["search_terms"]:
        for year in range(start_year, end_year + 1):
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"

            url = (
                f"https://efts.sec.gov/LATEST/search-index"
                f"?q=%22{search_term.replace(' ', '+')}%22"
                f"&forms=SC%2013D"
                f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
                f"&_source=display_names,file_date,form_type"
            )

            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                if resp.status_code != 200:
                    print(f"  EFTS error {year}: {resp.status_code}", file=sys.stderr)
                    continue

                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])

                for h in hits:
                    src = h.get("_source", {})
                    names = src.get("display_names", [])
                    file_date = src.get("file_date", "")
                    form_type = src.get("form_type", "")

                    # Only initial 13D filings (not amendments)
                    if "SC 13D/A" in form_type:
                        continue

                    # Extract target company (not the activist)
                    for name in names:
                        name_upper = name.upper()
                        # Skip if this IS the activist
                        if any(t.upper() in name_upper for t in info["search_terms"]):
                            continue

                        # Extract ticker from display name
                        ticker_match = re.search(r'\(([A-Z]{1,5})\)', name)
                        if ticker_match:
                            ticker = ticker_match.group(1)
                            dedup_key = f"{ticker}_{file_date[:7]}"  # dedup by month
                            if dedup_key not in seen_targets:
                                seen_targets.add(dedup_key)
                                all_events.append({
                                    "ticker": ticker,
                                    "date": file_date,
                                    "target_name": name.strip(),
                                    "activist": activist_name,
                                    "form_type": form_type,
                                })

            except Exception as e:
                print(f"  Error {year}: {e}", file=sys.stderr)

            time.sleep(0.15)  # Rate limiting

    # Sort by date
    all_events.sort(key=lambda x: x["date"])
    return all_events


def filter_largecap(events: list[dict], min_cap: float = 500_000_000) -> list[dict]:
    """Filter events to stocks with market cap > min_cap at time of filing."""
    if yf is None:
        print("yfinance not available, skipping market cap filter")
        return events

    filtered = []
    for event in events:
        try:
            ticker_obj = yf.Ticker(event["ticker"])
            info = ticker_obj.info
            cap = info.get("marketCap", 0)
            if cap and cap >= min_cap:
                event["market_cap"] = cap
                filtered.append(event)
            else:
                print(f"  SKIP {event['ticker']} (cap={cap/1e6:.0f}M < {min_cap/1e6:.0f}M)", file=sys.stderr)
        except Exception:
            # If we can't get cap, include it (might be delisted)
            event["market_cap"] = None
            filtered.append(event)
        time.sleep(0.1)

    return filtered


def run_backtest(events: list[dict]) -> dict:
    """Run price impact analysis on activist 13D events."""
    from tools.yfinance_utils import safe_download
    import pandas as pd
    import numpy as np

    results = []
    for event in events:
        ticker = event["ticker"]
        date = event["date"]

        try:
            event_date = pd.Timestamp(date)

            # Get price data: 10 days before to 20 days after
            start = (event_date - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            end = (event_date + pd.Timedelta(days=40)).strftime("%Y-%m-%d")

            stock_data = safe_download(ticker, start, end)
            spy_data = safe_download("SPY", start, end)

            if stock_data is None or stock_data.empty or spy_data is None or spy_data.empty:
                print(f"  SKIP {ticker} {date}: no price data", file=sys.stderr)
                continue

            # Get Close column
            stock_close = stock_data["Close"].squeeze()
            spy_close = spy_data["Close"].squeeze()

            # Find entry: next trading day open after filing date
            future_dates = stock_data.index[stock_data.index > event_date]
            if len(future_dates) < 4:
                print(f"  SKIP {ticker} {date}: insufficient future data", file=sys.stderr)
                continue

            entry_date = future_dates[0]

            # Use Open price for entry
            if "Open" in stock_data.columns:
                stock_open = stock_data["Open"].squeeze()
                entry_price = float(stock_open.loc[entry_date])
            else:
                entry_price = float(stock_close.loc[entry_date])

            spy_entry = float(spy_close.loc[entry_date])

            # Calculate returns at 1d, 3d, 5d, 10d
            horizons = {"1d": 1, "3d": 3, "5d": 5, "10d": 10}
            event_result = {
                "ticker": ticker,
                "date": date,
                "entry_date": str(entry_date.date()),
                "entry_price": entry_price,
                "activist": event.get("activist"),
                "target_name": event.get("target_name", ""),
            }

            for label, days in horizons.items():
                if days < len(future_dates):
                    exit_date = future_dates[days]
                    exit_price = float(stock_close.loc[exit_date])
                    spy_exit = float(spy_close.loc[exit_date])

                    raw_return = (exit_price - entry_price) / entry_price * 100
                    spy_return = (spy_exit - spy_entry) / spy_entry * 100
                    abnormal = raw_return - spy_return

                    event_result[f"{label}_raw"] = round(raw_return, 2)
                    event_result[f"{label}_spy"] = round(spy_return, 2)
                    event_result[f"{label}_abnormal"] = round(abnormal, 2)

            results.append(event_result)

        except Exception as e:
            print(f"  ERROR {ticker} {date}: {e}", file=sys.stderr)
            continue

        time.sleep(0.1)

    # Aggregate statistics
    if not results:
        return {"events": [], "summary": "No events with price data"}

    import numpy as np
    from scipy import stats

    summary = {"n": len(results)}
    for horizon in ["1d", "3d", "5d", "10d"]:
        key = f"{horizon}_abnormal"
        values = [r[key] for r in results if key in r]
        if values:
            arr = np.array(values)
            t_stat, p_val = stats.ttest_1samp(arr, 0)
            pos_rate = np.mean(arr > 0) * 100
            summary[horizon] = {
                "mean": round(float(np.mean(arr)), 2),
                "median": round(float(np.median(arr)), 2),
                "std": round(float(np.std(arr)), 2),
                "pos_rate": round(pos_rate, 1),
                "t_stat": round(float(t_stat), 3),
                "p_value": round(float(p_val), 4),
                "n": len(values),
            }

    return {"events": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="Backtest activist 13D filings")
    parser.add_argument("--activist", required=True, help="Activist name")
    parser.add_argument("--years", default="2020-2024", help="Year range (e.g., 2020-2024)")
    parser.add_argument("--skip-cap-filter", action="store_true", help="Skip market cap filter")
    parser.add_argument("--min-cap", type=float, default=500_000_000, help="Min market cap")
    args = parser.parse_args()

    start_year, end_year = map(int, args.years.split("-"))

    print(f"Searching EDGAR for {args.activist} SC 13D filings ({start_year}-{end_year})...")
    events = search_activist_13d(args.activist, start_year, end_year)
    print(f"  Found {len(events)} initial 13D filings")

    if not events:
        print("No events found.")
        return

    for e in events:
        print(f"  {e['date']} {e['ticker']:6s} {e['target_name'][:50]}")

    if not args.skip_cap_filter:
        print(f"\nFiltering to market cap > ${args.min_cap/1e6:.0f}M...")
        events = filter_largecap(events, args.min_cap)
        print(f"  {len(events)} events after cap filter")

    if not events:
        print("No events after filtering.")
        return

    print(f"\nRunning price impact backtest on {len(events)} events...")
    results = run_backtest(events)

    print(f"\n{'='*60}")
    print(f"RESULTS: {args.activist} SC 13D Filing Impact")
    print(f"{'='*60}")
    print(json.dumps(results["summary"], indent=2))

    print(f"\nIndividual events:")
    for r in results["events"]:
        ab3 = r.get("3d_abnormal", "?")
        ab5 = r.get("5d_abnormal", "?")
        print(f"  {r['date']} {r['ticker']:6s} 3d={ab3:+.1f}% 5d={ab5:+.1f}%")

    # Output JSON for pipeline
    print(f"\n### JSON ###")
    print(json.dumps(results["summary"], indent=2))


if __name__ == "__main__":
    main()
