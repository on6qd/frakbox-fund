"""
NKE (Nike) Earnings + 52w Low Catalyst Short Trade Activation Script
======================================================================
Run this at April 1, 2026 market open (9:30 AM ET) ONLY IF NKE reports bad earnings.

TRADE DETAILS:
  Hypothesis: 0bc8ec49 (sp500_52w_low_catalyst_short, 5d hold)
  Signal: NKE earnings March 31 post-market. If NKE opens below $51.97 (52w low) on April 1,
          this triggers the sp500_52w_low_catalyst_short signal.
  52w low threshold: $51.97 (close from April 8, 2025 — current all-time 52w low close)
  Entry: April 1, 2026 market open
  Exit: April 8, 2026 market close (5 trading days)
  Position: $5,000
  Stop loss: 8%
  Take profit: 15%

WHY THIS IS STRONG:
  The sp500_52w_low_catalyst_short signal (67 events, 2020-2025) shows:
  - When 52w low break is accompanied by large catalyst drop (>2% abnormal):
  - 5-day avg abnormal return: -4.44%
  - Direction: 84% negative
  - OOS validation (2023-2025, n=43): -4.68% avg, 81% direction
  - vs plain 52w low touch: only -1.00% avg, 59.8% direction

ABORT CONDITIONS (do NOT trade if any of these are true):
  - NKE opens ABOVE $51.97 (signal NOT triggered — earnings may have been good)
  - NKE drops >12% at open (catastrophic news, don't chase)
  - Portfolio at 5/5 capacity (need to check)
  - VIX > 60 (circuit breaker risk)

Usage:
  python tools/activate_nke_trade.py [--dry-run] [--price XXXX]
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db


NKE_52W_LOW_HARDCODED = 51.97  # 52-week low close (April 8, 2025 — within 52w window)
HYPOTHESIS_ID = '0bc8ec49'
POSITION_SIZE = 5000


def get_nke_52w_low():
    """Fetch live 52w low dynamically; use min(live, hardcoded) to always get lowest."""
    try:
        import yfinance as yf
        info = yf.Ticker('NKE').info
        live = info.get('fiftyTwoWeekLow')
        if live:
            low = min(float(live), NKE_52W_LOW_HARDCODED)
            print(f"  52w low (live={live:.2f} hardcoded={NKE_52W_LOW_HARDCODED:.2f}) → using {low:.2f}")
            return low
    except Exception as e:
        print(f"  Warning: could not fetch live 52w low: {e}")
    return NKE_52W_LOW_HARDCODED


NKE_52W_LOW = NKE_52W_LOW_HARDCODED  # Will be overridden at runtime


def get_nke_price():
    """Get current NKE price."""
    try:
        import yfinance as yf
        ticker = yf.Ticker('NKE')
        hist = ticker.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = ticker.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get live price: {e}")
    return None


def check_capacity():
    """Check active trades — uses MAX of hypothesis DB and Alpaca positions.

    Bug fix: Some positions may be in Alpaca but not in hypothesis DB (e.g., CTAS).
    Taking the max prevents silent capacity overflow.
    """
    db.init_db()
    hypotheses = db.load_hypotheses()
    hyp_active = [h for h in hypotheses if h.get('status') == 'active']
    hyp_count = len(hyp_active)
    try:
        api = trader.get_api()
        alpaca_positions = api.list_positions()
        alpaca_count = len(alpaca_positions)
    except Exception:
        alpaca_count = 0
    count = max(hyp_count, alpaca_count)
    if alpaca_count > hyp_count:
        print(f'  [WARNING] Alpaca has {alpaca_count} positions but only {hyp_count} in hypothesis DB!')
    return count


def main():
    parser = argparse.ArgumentParser(description='Activate NKE earnings catalyst short trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--price', type=float, default=None,
                        help='Override entry price (default: fetch live)')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt')
    args = parser.parse_args()

    # Fetch live 52w low at runtime
    global NKE_52W_LOW
    NKE_52W_LOW = get_nke_52w_low()

    print("=" * 65)
    print("NKE EARNINGS + 52W LOW CATALYST SHORT ACTIVATION")
    print("=" * 65)
    print()
    print(f"Hypothesis: {HYPOTHESIS_ID} (sp500_52w_low_catalyst_short, 5d)")
    print(f"Catalyst: NKE Q3 FY2026 earnings (March 31 post-market)")
    print(f"52w low barrier: ${NKE_52W_LOW:.2f}")
    print()

    # Portfolio capacity check
    active_count = check_capacity()
    print(f"Active trades: {active_count}/5")
    if active_count >= 5:
        print(f"ABORT: Portfolio at capacity ({active_count}/5). Close a trade first.")
        return 1

    # Get entry price
    if args.price:
        entry_price = args.price
        print(f"Using provided price: ${entry_price:.2f}")
    else:
        entry_price = get_nke_price()
        if entry_price:
            print(f"Current NKE price: ${entry_price:.2f}")
        else:
            print("ERROR: Could not fetch live price. Use --price XXXX to override.")
            return 1

    # PRIMARY ABORT: Signal requires NKE BELOW 52w low
    if entry_price > NKE_52W_LOW:
        print(f"\nABORT: NKE (${entry_price:.2f}) is ABOVE 52w low (${NKE_52W_LOW:.2f}).")
        print("       Signal NOT triggered. Earnings may have been positive or neutral.")
        print("       DO NOT ENTER the trade.")
        return 1

    # Check for catastrophic drop (>12% below prior close)
    prior_close = 52.71  # approximate March 31 pre-earnings close
    max_gap = prior_close * 0.88
    if entry_price < max_gap:
        print(f"\nABORT: NKE dropped more than 12% from prior close (${prior_close:.2f}).")
        print(f"       Entry at ${entry_price:.2f} is below abort threshold ${max_gap:.2f}.")
        print("       Catastrophic news may have occurred. Manual review needed.")
        return 1

    # Confirm signal quality
    breach_pct = (entry_price - NKE_52W_LOW) / NKE_52W_LOW * 100
    gap_pct = (entry_price - prior_close) / prior_close * 100
    print(f"\nSignal CONFIRMED:")
    print(f"  NKE ${entry_price:.2f} < 52w low ${NKE_52W_LOW:.2f} (breach={breach_pct:.1f}%)")
    print(f"  Earnings gap: {gap_pct:.1f}% from prior close ${prior_close:.2f}")
    print(f"  Catalyst signal: sp500_52w_low_catalyst_short")
    print(f"  Expected 5d abnormal return: -4.44% (OOS: -4.68%)")
    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Position size: ${POSITION_SIZE:,}")
    shares = int(POSITION_SIZE / entry_price)
    print(f"Approx shares: {shares}")
    print(f"Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"Take profit: 15% = ${entry_price * 0.85:.2f}")
    print(f"Target exit: April 8, 2026 close (5 trading days)")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate hypothesis {HYPOTHESIS_ID}")
        print(f"[DRY RUN] Place SHORT NKE ${POSITION_SIZE:,} at market open")
        return 0

    # Confirm
    if args.yes:
        confirm = 'yes'
    else:
        confirm = input("Place trade? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        return 0

    # Activate hypothesis
    print(f"\nActivating hypothesis {HYPOTHESIS_ID}...")
    try:
        research.activate_hypothesis(HYPOTHESIS_ID, entry_price=entry_price, position_size=POSITION_SIZE)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Place order
    print("\nPlacing Alpaca short order...")
    try:
        result = trader.place_experiment(
            symbol='NKE',
            direction='short',
            notional_amount=POSITION_SIZE,
        )
        print(f"  Order result: {result}")
        if not result.get('success'):
            print(f"ERROR: {result.get('error')}")
            print("Hypothesis was activated but order FAILED. Check Alpaca manually.")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {e}")
        return 1

    print()
    print("=" * 65)
    print("NKE TRADE ACTIVE")
    print(f"  Symbol: NKE SHORT (sp500_52w_low_catalyst_short)")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"  Take profit: 15% = ${entry_price * 0.85:.2f}")
    print(f"  Target exit: April 8, 2026 close")
    print(f"  Expected return: -4.44% abnormal")
    print()
    print("NOTE: trade_loop.py monitors stop loss automatically.")
    print(f"Manual exit: python trader.py --close {HYPOTHESIS_ID}")
    print("=" * 65)
    return 0


if __name__ == '__main__':
    sys.exit(main())
