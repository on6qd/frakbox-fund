#!/usr/bin/env python3
"""Activate BSX catalyst_short when BSX closes below 52w low ($67.56) and earnings miss.
Pre-registered hypothesis: 359e9e42
Trigger: BSX closes below $67.56 AND abnormal return < -2% on earnings day (April 22)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import trader

db.init_db()

hypothesis_id = '359e9e42'
symbol = 'BSX'
entry_52w_low = 67.56

# Set trigger fields
db.update_hypothesis_fields(hypothesis_id,
    trigger="next_market_open",
    trigger_position_size=5000,
    trigger_stop_loss_pct=8,
    trigger_take_profit_pct=None,
    trigger_notes=f"BSX closed below 52w low ({entry_52w_low}) AND earnings miss (>2% abnormal decline). Signal: sp500_52w_low_catalyst_short. Deadline: 5 trading days from activation."
)

print(f"Trigger set for BSX ({hypothesis_id}). Running activation...")
result = trader.activate_hypothesis(hypothesis_id)
print("Result:", result)
