"""
Systemic 52-Week Low Signal — Informal OOS Observer
====================================================
Signal: sp500_52w_low_systemic_short
Event: March 27, 2026 (28 first-touch 52w lows, SPY -1.71%)
Entry: March 31, 2026 (next open — NOT TRADED due to capacity conflict)
Measurement: 5d abnormal return vs SPY

This is an INFORMAL observation (no trade placed). Tracks a sample of the
28 stocks that triggered on March 27 to collect OOS data for the signal.

Expected: -1.91% average 5d abnormal (N=930 training+OOS data)

Usage:
  python3 tools/systemic_52w_low_observer.py                  # check current state
  python3 tools/systemic_52w_low_observer.py --record          # record results to KB
  python3 tools/systemic_52w_low_observer.py --date 2026-04-08 # check specific date
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
from tools.yfinance_utils import safe_download
import pandas as pd
import numpy as np

EVENT_DATE = '2026-03-27'
ENTRY_DATE = '2026-03-31'  # Monday open
# 5 trading days from March 31: April 1, 2, [3 holiday], 7, 8 → exits April 8
EXIT_DATE_5D = '2026-04-08'
HOLD_DAYS = 5

KNOWLEDGE_KEY = 'sp500_52w_low_systemic_short'

# Sample stocks from the March 27 event
# 28 total: ADBE, QCOM, SNPS, NOW, PTC, ZBRA, TTWO, UBER, CPRT, WHR, AXP, COF, V, MA,
#           GPN, MET, EQH, RJF, SYK, BAX, A, ADP, J, AVB, ESS, BXP, VNO, HIW
SAMPLE_STOCKS = [
    'ADBE', 'QCOM', 'UBER', 'AXP', 'COF', 'V', 'MA',
    'SNPS', 'NOW', 'WHR', 'MET', 'ADP', 'ESS', 'SYK'
]
# Note: SYK excluded (we shorted it separately)
TRACKED_STOCKS = [s for s in SAMPLE_STOCKS if s != 'SYK']

BENCHMARK = 'SPY'


def get_prices():
    """Download prices for tracked stocks and benchmark."""
    start = (pd.Timestamp(ENTRY_DATE) - timedelta(days=2)).strftime('%Y-%m-%d')
    end = (pd.Timestamp(EXIT_DATE_5D) + timedelta(days=5)).strftime('%Y-%m-%d')

    prices = {}
    for ticker in TRACKED_STOCKS + [BENCHMARK]:
        try:
            df = safe_download(ticker, start=start, end=end)
            if df is not None and not df.empty:
                prices[ticker] = df['Close']
        except Exception:
            pass  # Skip missing tickers

    return prices


def compute_abnormal_returns(prices, entry_date, n_days):
    """Compute abnormal returns vs SPY over n trading days from entry."""
    entry_ts = pd.Timestamp(entry_date)
    spy_series = prices.get(BENCHMARK)
    if spy_series is None:
        return {}

    results = {}
    for ticker in TRACKED_STOCKS:
        if ticker not in prices:
            continue

        stock = prices[ticker]

        # Find entry price
        entry_idx = stock.index.searchsorted(entry_ts)
        if entry_idx >= len(stock):
            continue

        # Find exit price (n trading days from entry)
        exit_idx = min(entry_idx + n_days, len(stock) - 1)

        entry_price = float(stock.iloc[entry_idx])
        exit_price = float(stock.iloc[exit_idx])

        spy_entry = float(spy_series.iloc[entry_idx])
        spy_exit = float(spy_series.iloc[exit_idx])

        stock_ret = (exit_price / entry_price - 1) * 100
        spy_ret = (spy_exit / spy_entry - 1) * 100
        abnormal = stock_ret - spy_ret

        results[ticker] = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return': stock_ret,
            'spy_return': spy_ret,
            'abnormal': abnormal,
            'entry_date': str(stock.index[entry_idx].date()),
            'exit_date': str(stock.index[exit_idx].date()),
        }

    return results


def run_observer(record=False):
    db.init_db()

    today = pd.Timestamp.today().normalize()
    entry_ts = pd.Timestamp(ENTRY_DATE)
    exit_ts = pd.Timestamp(EXIT_DATE_5D)

    print("=" * 65)
    print("SYSTEMIC 52W LOW SHORT — MARCH 27, 2026 OOS OBSERVER")
    print("=" * 65)
    print(f"Event date:  {EVENT_DATE} (SPY -1.71%, 28 first-touch 52w lows)")
    print(f"Entry date:  {ENTRY_DATE} (NOT TRADED — capacity conflict)")
    print(f"Exit date:   {EXIT_DATE_5D} (5 trading days from entry)")
    print(f"Tracked:     {len(TRACKED_STOCKS)} stocks (sample of 28 triggered)")
    print(f"Expected:    -1.91% avg abnormal (N=930 training+OOS)")
    print()

    prices = get_prices()

    if today < entry_ts:
        print(f"Entry date {ENTRY_DATE} has not yet occurred. No observation yet.")
        return

    # Days elapsed
    trading_days_elapsed = len(pd.bdate_range(ENTRY_DATE, today.strftime('%Y-%m-%d'))) - 1
    print(f"Trading days elapsed since entry: {trading_days_elapsed}/{HOLD_DAYS}")
    print()

    # Compute returns at available horizons
    print("Current performance (abnormal vs SPY):")
    results = compute_abnormal_returns(prices, ENTRY_DATE, min(trading_days_elapsed, HOLD_DAYS))

    if not results:
        print("No data available yet.")
        return

    abnormals = [r['abnormal'] for r in results.values()]
    avg_abnormal = np.mean(abnormals)
    direction_neg = sum(1 for a in abnormals if a < -0.5) / len(abnormals)

    print(f"  Stocks measured: {len(results)}")
    print(f"  Avg abnormal: {avg_abnormal:+.2f}%")
    print(f"  Direction negative (>0.5%): {direction_neg:.0%}")
    print()

    for ticker, r in sorted(results.items(), key=lambda x: x[1]['abnormal']):
        status = "✓" if r['abnormal'] < -0.5 else ("✗" if r['abnormal'] > 0.5 else "~")
        print(f"  {status} {ticker}: {r['return']:+.1f}% raw, {r['abnormal']:+.2f}% abnormal "
              f"(entry {r['entry_date']} → {r['exit_date']})")

    print()

    # Full 5d results if available
    if today >= exit_ts:
        results_5d = compute_abnormal_returns(prices, ENTRY_DATE, HOLD_DAYS)
        if results_5d:
            abnormals_5d = [r['abnormal'] for r in results_5d.values()]
            avg_5d = np.mean(abnormals_5d)
            dir_5d = sum(1 for a in abnormals_5d if a < -0.5) / len(abnormals_5d)

            print("=" * 65)
            print("FINAL 5D RESULTS:")
            print(f"  Avg 5d abnormal: {avg_5d:+.2f}%")
            print(f"  Direction negative: {dir_5d:.0%} ({sum(1 for a in abnormals_5d if a < -0.5)}/{len(abnormals_5d)})")

            signal_confirmed = avg_5d < -1.0 and dir_5d > 0.5
            print(f"  Signal confirmed: {'YES ✓' if signal_confirmed else 'NO ✗'}")
            print(f"  Expected: avg=-1.91%, direction>50%")
            print()

            if record:
                # Update knowledge base
                import json
                conn = db.get_db()
                rows = db._q('SELECT event_type, data FROM known_effects WHERE event_type = ?',
                             (KNOWLEDGE_KEY,))
                if rows:
                    d = json.loads(rows[0]['data'])
                    if 'informal_oos_instances' not in d:
                        d['informal_oos_instances'] = []
                    d['informal_oos_instances'].append({
                        'event_date': EVENT_DATE,
                        'entry_date': ENTRY_DATE,
                        'n_stocks': len(results_5d),
                        'avg_5d_abnormal': round(avg_5d, 2),
                        'direction_pct': round(dir_5d * 100, 1),
                        'signal_confirmed': signal_confirmed,
                        'note': 'INFORMAL — not traded due to Liberation Day capacity conflict',
                        'recorded': datetime.now().strftime('%Y-%m-%d'),
                    })
                    db._exec('UPDATE known_effects SET data = ? WHERE event_type = ?',
                             (json.dumps(d), KNOWLEDGE_KEY))
                    conn.commit()
                    print(f"✓ Recorded OOS instance to knowledge base ({KNOWLEDGE_KEY})")
        else:
            print("5d exit data not yet available.")
    else:
        days_until_exit = len(pd.bdate_range(today.strftime('%Y-%m-%d'), EXIT_DATE_5D)) - 1
        print(f"Full 5d results available on {EXIT_DATE_5D} ({days_until_exit} trading days).")
        print(f"  Run: python3 tools/systemic_52w_low_observer.py --record")


if __name__ == '__main__':
    record = '--record' in sys.argv
    run_observer(record=record)
