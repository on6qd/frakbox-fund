"""
Liberation Day April 2, 2026 - Trade Activation Script

Run on April 2 evening or April 3 morning AFTER confirming:
1. Tariff announcement was escalatory (new tariffs announced, not paused/benign)
2. SPY down >= 0.5% on April 2 vs prior close
3. If SPY bounced instead, DO NOT run this script

Usage:
  python3 tools/activate_liberation_day_trades.py --check   # Preview, no trades
  python3 tools/activate_liberation_day_trades.py --apr3    # AMD/QCOM on April 3 open
  python3 tools/activate_liberation_day_trades.py --apr7    # WFC/COST/GLD/KRE/AEP on April 7 open
"""
import sys
sys.path.insert(0, '.')
import db

# AMD/QCOM: Enter April 3 open (day after announcement)
SEMICONDUCTOR_SHORTS = [
    {'id': '132e9128', 'symbol': 'AMD',  'trigger': '2026-04-03T09:30', 'size': 2500},
    {'id': '14de5527', 'symbol': 'QCOM', 'trigger': '2026-04-03T09:30', 'size': 2500},
]

# Longer-horizon tariff plays: Enter April 7 open (let dust settle)
APR7_TRADES = [
    {'id': 'b73efac3', 'symbol': 'WFC',  'trigger': '2026-04-07T09:30', 'size': 5000},
    {'id': '8c2f8cbb', 'symbol': 'COST', 'trigger': '2026-04-07T09:30', 'size': 5000},
    {'id': 'b768e8d8', 'symbol': 'GLD',  'trigger': '2026-04-07T09:30', 'size': 5000},
    {'id': '6e732966', 'symbol': 'KRE',  'trigger': '2026-04-07T09:30', 'size': 5000},
    {'id': '35b63a23', 'symbol': 'AEP',  'trigger': '2026-04-07T09:30', 'size': 5000},
]

def check_condition():
    """Check if Liberation Day was escalatory before setting triggers."""
    from tools.yfinance_utils import get_close_prices
    import pandas as pd
    close = get_close_prices(['SPY'], start='2026-04-01', end='2026-04-04')
    if len(close) >= 2:
        apr2 = close.iloc[-1]['SPY']
        prior = close.iloc[-2]['SPY']
        change = (apr2 - prior) / prior * 100
        print(f"SPY on April 2: {apr2:.2f} vs prior {prior:.2f} = {change:+.2f}%")
        if change < -0.5:
            print("CONDITION MET: SPY down >= 0.5% - Liberation Day was escalatory")
            return True
        else:
            print("CONDITION NOT MET: SPY not down enough. Do NOT activate tariff trades.")
            return False
    print("Cannot verify SPY price - check manually")
    return None

def activate_apr3():
    """Set AMD/QCOM triggers for April 3 open."""
    for t in SEMICONDUCTOR_SHORTS:
        db.update_hypothesis_fields(t['id'], trigger=t['trigger'])
        print(f"SET TRIGGER: {t['symbol']} -> {t['trigger']} (\${t['size']})")

def activate_apr7():
    """Set WFC/COST/GLD/KRE/AEP triggers for April 7 open."""
    for t in APR7_TRADES:
        db.update_hypothesis_fields(t['id'], trigger=t['trigger'])
        print(f"SET TRIGGER: {t['symbol']} -> {t['trigger']} (\${t['size']})")

if __name__ == '__main__':
    db.init_db()
    if '--check' in sys.argv:
        print("=== CHECKING LIBERATION DAY CONDITION ===")
        check_condition()
    elif '--apr3' in sys.argv:
        print("=== ACTIVATING APRIL 3 SEMICONDUCTOR SHORTS ===")
        activate_apr3()
    elif '--apr7' in sys.argv:
        print("=== ACTIVATING APRIL 7 TRADES ===")
        activate_apr7()
    else:
        print(__doc__)
        print("\nCurrent status:")
        for t in SEMICONDUCTOR_SHORTS + APR7_TRADES:
            r = db._q1('SELECT expected_symbol, trigger FROM hypotheses WHERE id LIKE ?', (t['id']+'%',))
            if r:
                print(f"  {r['expected_symbol']}: trigger={r['trigger']}")
