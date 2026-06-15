"""
WD (Walker & Dunlop) Trade Closing Script
==========================================
Run this on Tuesday March 30 at market close (3:55-4:00 PM ET).
Or earlier (Thursday March 26) if taking early exit on 3d signal.

WHAT THIS DOES:
  1. Gets current WD price
  2. Calls trader.close_position('WD') to submit market sell
  3. Calculates actual return vs SPY return
  4. Calls research.complete_hypothesis() with post-mortem
  5. Records the result in SQLite (hypotheses + pre_registrations tables)

Usage:
  python tools/close_wd_trade.py [--dry-run]
  python tools/close_wd_trade.py --entry-price 43.50
"""

import sys
import argparse
import json
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
        entry_close = hist['Close'].iloc[0]
        current = hist['Close'].iloc[-1]
        return (current / entry_close - 1) * 100
    except Exception:
        return 0.0


def get_sector_return_since(entry_date_str: str) -> float:
    """Calculate XLF (Financial sector ETF) return from entry date to now."""
    try:
        xlf = yf.Ticker('XLF')
        hist = xlf.history(period='10d')
        if len(hist) < 2:
            return 0.0
        entry_close = hist['Close'].iloc[0]
        current = hist['Close'].iloc[-1]
        return (current / entry_close - 1) * 100
    except Exception:
        return 0.0


def main():
    parser = argparse.ArgumentParser(description='Close WD insider cluster trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--entry-price', type=float, default=None,
                        help='Entry price (for return calculation, overrides stored value)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt (for automated/non-interactive execution)')
    args = parser.parse_args()

    print("=" * 60)
    print("WD (WALKER & DUNLOP) INSIDER CLUSTER TRADE CLOSE")
    print("=" * 60)
    print()

    # Load hypothesis to get entry details
    import db as _db
    hyps = _db.load_hypotheses()
    h = next((h for h in hyps if h['id'] == '76678219'), None)
    if not h:
        print("ERROR: Hypothesis 76678219 not found")
        return 1

    if h['status'] != 'active':
        print(f"ERROR: Hypothesis status is '{h['status']}', expected 'active'")
        print("       Was the trade activated? Run activate_wd_trade.py first.")
        return 1

    trade = h.get('trade', {})
    entry_price = args.entry_price or trade.get('entry_price')
    if not entry_price:
        print("ERROR: No entry price found. Use --entry-price XXXX")
        return 1

    entry_date = trade.get('entry_date', 'unknown')
    print(f"Hypothesis: 76678219 (5d hold, insider_buying_cluster)")
    print(f"Symbol: WD (Walker & Dunlop)")
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Entry date: {entry_date}")
    print()

    # Get current WD price
    try:
        current = trader.get_current_price('WD')
        print(f"Current WD price: ${current:.2f}")
    except Exception as e:
        print(f"Warning: could not fetch live price via trader: {e}")
        try:
            ticker = yf.Ticker('WD')
            hist = ticker.history(period='1d', interval='1m')
            if hist.empty:
                hist = ticker.history(period='2d')
            current = float(hist['Close'].iloc[-1])
            print(f"Current WD price (yfinance): ${current:.2f}")
        except Exception as e2:
            if args.yes:
                print("ERROR: Cannot auto-fetch WD price, and --yes flag set. Aborting.")
                return 1
            current_input = input(f"Enter current WD price: ")
            current = float(current_input)

    raw_return = (current / entry_price - 1) * 100
    spy_return = get_spy_return_since(str(entry_date))
    sector_return = get_sector_return_since(str(entry_date))
    abnormal_return = raw_return - spy_return

    print(f"Raw return: {raw_return:+.2f}%")
    print(f"SPY return (approx): {spy_return:+.2f}%")
    print(f"XLF sector return (approx): {sector_return:+.2f}%")
    print(f"Abnormal return vs SPY: {abnormal_return:+.2f}%")
    print()

    if args.dry_run:
        print("[DRY RUN] Would close position and complete hypothesis")
        outcome = "WIN" if raw_return > 0 else "LOSS"
        print(f"[DRY RUN] Outcome would be: {outcome}")
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
        result = trader.close_position('WD')
        print(f"  Position closed: {result}")
    except Exception as e:
        print(f"WARNING: Could not close via trader.close_position: {e}")
        print("         Please close WD position manually in Alpaca.")

    # Complete hypothesis
    print("\nCompleting hypothesis...")
    outcome = "WIN" if raw_return > 0 else "LOSS"
    direction_correct = raw_return > 0
    signal_validated = abnormal_return > 0.5  # above 0.5% abnormal threshold

    post_mortem = (
        f"{outcome}: WD trade closed at ${current:.2f} (entry ${entry_price:.2f}). "
        f"Raw return: {raw_return:+.2f}%. Abnormal vs SPY: {abnormal_return:+.2f}%. "
        f"XLF sector return: {sector_return:+.2f}%. "
        f"Cluster: 3 insiders filed March 20 2026, last purchase March 18. "
        f"WD near 52W low ($42.12), down ~51% from 52W high. Mortgage Finance sector. "
        f"Signal {'validated' if signal_validated else 'not validated'}: "
        f"direction {'correct' if direction_correct else 'incorrect'}, "
        f"abnormal {'above' if signal_validated else 'below'} 0.5% threshold."
    )

    try:
        research.complete_hypothesis(
            hypothesis_id='76678219',
            exit_price=current,
            actual_return_pct=raw_return,
            post_mortem=post_mortem,
            spy_return_pct=spy_return,
            sector_etf_return_pct=sector_return,
            timing_accuracy='on_time',
            mechanism_validated=signal_validated,
            confounder_attribution='none_identified',
            surprise_factor=abs(raw_return - 2.0),
        )
        print(f"  Hypothesis completed. Return: {raw_return:+.2f}%")
    except Exception as e:
        print(f"ERROR completing hypothesis: {e}")
        return 1

    print()
    print("=" * 60)
    print(f"TRADE COMPLETE: {outcome}")
    print(f"  Raw: {raw_return:+.2f}% | Abnormal vs SPY: {abnormal_return:+.2f}%")
    print(f"  Signal {'VALIDATED' if signal_validated else 'NOT validated'} (threshold: +0.5% abnormal)")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
