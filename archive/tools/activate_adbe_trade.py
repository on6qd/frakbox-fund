"""
ADBE (Adobe Inc) Systemic Short Trade Activation Script
=========================================================
ONLY activate if Liberation Day (April 2, 2026) creates SYSTEMIC CONDITIONS:
  - SPY down >0.5% on April 2
  - 5+ S&P 500 large-cap stocks at first-touch 52-week lows

First verify signal with: python tools/systemic_52w_low_scanner.py

TRADE DETAILS:
  Hypothesis: f93527a2 (sp500_52w_low_systemic_short, 5d hold)
  Signal: ADBE first crossed 52-week low on 2026-03-24 ($238.87), $98B cap, S&P 500
  52w low: $238.87 (must still be at/below this at entry)
  Entry: April 3 morning market open (day after Liberation Day systemic selloff)
  Exit: April 10 close (5 trading days from April 3)
  Position: $5,000 short
  Stop loss: 8%

ABORT CONDITIONS:
  - SPY NOT down >0.5% on April 2 (signal requires systemic day)
  - Fewer than 5 S&P 500 stocks at first-touch 52w lows
  - ADBE recovered above $238.87 (signal invalidated)
  - Portfolio at 5/5 capacity

Usage:
  python tools/activate_adbe_trade.py [--dry-run] [--yes]
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

ADBE_52W_LOW = 238.87
HYPOTHESIS_ID = 'f93527a2'


def get_adbe_current_price():
    try:
        import yfinance as yf
        t = yf.Ticker('ADBE')
        hist = t.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = t.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"yfinance error: {e}")
    try:
        return trader.get_current_price('ADBE')
    except Exception as e:
        print(f"Alpaca price error: {e}")
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
    count = max(hyp_active, alpaca_count)
    if alpaca_count > hyp_active:
        print(f"  [WARNING] Alpaca has {alpaca_count} positions but only {hyp_active} in hypothesis DB!")
    return count


def main():
    parser = argparse.ArgumentParser(description='Activate ADBE systemic short trade')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--price', type=float, default=None)
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()

    db.init_db()

    print("=" * 60)
    print("ADBE SYSTEMIC SHORT — ACTIVATION CHECK")
    print("=" * 60)
    print()
    print("PREREQUISITE: Run systemic_52w_low_scanner.py FIRST.")
    print("This trade is ONLY valid if Liberation Day creates systemic conditions.")
    print("  - SPY down >0.5% on April 2, 2026")
    print("  - 5+ S&P 500 stocks at first-touch 52w lows")
    print()

    if not args.yes:
        confirm = input("Have you verified systemic conditions with scanner? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted. Run systemic_52w_low_scanner.py first.")
            return 1

    # Check hypothesis status
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found")
        return 1
    if h['status'] == 'active':
        print("Hypothesis already ACTIVE — trade was already placed.")
        return 0
    if h['status'] != 'pending':
        print(f"ERROR: Hypothesis status is '{h['status']}' — expected 'pending'")
        return 1

    # Get price
    entry_price = args.price or get_adbe_current_price()
    if entry_price is None:
        print("ERROR: Could not get ADBE price.")
        return 1

    print(f"ADBE current price: ${entry_price:.2f}")
    print(f"52-week low (signal level): ${ADBE_52W_LOW:.2f}")

    # Signal validity check
    if entry_price > ADBE_52W_LOW * 1.005:
        print(f"\nABORT: ADBE (${entry_price:.2f}) recovered above 52w low (${ADBE_52W_LOW:.2f}).")
        print("       Signal invalidated. Do not trade.")
        return 1

    # Catastrophic news check
    if entry_price < ADBE_52W_LOW * 0.88:
        print(f"\nABORT: ADBE dropped >12% from signal level. Catastrophic news. Manual review needed.")
        return 1

    # Portfolio capacity
    active_count = check_capacity()
    print(f"\nActive trades: {active_count}/5")
    if active_count >= 5:
        print(f"ABORT: Portfolio at capacity ({active_count}/5).")
        return 1

    breach_pct = (entry_price - ADBE_52W_LOW) / ADBE_52W_LOW * 100
    print(f"Signal valid: ADBE {breach_pct:.1f}% vs 52w low")
    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Position size: $5,000")
    shares = int(5000 / entry_price)
    print(f"Approx shares: {shares}")
    print(f"Stop loss: 8% (trigger at ${entry_price * 1.08:.2f})")
    print(f"Take profit: 10% (trigger at ${entry_price * 0.90:.2f})")
    print(f"Expected exit: 5 trading days from entry")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate hypothesis {HYPOTHESIS_ID} and place market short order")
        return 0

    if not args.yes:
        confirm = input("Place trade? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 0

    # Activate hypothesis
    print(f"\nActivating hypothesis {HYPOTHESIS_ID}...")
    try:
        research.activate_hypothesis(HYPOTHESIS_ID, entry_price=entry_price, position_size=5000)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Place order
    print("\nPlacing Alpaca short order...")
    try:
        result = trader.place_experiment(
            symbol='ADBE',
            direction='short',
            notional_amount=5000,
        )
        print(f"  Order result: {result}")
        if not result.get('success'):
            print(f"ERROR: {result.get('error')}")
            print("Hypothesis activated but order FAILED. Check Alpaca manually.")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {e}")
        return 1

    print()
    print("=" * 60)
    print("TRADE ACTIVE: ADBE SHORT")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"  Take profit: 10% = ${entry_price * 0.90:.2f}")
    print(f"  Hypothesis: {HYPOTHESIS_ID}")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
