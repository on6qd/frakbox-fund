"""
Monday March 30, 2026 — Close HD, ABT, BAX Short Positions
============================================================
Run at 9:30-9:35 AM ET Monday March 30 to close the 3 expired short positions.

These are all NON-FIRST-TOUCH entries (invalid under sp500_52w_low_momentum_short).
Record as invalid in post-mortems.

Usage:
  python tools/monday_close_runbook.py [--dry-run]
  python tools/monday_close_runbook.py --symbol HD   (close just one)
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import market_data
import db

# Trades to close
TRADES = [
    {
        'hypothesis_id': '86d28864',
        'symbol': 'HD',
        'prior_crossings': 1,
        'entry_note': 'HD - had 1 prior 52w low crossing in 2-year lookback. Non-first-touch = INVALID test.',
    },
    {
        'hypothesis_id': '9777ef67',
        'symbol': 'ABT',
        'prior_crossings': 4,
        'entry_note': 'ABT - had 4 prior 52w low crossings. Non-first-touch = INVALID test.',
    },
    {
        'hypothesis_id': '6bd7d035',
        'symbol': 'BAX',
        'prior_crossings': 25,
        'entry_note': 'BAX - had 25 prior 52w low crossings. Non-first-touch = INVALID test.',
    },
]


def get_spy_return_since(entry_date_str: str) -> float:
    """Get SPY return from entry date to today."""
    try:
        from tools.yfinance_utils import safe_download
        import pandas as pd
        spy = safe_download('SPY', start=entry_date_str, end=datetime.now().strftime('%Y-%m-%d'), progress=False)
        if spy is None or spy.empty:
            return 0.0
        close = spy['Close'].dropna()
        if len(close) < 2:
            return 0.0
        return float((close.iloc[-1] / close.iloc[0] - 1) * 100)
    except Exception as e:
        print(f"  Could not get SPY return: {e}")
        return 0.0


def close_trade(trade: dict, dry_run: bool = False):
    """Close a single trade and record outcome."""
    hid = trade['hypothesis_id']
    sym = trade['symbol']

    print(f"\n{'='*50}")
    print(f"  {sym} (hypothesis {hid})")
    print(f"{'='*50}")

    # Load hypothesis
    h = db.get_hypothesis_by_id(hid)
    if not h:
        print(f"  ERROR: Hypothesis {hid} not found!")
        return False

    print(f"  Status: {h['status']}")
    if h['status'] != 'active':
        print(f"  SKIP: Not active (status={h['status']})")
        return False

    # Get current position from Alpaca
    try:
        positions = trader.get_api().list_positions()
        position = next((p for p in positions if p.symbol == sym), None)
        if position is None:
            print(f"  WARNING: No Alpaca position for {sym}. May already be closed.")
            # Still record outcome using last known price
            current_price = trader.get_current_price(sym)
            entry_price = float(h.get('entry_price', current_price) or current_price)
            qty = 0
        else:
            current_price = float(position.current_price)
            entry_price = float(position.avg_entry_price)
            qty = float(position.qty)
    except Exception as e:
        print(f"  ERROR getting position: {e}")
        return False

    # Calculate returns (SHORT position)
    raw_return = (entry_price - current_price) / entry_price * 100
    entry_date = (h.get('activated_at') or '2026-03-23')[:10]
    spy_return = get_spy_return_since(entry_date)
    abnormal_return = raw_return - spy_return  # For short: negative means stock beat SPY

    print(f"  Shares: {qty} (short)")
    print(f"  Entry price: ${entry_price:.2f}")
    print(f"  Current price: ${current_price:.2f}")
    print(f"  Raw return (short): {raw_return:+.2f}%")
    print(f"  SPY return (same period): {spy_return:+.2f}%")
    print(f"  Abnormal return: {abnormal_return:+.2f}%")
    print(f"  Non-first-touch: {trade['prior_crossings']} prior crossings")

    if dry_run:
        print(f"  [DRY RUN] Would close position and record outcome")
        return True

    # Close position
    if qty != 0:
        try:
            order = trader.close_position(sym)
            print(f"  Order submitted: {order}")
        except Exception as e:
            print(f"  ERROR closing position: {e}")
            print(f"  Will still record outcome in DB.")

    # Record outcome - these are invalid tests (non-first-touch)
    post_mortem = (
        f"NON-FIRST-TOUCH ENTRY (invalid test). {trade['entry_note']} "
        f"Raw return (short): {raw_return:+.2f}%. SPY: {spy_return:+.2f}%. "
        f"Abnormal: {abnormal_return:+.2f}%. "
        f"RESULT: Stock moved {-raw_return:+.2f}% vs hypothesis expectation of -3.2% (short). "
        f"Cannot count this as valid evidence for or against the hypothesis."
    )

    try:
        research.complete_hypothesis(
            hypothesis_id=hid,
            outcome='completed',
            actual_return_pct=abnormal_return,
            hypothesis_correct=(abnormal_return < -0.5),  # Short correct if abnormal < -0.5%
            mechanism_validated=False,  # Can't validate from non-first-touch
            post_mortem=post_mortem,
        )
        print(f"  Hypothesis {hid} completed. Abnormal={abnormal_return:+.2f}%")
    except Exception as e:
        print(f"  ERROR completing hypothesis: {e}")
        # Force update status
        db.update_hypothesis_fields(hid, status='completed', post_mortem=post_mortem)
        print(f"  Forced status to completed")

    return True


def main():
    parser = argparse.ArgumentParser(description='Close Monday expired short trades')
    parser.add_argument('--dry-run', action='store_true', help='Preview without executing')
    parser.add_argument('--symbol', type=str, help='Close only this symbol')
    args = parser.parse_args()

    db.init_db()

    print("=" * 70)
    print("MONDAY CLOSE RUNBOOK — March 30, 2026")
    print("Closing HD, ABT, BAX (expired, non-first-touch, invalid)")
    print("=" * 70)

    trades = TRADES
    if args.symbol:
        trades = [t for t in trades if t['symbol'] == args.symbol.upper()]
        if not trades:
            print(f"ERROR: {args.symbol} not in runbook")
            return

    success = 0
    for trade in trades:
        ok = close_trade(trade, dry_run=args.dry_run)
        if ok:
            success += 1

    print(f"\n{'='*50}")
    print(f"Closed {success}/{len(trades)} trades")
    if args.dry_run:
        print("(DRY RUN - no actual orders placed)")
    print()
    print("NEXT STEPS:")
    print("1. Verify positions closed in Alpaca dashboard")
    print("2. Check SYK (deadline April 2), TDG (deadline April 7) still running")
    print("3. Run april2_liberation_day_runbook.py on April 2 at 4:15 PM ET")
    print("4. COST auto-fires April 7 at 9:30 AM ET (no action needed)")


if __name__ == '__main__':
    main()
