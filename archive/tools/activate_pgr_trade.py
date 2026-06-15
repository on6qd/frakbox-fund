"""
PGR (Progressive Insurance) Earnings + 52w Low Catalyst Short Trade Activation Script
=======================================================================================
Run this at April 15, 2026 market open (9:30 AM ET) ONLY IF PGR misses earnings.

TRADE DETAILS:
  Hypothesis: 62a654cc (sp500_52w_low_catalyst_short, 5d hold)
  Signal: PGR Q1 2026 earnings report April 15, 2026.
          If PGR opens below $197.92 (52w low) on April 15 with >2% abnormal decline,
          this triggers the sp500_52w_low_catalyst_short signal.
  52w low threshold: $197.92 (52-week low as of March 25, 2026)
  Entry: April 15, 2026 market open (earnings pre-market or after-hours prior day)
  Exit: April 22, 2026 market close (5 trading days)
  Position: $5,000
  Stop loss: 8%
  Take profit: 15%

CONTEXT:
  - PGR current price ~$206.21 (March 25, 2026), 52w low = $197.92
  - Q1 2026 earnings report: April 15, 2026
  - Risk: Rising combined ratios from catastrophic weather events
  - Risk: Loss ratio deterioration in personal auto lines
  - Stock is 4.2% above 52w low — earnings miss could break the barrier

WHY THIS IS STRONG:
  The sp500_52w_low_catalyst_short signal (67 events, 2020-2025) shows:
  - When 52w low break is accompanied by large catalyst drop (>2% abnormal):
  - 5-day avg abnormal return: -4.44%
  - Direction: 84% negative
  - OOS validation (2023-2025, n=43): -4.68% avg, 81% direction
  - vs plain 52w low touch: only -1.00% avg, 59.8% direction

ABORT CONDITIONS (do NOT trade if any of these are true):
  - PGR opens ABOVE $197.92 (signal NOT triggered — earnings may have been OK)
  - Abnormal return at open is LESS than -2% vs SPY (weak catalyst, not clean signal)
  - PGR drops >15% at open (catastrophic news, don't chase)
  - Portfolio at 5/5 capacity (need to check)
  - VIX > 60 (circuit breaker risk)

Usage:
  python tools/activate_pgr_trade.py [--dry-run] [--price XXXX]
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


PGR_52W_LOW_HARDCODED = 197.92  # 52-week low as of March 25, 2026
PGR_PRIOR_CLOSE = 206.21        # Prior close (March 25, 2026); update before running


def get_pgr_52w_low():
    """Fetch live 52w low dynamically; fall back to hardcoded if unavailable."""
    try:
        import yfinance as yf
        info = yf.Ticker('PGR').info
        live = info.get('fiftyTwoWeekLow')
        if live:
            low = min(float(live), PGR_52W_LOW_HARDCODED)
            print(f"  52w low (live={live:.2f} hardcoded={PGR_52W_LOW_HARDCODED:.2f}) → using {low:.2f}")
            return low
    except Exception as e:
        print(f"  Warning: could not fetch live 52w low: {e}")
    return PGR_52W_LOW_HARDCODED


PGR_52W_LOW = PGR_52W_LOW_HARDCODED  # Will be overridden at runtime by get_pgr_52w_low()
HYPOTHESIS_ID = '62a654cc'
POSITION_SIZE = 5000
MIN_CATALYST_DROP_PCT = 2.0    # Minimum abnormal gap-down % (absolute, not sign-adjusted)
MAX_CATASTROPHIC_DROP_PCT = 15.0  # Abort if gap > 15%


def get_pgr_price():
    """Get current PGR price (use at open for real signal check)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker('PGR')
        hist = ticker.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = ticker.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get live price: {e}")
    return None


