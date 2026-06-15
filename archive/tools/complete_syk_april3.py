#!/usr/bin/env python3
"""
Complete SYK short hypothesis after BUY close order fills.
NOTE: April 3, 2026 is Good Friday (market CLOSED). Order fills at April 6 (Monday) open.
SYK hypothesis ID: 5b09b097
BUY order: db5e3b80-739e-4d25-ad33-207254db303e
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trader, research, db

db.init_db()
api = trader.get_api()

order = api.get_order('db5e3b80-739e-4d25-ad33-207254db303e')
print(f'Order status: {order.status}')
print(f'Filled avg price: {order.filled_avg_price}')

if order.status == 'filled':
    exit_price = float(order.filled_avg_price)
    entry_price = 326.229333
    pnl_pct = (entry_price - exit_price) / entry_price * 100
    print(f'Entry: {entry_price:.2f}, Exit: {exit_price:.2f}, PnL: {pnl_pct:.2f}%')
    
    result = research.complete_hypothesis(
        hypothesis_id='5b09b097',
        exit_price=exit_price,
        mechanism_validated=False,
        post_mortem=f'SYK 52w low short: Entry=${entry_price:.2f}, Exit=${exit_price:.2f} ({pnl_pct:.2f}%). SYK rebounded after 52w low touch. Confirms sp500_52w_low_momentum_short dead end (OOS). Liberation Day 2026 mild, no market distress.',
    )
    print('Hypothesis completed!')
else:
    print('Order not yet filled - run again after April 6 market open (April 3 = Good Friday, market closed)')
