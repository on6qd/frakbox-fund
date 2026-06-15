"""
COST (Costco) Tariff Defensive Retail Long Trade Activation
============================================================
Run this on April 7, 2026 at market open (first open after Good Friday April 3).

TRADE DETAILS:
  Hypothesis: 8c2f8cbb (tariff_defensive_retail_long, 5d hold)
  Signal: COST outperforms SPY after major tariff escalations
  - n=21 (COST+WMT+XLP x 7 events), 86% direction, MT PASSES
  - OOS: 6/6 positive in 2025 events (100%)
  - Expected: +3.57% abnormal over 5 days
  Entry: April 7 open (trigger already set in trade_loop)
  Exit: 5 trading days after entry (deadline April 13-14)
  Position: $5,000
  Stop loss: 8%
  Take profit: 10%

NOTE: This trade fires AUTOMATICALLY via trade_loop at April 7 09:30.
Run this script ONLY if trade_loop fails to activate it automatically.

CONFIRMATION CHECKS:
  1. Portfolio capacity < 5/5
  2. No major company-specific news on COST (earnings, recall, management change)
  3. April 2 tariff announcement was indeed a major escalation (>10% broad tariffs)
  4. COST still below any abnormal post-announcement high (check it hasn't +10% already)

Usage:
  python tools/activate_cost_tariff_trade.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db

HYPOTHESIS_ID = '8c2f8cbb'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 8
TAKE_PROFIT_PCT = 10


def main():
    parser = argparse.ArgumentParser(description='Activate COST tariff defensive long trade')
    parser.add_argument('--dry-run', action='store_true', help='Check conditions but do not trade')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    db.init_db()

    print("=" * 60)
    print("COST TARIFF DEFENSIVE RETAIL LONG - ACTIVATION CHECK")
    print("=" * 60)
    print()

    # Check hypothesis status
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found")
        sys.exit(1)

    print(f"Hypothesis: {HYPOTHESIS_ID}")
    print(f"Status: {h.get('status')}")
    print(f"Expected: COST long, +3.57% over 5 days")
    print()

    if h.get('status') not in ['pending']:
        print(f"WARNING: Expected status 'pending', got '{h.get('status')}'")
        if h.get('status') == 'active':
            print("Trade is already active — no action needed")
            sys.exit(0)
        elif h.get('status') == 'completed':
            print("Trade already completed — no action needed")
            sys.exit(0)

    # Check portfolio capacity
    api = trader.get_api()
    positions = api.list_positions()
    active_hyps = [hx for hx in db.load_hypotheses() if hx.get('status') == 'active']
    print(f"Portfolio: {len(active_hyps)}/5 active")
    if len(active_hyps) >= 5:
        print("ERROR: Portfolio at max capacity (5/5). Cannot enter new trade.")
        sys.exit(1)

    # Check current COST price
    try:
        import yfinance as yf
        ticker = yf.Ticker('COST')
        cost_price = ticker.history(period='1d')['Close'].iloc[-1]
        print(f"Current COST price: ${cost_price:.2f}")
    except Exception as e:
        print(f"Could not fetch COST price: {e}")
        cost_price = None

    print()
    print("CONFIRMATION CHECKS (verify manually):")
    print("  [ ] April 2 tariff announcement was major (>10% broad tariff)?")
    print("  [ ] No COST earnings within 5 days?")
    print("  [ ] No major COST-specific negative news?")
    print()

    if args.dry_run:
        print("DRY RUN: Would place $5,000 COST long order at market open")
        print(f"  Entry price: ~${cost_price:.2f}" if cost_price else "  Entry price: market price")
        print("  Stop loss: 8%")
        print("  Take profit: 10%")
        print("  Holding period: 5 trading days")
        return

    if not args.yes:
        confirm = input("Place $5,000 COST long trade? [yes/no]: ")
        if confirm.lower() != 'yes':
            print("Trade cancelled.")
            sys.exit(0)

    # Activate the trade
    print("Activating COST tariff defensive long...")
    result = trader.place_experiment(
        hypothesis_id=HYPOTHESIS_ID,
        symbol='COST',
        side='buy',
        position_size=POSITION_SIZE,
        stop_loss_pct=STOP_LOSS_PCT,
        take_profit_pct=TAKE_PROFIT_PCT,
        holding_period_days=5,
    )
    print(f"Trade placed: {result}")
    print()
    print("COST tariff defensive long activated successfully!")
    print(f"  Expected exit: 5 trading days from today")
    print("  Signal: tariff_defensive_retail_long (hypothesis 8c2f8cbb)")


if __name__ == '__main__':
    main()
