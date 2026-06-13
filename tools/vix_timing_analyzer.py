"""
VIX Spike Timing Analyzer
==========================
Analyzes SPY return patterns after VIX first crosses above 30.
Useful for determining optimal hold period after VIX spikes.

Usage:
    python3 tools/vix_timing_analyzer.py [--vix-threshold 30] [--since 2000]

Output:
    - Table of all VIX spike events with SPY returns at 5/10/20/30d
    - Summary statistics (median days to bottom, avg max drawdown)
    - Tariff-specific analog returns
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
import argparse
from tools.yfinance_utils import safe_download


def analyze_vix_timing(vix_threshold=30, since_year=2000, min_days_below=20, verbose=True):
    """
    Find all VIX first-cross events and measure subsequent SPY performance.
    
    Returns:
        pd.DataFrame with event stats
    """
    print("Fetching VIX and SPY data...")
    vix = safe_download('^VIX', start=f'{since_year}-01-01', end='2026-12-31')
    spy = safe_download('SPY', start=f'{since_year}-01-01', end='2026-12-31')

    vix_close = vix['Close'].squeeze()
    spy_close = spy['Close'].squeeze()

    # Find first-cross events (VIX goes above threshold after min_days_below consecutive below)
    vix_above = vix_close > vix_threshold
    events = []
    below_count = 0
    for i in range(len(vix_close)):
        val = vix_above.iloc[i]
        date = vix_close.index[i]
        if not val:
            below_count += 1
        else:
            if below_count >= min_days_below:
                events.append(date)
            below_count = 0

    if verbose:
        print(f"\nVIX>{vix_threshold} first-cross events (after {min_days_below}+ days below): {len(events)}")

    results = []
    for event_date in events:
        event_idx = spy_close.index.get_indexer([event_date], method='nearest')[0]
        entry_idx = event_idx + 1
        if entry_idx >= len(spy_close):
            continue

        entry_price = spy_close.iloc[entry_idx]
        window_end = min(entry_idx + 65, len(spy_close))
        spy_window = spy_close.iloc[entry_idx:window_end]

        min_price = spy_window.min()
        min_idx = spy_window.idxmin()
        min_days = spy_close.index.get_indexer([min_idx])[0] - entry_idx
        max_dd = (min_price / entry_price - 1) * 100

        def ret_at(n):
            idx = min(entry_idx + n, len(spy_close) - 1)
            return (spy_close.iloc[idx] / entry_price - 1) * 100

        vix_val = float(vix_close.loc[event_date]) if event_date in vix_close.index else None

        results.append({
            'event_date': event_date.date(),
            'vix_at_cross': round(vix_val, 1) if vix_val else None,
            'days_to_bottom': min_days,
            'max_dd_pct': round(max_dd, 1),
            'ret_5d': round(ret_at(5), 1),
            'ret_10d': round(ret_at(10), 1),
            'ret_20d': round(ret_at(20), 1),
            'ret_30d': round(ret_at(30), 1),
        })

    df = pd.DataFrame(results)

    if verbose:
        print(df.to_string(index=False))
        print("\n--- SUMMARY ---")
        print(f"Median days to bottom: {df['days_to_bottom'].median():.0f}")
        print(f"Mean days to bottom: {df['days_to_bottom'].mean():.0f}")
        print(f"% bottom within 5d: {(df['days_to_bottom'] <= 5).mean()*100:.0f}%")
        print(f"% bottom within 10d: {(df['days_to_bottom'] <= 10).mean()*100:.0f}%")
        print(f"% bottom within 20d: {(df['days_to_bottom'] <= 20).mean()*100:.0f}%")
        print()
        print(f"Avg SPY return after 5d:  {df['ret_5d'].mean():.1f}%")
        print(f"Avg SPY return after 10d: {df['ret_10d'].mean():.1f}%")
        print(f"Avg SPY return after 20d: {df['ret_20d'].mean():.1f}%")
        print(f"Avg SPY return after 30d: {df['ret_30d'].mean():.1f}%")
        print(f"Avg max drawdown before recovery: {df['max_dd_pct'].mean():.1f}%")
        print()
        print(f"% positive at 20d: {(df['ret_20d'] > 0).mean()*100:.0f}%")
        print(f"% positive at 30d: {(df['ret_30d'] > 0).mean()*100:.0f}%")

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--vix-threshold', type=float, default=30)
    parser.add_argument('--since', type=int, default=2000)
    parser.add_argument('--min-days-below', type=int, default=20)
    args = parser.parse_args()
    analyze_vix_timing(args.vix_threshold, args.since, args.min_days_below)
