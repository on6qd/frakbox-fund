"""
Sector abnormal returns after tariff-driven VIX>30 spikes.
Calculates 20-day returns from open after each event date, net of SPY.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.yfinance_utils import safe_download
import pandas as pd
from datetime import datetime, timedelta

SECTORS = {
    'XLK': 'Tech',
    'XLF': 'Financials',
    'XLI': 'Industrials',
    'XLE': 'Energy',
    'XLV': 'Healthcare',
    'XLP': 'Staples',
    'XLRE': 'Real Estate',
    'XLU': 'Utilities',
    'XLB': 'Materials',
    'XLC': 'Comm Svcs',
}

EVENTS = {
    '2018-12-21': 'Dec-2018 tariff VIX>30',
    '2019-08-26': 'Aug-2019 tariff VIX>30',
    '2025-04-03': 'Apr-2025 Liberation Day',
}

HOLD_DAYS = 20  # calendar fetch window — we use trading days

def get_20day_return_from_flat(data: pd.DataFrame, ticker: str, start_date: str) -> float | None:
    """
    Return net % change from open of first trading day on/after start_date
    to close 20 trading days later.
    Uses flattened column names like 'Open_SPY', 'Close_XLK'.
    """
    open_col = f'Open_{ticker}'
    close_col = f'Close_{ticker}'
    if open_col not in data.columns or close_col not in data.columns:
        return None
    df = data.loc[start_date:, [open_col, close_col]].dropna()
    if len(df) < 21:
        return None
    entry_open = df.iloc[0][open_col]
    exit_close = df.iloc[20][close_col]  # 20 trading days forward
    return (exit_close / entry_open - 1) * 100


def main():
    # Fetch ~6 months around each event date to ensure enough trading days
    all_tickers = list(SECTORS.keys()) + ['SPY']

    # We need data for three separate periods
    fetch_windows = [
        ('2018-12-01', '2019-04-01'),
        ('2019-08-01', '2019-12-01'),
        ('2025-04-01', '2025-07-01'),
    ]

    results = {}  # event_date -> {ticker: abnormal_return}
    raw = {}      # event_date -> {ticker: raw_return}

    for (start_fetch, end_fetch), event_date in zip(fetch_windows, EVENTS.keys()):
        print(f"\nFetching data for event {event_date} ({start_fetch} to {end_fetch})...")
        data = safe_download(all_tickers, start=start_fetch, end=end_fetch, auto_adjust=True)

        if data is None or data.empty:
            print(f"  ERROR: No data returned for {event_date}")
            continue

        # Get SPY return first
        spy_ret = get_20day_return_from_flat(data, 'SPY', event_date)

        if spy_ret is None:
            print(f"  WARNING: SPY return unavailable for {event_date}")
            continue

        print(f"  SPY 20-day raw return: {spy_ret:.2f}%")

        event_results = {}
        event_raw = {}

        for ticker in SECTORS.keys():
            try:
                raw_ret = get_20day_return_from_flat(data, ticker, event_date)
                if raw_ret is not None:
                    abnormal = raw_ret - spy_ret
                    event_results[ticker] = abnormal
                    event_raw[ticker] = raw_ret
                    print(f"  {ticker}: raw={raw_ret:.2f}%  abnormal={abnormal:+.2f}%")
                else:
                    print(f"  {ticker}: insufficient data")
            except Exception as e:
                print(f"  {ticker}: ERROR — {e}")

        results[event_date] = event_results
        raw[event_date] = event_raw
        raw[event_date]['SPY'] = spy_ret

    # Build summary table
    print("\n" + "="*90)
    print(f"{'SECTOR ABNORMAL RETURNS — 20 TRADING DAYS POST TARIFF VIX>30 SPIKE':^90}")
    print(f"{'(Abnormal = Sector Return minus SPY Return over same window)':^90}")
    print("="*90)

    event_dates = list(EVENTS.keys())
    labels = ['2018-12-21', '2019-08-26', '2025-04-03']

    header = f"{'Sector':<20} {'Name':<14}"
    for lbl in labels:
        header += f" {lbl:>12}"
    header += f" {'Avg':>8} {'Dir%':>6}"
    print(header)
    print("-"*90)

    sector_summary = []

    for ticker, name in SECTORS.items():
        row_vals = []
        for ev in event_dates:
            val = results.get(ev, {}).get(ticker)
            row_vals.append(val)

        valid_vals = [v for v in row_vals if v is not None]
        avg = sum(valid_vals) / len(valid_vals) if valid_vals else None
        direction_pct = (sum(1 for v in valid_vals if v > 0) / len(valid_vals) * 100) if valid_vals else None

        row = f"{ticker:<20} {name:<14}"
        for val in row_vals:
            if val is not None:
                row += f" {val:>+11.2f}%"
            else:
                row += f" {'N/A':>12}"
        if avg is not None:
            row += f" {avg:>+7.2f}%"
            row += f" {direction_pct:>5.0f}%"
        print(row)

        sector_summary.append({
            'ticker': ticker,
            'name': name,
            'vals': row_vals,
            'avg': avg,
            'dir_pct': direction_pct,
        })

    # Print SPY raw returns for reference
    print("-"*90)
    spy_row = f"{'SPY (benchmark)':<20} {'--':<14}"
    for ev in event_dates:
        spy_ret = raw.get(ev, {}).get('SPY')
        if spy_ret is not None:
            spy_row += f" {spy_ret:>+11.2f}%"
        else:
            spy_row += f" {'N/A':>12}"
    spy_row += f" {'[0.00%]':>8} {'--':>6}"
    print(spy_row)
    print("="*90)

    # Leaders and laggards
    valid_sectors = [s for s in sector_summary if s['avg'] is not None]
    valid_sectors.sort(key=lambda x: x['avg'], reverse=True)

    print("\nRANKED BY AVERAGE ABNORMAL RETURN:")
    print("-"*50)
    for i, s in enumerate(valid_sectors, 1):
        vals_str = "  |  ".join(
            f"{v:+.2f}%" if v is not None else "N/A"
            for v in s['vals']
        )
        print(f"  {i:2}. {s['ticker']:<6} ({s['name']:<14}) avg={s['avg']:>+6.2f}%  dir={s['dir_pct']:.0f}%  [{vals_str}]")

    print("\nCONSISTENT OUTPERFORMERS (dir=100%, avg > 0%):")
    consistent = [s for s in valid_sectors if s['dir_pct'] is not None and s['dir_pct'] >= 100 and (s['avg'] or 0) > 0]
    if consistent:
        for s in consistent:
            print(f"  {s['ticker']} ({s['name']}): avg={s['avg']:+.2f}%")
    else:
        print("  None with 100% direction rate.")

    print("\nCONSISTENT UNDERPERFORMERS (dir=0%, avg < 0%):")
    laggards = [s for s in valid_sectors if s['dir_pct'] is not None and s['dir_pct'] == 0 and (s['avg'] or 0) < 0]
    if laggards:
        for s in laggards:
            print(f"  {s['ticker']} ({s['name']}): avg={s['avg']:+.2f}%")
    else:
        print("  None with 0% direction rate.")

    print("\nNOTE: 2025-04-03 data uses partial window if market data not yet complete.")
    print("      Entry = open of first trading day on/after event date.")
    print("      Exit = close 20 trading days later.")


if __name__ == '__main__':
    main()
