"""
KO (Coca-Cola) Tariff Defensive Long Trade Activation
======================================================
Run this on April 7, 2026 at market open (first open after Good Friday April 3).

TRADE DETAILS:
  Hypothesis: dbe0dc29 (tariff_ko_defensive_long, 10d hold)
  Signal: KO outperforms SPY after major tariff escalations
  - n=10 events (2018-2026), 80% direction up, p=0.0061 (10d)
  - OOS (2024+, n=4): +5.06% avg, 100% direction correct
  - Passes multiple testing (2 horizons p<0.05; 10d alone p<0.01)
  - Bonferroni-corrected (tested 5 stocks, p<0.01 threshold)
  Expected: +3.14% abnormal over 10 trading days
  Entry: April 7 open (trigger already set in trade_loop)
  Exit: 10 trading days after entry (deadline ~April 21)
  Position: $5,000
  Stop loss: 10%
  Take profit: 15%

NOTE: This trade fires AUTOMATICALLY via trade_loop at April 7 09:30.
Run this script ONLY if trade_loop fails to activate it automatically.

CONFIRMATION CHECKS:
  1. Portfolio capacity < 5/5
  2. No major company-specific news on KO (earnings, recall, management change)
  3. April 2 Liberation Day tariff announcement was a major escalation
  4. KO hasn't already run up >5% post-announcement (don't chase)

Usage:
  python tools/activate_ko_tariff_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db

HYPOTHESIS_ID = 'dbe0dc29'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 10
TAKE_PROFIT_PCT = 15


def main():
    parser = argparse.ArgumentParser(description='Activate KO tariff defensive long trade')
    parser.add_argument('--dry-run', action='store_true', help='Check conditions but do not trade')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    db.init_db()

    print("=" * 60)
    print("KO TARIFF DEFENSIVE LONG - ACTIVATION CHECK")
    print("=" * 60)
    print()

    # Load hypothesis
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found")
        return

    print(f"Hypothesis: {HYPOTHESIS_ID} ({h['event_type']})")
    print(f"Status: {h['status']}")
    print(f"Expected: {h['expected_direction']} {h['expected_magnitude_pct']}% in {h['expected_timeframe_days']}d")
    print()

    if h['status'] == 'active':
        print("Trade already active. Check Alpaca for position.")
        return
    if h['status'] not in ['pending', 'approved']:
        print(f"WARNING: Hypothesis status is '{h['status']}' — expected 'pending'. Review before proceeding.")

    # Check current KO price
    try:
        import yfinance as yf
        ko = yf.Ticker('KO')
        current_price = ko.history(period='2d')['Close'].iloc[-1]
        print(f"KO current price: ${current_price:.2f}")
    except Exception as e:
        print(f"WARNING: Could not fetch KO price: {e}")
        current_price = None

    # Check capacity
    try:
        positions = trader.get_api().list_positions()
        active_count = len(positions)
        print(f"Active positions: {active_count}/5")
        if active_count >= 5:
            print("ERROR: Portfolio at capacity (5/5). Cannot add new position.")
            if not args.dry_run:
                return
    except Exception as e:
        print(f"WARNING: Could not check positions: {e}")

    print()
    print("CONFIRMATION CHECKLIST:")
    print("  [ ] Liberation Day (April 2) tariff announcement was major escalation (>10% broad)")
    print("  [ ] No major KO company-specific news (earnings not until late April)")
    print("  [ ] Portfolio capacity < 5 active positions")
    print("  [ ] KO price hasn't already jumped >5% since April 2 announcement")
    print()

    if args.dry_run:
        print(f"DRY RUN: Would buy KO long ${POSITION_SIZE} at market open")
        print(f"  Stop loss: {STOP_LOSS_PCT}%")
        print(f"  Take profit: {TAKE_PROFIT_PCT}%")
        print(f"  Hold: 10 trading days (exit ~April 21)")
        return

    if not args.yes:
        confirm = input(f"Activate KO long ${POSITION_SIZE}? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Cancelled.")
            return

    # Place the trade
    print(f"Activating KO long ${POSITION_SIZE}...")
    try:
        result = trader.place_order(
            symbol='KO',
            side='buy',
            position_size_usd=POSITION_SIZE,
            hypothesis_id=HYPOTHESIS_ID,
            stop_loss_pct=STOP_LOSS_PCT,
            take_profit_pct=TAKE_PROFIT_PCT
        )
        print(f"Order placed: {result}")
        print(f"Hold 10 trading days, exit ~April 21")
    except Exception as e:
        print(f"ERROR placing order: {e}")
        return

    print(f"\nKO long activated. Monitor via trade_loop.")
    print(f"Hypothesis {HYPOTHESIS_ID} is now active.")


if __name__ == '__main__':
    main()
