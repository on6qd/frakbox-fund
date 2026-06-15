"""
GLD (SPDR Gold Shares) Tariff Escalation Long Trade Activation Script
=======================================================================
Run this on April 7, 2026 at market open IF Liberation Day (April 2) confirmed
major tariff escalation AND SPY was DOWN on April 2.

TRADE DETAILS:
  Hypothesis: b768e8d8 (tariff_escalation_gld_long, 20d hold)
  Signal: GLD outperforms SPY +3.87% avg over 20 days after major tariff escalations
  - n=19 events (2018-2025), 84% LONG direction, p=0.0016
  - 10d signal: +2.4%, 79% dir, p=0.014 (optimal for Liberation Day)
  - OOS 2025 Liberation Day: GLD +8.8% at 10d (strong confirmation)
  Entry: April 7 open (first open after Good Friday April 3)
  Exit: 20 trading days after entry (deadline ~April 28)
  Stop loss: 10%
  Take profit: 15%

ABORT CONDITIONS:
  - SPY UP on April 2 (market not pricing tariff shock — 2019-08-23 analog)
  - GLD already up >5% since April 2 (too late, missed the entry)
  - Portfolio at 5/5 capacity
  - Tariff announcement < 10% broad tariff (not a major escalation)

SIGNAL CONTEXT:
  - Tariff escalation raises inflation expectations → gold rally
  - Safe haven demand during equity market stress
  - 2025 Liberation Day analog: GLD +4.3% at 5d, +8.8% at 10d (then reversed at 90d rollback)
  - NOTE: 20d hold covers ~April 7–April 28. Rollback risk exists. Stop at 10% protects capital.

Usage:
  python tools/activate_gld_tariff_trade.py [--dry-run] [--yes]
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import trader

HYPOTHESIS_ID = 'b768e8d8'
SYMBOL = 'GLD'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 10.0
TAKE_PROFIT_PCT = 15.0
HOLD_DAYS = 20


def main():
    parser = argparse.ArgumentParser(description='Activate GLD tariff long trade')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation and execute')
    args = parser.parse_args()

    db.init_db()

    print("=" * 70)
    print("GLD TARIFF ESCALATION LONG — ACTIVATION SCRIPT")
    print("=" * 70)
    print()

    # Check hypothesis
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found in database")
        sys.exit(1)

    print(f"Hypothesis: {HYPOTHESIS_ID}")
    print(f"Status: {h.get('status')}")
    print()

    if h.get('status') == 'active':
        print("Trade already ACTIVE. Nothing to do.")
        sys.exit(0)

    if h.get('status') not in ('pending',):
        print(f"Hypothesis is {h.get('status')} — cannot activate. Exiting.")
        sys.exit(1)

    # Check portfolio capacity
    try:
        api = trader.get_api()
        positions = api.list_positions()
        active_count = len(positions)
        print(f"Portfolio: {active_count}/5 positions active")
        if active_count >= 5:
            print("ERROR: Portfolio at 5/5 capacity. Cannot add GLD.")
            sys.exit(1)
    except Exception as e:
        print(f"Warning: Could not check portfolio capacity: {e}")

    # Get GLD current price
    try:
        quote = api.get_latest_trade(SYMBOL)
        current_price = float(quote.price)
        print(f"GLD current price: ${current_price:.2f}")
    except Exception as e:
        print(f"Warning: Could not get GLD price: {e}")
        current_price = None

    # Calculate trade details
    shares = int(POSITION_SIZE / current_price) if current_price else None
    stop_price = current_price * (1 - STOP_LOSS_PCT / 100) if current_price else None
    take_profit = current_price * (1 + TAKE_PROFIT_PCT / 100) if current_price else None

    # Calculate exit deadline (20 trading days from today)
    today = datetime.now()
    # Approx: 20 trading days ≈ 28 calendar days
    exit_deadline = today + timedelta(days=28)
    exit_str = exit_deadline.strftime('%Y-%m-%dT15:50')

    print()
    print("=" * 70)
    print("TRADE SUMMARY")
    print("=" * 70)
    print(f"  Action: BUY {SYMBOL} (long)")
    print(f"  Entry: ${current_price:.2f} (market order)" if current_price else "  Entry: market order")
    print(f"  Position: ${POSITION_SIZE:,}")
    print(f"  Shares: ~{shares}" if shares else "  Shares: TBD")
    print(f"  Stop loss: {STOP_LOSS_PCT}%  (${stop_price:.2f})" if stop_price else f"  Stop loss: {STOP_LOSS_PCT}%")
    print(f"  Take profit: {TAKE_PROFIT_PCT}%  (${take_profit:.2f})" if take_profit else f"  Take profit: {TAKE_PROFIT_PCT}%")
    print(f"  Exit deadline: {exit_str} ({HOLD_DAYS} trading days)")
    print()
    print(f"  EXPECTED: +3.87% 20d abnormal (N=19, 84% direction, p=0.0016)")
    print(f"  2025 OOS: GLD +8.8% at 10d after Liberation Day 2025")
    print()
    print("  VERIFY BEFORE RUNNING:")
    print("  [ ] Liberation Day (April 2) had major tariff announcement (>10% broad tariffs)")
    print("  [ ] SPY was DOWN on April 2 (market pricing in shock)")
    print("  [ ] GLD has NOT already moved >5% since April 2 (missed entry)")
    print("  [ ] Portfolio has capacity (< 5/5 positions)")
    print()

    if args.dry_run:
        print("[DRY RUN] — no trade placed.")
        sys.exit(0)

    if not args.yes:
        confirm = input("All conditions met? Place trade? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            sys.exit(0)

    print("\nActivating GLD long trade...")

    # Set trigger for immediate execution
    db.update_hypothesis_fields(HYPOTHESIS_ID,
        trigger='next_market_open',
        trigger_position_size=POSITION_SIZE,
        trigger_stop_loss_pct=STOP_LOSS_PCT,
        trigger_take_profit_pct=TAKE_PROFIT_PCT,
    )
    print(f"Trigger set to 'next_market_open'. Trade_loop will execute at next open.")
    print()
    print("Done. GLD long will enter at next market open.")
    print(f"Expected exit: ~{exit_str} ({HOLD_DAYS} trading days)")


if __name__ == '__main__':
    main()
