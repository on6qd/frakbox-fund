#!/usr/bin/env python3
"""Test whether SEO bought-deal stocks mean-revert after the validated 5d drop.

If stocks bounce from day 5-10, we could go LONG at day 5 after closing the short.
"""
import sys, json
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from pathlib import Path
from tools.yfinance_utils import safe_download
from scipy import stats

# Load cached deals with tickers
cache_path = Path('data/seo_cik_ticker_cache.json')
with open(cache_path) as f:
    ticker_cache = json.load(f)

raw_path = Path('data/seo_bought_deals_raw.json')
with open(raw_path) as f:
    raw_deals = json.load(f)

# Filter to deals with tickers, same-day filing, >$500M
events = []
seen = set()
for d in raw_deals:
    cik = d['cik']
    ticker = d.get('ticker') or ticker_cache.get(cik)
    if not ticker:
        continue
    gap = d.get('days_between', 99)
    if gap > 3:
        continue
    date = d.get('424b4_date') or d.get('announcement_date')
    if not date:
        continue
    key = f"{ticker}_{date[:7]}"
    if key in seen:
        continue
    seen.add(key)
    events.append({'ticker': ticker, 'date': date})

print(f"Processing {len(events)} events...")

# Download SPY benchmark
spy_data = safe_download("SPY", start="2019-06-01", end="2026-04-15")
if spy_data.empty:
    print("ERROR: Could not download SPY data")
    sys.exit(1)

spy_returns = spy_data['Close'].pct_change()

results = {h: [] for h in [5, 10, 20]}
day5_to_10 = []
day5_to_20 = []

for ev in events:
    ticker = ev['ticker']
    date = ev['date']

    try:
        stock = safe_download(ticker, start="2019-06-01", end="2026-04-15")
        if stock.empty or len(stock) < 25:
            continue

        # Find the filing date index
        filing_idx = stock.index.get_indexer([pd.Timestamp(date)], method='ffill')[0]
        if filing_idx < 0 or filing_idx >= len(stock) - 1:
            continue

        # Entry = next trading day open after filing
        entry_idx = filing_idx + 1
        if entry_idx >= len(stock):
            continue

        entry_price = stock['Open'].iloc[entry_idx]
        entry_date = stock.index[entry_idx]

        for horizon in [5, 10, 20]:
            exit_idx = entry_idx + horizon
            if exit_idx >= len(stock):
                continue

            exit_price = stock['Close'].iloc[exit_idx]
            stock_return = (exit_price / entry_price - 1) * 100

            # SPY benchmark
            spy_entry_idx = spy_data.index.get_indexer([entry_date], method='ffill')[0]
            spy_exit_idx = spy_entry_idx + horizon
            if spy_exit_idx >= len(spy_data):
                continue

            spy_entry = spy_data['Open'].iloc[spy_entry_idx]
            spy_exit = spy_data['Close'].iloc[spy_exit_idx]
            spy_return = (spy_exit / spy_entry - 1) * 100

            abnormal = stock_return - spy_return
            results[horizon].append({
                'ticker': ticker,
                'date': date,
                'raw': stock_return,
                'spy': spy_return,
                'abnormal': abnormal,
            })

        # Day 5-to-10 return (for mean reversion test)
        if entry_idx + 10 < len(stock) and entry_idx + 5 < len(stock):
            day5_price = stock['Close'].iloc[entry_idx + 5]
            day10_price = stock['Close'].iloc[entry_idx + 10]

            spy_day5 = spy_data['Close'].iloc[spy_data.index.get_indexer([stock.index[entry_idx + 5]], method='ffill')[0]]
            spy_day10 = spy_data['Close'].iloc[spy_data.index.get_indexer([stock.index[entry_idx + 10]], method='ffill')[0]]

            stock_5to10 = (day10_price / day5_price - 1) * 100
            spy_5to10 = (spy_day10 / spy_day5 - 1) * 100
            day5_to_10.append(stock_5to10 - spy_5to10)

        if entry_idx + 20 < len(stock) and entry_idx + 5 < len(stock):
            day5_price = stock['Close'].iloc[entry_idx + 5]
            day20_price = stock['Close'].iloc[entry_idx + 20]

            spy_day5 = spy_data['Close'].iloc[spy_data.index.get_indexer([stock.index[entry_idx + 5]], method='ffill')[0]]
            spy_day20 = spy_data['Close'].iloc[spy_data.index.get_indexer([stock.index[entry_idx + 20]], method='ffill')[0]]

            stock_5to20 = (day20_price / day5_price - 1) * 100
            spy_5to20 = (spy_day20 / spy_day5 - 1) * 100
            day5_to_20.append(stock_5to20 - spy_5to20)

    except Exception as e:
        continue

print("\n" + "="*60)
print("SEO BOUGHT-DEAL MULTI-HORIZON ANALYSIS")
print("="*60)

for h in [5, 10, 20]:
    data = [r['abnormal'] for r in results[h]]
    if not data:
        continue
    arr = np.array(data)
    t_stat, p_val = stats.ttest_1samp(arr, 0)
    _, wilcoxon_p = stats.wilcoxon(arr)
    neg_rate = (arr < 0).mean() * 100

    # Remove outlier (SBET) for robust stats
    trimmed = arr[(arr > np.percentile(arr, 2)) & (arr < np.percentile(arr, 98))]

    print(f"\n{h}d ABNORMAL RETURNS (N={len(arr)}):")
    print(f"  Mean:    {arr.mean():+.2f}%")
    print(f"  Median:  {np.median(arr):+.2f}%")
    print(f"  Trimmed: {trimmed.mean():+.2f}% (2-98 pctl)")
    print(f"  Neg rate: {neg_rate:.1f}%")
    print(f"  t-test p: {p_val:.4f}")
    print(f"  Wilcoxon p: {wilcoxon_p:.4f}")

if day5_to_10:
    arr = np.array(day5_to_10)
    t_stat, p = stats.ttest_1samp(arr, 0)
    print(f"\nDAY 5-TO-10 ABNORMAL (N={len(arr)}):")
    print(f"  Mean: {arr.mean():+.2f}%")
    print(f"  Median: {np.median(arr):+.2f}%")
    print(f"  Pos rate: {(arr > 0).mean()*100:.1f}%")
    print(f"  p-value: {p:.4f}")

if day5_to_20:
    arr = np.array(day5_to_20)
    t_stat, p = stats.ttest_1samp(arr, 0)
    print(f"\nDAY 5-TO-20 ABNORMAL (N={len(arr)}):")
    print(f"  Mean: {arr.mean():+.2f}%")
    print(f"  Median: {np.median(arr):+.2f}%")
    print(f"  Pos rate: {(arr > 0).mean()*100:.1f}%")
    print(f"  p-value: {p:.4f}")

# Summary
print("\n" + "="*60)
print("MEAN REVERSION ASSESSMENT:")
if day5_to_10:
    d5_10 = np.mean(day5_to_10)
    if d5_10 > 0.5 and p < 0.1:
        print("  SIGNAL: Mean reversion detected day 5-10. Potential LONG entry.")
    elif d5_10 > 0:
        print("  WEAK: Slight positive drift day 5-10, but not significant.")
    else:
        print("  NO REVERSION: Stocks continue to underperform after day 5.")
