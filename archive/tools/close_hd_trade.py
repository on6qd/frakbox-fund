"""
HD Short Trade Closing Script
==============================
Run this on Friday March 27 at market close (3:55-4:00 PM ET).

WHAT THIS DOES:
  1. Gets current HD price from Alpaca
  2. Calls trader.close_position('HD') to submit market buy-to-cover
  3. Calculates actual return vs SPY return
  4. Calls research.complete_hypothesis() with post-mortem
  5. Records the result in SQLite (hypotheses + pre_registrations tables)

Hypothesis: 86d28864 (sp500_52w_low_momentum_short)
Entry: SHORT HD at Monday March 23 open
Exit: Friday March 27 close (5d hold)
Expected: -1.68% abnormal return (short profits from decline)

Usage:
  python tools/close_hd_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import market_data

HYPOTHESIS_ID = '86d28864'
SYMBOL = 'HD'


def main():
    parser = argparse.ArgumentParser(description='Close HD short trade')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without executing')
    args = parser.parse_args()

    print("=== HD Short Trade Closer ===")
    print(f"Hypothesis: {HYPOTHESIS_ID}")
    print(f"Symbol: {SYMBOL}")
    print()

    # Load hypothesis
    hypotheses = research.load_hypotheses()
    h = next((h for h in hypotheses if h['id'] == HYPOTHESIS_ID), None)
    if h is None:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found!")
        return
    print(f"Status: {h['status']}")
    if h['status'] != 'active':
        print(f"WARNING: Hypothesis is not 'active' — it is '{h['status']}'")
        if not args.dry_run:
            print("Cannot close a non-active hypothesis. Run after trade executes.")
            return

    # Get current position
    try:
        position = next((p for p in trader.get_api().list_positions() if p.symbol == SYMBOL), None)
        if position is None:
            print(f"ERROR: No position found for {SYMBOL} in Alpaca.")
            return
        current_price = float(position.current_price)
        entry_price = float(position.avg_entry_price)
        qty = float(position.qty)
        print(f"Position: {qty} shares at avg ${entry_price:.2f}")
        print(f"Current price: ${current_price:.2f}")
        # For SHORT: profit when price falls
        raw_return = (entry_price - current_price) / entry_price * 100
        print(f"Raw return (short): {raw_return:+.2f}%")
    except Exception as e:
        print(f"ERROR getting position: {e}")
        if not args.dry_run:
            return
        entry_price = 320.75  # Approximate from detection
        current_price = 320.75
        raw_return = 0.0

    # Get SPY return over same period
    entry_date = h.get('activated_at', '').split('T')[0] if h.get('activated_at') else None
    if entry_date:
        try:
            spy_impact = market_data.measure_event_impact(
                'SPY', [entry_date], benchmark=None
            )
            spy_return = spy_impact.get('avg_raw_5d', 0)
            print(f"SPY return since entry: {spy_return:+.2f}%")
        except Exception as e:
            print(f"Could not get SPY return: {e}")
            spy_return = 0.0
    else:
        spy_return = 0.0

    abnormal_return = raw_return - spy_return
    print(f"Abnormal return: {abnormal_return:+.2f}%")
    print()

    if args.dry_run:
        print("DRY RUN: Would close HD short and record to hypothesis.")
        return

    # Close the position
    print("Closing HD short position...")
    try:
        trader.close_position(SYMBOL)
        print("Position closed.")
    except Exception as e:
        print(f"ERROR closing position: {e}")
        return

    # Complete the hypothesis
    direction_correct = raw_return > 0.5  # short profits from price decline

    research.complete_hypothesis(
        hypothesis_id=HYPOTHESIS_ID,
        exit_price=current_price,
        actual_return_pct=raw_return,
        post_mortem=(
            f"HD short trade (52-week low first-touch signal). "
            f"Entry: ${entry_price:.2f} (short), Exit: ${current_price:.2f}. "
            f"Raw return: {raw_return:+.2f}%, SPY: {spy_return:+.2f}%, Abnormal: {abnormal_return:+.2f}%. "
            f"Direction {'CORRECT' if direction_correct else 'WRONG'} (expected stock to fall). "
        ),
        spy_return_pct=spy_return,
        timing_accuracy='on_schedule',
        mechanism_validated=direction_correct,
        surprise_factor=0 if direction_correct else 1,
    )
    print(f"Hypothesis {HYPOTHESIS_ID} completed.")
    print(f"Return: {raw_return:+.2f}% raw, {abnormal_return:+.2f}% abnormal.")


if __name__ == '__main__':
    main()
