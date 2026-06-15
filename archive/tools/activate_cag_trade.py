"""
CAG (ConAgra Brands) Earnings + 52w Low Catalyst Short Trade Activation Script
===============================================================================
Run this at April 1, 2026 market open (9:30 AM ET) ONLY IF CAG misses earnings.

TRADE DETAILS:
  Hypothesis: f48d6a66 (sp500_52w_low_catalyst_short, 5d hold)
  Signal: CAG Q3 FY2026 earnings BEFORE MARKET OPEN April 1, 2026.
          If CAG opens below $15.16 (52w low) on April 1 with >2% abnormal decline,
          this triggers the sp500_52w_low_catalyst_short signal.
  52w low threshold: $15.16 (all-time 52w low as of March 20, 2026)
  Entry: April 1, 2026 market open (same day as pre-market earnings)
  Exit: April 8, 2026 market close (5 trading days)
  Position: $5,000
  Stop loss: 8%
  Take profit: 15%

EARNINGS CONSENSUS (Q3 FY2026, research 2026-03-24):
  - Consensus EPS: $0.40 (non-GAAP adjusted) | Beat threshold (+10%): $0.44
  - Historical beat/miss (last 4Q): 50% beat rate (2/4)
    * Q3 FY2025 (SAME QUARTER LAST YEAR): MISSED by -3.8%  ← key signal
    * Q4 FY2025: MISSED by -6.7%
    * Q1 FY2026: Beat +18.2%
    * Q2 FY2026: Beat +2.3% (but revenue missed $2.98B vs $3.0B estimate)
  - Analyst consensus: REDUCE (1 buy, 10 hold, 3 SELL). Goldman Sachs: SELL, PT=$16.
  - FY2026 EPS guidance: $1.70-$1.85 (already cut from prior range)
  - PEAD long: skip (analyst sentiment bearish, 50% beat rate, revenue declining)

CONTEXT:
  - CAG fell from ~$18 post-Q2 (Dec 2025) to $15.46 now — additional 14% decline
  - Q3 FY2026 (Dec-Feb quarter) under pressure from tariffs + consumer slowdown
  - Consumer staples sector broadly weak in tariff environment
  - Revenue declining organically -3.0% YoY; Ardent Mills JV guidance cut
  - CAG has high exposure to commodity/tariff costs

WHY THIS IS STRONG:
  The sp500_52w_low_catalyst_short signal (67 events, 2020-2025) shows:
  - 5-day avg abnormal return: -4.44%, direction: 84%
  - OOS validation (2023-2025, n=43): -4.68% avg, 81% direction

ABORT CONDITIONS:
  - CAG opens ABOVE $15.16 (signal NOT triggered)
  - Abnormal return < 2% vs SPY (weak catalyst)
  - CAG drops >15% at open (catastrophic — manual review)
  - Portfolio at 5/5 capacity
  - VIX > 60
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db


CAG_52W_LOW_HARDCODED = 15.07  # 52-week low as of March 24, 2026 (updated from 15.16)
CAG_PRIOR_CLOSE = 15.46  # Update to actual prior close before running
HYPOTHESIS_ID = 'f48d6a66'
POSITION_SIZE = 5000
MIN_CATALYST_DROP_PCT = 2.0
MAX_CATASTROPHIC_DROP_PCT = 15.0


def get_cag_52w_low():
    """Fetch live 52w low dynamically; use min(live, hardcoded) to always get lowest."""
    try:
        import yfinance as yf
        info = yf.Ticker('CAG').info
        live = info.get('fiftyTwoWeekLow')
        if live:
            low = min(float(live), CAG_52W_LOW_HARDCODED)
            print(f"  52w low (live={live:.2f} hardcoded={CAG_52W_LOW_HARDCODED:.2f}) → using {low:.2f}")
            return low
    except Exception as e:
        print(f"  Warning: could not fetch live 52w low: {e}")
    return CAG_52W_LOW_HARDCODED


CAG_52W_LOW = CAG_52W_LOW_HARDCODED  # Will be overridden at runtime


def get_cag_price():
    try:
        import yfinance as yf
        ticker = yf.Ticker('CAG')
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
    parser = argparse.ArgumentParser(description='Activate CAG earnings catalyst short trade')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--price', type=float, default=None)
    parser.add_argument('--prior-close', type=float, default=CAG_PRIOR_CLOSE)
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()

    # Fetch live 52w low at runtime
    global CAG_52W_LOW
    CAG_52W_LOW = get_cag_52w_low()

    print("=" * 65)
    print("CAG EARNINGS + 52W LOW CATALYST SHORT ACTIVATION")
    print("=" * 65)
    print()
    print(f"Hypothesis: {HYPOTHESIS_ID} (sp500_52w_low_catalyst_short, 5d)")
    print(f"Catalyst:   CAG Q3 FY2026 earnings (April 1 pre-market)")
    print(f"52w low barrier: ${CAG_52W_LOW:.2f}")
    print(f"Prior close: ${args.prior_close:.2f}")
    print()

    active_count = check_capacity()
    print(f"Active trades: {active_count}/5")
    if active_count >= 5:
        print(f"ABORT: Portfolio at capacity ({active_count}/5). Close a trade first.")
        return 1

    if args.price:
        entry_price = args.price
        print(f"Using provided price: ${entry_price:.2f}")
    else:
        entry_price = get_cag_price()
        if entry_price:
            print(f"Current CAG price: ${entry_price:.2f}")
        else:
            print("ERROR: Could not fetch live price. Use --price XXXX to override.")
            return 1

    if entry_price >= CAG_52W_LOW:
        print(f"\nABORT: CAG (${entry_price:.2f}) is AT OR ABOVE 52w low (${CAG_52W_LOW:.2f}).")
        print("       Signal NOT triggered. DO NOT ENTER the trade.")
        return 1

    max_gap_price = args.prior_close * (1 - MAX_CATASTROPHIC_DROP_PCT / 100)
    if entry_price < max_gap_price:
        print(f"\nABORT: CAG dropped >{MAX_CATASTROPHIC_DROP_PCT}% from prior close. Manual review needed.")
        return 1

    gap_pct = (entry_price - args.prior_close) / args.prior_close * 100
    spy_pct = get_spy_change_pct()
    abnormal_pct = gap_pct - spy_pct
    breach_pct = (entry_price - CAG_52W_LOW) / CAG_52W_LOW * 100

    print(f"\nSignal analysis:")
    print(f"  CAG at open:    ${entry_price:.2f}")
    print(f"  52w low:        ${CAG_52W_LOW:.2f}  (breach: {breach_pct:.1f}%)")
    print(f"  Gap from close: {gap_pct:.2f}%")
    print(f"  SPY change:     {spy_pct:.2f}%")
    print(f"  Abnormal:       {abnormal_pct:.2f}%  (need < -{MIN_CATALYST_DROP_PCT:.1f}%)")

    if abnormal_pct > -MIN_CATALYST_DROP_PCT:
        print(f"\nWARNING: Abnormal return below threshold. Consider aborting.")
        if not args.yes:
            go = input("Proceed anyway? (yes/no): ").strip().lower()
            if go != 'yes':
                print("Aborted.")
                return 0
    else:
        print(f"\nSignal CONFIRMED: 52w low broken, abnormal={abnormal_pct:.2f}%")
        print(f"  Expected 5d abnormal return: -4.44%")

    print()
    print(f"Entry: ${entry_price:.2f}, size=${POSITION_SIZE}, stop=8%, target=April 8 close")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate hypothesis {HYPOTHESIS_ID} and short CAG")
        return 0

    if not args.yes:
        confirm = input("Place trade? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 0

    print(f"\nActivating hypothesis {HYPOTHESIS_ID}...")
    try:
        research.activate_hypothesis(HYPOTHESIS_ID, entry_price=entry_price, position_size=POSITION_SIZE)
        print(f"  Activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    print("\nPlacing Alpaca short order...")
    try:
        result = trader.place_experiment(symbol='CAG', direction='short', notional_amount=POSITION_SIZE)
        print(f"  Order: {result}")
        if not result.get('success'):
            print(f"ERROR: {result.get('error')}")
            return 1
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    print()
    print("=" * 65)
    print("CAG TRADE ACTIVE — SHORT at open")
    print(f"  Stop: 8% = ${entry_price * 1.08:.2f}")
    print(f"  Target exit: April 8 close")
    print(f"  Manual exit: python trader.py --close {HYPOTHESIS_ID}")
    print("=" * 65)
    return 0


if __name__ == '__main__':
    sys.exit(main())
