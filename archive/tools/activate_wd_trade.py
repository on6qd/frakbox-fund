"""
WD (Walker & Dunlop) Trade Activation Script
=============================================
Run this at Monday March 23 market open (9:30 AM ET or shortly after).

TRADE DETAILS:
  Hypothesis: 76678219 (insider_buying_cluster, 5d hold)
  Signal: 3 insiders filed March 20 2026, last purchase March 18 2026
  Company: Walker & Dunlop, Inc. (WD) - Mortgage Finance, $1.49B market cap
  Current price: ~$43.82 (near 52W low of $42.12, down ~51% from 52W high of $90)
  Entry: Monday March 23 market open
  Exit: Tuesday March 30 at market close (5 trading days)
  Position: $5,000

EXECUTION:
  1. Run: python tools/activate_wd_trade.py
  2. This will:
     a. Fetch the current WD price
     b. Call research.activate_hypothesis() to pre-register the entry
     c. Place a $5,000 market buy order via Alpaca
  3. On Tuesday March 30 close, run: python tools/close_wd_trade.py

ABORT CONDITIONS (do NOT trade if any of these are true):
  - WD is down >10% from Friday close ($43.82) at Monday open
  - Market is circuit-breaker halted
  - WD has announced news since March 20 that changes the fundamental picture

Note: Both GO and WD trades execute Monday March 23. Two concurrent experiments.
  GO: hypothesis 1cb6140f, 3d hold, exit Thursday March 26
  WD: hypothesis 76678219, 5d hold, exit Tuesday March 30

Usage:
  python tools/activate_wd_trade.py [--dry-run]
  python tools/activate_wd_trade.py --price 43.50
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import yfinance as yf


def get_wd_current_price():
    """Get current WD price."""
    try:
        ticker = yf.Ticker('WD')
        hist = ticker.history(period='1d', interval='1m')
        if hist.empty:
            hist = ticker.history(period='2d')
            return float(hist['Close'].iloc[-1])
        return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get live price: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description='Activate WD insider cluster trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--price', type=float, default=None,
                        help='Override entry price (default: fetch live)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt (for automated/non-interactive execution)')
    args = parser.parse_args()

    print("=" * 60)
    print("WD (WALKER & DUNLOP) INSIDER CLUSTER TRADE ACTIVATION")
    print("=" * 60)
    print()
    print("Hypothesis: 76678219 (5d hold, 2+ insider cluster)")
    print("Signal: 3 insiders filed March 20 2026, last purchase March 18")
    print("Company: Walker & Dunlop (Mortgage Finance, $1.49B mcap)")
    print("Entry: Monday March 23 market open")
    print("Exit: Tuesday March 30 market close (5 trading days)")
    print()

    # Get entry price
    if args.price:
        entry_price = args.price
        print(f"Using provided price: ${entry_price:.2f}")
    else:
        entry_price = get_wd_current_price()
        if entry_price:
            print(f"Current WD price: ${entry_price:.2f}")
        else:
            print("ERROR: Could not fetch live price. Use --price XXXX to override.")
            return 1

    # Abort check: down >10% from Friday close
    friday_close = 43.82  # March 20, 2026 close
    if entry_price < friday_close * 0.90:
        print(f"ABORT: WD dropped >10% from Friday close (${friday_close:.2f})")
        print(f"       Current: ${entry_price:.2f} ({(entry_price/friday_close - 1)*100:.1f}%)")
        print("       Something may have changed fundamentally. Manual review needed.")
        return 1

    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Friday close: ${friday_close:.2f} (change: {(entry_price/friday_close - 1)*100:+.1f}%)")
    print(f"Position size: $5,000")
    shares = int(5000 / entry_price)
    print(f"Approx shares: {shares} (fractional rounding)")
    print()
    print("Exit schedule:")
    print("  3d hold check: Thursday March 26 close (optional early exit)")
    print("  5d hold (target): Tuesday March 30 close")
    print()

    if args.dry_run:
        print("[DRY RUN] Would activate hypothesis 76678219 and place market buy order")
        print(f"[DRY RUN] research.activate_hypothesis('76678219', {entry_price:.2f}, 5000)")
        print(f"[DRY RUN] trader.place_experiment('WD', 'long', 5000)")
        return 0

    # Confirm
    if args.yes:
        print("Auto-confirming (--yes flag set).")
        confirm = 'yes'
    else:
        confirm = input("Place trade? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        return 0

    # Step 1: Activate hypothesis (pre-registers entry)
    print("\nActivating hypothesis 76678219...")
    try:
        research.activate_hypothesis('76678219', entry_price=entry_price, position_size=5000)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Step 2: Place the order via trader.py
    print("\nPlacing Alpaca order...")
    try:
        result = trader.place_experiment(
            symbol='WD',
            direction='long',
            notional_amount=5000,
        )
        print(f"  Order result: {result}")
        if not result.get('success'):
            print(f"ERROR: {result.get('error')}")
            print("Hypothesis was activated but order FAILED. Check Alpaca manually.")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {e}")
        print("Hypothesis was activated but order FAILED. Check Alpaca manually.")
        return 1

    print()
    print("=" * 60)
    print("TRADE ACTIVE")
    print(f"  Symbol: WD (Walker & Dunlop)")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Target exit: Tuesday March 30 close (5d hold)")
    print(f"  Expected return: +2.0% abnormal (hypothesis 76678219)")
    print()
    print("EXIT REMINDER:")
    print("  Run 'python tools/close_wd_trade.py' on Tuesday March 30 at close")
    print("  Or early exit Thursday March 26 if position shows >5% gain")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
