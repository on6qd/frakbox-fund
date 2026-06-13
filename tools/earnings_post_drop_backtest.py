"""
Post-earnings large negative drift backtest.
Tests: after a large earnings-day drop (>5%), does the stock continue to decline over 5 days?
This is a proxy for guidance cut events (guidance cuts tend to cause large day-0 drops).

If validated, would support the earnings_guidance_cut_drift hypothesis.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from tools.yfinance_utils import safe_download

# S&P 500 top stocks
TICKERS = [
    'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'NVDA', 'BRK-B', 'JPM', 'V', 'UNH',
    'MA', 'XOM', 'JNJ', 'PG', 'HD', 'CVX', 'MRK', 'ABBV', 'COST', 'PEP',
    'KO', 'AVGO', 'CSCO', 'TMO', 'ACN', 'LLY', 'ABT', 'NKE', 'WMT', 'MCD',
    'VZ', 'INTC', 'CMCSA', 'ADBE', 'CRM', 'NFLX', 'T', 'RTX', 'LOW', 'TXN',
    'NEE', 'AMGN', 'BMY', 'PM', 'QCOM', 'CAT', 'HON', 'BA', 'IBM', 'GE',
    'UPS', 'DE', 'SBUX', 'SPGI', 'MMM', 'AXP', 'GS', 'MS', 'BLK', 'C',
    'WFC', 'BAC', 'USB', 'TGT', 'CVS', 'CI', 'HUM', 'MCK', 'AIG', 'MET',
    'F', 'GM', 'FCX', 'NUE', 'CLF', 'X', 'AA', 'DD', 'DOW', 'EMN',
    'DIS', 'PARA', 'WBD', 'NWSA', 'OMC', 'IPG', 'PH', 'EMR', 'ETN', 'ROK',
    'DHR', 'WAT', 'A', 'ZBH', 'BSX', 'SYK', 'MDT', 'BAX', 'EW', 'ISRG'
]

import yfinance as yf

print("Loading price data...")
events = []

for ticker in TICKERS:
    try:
        # Get earnings dates  
        t = yf.Ticker(ticker)
        edf = t.earnings_dates
        if edf is None or edf.empty:
            continue
        
        # Get price data
        px = safe_download(ticker, start='2020-01-01', end='2026-03-28')
        if px is None or px.empty:
            continue
        
        px = px.sort_index()
        
        # For each earnings date, compute day0 and day1-5 returns
        for edate in edf.index:
            edate = pd.Timestamp(edate).tz_localize(None)
            
            # Find price on/after earnings date
            # Earnings reported after close -> next trading day is "day 0" reaction
            idx = px.index.searchsorted(edate)
            if idx >= len(px) - 6:  # Need 5 more days
                continue
            
            # Get prices
            day0_close = px.iloc[idx]['Close'] if 'Close' in px.columns else None
            
            # Check if we need to get the right day
            actual_date = px.index[idx]
            if abs((actual_date - edate).days) > 3:
                continue  # Too far, skip
            
            if idx == 0:
                continue
                
            day_before = px.iloc[idx-1]['Close'] if 'Close' in px.columns else None
            
            if day0_close is None or day_before is None or day_before == 0:
                continue
            
            day0_ret = (day0_close / day_before - 1) * 100
            
            # Get SPY return for same day (benchmark)
            # (We'll add benchmark adjustment later)
            
            # Get day 1-5 returns
            if idx + 5 < len(px):
                day5_close = px.iloc[idx+5]['Close']
                day5_ret = (day5_close / day0_close - 1) * 100
            else:
                continue
                
            events.append({
                'ticker': ticker,
                'date': actual_date,
                'day0_ret': day0_ret,
                'day5_ret': day5_ret
            })
    except Exception as e:
        continue

df = pd.DataFrame(events)
print(f"Total earnings events: {len(df)}")

if df.empty:
    print("No data collected")
    sys.exit(1)

# Analyze: large negative day0 events
thresholds = [-3, -5, -7, -10]

print(f"\n{'Threshold':<12} {'N':<8} {'Dir%':<8} {'Avg5d':<10} {'p-val':<8}")
print("-" * 50)

from scipy import stats

for thresh in thresholds:
    subset = df[df['day0_ret'] < thresh]
    if len(subset) < 10:
        continue
    
    n = len(subset)
    direction_pct = (subset['day5_ret'] < 0).mean() * 100
    avg_5d = subset['day5_ret'].mean()
    
    # t-test (null: 5d drift = 0)
    t_stat, p_val = stats.ttest_1samp(subset['day5_ret'], 0)
    
    print(f"Day0<{thresh}%   {n:<8} {direction_pct:<8.1f} {avg_5d:<10.2f} {p_val:<8.4f}")

print()
print("Control: Day0 > 0 (positive day)")
pos = df[df['day0_ret'] > 0]
if len(pos) > 10:
    t_stat, p_val = stats.ttest_1samp(pos['day5_ret'], 0)
    print(f"Day0>0%    {len(pos):<8} {(pos['day5_ret'] < 0).mean()*100:<8.1f} {pos['day5_ret'].mean():<10.2f} {p_val:<8.4f}")

# Benchmark adjustment: get SPY data
print("\nLoading SPY for benchmark adjustment...")
spy = safe_download('SPY', start='2020-01-01', end='2026-03-28')
if spy is not None and not spy.empty:
    spy = spy.sort_index()
    spy_rets = spy['Close'].pct_change() * 100
    
    # Add SPY benchmark to each event
    def get_spy_5d(edate):
        idx = spy.index.searchsorted(edate)
        if idx >= len(spy) - 5:
            return None
        spy_entry = spy['Close'].iloc[idx]
        spy_exit = spy['Close'].iloc[idx+5]
        return (spy_exit / spy_entry - 1) * 100
    
    df['spy_5d'] = df['date'].apply(get_spy_5d)
    df['abnormal_5d'] = df['day5_ret'] - df['spy_5d']
    df_clean = df.dropna(subset=['abnormal_5d'])
    
    print(f"\nABNORMAL RETURNS (vs SPY benchmark, n={len(df_clean)}):")
    print(f"{'Threshold':<12} {'N':<8} {'Dir%':<8} {'Avg Abn':<12} {'p-val':<8}")
    print("-" * 55)
    
    for thresh in thresholds:
        subset = df_clean[df_clean['day0_ret'] < thresh]
        if len(subset) < 10:
            continue
        
        n = len(subset)
        direction_pct = (subset['abnormal_5d'] < 0).mean() * 100
        avg_abn = subset['abnormal_5d'].mean()
        t_stat, p_val = stats.ttest_1samp(subset['abnormal_5d'], 0)
        
        print(f"Day0<{thresh}%   {n:<8} {direction_pct:<8.1f} {avg_abn:<12.2f} {p_val:<8.4f}")
    
    # Largest events - what are they?
    print("\nTop 10 largest single-day drops with worst 5d drift:")
    worst = df_clean[df_clean['day0_ret'] < -5].nsmallest(10, 'day0_ret')
    for _, row in worst.head(10).iterrows():
        print(f"  {row['ticker']} {row['date'].date()} day0={row['day0_ret']:.1f}% 5d={row['day5_ret']:.1f}% abn={row['abnormal_5d']:.1f}%")

