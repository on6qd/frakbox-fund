"""
Backfill PnL for completed trades from Alpaca order history.
Run once to populate result fields for early trades.
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import trader

def backfill():
    db.init_db()

    # Get all closed orders
    orders = trader.list_recent_orders(status='closed', limit=100)

    # Build a map of fills: symbol -> list of (date, side, qty, avg_price)
    fills = {}
    for o in orders:
        if o.status != 'filled' or not o.filled_avg_price:
            continue
        sym = o.symbol
        if sym not in fills:
            fills[sym] = []
        fills[sym].append({
            'date': o.submitted_at.strftime('%Y-%m-%d'),
            'side': o.side,
            'qty': float(o.qty) if o.qty else float(o.filled_qty),
            'avg_price': float(o.filled_avg_price),
        })

    # Load hypotheses
    hyps = db.load_hypotheses()
    updated = 0

    for h in hyps:
        if h.get('status') not in ('completed', 'abandoned', 'invalidated'):
            continue

        trade = h.get('trade') or {}
        entry_price = trade.get('entry_price')
        if not entry_price or entry_price == 0.01:
            continue  # Skip research-only hypotheses

        # Already has result with exit_price?
        result = h.get('result') or {}
        if result.get('exit_price'):
            continue

        sym = h.get('expected_symbol')
        direction = h.get('expected_direction', 'long')

        if sym not in fills:
            print(f"  {h['id'][:8]} {sym:6s} — No fills found in Alpaca")
            continue

        sym_fills = sorted(fills[sym], key=lambda x: x['date'])

        # For long: entry=buy, exit=sell
        # For short: entry=sell, exit=buy
        entry_side = 'buy' if direction == 'long' else 'sell'
        exit_side = 'sell' if direction == 'long' else 'buy'

        entry_fill = None
        exit_fill = None
        for f in sym_fills:
            if f['side'] == entry_side and abs(f['avg_price'] - entry_price) / entry_price < 0.02:
                entry_fill = f
            elif f['side'] == exit_side and entry_fill is not None:
                exit_fill = f
                break

        if not exit_fill:
            print(f"  {h['id'][:8]} {sym:6s} — No matching exit fill found")
            continue

        # Compute PnL
        if direction == 'long':
            raw_ret = (exit_fill['avg_price'] - entry_fill['avg_price']) / entry_fill['avg_price'] * 100
            pnl = (exit_fill['avg_price'] - entry_fill['avg_price']) * entry_fill['qty']
        else:
            raw_ret = (entry_fill['avg_price'] - exit_fill['avg_price']) / entry_fill['avg_price'] * 100
            pnl = (entry_fill['avg_price'] - exit_fill['avg_price']) * entry_fill['qty']

        # Record result
        h['result'] = {
            'exit_price': exit_fill['avg_price'],
            'exit_date': exit_fill['date'],
            'entry_price_actual': entry_fill['avg_price'],
            'raw_return_pct': round(raw_ret, 2),
            'pnl_dollars': round(pnl, 2),
            'direction_correct': raw_ret > 0,
            'backfilled': True,
        }

        # Also update trade dict
        h['trade']['exit_price'] = exit_fill['avg_price']
        h['trade']['pnl_dollars'] = round(pnl, 2)

        db.save_hypothesis(h)
        updated += 1
        print(f"  {h['id'][:8]} {sym:6s} {direction:5s} entry=${entry_fill['avg_price']:.2f} -> exit=${exit_fill['avg_price']:.2f} PnL=${pnl:+.2f} ({raw_ret:+.2f}%)")

    print(f"\nBackfilled {updated} hypotheses")


if __name__ == '__main__':
    backfill()
