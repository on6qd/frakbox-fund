#!/usr/bin/env python3
"""
NT 10-K Late Filing Trade Activation Script
============================================
When the NT filing scanner detects a FIRST-TIME large-cap filer, run this
script to activate hypothesis 3db5eb00 and place the short trade.

SIGNAL:
  NT 10-K filing by a company that has NOT filed NT 10-K in prior 2 years.
  First-time filers show -1.06% avg 1d, -2.0% avg 3d (validated subgroup).
  Repeat filers show NO signal — do NOT trade repeats.

TRADE DETAILS:
  Hypothesis: 3db5eb00 (nt_10k_late_filing_short, 3d hold)
  Direction: SHORT
  Hold period: 3 trading days
  Position: $5,000
  Stop loss: 5% (tighter than default due to small effect size)
  Take profit: 2.5% (per operational guidance 2026-04-12)

ABORT CONDITIONS:
  - Company is a REPEAT filer (has prior NT 10-K in past 2 years)
  - Market cap < $500M
  - Stock is hard to borrow or has no options
  - VIX > 50 (extreme market conditions)
  - Stock already dropped >5% on the filing date (effect priced in)

Usage:
  # With scanner output
  python tools/activate_nt10k_trade.py --symbol XXXX --filing-date 2026-04-14

  # Dry run (no actual order)
  python tools/activate_nt10k_trade.py --symbol XXXX --filing-date 2026-04-14 --dry-run

  # Override price
  python tools/activate_nt10k_trade.py --symbol XXXX --filing-date 2026-04-14 --price 50.00
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import research
import trader

try:
    import yfinance as yf
except ImportError:
    yf = None

HYPOTHESIS_ID = '3db5eb00'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 5.0
TAKE_PROFIT_PCT = 2.5
HOLD_DAYS = 3
MIN_MARKET_CAP = 500_000_000


def get_current_price(symbol: str) -> float | None:
    """Get current/latest price for a symbol."""
    if not yf:
        return None
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get price for {symbol}: {e}")
    return None


def check_market_cap(symbol: str) -> float | None:
    """Get market cap. Returns None if unavailable."""
    if not yf:
        return None
    try:
        info = yf.Ticker(symbol).info
        return info.get('marketCap')
    except Exception:
        return None


def verify_first_time_filer(symbol: str, filing_date: str) -> bool:
    """Check EDGAR for prior NT 10-K filings by the same company in past 2 years.

    Returns True if this is a first-time filer (no prior NT 10-K).
    """
    import requests
    import re
    import time

    HEADERS = {"User-Agent": "financial-researcher research@example.com"}

    # First, get CIK for this ticker
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms=NT%2010-K&from=0&size=5"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"Warning: EFTS query failed ({resp.status_code}). Cannot verify first-time status.")
            return True  # Assume first-time if can't check

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        # Find our filing to get CIK
        cik = None
        for h in hits:
            src = h.get("_source", {})
            names = src.get("display_names", [])
            if names:
                m = re.search(r'\(([A-Z]{1,5})\)', names[0])
                if m and m.group(1) == symbol:
                    ciks = src.get("ciks", [])
                    if ciks:
                        cik = ciks[0].lstrip("0")
                    break

        if not cik:
            print(f"Warning: Could not find CIK for {symbol}. Assuming first-time.")
            return True

        # Look back 2 years for prior NT 10-K
        fd = datetime.strptime(filing_date, "%Y-%m-%d")
        lookback_start = fd.replace(year=fd.year - 2).strftime("%Y-%m-%d")
        lookback_end = (fd - __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")

        time.sleep(0.15)
        url2 = (
            f"https://efts.sec.gov/LATEST/search-index?forms=NT%2010-K"
            f"&dateRange=custom&startdt={lookback_start}&enddt={lookback_end}"
            f"&ciks={cik}&from=0&size=5"
        )
        resp2 = requests.get(url2, headers=HEADERS, timeout=15)
        if resp2.status_code == 200:
            prior_hits = resp2.json().get("hits", {}).get("total", {}).get("value", 0)
            if prior_hits > 0:
                print(f"REPEAT FILER: {symbol} has {prior_hits} prior NT 10-K filings in past 2 years.")
                return False
            else:
                print(f"FIRST-TIME FILER confirmed: No prior NT 10-K for {symbol} (CIK {cik}).")
                return True
        else:
            print(f"Warning: Lookback query failed. Assuming first-time.")
            return True

    except Exception as e:
        print(f"Warning: EFTS check failed ({e}). Assuming first-time.")
        return True


def main():
    db.init_db()

    parser = argparse.ArgumentParser(description='Activate NT 10-K late filing short trade')
    parser.add_argument('--symbol', required=True, help='Stock ticker (e.g., XXXX)')
    parser.add_argument('--filing-date', required=True, help='NT 10-K filing date (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true', help='Simulate without placing order')
    parser.add_argument('--price', type=float, default=None, help='Override entry price')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--skip-first-time-check', action='store_true',
                        help='Skip EDGAR first-time filer verification (if already confirmed by scanner)')
    args = parser.parse_args()

    symbol = args.symbol.upper()

    print("=" * 60)
    print(f"NT 10-K LATE FILING SHORT — {symbol}")
    print("=" * 60)
    print()
    print(f"Hypothesis: {HYPOTHESIS_ID} (nt_10k_late_filing_short)")
    print(f"Filing date: {args.filing_date}")
    print(f"Direction: SHORT")
    print(f"Hold: {HOLD_DAYS} trading days")
    print(f"Position: ${POSITION_SIZE:,}")
    print(f"Stop loss: {STOP_LOSS_PCT}%")
    print(f"Take profit: {TAKE_PROFIT_PCT}%")
    print()

    # Check hypothesis status
    hyp = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not hyp:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found.")
        return 1
    if hyp.get('status') != 'pending':
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} is '{hyp.get('status')}', not 'pending'.")
        print("If a previous NT 10-K trade completed, create a NEW hypothesis for this event.")
        return 1

    # Verify first-time filer
    if not args.skip_first_time_check:
        print("Checking first-time filer status via EDGAR...")
        is_first_time = verify_first_time_filer(symbol, args.filing_date)
        if not is_first_time:
            print(f"\nABORT: {symbol} is a REPEAT NT 10-K filer. No tradeable signal for repeats.")
            print("Repeat filers: Wilcoxon p>0.35 at all horizons. Do not trade.")
            return 1
    else:
        print("Skipping first-time check (--skip-first-time-check flag).")

    # Check market cap
    print(f"\nChecking market cap for {symbol}...")
    mc = check_market_cap(symbol)
    if mc:
        mc_b = mc / 1e9
        print(f"  Market cap: ${mc_b:.1f}B")
        if mc < MIN_MARKET_CAP:
            print(f"ABORT: Market cap ${mc/1e6:.0f}M < ${MIN_MARKET_CAP/1e6:.0f}M minimum.")
            return 1
    else:
        print("  Warning: Could not verify market cap. Proceeding with caution.")

    # Get entry price
    if args.price:
        entry_price = args.price
        print(f"\nUsing provided price: ${entry_price:.2f}")
    else:
        entry_price = get_current_price(symbol)
        if entry_price:
            print(f"\nCurrent {symbol} price: ${entry_price:.2f}")
        else:
            print(f"\nERROR: Could not fetch price for {symbol}. Use --price to override.")
            return 1

    shares = int(POSITION_SIZE / entry_price)
    print(f"Shares to short: ~{shares}")
    print()

    # Summary
    print("TRADE SUMMARY:")
    print(f"  SHORT {shares} shares of {symbol} @ ~${entry_price:.2f}")
    print(f"  Stop loss: {STOP_LOSS_PCT}% (${entry_price * (1 + STOP_LOSS_PCT/100):.2f})")
    print(f"  Take profit: {TAKE_PROFIT_PCT}% (${entry_price * (1 - TAKE_PROFIT_PCT/100):.2f})")
    print(f"  Hold: {HOLD_DAYS} trading days from entry")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would activate {HYPOTHESIS_ID} for {symbol}")
        print(f"[DRY RUN] Would place SHORT {symbol} ${POSITION_SIZE} at market")
        return 0

    # Confirm
    if args.yes:
        print("Auto-confirming (--yes flag).")
        confirm = 'yes'
    else:
        confirm = input("Place trade? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        return 0

    # Step 1: Update hypothesis with specific symbol and activate
    print(f"\nUpdating hypothesis {HYPOTHESIS_ID} with symbol {symbol}...")
    try:
        db.update_hypothesis_fields(HYPOTHESIS_ID,
            expected_symbol=symbol,
            trigger='next_market_open',
            trigger_position_size=POSITION_SIZE,
            trigger_stop_loss_pct=STOP_LOSS_PCT,
            trigger_take_profit_pct=TAKE_PROFIT_PCT,
        )
        research.activate_hypothesis(HYPOTHESIS_ID,
                                     entry_price=entry_price,
                                     position_size=POSITION_SIZE)
        print(f"  Hypothesis activated at ${entry_price:.2f}")
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # Step 2: Place short order
    print(f"\nPlacing Alpaca short order...")
    try:
        result = trader.place_experiment(
            symbol=symbol,
            direction='short',
            notional_amount=POSITION_SIZE,
        )
        print(f"  Order result: {result}")
        if not result.get('success'):
            print(f"ERROR: {result.get('error')}")
            print("Hypothesis activated but order FAILED. Check Alpaca manually.")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {e}")
        print("Hypothesis activated but order FAILED. Check Alpaca manually.")
        return 1

    print()
    print("=" * 60)
    print("SHORT TRADE ACTIVE")
    print(f"  {symbol} SHORT @ ~${entry_price:.2f}")
    print(f"  Exit in {HOLD_DAYS} trading days (trade_loop handles auto-close)")
    print(f"  Expected: -2% abnormal (first-time NT 10-K filer)")
    print("=" * 60)

    # Record event in knowledge base
    db.record_known_effect(
        f'nt_10k_{symbol.lower()}_activation_{args.filing_date}',
        json.dumps({
            'status': 'TRADE_ACTIVATED',
            'symbol': symbol,
            'filing_date': args.filing_date,
            'entry_price': entry_price,
            'hypothesis_id': HYPOTHESIS_ID,
            'is_first_time_filer': True,
            'date': datetime.now().isoformat(),
        })
    )

    return 0


if __name__ == '__main__':
    sys.exit(main())
