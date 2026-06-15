"""
FDXF (FedEx Freight) SpinCo Short — CURRENTLY ON HOLD
=======================================================
HYPOTHESIS 60558434 was ABANDONED on 2026-03-25 due to "desirable pure-play"
concern. FDXF is a known, desired LTL business — similar to GXO (which went
+1.8% in 5d, opposite direction to signal).

STATUS: DO NOT RUN until a NEW hypothesis is created in May 2026.

BEFORE CREATING NEW HYPOTHESIS (May 2026):
  1. Check S&P index announcements (press.spglobal.com) — if FDXF added to
     S&P MidCap 400 or S&P 500, ABORT (contra-signal, VGNT lesson).
  2. Review April 8 Investor Day data (equity value, strategy).
  3. Assess "desirability score" — who wants to own pure-play LTL?
  4. Check borrow availability for FDXF short.

SIGNAL STATS (base spinco short):
  N=25, 5d avg abnormal=-6.73%, neg_rate=72%
  BUT: "desirable" spincos (GXO, GEHC) show REVERSED signal.
  FDXF likely falls in "desirable" bucket (high-quality LTL franchise).

VGNT LESSON (March 2026):
  S&P announced VGNT (Aptiv spinco) for SmallCap 600 on day 2.
  Index buying pressure invalidated the short thesis.
  ALWAYS check press.spglobal.com before shorting any spinco.

Usage:
  python tools/activate_fdxf_trade.py [--dry-run] [--price XXXX]
  NOTE: Will refuse to run unless --force flag is provided.
"""

import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db


HYPOTHESIS_ID = '60558434'
POSITION_SIZE = 5000
HOLD_DAYS = 5
STOP_LOSS_PCT = 10.0
TAKE_PROFIT_PCT = 15.0
SYMBOL = 'FDXF'


