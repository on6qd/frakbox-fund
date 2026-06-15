"""
WFC (Wells Fargo) Tariff Short Trade Activation Script
========================================================
ONLY activate after April 2, 2026 Liberation Day tariff announcement IF:
  - Major tariff escalation announced (>15% universal/reciprocal tariffs)
  - SPY is DOWN on April 2 (market pricing in tariff shock)

TRADE DETAILS:
  Hypothesis: b73efac3 (tariff_escalation_bank_short, 5d hold)
  Signal: WFC underperforms SPY by -2.39% avg over 5 days after major tariff events
  Sample: 8/8 events (2018-2025), 7/8 negative (88%), p=0.0045
  Entry: April 3 market open (day after Liberation Day announcement)
  Exit: April 10 close (5 trading days)
  Position: $5,000 short
  Stop loss: 10%

ABORT CONDITIONS:
  - Tariff announcement < 15% (not major escalation)
  - SPY UP on April 2 (market not pricing in shock - 2025-02-01 scenario)
  - WFC already down >5% before April 3 (too late to enter)
  - Portfolio at 5/5 capacity

Usage:
  python tools/activate_wfc_tariff_trade.py [--dry-run] [--yes]
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db

HYPOTHESIS_ID = 'b73efac3'
SYMBOL = 'WFC'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 10
TIMEFRAME_DAYS = 5


def get_wfc_price():
    try:
        import yfinance as yf
        t = yf.Ticker(SYMBOL)
        hist = t.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        return None
    except Exception as e:
        print(f"Warning: could not fetch WFC price: {e}")
        return None


def get_spy_return_today():
    """Get SPY return for April 2 (the tariff announcement day)."""
    try:
        import yfinance as yf
        t = yf.Ticker('SPY')
        hist = t.history(period='3d')
        if len(hist) >= 2:
            ret = (hist['Close'].iloc[-1] / hist['Close'].iloc[-2] - 1) * 100
            return ret
        return None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description='Activate WFC tariff short trade')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without placing order')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    print("=" * 65)
    print("WFC TARIFF ESCALATION SHORT — ACTIVATION CHECK")
    print("=" * 65)
    print()
    print("SIGNAL: WFC underperforms SPY by -2.39% avg over 5 days after")
    print("        major US tariff escalations. 7/8 events negative (p=0.0045)")
    print()

    db.init_db()

    # Load hypothesis
    hyps = db.load_hypotheses()
    h = next((h for h in hyps if h['id'] == HYPOTHESIS_ID), None)
    if h is None:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found!")
        return 1

    if h['status'] != 'pending':
        print(f"WARNING: Hypothesis status is '{h['status']}' (expected 'pending')")
        if h['status'] == 'active':
            print("  Trade already active!")
        return 1

    # Check current prices
    wfc_price = get_wfc_price()
    spy_ret = get_spy_return_today()

    print(f"WFC current price: ${wfc_price:.2f}" if wfc_price else "WFC price: could not fetch")
    print(f"SPY return today: {spy_ret:+.2f}%" if spy_ret else "SPY return: could not fetch")
    print()

    # Abort conditions
    abort = False

    if spy_ret is not None and spy_ret > 0:
        print("⚠️  WARNING: SPY is UP today. Market NOT pricing in tariff shock.")
        print("   Historical miss: 2025-02-01 (+0.5% abnormal when market rallied).")
        print("   Consider ABORTING unless tariff announcement was much larger than expected.")
        abort = True

    if wfc_price and wfc_price > 90:
        print(f"⚠️  NOTE: WFC at ${wfc_price:.2f} — check if already moved significantly.")

    print()
    print("CHECKLIST (verify manually before activating):")
    print("  1. April 2 tariff announcement: >15% universal/reciprocal tariffs? [Y/N]")
    print("  2. Market consensus: announcement bigger/worse than expected? [Y/N]")
    print("  3. SPY down on April 2 close? [Y/N]")
    print("  4. Portfolio capacity: <5 active positions? [Y/N]")
    print()

    if abort and not args.yes:
        confirm_abort = input("Conditions may not be met. Continue anyway? (yes/no): ").strip().lower()
        if confirm_abort != 'yes':
            print("Activation aborted.")
            return 0

    if args.dry_run:
        print(f"[DRY RUN] Would SHORT {SYMBOL} at market open.")
        print(f"  Position: ${POSITION_SIZE}")
        print(f"  Stop loss: {STOP_LOSS_PCT}%")
        print(f"  Hold: {TIMEFRAME_DAYS} trading days")
        print(f"  Expected: -2.39% abnormal return vs SPY")
        return 0

    if not args.yes:
        confirm = input(f"Activate WFC short trade (${POSITION_SIZE}, {STOP_LOSS_PCT}% stop)? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Activation cancelled.")
            return 0

    # Place the trade
    print(f"\nPlacing SHORT order for {SYMBOL}...")
    try:
        result = trader.place_order(
            symbol=SYMBOL,
            side='sell',
            notional=POSITION_SIZE,
        )
        if not result.get('success', False):
            print(f"ERROR: Order failed: {result}")
            return 1
        print(f"  Order placed: {result}")
    except Exception as e:
        print(f"ERROR placing order: {e}")
        return 1

    # Update hypothesis to active
    entry_price = wfc_price or 0

    from trade_loop import _trading_deadline
    deadline = _trading_deadline(TIMEFRAME_DAYS)

    import yfinance as yf
    spy = yf.Ticker('SPY')
    spy_hist = spy.history(period='2d')
    spy_entry = float(spy_hist['Close'].iloc[-1]) if not spy_hist.empty else None

    db.update_hypothesis_fields(
        HYPOTHESIS_ID,
        status='active',
        trade={
            'entry_price': entry_price,
            'position_size': POSITION_SIZE,
            'entry_time': datetime.now().isoformat(),
            'deadline': deadline.isoformat(),
            'stop_loss_pct': STOP_LOSS_PCT,
            'take_profit_pct': None,
            'spy_at_entry': spy_entry,
            'activated_by': 'manual_april3_tariff',
        }
    )

    print()
    print("=" * 65)
    print("TRADE ACTIVATED")
    print(f"  Symbol: {SYMBOL} SHORT")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Position: ${POSITION_SIZE}")
    print(f"  Stop loss: {STOP_LOSS_PCT}%")
    print(f"  Deadline: {deadline.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Expected: -2.39% abnormal vs SPY over 5 days")
    print("=" * 65)

    return 0


if __name__ == '__main__':
    sys.exit(main())
