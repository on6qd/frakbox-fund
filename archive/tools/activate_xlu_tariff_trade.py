"""
XLU Utilities Tariff Defensive Long Trade
==========================================
Hypothesis: 9184ba0f (tariff_xlu_utility_long)
Signal: XLU outperforms SPY by +3.36% avg over 20 days after tariff escalation.
         IS (2018-2019, n=9): 20d p=0.010, 25d p=0.0008, 77-100% direction.
         OOS (2025, n=4): +3.79% avg, 75% direction. All n=13: p=0.0015 at 20d.
         
Trigger: April 7, 2026 at 9:30 AM ET (day after Good Friday, first trading day after Liberation Day)
Condition: SPY closed < -0.5% on April 2 (Liberation Day)
Hold: 20 days (~April 27, 2026)

Usage:
    python tools/activate_xlu_tariff_trade.py [--yes] [--dry-run]
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import research, trader, db
db.init_db()

HYPOTHESIS_ID = '9184ba0f'
SYMBOL = 'XLU'
POSITION_SIZE = 5000

def main():
    parser = argparse.ArgumentParser(description='Activate XLU tariff defensive long trade')
    parser.add_argument('--yes', action='store_true', help='Execute trade (default: dry run)')
    parser.add_argument('--dry-run', action='store_true', help='Dry run only')
    args = parser.parse_args()
    
    print("=== XLU TARIFF DEFENSIVE LONG TRADE ===")
    print(f"Hypothesis: {HYPOTHESIS_ID}")
    print(f"Symbol: {SYMBOL}")
    print()
    
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found")
        return
    print(f"Status: {h.get('status')}")
    if h.get('status') != 'pending':
        print(f"WARNING: Expected 'pending', got '{h.get('status')}'")
    
    # Get current price
    try:
        api = trader.get_api()
        price = float(api.get_latest_trade(SYMBOL).price)
        qty = int(POSITION_SIZE / price)
        print(f"Current price: ${price:.2f}")
        print(f"Position: BUY {qty} shares = ${qty * price:.2f}")
    except Exception as e:
        print(f"Price error: {e}")
        price = None
        qty = 1
    
    print()
    print("Signal: XLU tariff defensive long")
    print("Expected: +3.36% abnormal vs SPY over 20 days")
    print("Stop: -10% | Take profit: +15%")
    print("Exit: ~April 27, 2026")
    
    if args.dry_run or not args.yes:
        print("\nDRY RUN: Add --yes to execute")
        return
    
    # Activate the trade
    print("\nActivating trade...")
    try:
        trader.buy_stock(SYMBOL, POSITION_SIZE)
        print(f"Order placed: BUY {SYMBOL}")
    except Exception as e:
        print(f"ERROR: {e}")
        return
    
    # Mark hypothesis as active
    import datetime
    deadline = (datetime.date.today() + datetime.timedelta(days=25)).isoformat()
    db.update_hypothesis_fields(HYPOTHESIS_ID,
        status='active',
        trade={
            'entry_price': price,
            'position_size': POSITION_SIZE,
            'entry_time': datetime.datetime.now().isoformat(),
            'deadline': deadline + 'T16:00',
            'stop_loss_pct': 10,
            'take_profit_pct': 15,
            'activated_by': 'activate_xlu_tariff_trade.py'
        }
    )
    print(f"Hypothesis {HYPOTHESIS_ID} marked active")
    print(f"Deadline: {deadline}")

if __name__ == '__main__':
    main()
