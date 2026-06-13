#!/usr/bin/env python3
"""
SEO Bought-Deal Scanner

Identifies secondary equity offering "bought deals" from EDGAR filings.
Bought deals = overnight block trades where announcement and pricing happen simultaneously.

Detection method: 424B4 prospectus filed within 1 business day of an 8-K filing
from the same company. This avoids the timing ambiguity of regular roadshow offerings.

Usage:
    python tools/seo_bought_deal_scanner.py --years 2020 2021 2022 2023 2024
    python tools/seo_bought_deal_scanner.py --year 2023 --min-market-cap 1000000000
"""

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
CACHE_DIR = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/seo_cache'))
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_quarterly_index(year, qtr):
    """Fetch EDGAR quarterly form.idx and return DataFrame of filings.

    form.idx is fixed-width with columns:
      Form Type (0-12), Company Name (12-74), CIK (74-86), Date Filed (86-98), File Name (98+)
    The dashed separator line marks where data rows begin.
    """
    # Use form.idx (sorted by form type) — fixed-width, accessible with proper User-Agent
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/form.idx"
    cache_file = CACHE_DIR / f"edgar_form_idx_{year}_Q{qtr}.csv.gz"

    if cache_file.exists():
        print(f"  Loading cached index: {year} Q{qtr}")
        return pd.read_csv(cache_file, dtype=str)

    print(f"  Fetching EDGAR index: {year} Q{qtr} ...")
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        print(f"  Failed to fetch {url}: {resp.status_code}")
        return pd.DataFrame()

    lines = resp.text.splitlines()

    # Use regex to reliably parse variable-width fixed-format lines.
    # Regex: form_type (no spaces), company name (anything), CIK (digits),
    #        date (YYYY-MM-DD), filename (starts with edgar/)
    import re
    PATTERN = re.compile(
        r'^(\S+)\s+(.*?)\s{2,}(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)'
    )

    records = []
    for line in lines:
        m = PATTERN.match(line.strip())
        if not m:
            continue
        records.append({
            'company_name': m.group(2).strip(),
            'form_type': m.group(1),
            'cik': m.group(3).lstrip('0') or '0',  # strip leading zeros for consistency
            'date_filed': m.group(4),
            'filename': m.group(5)
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df.to_csv(cache_file, index=False, compression='gzip')
    print(f"  Loaded {len(df)} filings from {year} Q{qtr}")
    return df


def find_bought_deals(years=None, min_market_cap=500_000_000):
    """Find bought deals: 424B4 + 8-K filed within 3 calendar days."""
    if years is None:
        years = [2020, 2021, 2022, 2023, 2024]

    all_filings = []
    for year in years:
        for qtr in [1, 2, 3, 4]:
            df = get_quarterly_index(year, qtr)
            if not df.empty:
                all_filings.append(df)
            time.sleep(0.2)

    if not all_filings:
        print("No filings loaded.")
        return []

    combined = pd.concat(all_filings, ignore_index=True)
    combined['date_filed'] = pd.to_datetime(combined['date_filed'], errors='coerce')

    # Separate 424B4 and 8-K filings
    filings_424b4 = combined[combined['form_type'] == '424B4'].copy()
    filings_8k = combined[combined['form_type'] == '8-K'].copy()

    print(f"\nTotal 424B4 filings: {len(filings_424b4)}")
    print(f"Total 8-K filings: {len(filings_8k)}")

    # Build 8-K lookup by CIK for fast join
    eighk_by_cik = filings_8k.groupby('cik')['date_filed'].apply(list).to_dict()

    bought_deals = []
    skipped_no_8k = 0
    skipped_gap_too_large = 0

    for _, row in filings_424b4.iterrows():
        cik = row['cik']
        date = row['date_filed']
        if pd.isna(date):
            continue

        if cik not in eighk_by_cik:
            skipped_no_8k += 1
            continue

        # Look for 8-K from same CIK within 3 calendar days before the 424B4
        window_start = date - timedelta(days=3)  # covers weekends
        window_end = date  # 424B4 can be same day or shortly after 8-K

        matching_dates = [
            d for d in eighk_by_cik[cik]
            if not pd.isna(d) and window_start <= d <= window_end
        ]

        if not matching_dates:
            skipped_gap_too_large += 1
            continue

        # Use the most recent 8-K before the 424B4
        best_8k_date = max(matching_dates)
        gap_days = (date - best_8k_date).days

        bought_deals.append({
            'cik': cik,
            'company_name': row['company_name'],
            'announcement_date': date.strftime('%Y-%m-%d'),
            '424b4_date': date.strftime('%Y-%m-%d'),
            '8k_date': best_8k_date.strftime('%Y-%m-%d'),
            'days_between': gap_days,
            'ticker': None,
        })

    print(f"\nSkipped (no 8-K ever filed by company): {skipped_no_8k}")
    print(f"Skipped (8-K gap > 3 days): {skipped_gap_too_large}")
    print(f"Found {len(bought_deals)} potential bought deals (424B4 + 8-K within 3 days)")
    return bought_deals


def resolve_ticker(cik):
    """Resolve CIK to ticker using EDGAR submissions API."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tickers = data.get('tickers', [])
            if tickers:
                return tickers[0]
    except Exception:
        pass
    return None


def resolve_tickers_batch(bought_deals, sample_size=None, sleep_secs=0.15):
    """Add ticker field to each deal by calling EDGAR submissions API."""
    deals_to_resolve = bought_deals[:sample_size] if sample_size else bought_deals
    cik_to_ticker = {}
    unique_ciks = list({d['cik'] for d in deals_to_resolve})
    print(f"\nResolving tickers for {len(unique_ciks)} unique CIKs...")

    for i, cik in enumerate(unique_ciks):
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(unique_ciks)}")
        cik_to_ticker[cik] = resolve_ticker(cik)
        time.sleep(sleep_secs)

    for deal in deals_to_resolve:
        deal['ticker'] = cik_to_ticker.get(deal['cik'])

    resolved = sum(1 for d in deals_to_resolve if d.get('ticker'))
    print(f"Resolved {resolved}/{len(deals_to_resolve)} deals to tickers")
    return deals_to_resolve


def filter_by_market_cap(deals, min_market_cap=500_000_000):
    """Filter deals to those with sufficient market cap using yfinance."""
    qualified = []
    skipped = 0
    for deal in deals:
        ticker = deal.get('ticker')
        if not ticker:
            skipped += 1
            continue
        try:
            info = yf.Ticker(ticker).info
            mc = info.get('marketCap', 0) or 0
            if mc >= min_market_cap:
                deal['market_cap'] = mc
                qualified.append(deal)
            else:
                skipped += 1
        except Exception:
            skipped += 1
    print(f"Market cap filter: {len(qualified)} kept, {skipped} skipped")
    return qualified


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="SEO Bought-Deal Scanner")
    parser.add_argument('--years', nargs='+', type=int, default=[2022, 2023, 2024])
    parser.add_argument('--year', type=int, help="Single year (overrides --years)")
    parser.add_argument('--min-market-cap', type=float, default=500_000_000,
                        help="Minimum market cap in dollars (default 500M)")
    parser.add_argument('--resolve-tickers', action='store_true',
                        help="Resolve CIK -> ticker via EDGAR API (slow, ~0.15s per CIK)")
    parser.add_argument('--filter-market-cap', action='store_true',
                        help="Filter by market cap after resolving tickers (requires --resolve-tickers)")
    parser.add_argument('--sample', type=int, default=None,
                        help="Only resolve/filter first N deals (for fast testing)")
    args = parser.parse_args()

    years = [args.year] if args.year else args.years
    print(f"Scanning years: {years}")

    deals = find_bought_deals(years=years, min_market_cap=args.min_market_cap)

    if args.resolve_tickers and deals:
        deals = resolve_tickers_batch(deals, sample_size=args.sample)

    if args.filter_market_cap and args.resolve_tickers and deals:
        deals = filter_by_market_cap(deals, min_market_cap=args.min_market_cap)

    print(f"\nSample of first 20 potential bought deals:")
    for d in deals[:20]:
        ticker_str = d.get('ticker') or 'N/A'
        print(f"  {d['company_name'][:35]:35s} | {ticker_str:6s} | {d['announcement_date']} | 8-K: {d['8k_date']} | gap: {d['days_between']}d")

    output_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/seo_bought_deals_raw.json')
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(deals, f, indent=2, default=str)
    print(f"\nSaved {len(deals)} events to {output_file}")

    # Summary stats
    if deals:
        gap_0 = sum(1 for d in deals if d['days_between'] == 0)
        gap_1 = sum(1 for d in deals if d['days_between'] == 1)
        gap_2 = sum(1 for d in deals if d['days_between'] == 2)
        gap_3 = sum(1 for d in deals if d['days_between'] == 3)
        print(f"\nGap distribution:")
        print(f"  Same day (gap=0): {gap_0} ({100*gap_0//len(deals)}%)")
        print(f"  1 day gap:        {gap_1} ({100*gap_1//len(deals)}%)")
        print(f"  2 day gap:        {gap_2} ({100*gap_2//len(deals)}%)")
        print(f"  3 day gap:        {gap_3} ({100*gap_3//len(deals)}%)")
