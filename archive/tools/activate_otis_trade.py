#!/usr/bin/env python3
# DEPRECATED 2026-04-09: hypothesis is abandoned (non-first-touch or strategic cancel).
# This script will not activate a live trade. Retained for pattern reference only.
# New activators MUST call tools.pre_event_contamination.check_pre_event_contamination(
#   symbol, event_date=<event_iso_date>) before entry.
"""Activate OTIS catalyst_short when OTIS closes below 52w low ($77.80) and earnings miss.
Pre-registered hypothesis: dbedf16e
Trigger: OTIS closes below $77.80 AND abnormal return < -2% on earnings day (April 22)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import trader

db.init_db()

hypothesis_id = 'dbedf16e'
symbol = 'OTIS'
entry_52w_low = 77.80

# Set trigger fields
db.update_hypothesis_fields(hypothesis_id,
    trigger="next_market_open",
    trigger_position_size=5000,
    trigger_stop_loss_pct=8,
    trigger_take_profit_pct=None,
    trigger_notes=f"OTIS closed below 52w low ({entry_52w_low}) AND earnings miss (>2% abnormal decline). Signal: sp500_52w_low_catalyst_short. Deadline: 5 trading days from activation."
)

print(f"Trigger set for OTIS ({hypothesis_id}). Running activation...")
result = trader.activate_hypothesis(hypothesis_id)
print("Result:", result)
