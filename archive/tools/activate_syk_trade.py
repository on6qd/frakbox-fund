"""
SYK (Stryker Corp) 52-Week Low Short Trade Activation Script
============================================================
Run this at Thursday March 26 market open (9:30 AM ET) after GO closes.

TRADE DETAILS:
  Hypothesis: 5b09b097 (sp500_52w_low_momentum_short, 5d hold)
  Signal: SYK first touched 52-week low on 2026-03-23 (close=$332.59, 52w low=$335.05)
  52w low level: $335.05 (must still be below this at entry)
  Entry: Thursday March 26 market open
  Exit: Tuesday March 31 at market close (5 trading days)
  Position: $5,000
  Stop loss: 8%
  Take profit: 10%

EXECUTION:
  1. Run: python tools/activate_syk_trade.py
  2. This will:
     a. Verify SYK is still below 52w low ($335.05)
     b. Check portfolio has capacity (<5 active trades)
     c. Call research.activate_hypothesis() to pre-register the entry
     d. Place a $5,000 market short order via Alpaca
  3. On Tuesday March 31 close, the trade deadline auto-closes via trade_loop

ABORT CONDITIONS (do NOT trade if any of these are true):
  - SYK has recovered above $335.05 (signal invalidated)
  - Portfolio at 5/5 capacity (GO must close first)
  - Market is circuit-breaker halted (VIX > 60)
  - SYK has announced news since March 23 that changes fundamental picture

Usage:
  python tools/activate_syk_trade.py [--dry-run]
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
from tools.yfinance_utils import safe_download
from datetime import date, timedelta


SYK_52W_LOW = 335.05  # 52-week low as of signal date
HYPOTHESIS_ID = '5b09b097'


def get_syk_current_price():
    """Get current SYK price."""
    try:
        import yfinance as yf
        ticker = yf.Ticker('SYK')
        hist = ticker.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = ticker.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get live price: {e}")
    return None


def check_capacity():
    """Check current number of active trades — uses MAX of hypothesis DB and Alpaca positions.

    Bug fix: CTAS was in Alpaca but not in hypothesis DB, causing silent overflow.
    We take the max to catch untracked Alpaca positions.
    """
    db.init_db()
    hypotheses = db.load_hypotheses()
    hyp_active = [h for h in hypotheses if h.get('status') == 'active']
    hyp_count = len(hyp_active)

    # Also check actual Alpaca positions
    try:
        api = trader.get_api()
        alpaca_positions = api.list_positions()
        alpaca_count = len(alpaca_positions)
    except Exception:
        alpaca_count = 0

    count = max(hyp_count, alpaca_count)
    if alpaca_count > hyp_count:
        print(f"  [WARNING] Alpaca has {alpaca_count} positions but only {hyp_count} in hypothesis DB — untracked positions detected!")
    return count


def main():
    parser = argparse.ArgumentParser(description='Activate SYK 52w-low short trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--price', type=float, default=None,
                        help='Override entry price (default: fetch live)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt')
    args = parser.parse_args()

    print("=" * 60)
    print("SYK 52-WEEK LOW SHORT TRADE ACTIVATION")
    print("=" * 60)
    print()
    print(f"Hypothesis: {HYPOTHESIS_ID} (sp500_52w_low_momentum_short, 5d hold)")
    print(f"Signal: First 52w low touch on 2026-03-23 (close=$332.59)")
    print(f"52w low barrier: ${SYK_52W_LOW:.2f}")
    print()

    # Check portfolio capacity
    active_count = check_capacity()
    print(f"Active trades: {active_count}/5")
    if active_count >= 5:
        print(f"ABORT: Portfolio at capacity ({active_count}/5). GO must close first.")
        print("       Wait for GO to close before running this script.")
        return 1

    # Get entry price
    if args.price:
        entry_price = args.price
        print(f"Using provided price: ${entry_price:.2f}")
    else:
        entry_price = get_syk_current_price()
        if entry_price:
            print(f"Current SYK price: ${entry_price:.2f}")
        else:
            print("ERROR: Could not fetch live price. Use --price XXXX to override.")
            return 1

    # Signal validity check
    if entry_price > SYK_52W_LOW:
        print(f"\nABORT: SYK (${entry_price:.2f}) has recovered ABOVE 52w low (${SYK_52W_LOW:.2f}).")
        print("       Signal is INVALIDATED. Do NOT enter the trade.")
        return 1

    breach_pct = (entry_price - SYK_52W_LOW) / SYK_52W_LOW * 100
    print(f"Signal valid: SYK ${entry_price:.2f} < 52w low ${SYK_52W_LOW:.2f} (breach={breach_pct:.1f}%)")

    # Large gap abort check (>10% drop from signal price suggests catastrophic news)
    signal_close = 332.59
    if entry_price < signal_close * 0.88:
        print(f"\nABORT: SYK dropped >12% from signal close (${signal_close:.2f}).")
        print("       Catastrophic news may have occurred. Manual review needed.")
        return 1

    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Position size: $5,000")
    shares = int(5000 / entry_price)
    print(f"Approx shares: {shares} (fractional rounding)")
    print(f"Stop loss: 8% (trigger at ${entry_price * 1.08:.2f})")
    print(f"Take profit: 10% (trigger at ${entry_price * 0.90:.2f})")
    print(f"Expected exit: Tuesday March 31 close (5 trading days)")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate hypothesis {HYPOTHESIS_ID} and place market short order")
        print(f"[DRY RUN] research.activate_hypothesis('{HYPOTHESIS_ID}', {entry_price:.2f}, 5000)")
        print(f"[DRY RUN] Place SHORT SYK $5,000 at market")
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
    print(f"\nActivating hypothesis {HYPOTHESIS_ID}...")
    try:
        research.activate_hypothesis(HYPOTHESIS_ID, entry_price=entry_price, position_size=5000)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Step 2: Place the order via trader.py
    print("\nPlacing Alpaca short order...")
    try:
        result = trader.place_experiment(
            symbol='SYK',
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
        print("Hypothesis was activated but order FAILED. Check Alpaca manually.")
        return 1

    print()
    print("=" * 60)
    print("TRADE ACTIVE")
    print(f"  Symbol: SYK SHORT")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"  Take profit: 10% = ${entry_price * 0.90:.2f}")
    print(f"  Target exit: Tuesday March 31 close")
    print(f"  Expected return: -1.68% abnormal (hypothesis {HYPOTHESIS_ID})")
    print()
    print("NOTE: trade_loop.py monitors stop loss automatically.")
    print("Manual exit on March 31: python trader.py --close 5b09b097")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
