"""
KHC (Kraft Heinz) 52-Week Low Short Trade Activation Script
=============================================================
AUTOMATICALLY TRIGGERED via trade_loop.py on Friday March 27 at 9:30 AM ET.
Run this manually ONLY if the auto-trigger failed.

TRADE DETAILS:
  Hypothesis: e7ac3803 (sp500_52w_low_momentum_short, 5d hold)
  Signal: KHC first touched 52-week low on 2026-03-23 (close=$21.21, 52w low=$21.33)
  52w low level: $21.33 (must still be at/below this to enter)
  Entry: Friday March 27 market open (after HD/ABT/BAX close at 15:50 March 27)

  NOTE: HD, ABT, BAX close at 15:50 March 27. The auto-trigger fires at 9:30 March 27
  with 4 active positions (HD+ABT+BAX+SYK). That's within the 5-max limit, so KHC
  CAN activate at 9:30 even before the others close.

  Exit: April 3 close (5 trading days from March 27)
  Position: $5,000
  Stop loss: 8%
  Take profit: 10%

ABORT CONDITIONS:
  - KHC has recovered above $21.33 (signal invalidated)
  - Portfolio at 5/5 capacity (trade_loop handles this check)
  - KHC has announced major news since March 23

Usage:
  python tools/activate_khc_trade.py [--dry-run] [--yes]
"""

# DEPRECATED 2026-04-09: hypothesis is abandoned (non-first-touch or strategic cancel).
# This script will not activate a live trade. Retained for pattern reference only.
# New activators MUST call tools.pre_event_contamination.check_pre_event_contamination(
#   symbol, event_date=<event_iso_date>) before entry.


import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db

KHC_52W_LOW = 21.33  # 52-week low as of signal date
HYPOTHESIS_ID = 'e7ac3803'


def get_khc_current_price():
    """Get current KHC price."""
    try:
        import yfinance as yf
        ticker = yf.Ticker('KHC')
        hist = ticker.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = ticker.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"yfinance error: {e}")
    try:
        return trader.get_current_price('KHC')
    except Exception as e:
        print(f"Alpaca price error: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description='Activate KHC short trade')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without executing')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    db.init_db()

    print("=" * 60)
    print("KHC 52-Week Low Short — Activation Check")
    print("=" * 60)

    # Check hypothesis status
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found in DB")
        return 1
    if h['status'] == 'active':
        print(f"Hypothesis already ACTIVE — trade was already placed (likely by auto-trigger).")
        return 0
    if h['status'] != 'pending':
        print(f"ERROR: Hypothesis status is '{h['status']}' — expected 'pending'")
        return 1

    # Get current price
    entry_price = get_khc_current_price()
    if entry_price is None:
        print("ERROR: Could not get KHC price. Check connection.")
        return 1

    print(f"\nKHC current price: ${entry_price:.2f}")
    print(f"52-week low (signal level): ${KHC_52W_LOW:.2f}")

    # Check if still below 52w low
    if entry_price > KHC_52W_LOW * 1.005:  # Allow 0.5% tolerance
        print(f"\nABORT: KHC has recovered to ${entry_price:.2f}, above 52w low ${KHC_52W_LOW:.2f}")
        print("       Signal has been invalidated. Do not trade.")
        return 1

    # Large gap abort check
    signal_close = 21.21  # Close on March 23 (signal date)
    if entry_price < signal_close * 0.88:
        print(f"\nABORT: KHC dropped >12% from signal close (${signal_close:.2f}).")
        print("       Catastrophic news may have occurred. Manual review needed.")
        return 1

    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Position size: $5,000")
    shares = int(5000 / entry_price)
    print(f"Approx shares: {shares}")
    print(f"Stop loss: 8% (trigger at ${entry_price * 1.08:.2f})")
    print(f"Take profit: 10% (trigger at ${entry_price * 0.90:.2f})")
    print(f"Expected exit: Thursday April 3 close (5 trading days)")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate hypothesis {HYPOTHESIS_ID} and place market short order")
        return 0

    if args.yes:
        confirm = 'yes'
    else:
        confirm = input("Place trade? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        return 0

    # Activate hypothesis
    print(f"\nActivating hypothesis {HYPOTHESIS_ID}...")
    try:
        research.activate_hypothesis(HYPOTHESIS_ID, entry_price=entry_price, position_size=5000)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Place order
    print("\nPlacing Alpaca short order...")
    try:
        result = trader.place_experiment(
            symbol='KHC',
            direction='short',
            notional_amount=5000,
        )
        print(f"  Order result: {result}")
        if not result.get('success'):
            print(f"ERROR: {result.get('error')}")
            print("Hypothesis was activated but order FAILED. Check Alpaca manually.")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {e}")
        return 1

    print()
    print("=" * 60)
    print("TRADE ACTIVE")
    print(f"  Symbol: KHC SHORT")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"  Take profit: 10% = ${entry_price * 0.90:.2f}")
    print(f"  Target exit: Thursday April 3 close")
    print(f"  Hypothesis: {HYPOTHESIS_ID}")
    print()
    print("NOTE: trade_loop.py monitors stop loss automatically.")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
