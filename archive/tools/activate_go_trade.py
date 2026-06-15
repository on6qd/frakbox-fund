"""
GO (Grocery Outlet) Trade Activation Script
===========================================
Run this at Monday March 23 market open (9:30 AM ET or shortly after).

TRADE DETAILS:
  Hypothesis: 1cb6140f (insider_buying_cluster, 3d hold)
  Why 3d (not 1d): 2023-24 OOS shows 3d avg=4.71% (69% pos) vs 1d avg=1.02% (55% pos)
  Signal: 6 insiders including CEO bought $6.3M at avg $6.02 (March 6-19, 2026)
  Current price: ~$5.79 (4% below insider avg)
  Entry: Monday March 23 market open
  Exit: Thursday March 26 at market close (3 trading days)
  Position: $5,000

EXECUTION:
  1. Run: python tools/activate_go_trade.py
  2. This will:
     a. Fetch the current GO open price from Alpaca
     b. Call research.activate_hypothesis() to pre-register the entry
     c. Place a $5,000 market buy order via Alpaca
  3. On Thursday March 26 close, run: python tools/close_go_trade.py (auto-generated)

ABORT CONDITIONS (do NOT trade if any of these are true):
  - GO is down >10% from Friday close at Monday open (stop-loss: something catastrophic happened)
  - Market is circuit-breaker halted (VIX > 60)
  - GO has announced news since March 21 that changes the fundamental picture

Usage:
  python tools/activate_go_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import yfinance as yf
from datetime import date


def get_go_current_price():
    """Get current GO price (or None if market is closed)."""
    try:
        ticker = yf.Ticker('GO')
        hist = ticker.history(period='1d', interval='1m')
        if hist.empty:
            # Fallback: get latest close
            hist = ticker.history(period='2d')
            return float(hist['Close'].iloc[-1])
        return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get live price: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description='Activate GO insider cluster trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--price', type=float, default=None,
                        help='Override entry price (default: fetch live)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt (for automated/non-interactive execution)')
    args = parser.parse_args()

    print("=" * 60)
    print("GO INSIDER CLUSTER TRADE ACTIVATION")
    print("=" * 60)
    print()
    print("Hypothesis: 1cb6140f (3d hold, 3+ insiders)")
    print("Signal: 6 insiders incl CEO bought $6.3M at avg $6.02")
    print("Entry: Monday March 23 market open")
    print("Exit: Thursday March 26 market close")
    print()

    # Get entry price
    if args.price:
        entry_price = args.price
        print(f"Using provided price: ${entry_price:.2f}")
    else:
        entry_price = get_go_current_price()
        if entry_price:
            print(f"Current GO price: ${entry_price:.2f}")
        else:
            print("ERROR: Could not fetch live price. Use --price XXXX to override.")
            return 1

    # Abort checks
    friday_close = 5.79  # March 20 close price
    if entry_price < friday_close * 0.90:
        print(f"ABORT: GO dropped >10% from Friday close (${friday_close:.2f})")
        print("       Something may have changed fundamentally. Manual review needed.")
        return 1

    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Position size: $5,000")
    shares = int(5000 / entry_price)
    print(f"Approx shares: {shares} (fractional rounding)")
    print()

    if args.dry_run:
        print("[DRY RUN] Would activate hypothesis 1cb6140f and place market buy order")
        print(f"[DRY RUN] research.activate_hypothesis('1cb6140f', {entry_price:.2f}, 5000)")
        print(f"[DRY RUN] Place LONG GO $5,000 at market")
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
    print("\nActivating hypothesis 1cb6140f...")
    try:
        research.activate_hypothesis('1cb6140f', entry_price=entry_price, position_size=5000)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Step 2: Place the order via trader.py
    print("\nPlacing Alpaca order...")
    try:
        result = trader.place_experiment(
            symbol='GO',
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
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Target exit: Thursday March 26 close")
    print(f"  Expected return: +2.5% abnormal (hypothesis 1cb6140f)")
    print()
    print("EXIT REMINDER: Run 'python trader.py --close 1cb6140f' on Thursday")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
