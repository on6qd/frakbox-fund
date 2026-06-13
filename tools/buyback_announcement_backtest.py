#!/usr/bin/env python3
"""
Share Repurchase Authorization Signal Backtest
Tests: When large S&P 500 companies announce major buyback programs
($1B+ or equivalent), do stocks outperform over the next 5-20 days?

Mechanism:
1. Actors/Incentives: Management signals stock is undervalued; uses excess cash
2. Transmission: Reduced float, EPS accretion, positive signal effect
3. Academic: Ikenberry et al. (1995) JFE - 12% 4yr drift after open market repurchase;
   Peyer & Vermaelen (2009) - smaller effect for larger caps;
   Bonaimé et al. (2020) - announcement vs. execution effects

Data: SEC EDGAR 8-K filings with Item 8.01 (other events) mentioning share repurchase
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from scipy import stats
import time
import re
import ast

HEADERS = {'User-Agent': 'financial-research@test.com'}

def get_cik_ticker_map():
    """Get ticker to CIK reverse mapping."""
    url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code == 200:
        data = r.json()
        ticker_to_cik = {}
        cik_to_ticker = {}
        for key, val in data.items():
            cik = str(val['cik_str']).zfill(10)
            ticker = val['ticker'].upper()
            ticker_to_cik[ticker] = cik
            cik_to_ticker[cik] = ticker
        return ticker_to_cik, cik_to_ticker
    return {}, {}


def search_buyback_8ks_page(start_date, end_date, from_offset=0):
    """Search EDGAR EFTS for buyback 8-K filings."""
    query = '%22repurchase+program%22+%22billion%22'
    url = (f"https://efts.sec.gov/LATEST/search-index?"
           f"q={query}&dateRange=custom&startdt={start_date}&enddt={end_date}"
           f"&forms=8-K&_source=_source&from={from_offset}&hits.hits.total.value=true")

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            hits = data.get('hits', {}).get('hits', [])
            total = data.get('hits', {}).get('total', {}).get('value', 0)
            return hits, total
    except Exception as e:
        print(f"  Error: {e}")
    return [], 0


def extract_ticker_from_display_name(display_name):
    """Extract ticker from EDGAR display_names field like 'APPLE INC (AAPL) (CIK 0000320193)'."""
    match = re.search(r'\(([A-Z]{1,5})\)\s*\(CIK', display_name)
    if match:
        return match.group(1)
    # Try simpler pattern
    match = re.search(r'\s\(([A-Z]{1,5})\)\s', display_name)
    if match:
        return match.group(1)
    return None


def get_sp500_tickers():
    """Load S&P 500 tickers from universe file."""
    try:
        with open('data/sp500_universe.json') as f:
            data = json.load(f)
        return set(data.get('tickers', []))
    except:
        # Fallback to common large caps
        return {'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'TSLA', 'JPM',
                'BAC', 'GS', 'MS', 'V', 'MA', 'UNH', 'JNJ', 'XOM', 'CVX', 'HD',
                'WMT', 'MCD', 'NFLX', 'AMGN', 'ABBV', 'COP', 'CAT', 'DE',
                'AAPL', 'INTC', 'QCOM', 'MMM', 'HON', 'IBM', 'GE', 'F', 'GM'}


def collect_buyback_events(years=None):
    """Collect buyback announcement events from SEC EDGAR."""
    if years is None:
        years = list(range(2021, 2026))

    sp500 = get_sp500_tickers()
    events = []

    for year in years:
        # Q1 and Q2 of each year (limit to avoid too many API calls)
        for q_start, q_end in [
            (f"{year}-01-01", f"{year}-03-31"),
            (f"{year}-04-01", f"{year}-06-30"),
            (f"{year}-07-01", f"{year}-09-30"),
            (f"{year}-10-01", f"{year}-12-31"),
        ]:
            hits, total = search_buyback_8ks_page(q_start, q_end)
            print(f"  {q_start} to {q_end}: {len(hits)} of {total} hits")

            for hit in hits:
                src = hit.get('_source', {})
                file_date = src.get('file_date', '')

                # Get display names
                display_names = src.get('display_names', '')
                if isinstance(display_names, str):
                    try:
                        display_names = ast.literal_eval(display_names)
                    except:
                        display_names = [display_names]

                if not isinstance(display_names, list):
                    continue

                for name in display_names:
                    ticker = extract_ticker_from_display_name(name)
                    if ticker and ticker in sp500 and file_date:
                        try:
                            event_date = date.fromisoformat(file_date[:10])
                            events.append({
                                'ticker': ticker,
                                'announcement_date': event_date,
                                'year': event_date.year,
                                'name': name[:50],
                            })
                        except:
                            pass

            time.sleep(0.5)  # Rate limit

    # Deduplicate (same ticker + week)
    seen = set()
    deduped = []
    for e in events:
        key = (e['ticker'], e['announcement_date'].isocalendar()[:2])  # year+week
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    return deduped


def measure_post_announcement_return(ticker, announcement_date, horizon_days):
    """Measure abnormal return for horizon_days after announcement."""
    start = announcement_date - timedelta(days=5)
    end = announcement_date + timedelta(days=horizon_days + 10)

    try:
        data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)

        if data.empty or spy.empty or len(data) < 3:
            return None

        # Handle multi-level columns
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.get_level_values(0)

        # Find entry: next trading day after announcement
        entry_idx = None
        for i, dt in enumerate(data.index):
            if dt.date() > announcement_date:
                entry_idx = i
                break

        if entry_idx is None or entry_idx + horizon_days > len(data):
            return None

        exit_idx = min(entry_idx + horizon_days, len(data) - 1)

        entry_price = float(data['Close'].iloc[entry_idx])
        exit_price = float(data['Close'].iloc[exit_idx])

        # SPY for same window
        entry_spy = spy['Close'].iloc[entry_idx] if entry_idx < len(spy) else None
        exit_spy = spy['Close'].iloc[exit_idx] if exit_idx < len(spy) else None

        if entry_spy is None or exit_spy is None:
            return None

        stock_ret = (exit_price - entry_price) / entry_price
        spy_ret = (float(exit_spy) - float(entry_spy)) / float(entry_spy)
        abnormal = stock_ret - spy_ret

        return {
            'ticker': ticker,
            'signal_date': announcement_date,
            'entry_date': data.index[entry_idx].date(),
            'exit_date': data.index[exit_idx].date(),
            'horizon': horizon_days,
            'stock_return': round(float(stock_ret) * 100, 3),
            'spy_return': round(float(spy_ret) * 100, 3),
            'abnormal_return': round(float(abnormal) * 100, 3),
            'directionally_correct': abnormal > 0.005,
        }
    except Exception as e:
        return None


def main():
    print("=" * 65)
    print("SHARE REPURCHASE AUTHORIZATION ANNOUNCEMENT SIGNAL BACKTEST")
    print("=" * 65)
    print()

    print("Step 1: Loading S&P 500 universe...")
    sp500 = get_sp500_tickers()
    print(f"  {len(sp500)} S&P 500 tickers loaded")

    print("\nStep 2: Collecting buyback announcement 8-Ks from EDGAR...")
    print("  (Searching 2021-2025, filtering to S&P 500 large caps)")
    events = collect_buyback_events(years=[2021, 2022, 2023, 2024, 2025])

    print(f"\n  Total unique buyback events found: {len(events)}")

    if len(events) < 10:
        print("  Too few events - check EDGAR search query")
        return

    # Show sample
    print("\n  Sample events:")
    for e in events[:10]:
        print(f"    {e['ticker']} {e['announcement_date']} | {e['name']}")

    # Split: discovery 2021-2022, validation 2023-2025
    discovery = [e for e in events if e['year'] in [2021, 2022]]
    validation = [e for e in events if e['year'] in [2023, 2024, 2025]]
    print(f"\n  Discovery (2021-2022): {len(discovery)} events")
    print(f"  Validation (2023-2025): {len(validation)} events")

    if len(discovery) < 10:
        print("  Insufficient discovery events. Using all years.")
        discovery = events

    print("\nStep 3: Measuring abnormal returns (discovery period)...")
    horizons = [3, 5, 10, 20]
    discovery_results = {h: [] for h in horizons}

    for i, event in enumerate(discovery):
        if i % 10 == 0:
            print(f"  Processing {i}/{len(discovery)}...")
        for horizon in horizons:
            result = measure_post_announcement_return(
                event['ticker'], event['announcement_date'], horizon
            )
            if result:
                discovery_results[horizon].append(result)
        time.sleep(0.05)

    print("\n" + "=" * 65)
    print("RESULTS: DISCOVERY PERIOD")
    print("=" * 65)

    passes_multiple_testing = False
    significant_horizons = []

    for horizon in horizons:
        results = discovery_results[horizon]
        if not results:
            print(f"\n  {horizon}d: No data")
            continue

        abnormals = [r['abnormal_return'] for r in results]
        correct = [r['directionally_correct'] for r in results]

        t_stat, p_val = stats.ttest_1samp(abnormals, 0)
        mean_abn = np.mean(abnormals)
        dir_rate = sum(correct) / len(correct)

        print(f"\n  {horizon}d horizon (n={len(results)}):")
        print(f"    Mean abnormal return: {mean_abn:.3f}%")
        print(f"    Median abnormal: {np.median(abnormals):.3f}%")
        print(f"    Direction > +0.5%: {sum(correct)}/{len(results)} = {dir_rate*100:.1f}%")
        print(f"    t-statistic: {t_stat:.3f}, p-value: {p_val:.4f}")

        if p_val < 0.05:
            significant_horizons.append(horizon)
            print(f"    *** SIGNIFICANT at p<0.05 ***")
        if p_val < 0.01:
            print(f"    *** HIGHLY SIGNIFICANT at p<0.01 ***")

    # Multiple testing check
    if len(significant_horizons) >= 2 or (len(significant_horizons) >= 1 and
        any(stats.ttest_1samp([r['abnormal_return'] for r in discovery_results[h]], 0)[1] < 0.01
            for h in significant_horizons)):
        passes_multiple_testing = True

    print(f"\n  Significant horizons: {significant_horizons}")
    print(f"  Passes multiple testing: {passes_multiple_testing}")

    # Save results
    output = {
        'test_date': str(date.today()),
        'n_discovery': len(discovery),
        'n_validation': len(validation),
        'passes_multiple_testing': passes_multiple_testing,
        'significant_horizons': significant_horizons,
        'results_by_horizon': {
            str(h): {
                'n': len(discovery_results[h]),
                'mean_abnormal_pct': round(np.mean([r['abnormal_return'] for r in discovery_results[h]]), 3) if discovery_results[h] else None,
                'direction_rate': round(sum(r['directionally_correct'] for r in discovery_results[h]) / len(discovery_results[h]), 3) if discovery_results[h] else None,
                'p_value': round(stats.ttest_1samp([r['abnormal_return'] for r in discovery_results[h]], 0)[1], 4) if len(discovery_results[h]) > 1 else None
            }
            for h in horizons
        },
        'sample_events': [
            {'ticker': e['ticker'], 'date': str(e['announcement_date'])}
            for e in events[:20]
        ]
    }

    with open('/tmp/buyback_backtest_results.json', 'w') as f:
        json.dump(output, f, indent=2)
    print("\nResults saved to /tmp/buyback_backtest_results.json")

    if passes_multiple_testing:
        mean_5d = np.mean([r['abnormal_return'] for r in discovery_results.get(5, [])])
        print(f"\n>>> SIGNAL DETECTED: Mean 5d abnormal {mean_5d:.2f}%")
        print(">>> Proceed to validation period if discovery looks solid")


if __name__ == '__main__':
    main()
