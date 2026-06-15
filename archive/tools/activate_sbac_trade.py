"""
SBAC (SBA Communications, $17.6B) 52-Week Low Catalyst Short Activation Script
========================================================================
ONLY activate on April 27 2026 if:
  1. SBAC opens down >2% abnormal vs prior close (earnings miss confirmed)
  2. SBAC is at or below its 52-week low ($166.76)
  3. Portfolio has capacity (<5 active trades)

HYPOTHESIS: 2e2a0aa2 (sp500_52w_low_catalyst_short, 5d hold)
Expected: -4.44% abnormal over 5 days

Usage:
  python tools/activate_sbac_trade.py [--dry-run] [--yes]
"""

# DEPRECATED 2026-04-09: hypothesis is abandoned (non-first-touch or strategic cancel).
# This script will not activate a live trade. Retained for pattern reference only.
# New activators MUST call tools.pre_event_contamination.check_pre_event_contamination(
#   symbol, event_date=<event_iso_date>) before entry.


import sys
import argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db

HYPOTHESIS_ID = '2e2a0aa2'
SYMBOL = 'SBAC'
LOW_52W = 166.76
SIGNAL_DATE = '2026-03-24'


def get_current_price():
    try:
        import yfinance as yf
        t = yf.Ticker(SYMBOL)
        hist = t.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = t.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"yfinance error: {{e}}")
    try:
        return trader.get_current_price(SYMBOL)
    except:
        return None


def check_capacity():
    db.init_db()
    hypotheses = db.load_hypotheses()
    hyp_active = len([h for h in hypotheses if h.get('status') == 'active'])
    try:
        api = trader.get_api()
        alpaca_count = len(api.list_positions())
    except Exception:
        alpaca_count = 0
    return max(hyp_active, alpaca_count)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--price', type=float, default=None)
    parser.add_argument('--prior-close', type=float, default=None,
                        help='Prior close for abnormal return calc')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()

    db.init_db()

    print("=" * 60)
    print(f"{{SYMBOL}} CATALYST SHORT — EARNINGS DAY ACTIVATION")
    print("=" * 60)
    print()
    print(f"Hypothesis: {{HYPOTHESIS_ID}}")
    print(f"Signal: First 52w low touch {2026-03-24} (${{LOW_52W:.2f}})")
    print()

    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {{HYPOTHESIS_ID}} not found")
        return 1
    if h['status'] == 'active':
        print("Already ACTIVE.")
        return 0
    if h['status'] != 'pending':
        print(f"ERROR: Status is '{{h["status"]}}', expected 'pending'")
        return 1

    # Get entry price (should be the open price on earnings day)
    entry_price = args.price or get_current_price()
    if entry_price is None:
        print("ERROR: Could not get current price")
        return 1
    print(f"Entry price (earnings open): ${{entry_price:.2f}}")

    # Check signal validity
    if entry_price > LOW_52W * 1.01:
        print(f"ABORT: {{SYMBOL}} (${entry_price:.2f}) recovered above 52w low (${{LOW_52W:.2f}})")
        print("       Signal invalidated.")
        return 1

    # Check abnormal return vs prior close (earnings miss confirmation)
    if args.prior_close:
        raw_move = (entry_price - args.prior_close) / args.prior_close * 100
        print(f"Move from prior close: {{raw_move:.1f}}%")
        if raw_move > -2.0:
            print(f"ABORT: Move is only {{raw_move:.1f}}% (need <-2% to confirm earnings miss)")
            return 1
        print(f"Earnings miss confirmed: {{raw_move:.1f}}%")
    else:
        if not args.yes:
            print("WARNING: No --prior-close provided. Manually verify earnings miss >2% before continuing.")
            confirm = input("Confirm earnings miss >2% abnormal? (yes/no): ").strip().lower()
            if confirm != 'yes':
                print("Aborted.")
                return 0

    # Capacity check
    active_count = check_capacity()
    print(f"Active trades: {{active_count}}/5")
    if active_count >= 5:
        print("ABORT: Portfolio at capacity")
        return 1

    print(f"Position: $5,000 short {{SYMBOL}}")
    print(f"Stop loss: 8% (trigger at ${{entry_price * 1.08:.2f}})")
    print(f"Take profit: 10% (trigger at ${{entry_price * 0.90:.2f}})")
    print(f"Expected exit: 5 trading days from entry")
    print()

    if args.dry_run:
        print("[DRY RUN] Would activate hypothesis and place short")
        return 0

    if not args.yes:
        confirm = input("Place trade? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 0

    # Activate
    try:
        research.activate_hypothesis(HYPOTHESIS_ID, entry_price=entry_price, position_size=5000)
        print(f"Hypothesis activated at ${{entry_price:.2f}}")
    except Exception as e:
        print(f"ERROR: {{e}}")
        return 1

    # Place order
    try:
        result = trader.place_experiment(SYMBOL, direction='short', notional_amount=5000)
        print(f"Order result: {{result}}")
        if not result.get('success'):
            print(f"ERROR: {{result.get('error')}}")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {{e}}")
        return 1

    print(f"TRADE ACTIVE: {{SYMBOL}} SHORT at ${{entry_price:.2f}}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
