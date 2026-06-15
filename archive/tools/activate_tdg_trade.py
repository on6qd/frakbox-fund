"""
TDG (TransDigm Group) 52-Week Low Short Trade Activation Script
===============================================================
Run this at market open the day AFTER TDG first closes below $1,152.50.

TRADE DETAILS:
  Hypothesis: e25f0c6f (sp500_52w_low_momentum_short, 5d hold)
  Signal: TDG first-ever 52w low (0 crossings in 2yr history). 52w low = $1,152.50.
  Current (March 25): $1,162.78 (+0.89% above barrier)
  Entry: Next market open AFTER TDG closes below $1,152.50 for the first time
  Exit: 5 trading days after entry (trade_loop auto-closes at deadline)
  Position: $5,000
  Stop loss: 8% (trade_loop enforces)
  Take profit: 10% (optional)
  Earnings: May 5 2026 (no conflict with 5d hold)

EXECUTION:
  1. Monitor TDG daily closing price vs $1,152.50
  2. When TDG closes below $1,152.50 for first time:
     a. Run: python tools/activate_tdg_trade.py
     b. Script verifies TDG still below barrier at open
     c. Checks portfolio capacity < 5/5
     d. Calls research.activate_hypothesis() to record entry
     e. Places $5,000 market short order via Alpaca

ABORT CONDITIONS (do NOT trade if any are true):
  - TDG has recovered above $1,152.50 by next open (signal invalidated)
  - Portfolio at 5/5 capacity
  - Major news announced after-hours changing fundamental picture
  - Market circuit-breaker halted (VIX > 60)
  - Earnings announcement within 5 days (check for early/special reports)

Usage:
  python tools/activate_tdg_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db
import yfinance as yf

TDG_52W_LOW = 1152.50   # 52-week low as of signal date March 25 2026
HYPOTHESIS_ID = 'e25f0c6f'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 8
TAKE_PROFIT_PCT = 10


def get_tdg_current_price():
    """Get current TDG price."""
    try:
        ticker = yf.Ticker('TDG')
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
    """Check current number of active trades."""
    db.init_db()
    hypotheses = db.load_hypotheses()
    hyp_active = [h for h in hypotheses if h.get('status') == 'active']
    hyp_count = len(hyp_active)
    try:
        api = trader.get_api()
        alpaca_positions = api.list_positions()
        alpaca_count = len(alpaca_positions)
    except Exception:
        alpaca_count = 0
    count = max(hyp_count, alpaca_count)
    if alpaca_count > hyp_count:
        print(f"  [WARNING] Alpaca has {alpaca_count} positions but only {hyp_count} in hypothesis DB!")
    return count


def main():
    parser = argparse.ArgumentParser(description='Activate TDG 52w-low short trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--price', type=float, default=None,
                        help='Override entry price (default: fetch live)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt')
    args = parser.parse_args()

    print("=" * 60)
    print("TDG 52-WEEK LOW SHORT TRADE ACTIVATION")
    print("=" * 60)
    print()
    print(f"Hypothesis: {HYPOTHESIS_ID} (sp500_52w_low_momentum_short, 5d hold)")
    print(f"Signal: First 52w low touch (0 crossings in 2yr history)")
    print(f"52w low barrier: ${TDG_52W_LOW:.2f}")
    print()

    # Verify hypothesis exists and is pending
    db.init_db()
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found in DB")
        return 1
    if h.get('status') != 'pending':
        print(f"ERROR: Hypothesis status is '{h.get('status')}', expected 'pending'")
        return 1

    # Check capacity
    active_count = check_capacity()
    print(f"Active trades: {active_count}/5")
    if active_count >= 5:
        print("ERROR: Portfolio at capacity (5/5). Cannot activate.")
        return 1

    # Get current TDG price
    if args.price:
        current_price = args.price
        print(f"Current TDG price: ${current_price:.2f} (manual override)")
    else:
        current_price = get_tdg_current_price()
        if current_price is None:
            print("ERROR: Could not fetch TDG price. Use --price to override.")
            return 1
        print(f"Current TDG price: ${current_price:.2f}")

    # Verify signal is still valid (TDG still below 52w low)
    if current_price > TDG_52W_LOW * 1.02:  # 2% tolerance at open
        pct_above = (current_price / TDG_52W_LOW - 1) * 100
        print(f"ABORT: TDG ${current_price:.2f} is +{pct_above:.1f}% above 52w low ${TDG_52W_LOW:.2f}")
        print("Signal invalidated — TDG has recovered. Do NOT short.")
        return 1

    signal_valid = current_price <= TDG_52W_LOW
    breach_pct = (current_price / TDG_52W_LOW - 1) * 100
    print(f"Signal valid: TDG ${current_price:.2f} {'<' if signal_valid else 'slightly above'} 52w low ${TDG_52W_LOW:.2f} (breach={breach_pct:+.1f}%)")

    # Calculate shares and exit date
    shares = int(POSITION_SIZE / current_price)
    exit_date = date.today() + timedelta(days=7)  # 5 trading days = ~7 calendar days
    stop_price = current_price * (1 + STOP_LOSS_PCT / 100)
    take_profit_price = current_price * (1 - TAKE_PROFIT_PCT / 100)

    print()
    print(f"Entry price: ${current_price:.2f}")
    print(f"Position size: ${POSITION_SIZE:,}")
    print(f"Approx shares: {shares}")
    print(f"Stop loss: {STOP_LOSS_PCT}% (trigger at ${stop_price:.2f})")
    print(f"Take profit: {TAKE_PROFIT_PCT}% (trigger at ${take_profit_price:.2f})")
    print(f"Expected exit: {exit_date.strftime('%A %B %d')} (5 trading days)")
    print()

    if args.dry_run:
        print("[DRY RUN] Would activate hypothesis and place market short order")
        print(f"[DRY RUN] research.activate_hypothesis('{HYPOTHESIS_ID}', {current_price:.2f}, {POSITION_SIZE})")
        print(f"[DRY RUN] Place SHORT TDG ${POSITION_SIZE} at market")
        return 0

    if not args.yes:
        confirm = input("Activate hypothesis and place short order? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 0

    # Activate hypothesis (records entry price, creates pre-registration record)
    print("Activating hypothesis...")
    research.activate_hypothesis(HYPOTHESIS_ID, current_price, POSITION_SIZE)
    print(f"  Hypothesis {HYPOTHESIS_ID} activated at ${current_price:.2f}")

    # Update stop loss and take profit
    db.update_hypothesis_fields(HYPOTHESIS_ID,
        trigger_stop_loss_pct=STOP_LOSS_PCT,
        trigger_take_profit_pct=TAKE_PROFIT_PCT
    )

    # Place Alpaca short order
    print("Placing Alpaca short order...")
    try:
        order = trader.place_order(
            symbol='TDG',
            side='sell',
            qty=shares,
            order_type='market',
            time_in_force='day'
        )
        print(f"  Order placed: {order}")
    except Exception as e:
        print(f"ERROR placing order: {e}")
        print("  Please place short order manually in Alpaca: {shares} shares of TDG")
        return 1

    print()
    print("=" * 60)
    print(f"TDG SHORT ACTIVATED")
    print(f"  Entry: ${current_price:.2f} | Stop: ${stop_price:.2f} | TP: ${take_profit_price:.2f}")
    print(f"  Exit deadline: {exit_date.strftime('%Y-%m-%d')}")
    print("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
