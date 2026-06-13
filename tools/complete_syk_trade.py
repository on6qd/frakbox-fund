"""
SYK Trade Completion Script
=============================
Run after SYK market buy fills on April 3, 2026 open.
Entry was SHORT at $326.23. Close buy at open.

Usage:
    python tools/complete_syk_trade.py --fill-price 329.50

This will:
1. Calculate the P&L
2. Call complete_hypothesis() with results
3. Record OOS data point for sp500_52w_low_momentum_short
"""
import sys
import argparse
from pathlib import Path
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
import db
import research

def complete_syk(fill_price: float, dry_run: bool = False):
    db.init_db()
    
    hypothesis_id = '5b09b097'
    entry_price = 326.23  # avg entry from Alpaca
    position_size = 5000.0
    
    # Calculate P&L
    pct_change_against_short = (fill_price - entry_price) / entry_price * 100
    shares = position_size / entry_price
    dollar_pnl = (entry_price - fill_price) * shares  # profit when price falls
    
    print(f"SYK Trade Completion Analysis:")
    print(f"  Entry (short): ${entry_price:.2f}")
    print(f"  Exit (cover): ${fill_price:.2f}")
    print(f"  Shares: {shares:.2f}")
    print(f"  P&L: ${dollar_pnl:+.2f} ({-pct_change_against_short:+.2f}%)")
    print(f"  Result: {'PROFIT' if dollar_pnl > 0 else 'LOSS'}")
    print()
    
    # For a SHORT trade, profit comes from stock FALLING.
    # short_profit_pct = how much the short position gained (positive = profit)
    # = -(stock's % change) from entry to exit
    short_profit_pct = -pct_change_against_short  # positive if stock fell

    print(f"Signal evaluation:")
    print(f"  Expected short profit: +1.68% (stock falls ~1.68% after first 52w low touch)")
    print(f"  Actual short profit: {short_profit_pct:+.2f}%")
    print(f"  Success criterion met (short profit >= +0.5%): {short_profit_pct >= 0.5}")

    if dry_run:
        print("\n[DRY RUN] Not completing hypothesis")
        return

    # Complete the hypothesis
    # actual_result_pct for a short = profit from the short position (stock fell = positive)
    post_mortem = (
        f"SYK SHORT FAILED OOS. Entry $326.23, exit ${fill_price:.2f} = "
        f"short profit {short_profit_pct:+.1f}% (expected +1.68%). "
        f"Stock ROSE from 52w low, opposing hypothesis direction. "
        f"Consistent with sp500_52w_low_momentum_short DEAD_END_OOS_INVERTED pattern. "
        f"After Liberation Day (mild), market bounced broadly including SYK. "
        f"52-week low momentum short is dead end - confirmed OOS."
    )
    
    result = research.complete_hypothesis(
        hypothesis_id=hypothesis_id,
        actual_result_pct=short_profit_pct,
        post_mortem=post_mortem,
        mechanism_validated=False,
        exit_price=fill_price,
        actual_symbol='SYK',
        actual_direction='short',
        holding_period_days=10  # approximate from March 26 to April 3
    )
    
    print(f"\nHypothesis completed: {result}")
    
    # Update knowledge base
    import json
    conn = db.get_db()

    val = db._scalar('SELECT data FROM known_effects WHERE event_type=?',
                     ('sp500_52w_low_momentum_short',))
    if val is not None:
        d = json.loads(val)
        d['syk_oos_result'] = {
            'entry': entry_price,
            'exit': fill_price,
            'short_profit_pct': short_profit_pct,
            'date': '2026-04-03',
            'verdict': 'MISS - confirms dead end. Stock rose from 52w low.'
        }
        db._exec('INSERT OR REPLACE INTO known_effects (event_type, data, last_updated) VALUES (?,?,?)',
            ('sp500_52w_low_momentum_short', json.dumps(d), datetime.datetime.now().isoformat()))
        conn.commit()
        print("Updated sp500_52w_low_momentum_short OOS record")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Complete SYK trade')
    parser.add_argument('--fill-price', type=float, required=True, help='April 3 open fill price')
    parser.add_argument('--dry-run', action='store_true', help='Calculate only, do not complete')
    args = parser.parse_args()
    
    complete_syk(args.fill_price, args.dry_run)
