#!/usr/bin/env python3
"""
VIX Spike Recovery Backtest
Signal: When VIX closes above 30, buy SPY next open, hold 5 days.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yfinance as yf
import pandas as pd
import numpy as np
from scipy import stats
from datetime import date, timedelta
import warnings
warnings.filterwarnings('ignore')

# Download VIX and SPY data
print("Downloading VIX and SPY data (2015-2026)...")
vix = yf.download("^VIX", start="2015-01-01", end="2026-03-23", progress=False, auto_adjust=True)
spy = yf.download("SPY", start="2015-01-01", end="2026-03-24", progress=False, auto_adjust=True)

# Flatten columns if multi-level
if isinstance(vix.columns, pd.MultiIndex):
    vix.columns = vix.columns.get_level_values(0)
if isinstance(spy.columns, pd.MultiIndex):
    spy.columns = spy.columns.get_level_values(0)

print(f"VIX rows: {len(vix)}, SPY rows: {len(spy)}")

# Find days where VIX CLOSED above 30
vix_above_30 = vix[vix['Close'] > 30].copy()
print(f"\nDays VIX closed above 30: {len(vix_above_30)}")

# Identify distinct events (clusters within 30 days = same event)
events = []
last_event = None
for dt in vix_above_30.index:
    if last_event is None or (dt - last_event).days > 30:
        events.append(dt)
        last_event = dt
        
print(f"Distinct VIX>30 events (30d clustering): {len(events)}")
print("Events:")
for e in events:
    print(f"  {e.date()} VIX close: {vix['Close'].loc[e]:.2f}")

# Measure SPY returns after each event
results = []
spy_close = spy['Close']
spy_open = spy['Open']

for event_date in events:
    # Entry: open of next trading day after VIX>30 close
    future_dates = spy_open[spy_open.index > event_date]
    if len(future_dates) < 2:
        continue
    
    entry_date = future_dates.index[0]
    entry_price = future_dates.iloc[0]  # open
    
    # 3-day, 5-day, 10-day exits (using closes)
    future_close = spy_close[spy_close.index >= entry_date]
    
    for horizon in [3, 5, 10]:
        if len(future_close) > horizon:
            exit_price = future_close.iloc[horizon]
            ret = (exit_price - entry_price) / entry_price
            results.append({
                'event_date': event_date.date(),
                'entry_date': entry_date.date(),
                'entry_price': round(float(entry_price), 2),
                'exit_price': round(float(exit_price), 2),
                'return': round(float(ret), 4),
                'horizon': horizon,
                'direction': 1 if ret > 0.005 else (-1 if ret < -0.005 else 0)
            })

# Analyze by horizon
print("\n" + "="*60)
print("RESULTS BY HORIZON")
print("="*60)

all_pass = True
for horizon in [3, 5, 10]:
    h_results = [r for r in results if r['horizon'] == horizon]
    if not h_results:
        continue
    
    rets = [r['return'] for r in h_results]
    dirs = [r['direction'] for r in h_results]
    pos = sum(1 for d in dirs if d == 1)
    
    t_stat, p_val = stats.ttest_1samp(rets, 0)
    
    print(f"\n{horizon}d horizon (n={len(h_results)}):")
    print(f"  Mean return: {np.mean(rets)*100:.2f}%")
    print(f"  Median return: {np.median(rets)*100:.2f}%")
    print(f"  Direction>0.5%: {pos}/{len(h_results)} = {pos/len(h_results)*100:.1f}%")
    print(f"  p-value: {p_val:.4f}")
    print(f"  passes p<0.05: {'YES' if p_val < 0.05 else 'NO'}")
    
    # Show individual events at 5d
    if horizon == 5:
        print(f"\n  Individual events (5d):")
        for r in sorted(h_results, key=lambda x: x['event_date']):
            print(f"    {r['event_date']}: {r['return']*100:+.1f}%")

# Check temporal split
print("\n" + "="*60)
print("TEMPORAL SPLIT")
print("="*60)

for horizon in [3, 5, 10]:
    h_results = [r for r in results if r['horizon'] == horizon]
    discovery = [r for r in h_results if r['event_date'].year <= 2021]
    validation = [r for r in h_results if r['event_date'].year >= 2022]
    
    if discovery and validation:
        d_rets = [r['return'] for r in discovery]
        v_rets = [r['return'] for r in validation]
        _, dp = stats.ttest_1samp(d_rets, 0)
        _, vp = stats.ttest_1samp(v_rets, 0)
        print(f"{horizon}d: Discovery (n={len(discovery)}) mean={np.mean(d_rets)*100:.2f}% p={dp:.3f} | Validation (n={len(validation)}) mean={np.mean(v_rets)*100:.2f}% p={vp:.3f}")