def get_spy_change_pct():
    """Get today's SPY change % vs prior close (for abnormal return calc)."""
    try:
        import yfinance as yf
        spy = yf.Ticker('SPY')
        hist = spy.history(period='2d')
        if len(hist) >= 2:
            prior = float(hist['Close'].iloc[-2])
            current = float(hist['Close'].iloc[-1])
            return (current - prior) / prior * 100
    except Exception as e:
        print(f"Warning: could not get SPY change: {e}")
    return 0.0


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
    parser = argparse.ArgumentParser(description='Activate PGR earnings catalyst short trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--price', type=float, default=None,
                        help='Override entry price (default: fetch live)')
    parser.add_argument('--prior-close', type=float, default=PGR_PRIOR_CLOSE,
                        help=f'Prior close price (default: {PGR_PRIOR_CLOSE})')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt')
    args = parser.parse_args()

    # Fetch live 52w low at runtime
    global PGR_52W_LOW
    PGR_52W_LOW = get_pgr_52w_low()

    print("=" * 65)
    print("PGR EARNINGS + 52W LOW CATALYST SHORT ACTIVATION")
    print("=" * 65)
    print()
    print(f"Hypothesis: {HYPOTHESIS_ID} (sp500_52w_low_catalyst_short, 5d)")
    print(f"Catalyst:   PGR Q1 2026 earnings (April 15 pre-market or prior close)")
    print(f"52w low barrier: ${PGR_52W_LOW:.2f}")
    print(f"Prior close: ${args.prior_close:.2f}")
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
        entry_price = get_pgr_price()
        if entry_price:
            print(f"Current PGR price: ${entry_price:.2f}")
        else:
            print("ERROR: Could not fetch live price. Use --price XXXX to override.")
            return 1

    # PRIMARY ABORT: Signal requires PGR BELOW 52w low
    if entry_price >= PGR_52W_LOW:
        print(f"\nABORT: PGR (${entry_price:.2f}) is AT OR ABOVE 52w low (${PGR_52W_LOW:.2f}).")
        print("       Signal NOT triggered. Earnings may have been OK or market absorbed it.")
        print("       DO NOT ENTER the trade.")
        return 1

    # Check for catastrophic drop (>15% below prior close — anomalous)
    max_gap_price = args.prior_close * (1 - MAX_CATASTROPHIC_DROP_PCT / 100)
    if entry_price < max_gap_price:
        print(f"\nABORT: PGR dropped more than {MAX_CATASTROPHIC_DROP_PCT}% from prior close.")
        print(f"       Entry ${entry_price:.2f} is below abort threshold ${max_gap_price:.2f}.")
        print("       Catastrophic news may have occurred. Manual review needed.")
        return 1

    # Calculate returns
    gap_pct = (entry_price - args.prior_close) / args.prior_close * 100
    spy_pct = get_spy_change_pct()
    abnormal_pct = gap_pct - spy_pct
    breach_pct = (entry_price - PGR_52W_LOW) / PGR_52W_LOW * 100

    print(f"\nSignal analysis:")
    print(f"  PGR at open:    ${entry_price:.2f}")
    print(f"  52w low:        ${PGR_52W_LOW:.2f}  (breach: {breach_pct:.1f}%)")
    print(f"  Gap from close: {gap_pct:.2f}%")
    print(f"  SPY change:     {spy_pct:.2f}%")
    print(f"  Abnormal:       {abnormal_pct:.2f}%  (need < -{MIN_CATALYST_DROP_PCT:.1f}%)")

    # Check minimum catalyst decline
    if abnormal_pct > -MIN_CATALYST_DROP_PCT:
        print(f"\nWARNING: Abnormal return ({abnormal_pct:.2f}%) is above -{MIN_CATALYST_DROP_PCT:.1f}% threshold.")
        print("         Signal quality is below validation standard. Consider aborting.")
        if not args.yes:
            go = input("Proceed anyway? (yes/no): ").strip().lower()
            if go != 'yes':
                print("Aborted.")
                return 0
    else:
        print(f"\nSignal CONFIRMED:")
        print(f"  52w low broken, abnormal decline = {abnormal_pct:.2f}%")
        print(f"  Catalyst: sp500_52w_low_catalyst_short")
        print(f"  Expected 5d abnormal return: -4.44% (OOS: -4.68%)")

    print()
    print(f"Entry price: ${entry_price:.2f}")
    print(f"Position size: ${POSITION_SIZE:,}")
    shares = int(POSITION_SIZE / entry_price)
    print(f"Approx shares: {shares}")
    print(f"Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"Take profit: 15% = ${entry_price * 0.85:.2f}")
    print(f"Target exit: April 22, 2026 close (5 trading days)")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate hypothesis {HYPOTHESIS_ID}")
        print(f"[DRY RUN] Place SHORT PGR ${POSITION_SIZE:,} at market open")
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
            symbol='PGR',
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
    print("PGR TRADE ACTIVE")
    print(f"  Symbol: PGR SHORT (sp500_52w_low_catalyst_short)")
    print(f"  Entry: ${entry_price:.2f}")
    print(f"  Stop loss: 8% = ${entry_price * 1.08:.2f}")
    print(f"  Take profit: 15% = ${entry_price * 0.85:.2f}")
    print(f"  Target exit: April 22, 2026 close")
    print(f"  Expected return: -4.44% abnormal")
    print()
    print("NOTE: trade_loop.py monitors stop loss automatically.")
    print(f"Manual exit: python trader.py --close {HYPOTHESIS_ID}")
    print("=" * 65)
    return 0


if __name__ == '__main__':
    sys.exit(main())
