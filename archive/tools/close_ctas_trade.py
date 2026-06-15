"""
CTAS Short Trade Closing Script
================================
Run BEFORE CTAS earnings pre-market March 25, 2026.

Reason for early close: CTAS reports earnings pre-market March 25.
The momentum short thesis does not incorporate earnings outcomes.
Holding through a binary earnings event adds risk outside the hypothesis scope.
Current price (~$181.21) is slightly above entry (~$179.30) — small loss acceptable
vs. risk of large loss if CTAS beats and rallies.

WHAT THIS DOES:
  1. Gets current CTAS price from Alpaca
  2. Calls trader.close_position('CTAS') to submit market buy-to-cover
  3. Calculates actual return vs SPY return
  4. Calls research.complete_hypothesis() with post-mortem
  5. Records the result in SQLite

Hypothesis: cc88dd18 (sp500_52w_low_momentum_short)
Entry: SHORT CTAS at March 20 open (~$179.30)
Exit: March 24 close (early — pre-earnings)
Expected: -1.68% abnormal return (short profits from decline)

Usage:
  python tools/close_ctas_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import market_data
from tools.yfinance_utils import safe_download

HYPOTHESIS_ID = 'cc88dd18'
SYMBOL = 'CTAS'


def main():
    parser = argparse.ArgumentParser(description='Close CTAS short trade (pre-earnings)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without executing')
    args = parser.parse_args()

    print("=== CTAS Short Trade Closer (Pre-Earnings) ===")
    print(f"Hypothesis: {HYPOTHESIS_ID}")
    print(f"Symbol: {SYMBOL}")
    print("Reason: Closing early — CTAS earnings pre-market March 25 (binary risk)")
    print()

    # Load hypothesis
    hypotheses = research.load_hypotheses()
    h = next((hh for hh in hypotheses if hh['id'] == HYPOTHESIS_ID), None)
    if h is None:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found!")
        return
    print(f"Status: {h['status']}")
    if h['status'] != 'active':
        print(f"WARNING: Hypothesis is not 'active' — it is '{h['status']}'")
        if not args.dry_run:
            print("Cannot close a non-active hypothesis. Run after trade executes.")
            return

    # Get current position from Alpaca
    try:
        api = trader.get_api()
        position = api.get_position(SYMBOL)
        entry_price = float(position.avg_entry_price)
        qty = float(position.qty)

        # Get real current price from yfinance (Alpaca may be stale)
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        df = safe_download(SYMBOL, start=yesterday, end=today)
        if df is not None and not df.empty:
            current_price = float(df['Close'].iloc[-1])
            print(f"Current price (yfinance): ${current_price:.2f}")
        else:
            current_price = float(position.current_price)
            print(f"Current price (Alpaca): ${current_price:.2f}")

        print(f"Position: {qty} shares at avg ${entry_price:.2f}")
        # For SHORT: profit when price falls
        raw_return = (entry_price - current_price) / entry_price * 100
        print(f"Raw return (short): {raw_return:+.2f}%")

    except Exception as e:
        print(f"ERROR getting position: {e}")
        if not args.dry_run:
            return
        entry_price = 179.30
        current_price = 181.21
        raw_return = (entry_price - current_price) / entry_price * 100

    # Get SPY return over same period
    entry_date = h.get('activated_at', '').split('T')[0] if h.get('activated_at') else '2026-03-20'
    try:
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        spy_df = safe_download('SPY', start=entry_date, end=today)
        if spy_df is not None and not spy_df.empty:
            spy_entry = float(spy_df['Close'].iloc[0])
            spy_exit = float(spy_df['Close'].iloc[-1])
            spy_return = (spy_exit - spy_entry) / spy_entry * 100
            print(f"SPY return since entry ({entry_date}): {spy_return:+.2f}%")
        else:
            spy_return = 0.0
    except Exception as e:
        print(f"Could not get SPY return: {e}")
        spy_return = 0.0

    abnormal_return = raw_return - spy_return
    print(f"Abnormal return: {abnormal_return:+.2f}%")
    print()

    if args.dry_run:
        print("DRY RUN: Would close CTAS short and record to hypothesis.")
        return

    # Close the position
    print("Closing CTAS short position (market order)...")
    try:
        trader.close_position(SYMBOL)
        print("Position closed successfully.")
    except Exception as e:
        print(f"ERROR closing position: {e}")
        return

    # Complete the hypothesis
    direction_correct = raw_return > 0.5  # short profits when price falls >0.5%

    research.complete_hypothesis(
        hypothesis_id=HYPOTHESIS_ID,
        exit_price=current_price,
        actual_return_pct=raw_return,
        post_mortem=(
            f"CTAS short (52-week low first-touch momentum). "
            f"Entry: ${entry_price:.2f} (short), Exit: ${current_price:.2f}. "
            f"Closed EARLY (pre-earnings March 25 2026) to avoid binary earnings risk. "
            f"The momentum short thesis does not incorporate earnings outcomes. "
            f"Raw return: {raw_return:+.2f}%, SPY: {spy_return:+.2f}%, Abnormal: {abnormal_return:+.2f}%. "
            f"Direction {'CORRECT' if direction_correct else 'WRONG'} (expected stock to fall). "
            f"CTAS rallied slightly from 52w low level — momentum signal may not have had time to develop."
        ),
        spy_return_pct=spy_return,
        timing_accuracy='early_exit',
        mechanism_validated=direction_correct,
        surprise_factor=1 if not direction_correct else 0,
    )
    print(f"Hypothesis {HYPOTHESIS_ID} completed.")
    print(f"Return: {raw_return:+.2f}% raw, {abnormal_return:+.2f}% abnormal.")


if __name__ == '__main__':
    main()