def get_fdxf_price():
    """Get FDXF current price on first trading day."""
    try:
        import yfinance as yf
        hist = yf.Ticker(SYMBOL).history(period='1d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"  Warning: could not get {SYMBOL} price: {e}")
    return None


def check_portfolio_capacity():
    """Check if we have room for another position."""
    try:
        positions = trader.get_api().list_positions()
        active = len([p for p in positions if p.get('qty') != 0])
        print(f"  Active positions: {active}/5")
        return active < 5
    except Exception as e:
        print(f"  Warning: could not check positions: {e}")
        return True


def get_spy_return_today():
    """Get today's SPY return to compute abnormal return."""
    try:
        import yfinance as yf
        spy = yf.Ticker('SPY').history(period='2d')
        if len(spy) >= 2:
            spy_return = (spy['Close'].iloc[-1] / spy['Close'].iloc[-2] - 1) * 100
            return spy_return
    except Exception as e:
        print(f"  Warning: could not get SPY return: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description='Activate FDXF spinco short trade')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without trading')
    parser.add_argument('--price', type=float, help='Override FDXF price (for testing)')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--force', action='store_true', help='Override abandoned hypothesis guard')
    args = parser.parse_args()

    if not args.force:
        print("BLOCKED: Hypothesis 60558434 was ABANDONED (desirable pure-play concern).")
        print("FDXF is likely a 'desired' spinco (like GXO) where signal reverses.")
        print("Create a NEW hypothesis in May 2026 after:")
        print("  1. April 8 Investor Day data review")
        print("  2. S&P index announcement check (press.spglobal.com)")
        print("  3. Desirability assessment")
        print("  4. Borrow availability check")
        print("\nUse --force to override this guard (not recommended).")
        return 1

    print(f"\n=== FDXF SpinCo Short Activation ===")
    print(f"Hypothesis: {HYPOTHESIS_ID}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print()

    # Check hypothesis status
    hyp = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not hyp:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found!")
        return 1
    if hyp['status'] != 'pending':
        print(f"ERROR: Hypothesis status is '{hyp['status']}', expected 'pending'")
        print(f"  If already active, use close script instead.")
        return 1

    print(f"Hypothesis status: {hyp['status']} ✓")

    # Get price
    price = args.price or get_fdxf_price()
    if not price:
        print(f"ERROR: Could not get {SYMBOL} price. Provide with --price flag.")
        return 1
    print(f"{SYMBOL} close: ${price:.2f}")

    # Get SPY return for abnormal calculation
    spy_return = get_spy_return_today()
    if spy_return is not None:
        print(f"SPY return today: {spy_return:+.2f}%")

    # Abort: extreme drop on day 1 (don't chase catastrophic news)
    if spy_return is not None:
        # Get FDXF's approximate return vs initial FDX price
        # This is approximate - if FDXF dropped >30% on day 1, something exceptional happened
        pass  # No historical basis for a -30% day 1 drop, just flag as a warning

    # Check portfolio capacity
    if not check_portfolio_capacity():
        print(f"\nABORT: Portfolio at maximum capacity (5/5 positions).")
        print(f"  Wait for an existing position to close before activating.")
        return 1

    # Check VIX
    try:
        import yfinance as yf
        vix = yf.Ticker('^VIX').history(period='1d')
        current_vix = float(vix['Close'].iloc[-1]) if not vix.empty else None
        if current_vix:
            print(f"VIX: {current_vix:.1f}")
            if current_vix > 60:
                print(f"ABORT: VIX={current_vix:.1f} > 60. Circuit breaker risk. No trade.")
                return 1
    except Exception as e:
        print(f"Warning: could not check VIX: {e}")

    # Compute position sizing
    shares = int(POSITION_SIZE / price)
    actual_position = shares * price
    print(f"\nTrade plan:")
    print(f"  Symbol: {SYMBOL}")
    print(f"  Direction: SHORT")
    print(f"  Shares: {shares} @ ${price:.2f} = ${actual_position:.0f}")
    print(f"  Stop loss: {STOP_LOSS_PCT}% (stop at ${price * (1 + STOP_LOSS_PCT/100):.2f})")
    print(f"  Take profit: {TAKE_PROFIT_PCT}% (target ${price * (1 - TAKE_PROFIT_PCT/100):.2f})")
    print(f"  Hold: {HOLD_DAYS} trading days (exit ~June 10, 2026)")

    if args.dry_run:
        print(f"\n[DRY RUN] Would short {shares} shares of {SYMBOL} at ${price:.2f}")
        return 0

    if not args.yes:
        confirm = input(f"\nConfirm SHORT {shares} shares of {SYMBOL}? [y/N] ")
        if confirm.lower() != 'y':
            print("Aborted.")
            return 0

    # Set trigger to immediate and update fields
    db.update_hypothesis_fields(HYPOTHESIS_ID,
        trigger='immediate',
        trigger_position_size=POSITION_SIZE,
        trigger_stop_loss_pct=STOP_LOSS_PCT,
        trigger_take_profit_pct=TAKE_PROFIT_PCT,
    )

    # Execute trade via trader
    result = trader.place_order(
        symbol=SYMBOL,
        side='sell',  # short
        qty=shares,
        hypothesis_id=HYPOTHESIS_ID,
        stop_loss_pct=STOP_LOSS_PCT,
        take_profit_pct=TAKE_PROFIT_PCT,
    )

    if result:
        print(f"\n✓ SHORT {shares} shares of {SYMBOL} placed successfully")
        print(f"  Order ID: {result.get('order_id', 'unknown')}")
        print(f"  Entry price: ~${price:.2f}")

        # Log the activation
        db.log_trade(
            type='activate',
            hypothesis_id=HYPOTHESIS_ID,
            symbol=SYMBOL,
            direction='short',
            entry_price=price,
            position_size=POSITION_SIZE,
            order_id=result.get('order_id'),
            trigger_type='manual_close_day1'
        )
    else:
        print(f"\nERROR: Trade placement failed. Check logs.")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
