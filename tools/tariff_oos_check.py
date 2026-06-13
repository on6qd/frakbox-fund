"""
Tariff OOS Check Tool
=====================
Verify tariff signal performance for a specific tariff event date.
Used to validate our tariff hypotheses against each new Liberation Day / tariff escalation event.

Usage:
    python3 tools/tariff_oos_check.py [--entry-date 2025-04-03]
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import pandas as pd
import numpy as np
from tools.yfinance_utils import safe_download
from datetime import date, timedelta

def check_tariff_oos(entry_date: str):
    """
    Check the performance of validated tariff signals after a tariff event.
    
    Args:
        entry_date: The first market open day after the tariff announcement
    """
    assets = {
        'GLD':  ('long',  'Gold ETF - tariff_gld_gold_long_20d, expected +3.87%'),
        'KO':   ('long',  'Coca-Cola - tariff_ko_defensive_long_10d, expected +4.4%'),
        'KRE':  ('short', 'Regional Bank ETF - tariff_kre_regional_banks_short, expected -3.1%'),
        'WFC':  ('short', 'Wells Fargo - tariff_escalation_bank_short, expected -2.4%'),
        'STLD': ('short', 'Steel Dynamics - stld_domestic_steel_tariff_short, expected -2.6%'),
        'XLU':  ('long',  'Utilities ETF - tariff_xlu_utility_long_20d, expected +3.4%'),
        'COST': ('long',  'Costco - tariff_defensive_retail_long, expected +3.57%'),
        'SPY':  ('long',  'S&P 500 - benchmark'),
        'GDX':  ('long',  'Gold Miners - tariff_gdx_gold_miners, expected +5.5%'),
        'XLV':  ('long',  'Healthcare - tariff_xlv_healthcare, expected +1.5%'),
        'AEP':  ('long',  'Am Elec Power - tariff_aep_utility_long, expected +3.79% at 10d'),
        'AMD':  ('short', 'AMD - tariff_semiconductor_basket (half), expected -2% at 5d'),
        'QCOM': ('short', 'Qualcomm - tariff_semiconductor_basket (half), expected -2% at 5d'),
    }

    start = (pd.Timestamp(entry_date) - timedelta(days=5)).strftime('%Y-%m-%d')
    end = (pd.Timestamp(entry_date) + timedelta(days=50)).strftime('%Y-%m-%d')

    spy_data = safe_download('SPY', start=start, end=end)
    spy_close = spy_data['Close']
    spy_close.index = pd.to_datetime(spy_close.index)

    spy_future = spy_close.index[spy_close.index >= pd.Timestamp(entry_date)]
    if len(spy_future) == 0:
        print(f"ERROR: No SPY data found after {entry_date}")
        return

    evt_actual = spy_future[0]
    spy_entry = spy_close.loc[evt_actual]

    print(f"Tariff Signal OOS Check: Entry {entry_date} (actual: {evt_actual.date()})")
    print(f"SPY entry price: {spy_entry:.2f}")
    print()
    print(f"{'Ticker':<6} {'Dir':>5}  {'5d':>7}  {'10d':>7}  {'20d':>7}  {'30d':>7}  Description")
    print("-"*100)

    for ticker, (direction, desc) in assets.items():
        data = safe_download(ticker, start=start, end=end)
        if data is None or data.empty:
            print(f"{ticker:<6} ERROR: no data")
            continue

        close = data['Close']
        close.index = pd.to_datetime(close.index)

        future = close.index[close.index >= pd.Timestamp(entry_date)]
        if len(future) == 0:
            print(f"{ticker:<6} no data at entry date")
            continue

        actual_evt = future[0]
        entry_price = close.loc[actual_evt]

        rets = {}
        for d in [5, 10, 20, 30]:
            future_days = close.index[close.index > actual_evt]
            spy_future_days = spy_close.index[spy_close.index > actual_evt]
            if len(future_days) >= d:
                exit_price = close.loc[future_days[d-1]]
                raw_ret = (exit_price / entry_price - 1) * 100
                if ticker != 'SPY' and len(spy_future_days) >= d:
                    spy_exit = spy_close.loc[spy_future_days[d-1]]
                    spy_ret = (spy_exit / spy_entry - 1) * 100
                    rets[d] = raw_ret - spy_ret
                else:
                    rets[d] = raw_ret
            else:
                rets[d] = None

        # Flip for short: positive = short was profitable
        if direction == 'short':
            rets = {d: -v if v is not None else None for d, v in rets.items()}

        def fmt(v): return f'{v:+7.1f}%' if v is not None else '    N/A'
        print(f"{ticker:<6} {direction:>5}  {fmt(rets[5])}  {fmt(rets[10])}  {fmt(rets[20])}  {fmt(rets[30])}  {desc[:55]}")

    print()
    print("Note: Abnormal returns vs SPY. Short returns sign-flipped (positive=short profitable).")
    print("WARNING: If tariff rollback occurs within 30d, defensive signals fail at 20-30d (2025 Liberation Day lesson).")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check tariff signal OOS performance')
    parser.add_argument('--entry-date', default='2025-04-03',
                       help='First market open day after tariff announcement (default: 2025-04-03)')
    args = parser.parse_args()
    check_tariff_oos(args.entry_date)
