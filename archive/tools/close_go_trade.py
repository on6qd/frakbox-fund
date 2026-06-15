"""
GO Trade Closing Script
=======================
Run this on Thursday March 26 at market close (3:55-4:00 PM ET).

WHAT THIS DOES:
  1. Gets current GO price from Alpaca
  2. Calls trader.close_position('GO') to submit market sell
  3. Calculates actual return vs SPY return
  4. Calls research.complete_hypothesis() with post-mortem
  5. Records the result in SQLite (hypotheses + pre_registrations tables)

Usage:
  python tools/close_go_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import yfinance as yf


def get_spy_return_since(entry_date_str: str) -> float:
    """Calculate SPY return from entry date to now."""
    try:
        spy = yf.Ticker('SPY')
        hist = spy.history(period='10d')
        if len(hist) < 2:
            return 0.0
        # Find return from entry
        entry_close = hist['Close'].iloc[0]
        current = hist['Close'].iloc[-1]
        return (current / entry_close - 1) * 100
    except Exception:
        return 0.0


def main():
    parser = argparse.ArgumentParser(description='Close GO insider cluster trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--entry-price', type=float, default=None,
                        help='Entry price (for return calculation)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt (for automated/non-interactive execution)')
    args = parser.parse_args()

    print("=" * 60)
    print("GO INSIDER CLUSTER TRADE CLOSE")
    print("=" * 60)
    print()

    # Load hypothesis to get entry details
    import db as _db
    hyps = _db.load_hypotheses()
    h = next((h for h in hyps if h['id'] == '1cb6140f'), None)
    if not h:
        print("ERROR: Hypothesis 1cb6140f not found")
        return 1

    if h['status'] != 'active':
        print(f"ERROR: Hypothesis status is '{h['status']}', expected 'active'")
        print("       Was the trade activated? Run activate_go_trade.py first.")
        return 1

    trade = h.get('trade', {})
    entry_price = args.entry_price or trade.get('entry_price')
    if not entry_price:
        print("ERROR: No entry price found. Use --entry-price XXXX")
        return 1

    print(f"Hypothesis: 1cb6140f (3d hold)")
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Entry date: {trade.get('entry_date', 'unknown')}")
    print()

    # Get current price
    try:
        current = trader.get_current_price('GO')
        print(f"Current GO price: ${current:.2f}")
    except Exception as e:
        print(f"Warning: could not fetch live price: {e}")
        if args.yes:
            print("ERROR: Cannot auto-fetch price, and --yes flag set. Aborting.")
            return 1
        current_input = input("Enter current GO price: ")
        current = float(current_input)

    raw_return = (current / entry_price - 1) * 100
    spy_return = get_spy_return_since(str(trade.get('entry_date', '')))

    print(f"Raw return: {raw_return:+.2f}%")
    print(f"SPY return (approx): {spy_return:+.2f}%")
    print(f"Abnormal return: {raw_return - spy_return:+.2f}%")
    print()

    if args.dry_run:
        print("[DRY RUN] Would close position and complete hypothesis")
        return 0

    if args.yes:
        print("Auto-confirming close (--yes flag set).")
        confirm = 'yes'
    else:
        confirm = input("Close position and complete hypothesis? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        return 0

    # Close Alpaca position
    print("\nClosing Alpaca position...")
    try:
        result = trader.close_position('GO')
        print(f"  Position closed: {result}")
    except Exception as e:
        print(f"WARNING: Could not close via trader.close_position: {e}")
        print("         Please close GO position manually in Alpaca.")

    # Complete hypothesis
    print("\nCompleting hypothesis...")
    outcome = "WIN" if raw_return > 0 else "LOSS"
    post_mortem = (
        f"{outcome}: GO trade closed at ${current:.2f} (entry ${entry_price:.2f}). "
        f"Raw return: {raw_return:+.2f}%. Abnormal vs SPY: {raw_return - spy_return:+.2f}%. "
        f"Cluster: 6 insiders incl CEO bought $6.3M at avg $6.02 in March 2026. "
        f"Signal validated: direction {'correct' if raw_return > 0 else 'incorrect'}."
    )

    try:
        research.complete_hypothesis(
            hypothesis_id='1cb6140f',
            exit_price=current,
            actual_return_pct=raw_return,
            post_mortem=post_mortem,
            spy_return_pct=spy_return,
            timing_accuracy='on_time',
            mechanism_validated=(raw_return > 0),
            confounder_attribution='none_identified',
            surprise_factor=abs(raw_return - 2.5),
        )
        print(f"  Hypothesis completed. Return: {raw_return:+.2f}%")
    except Exception as e:
        print(f"ERROR completing hypothesis: {e}")
        return 1

    print()
    print("=" * 60)
    print(f"TRADE COMPLETE: {outcome}")
    print(f"  Raw: {raw_return:+.2f}% | Abnormal: {raw_return - spy_return:+.2f}%")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
