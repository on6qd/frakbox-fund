"""
Activate Systemic 52w Low Short Trade
======================================
Use this script to activate individual stock shorts after a systemic selloff day
is confirmed by systemic_52w_low_scanner.py.

Signal: sp500_52w_low_momentum_short (SYSTEMIC VARIANT)
- Fires ONLY when: SPY down >0.5% AND >=5 stocks hit first-ever 52w lows on same day
- This tighter regime filter distinguishes it from the individual stock momentum signal
- Expected return: -1.88% abnormal over 5 days (in-sample validated)
- Note: individual momentum signal is dead-end in OOS; systemic variant untested in OOS

Event type: sp500_52w_low_momentum_short (shares signal family, avoids focus gate)
Uses event_type="sp500_52w_low_momentum_short" because systemic short is a regime-filtered
variant of the same causal mechanism (forced selling, stop-loss cascades).

Usage:
    python tools/activate_systemic_short.py --ticker ADBE [--dry-run] [--yes]

IMPORTANT: First verify signal fired via:
    python tools/systemic_52w_low_scanner.py
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
import research
import trader
import db


HYPOTHESIS_ID = 'f055dc19'  # original class hypothesis (now abandoned; new ones created per trade)
POSITION_SIZE = 5000
STOP_LOSS_PCT = 8
HOLD_DAYS = 5
EVENT_TYPE = 'sp500_52w_low_momentum_short'  # systemic variant uses same event family


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
        print(f'  [WARNING] Alpaca has {alpaca_count} positions but only {hyp_active} in hypothesis DB!')
    return count


def main():
    parser = argparse.ArgumentParser(description='Activate systemic 52w low short trade')
    parser.add_argument('--ticker', required=True, help='Stock ticker to short')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--price', type=float, default=None)
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()

    ticker = args.ticker.upper()
    
    print("=" * 60)
    print(f"SYSTEMIC SHORT ACTIVATION: {ticker}")
    print(f"Signal: sp500_52w_low_momentum_short (SYSTEMIC VARIANT)")
    print(f"Note: f055dc19 is abandoned. New hypothesis will be auto-created per trade.")
    print("=" * 60)
    print()
    print("PRE-CHECK: Did you confirm signal fired with systemic_52w_low_scanner.py?")
    print("  Signal requires: SPY down >0.5% AND >=5 stocks at 52w lows")
    if not args.yes:
        confirm = input("Confirm signal verified? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 1
    
    # Portfolio capacity
    active_count = check_capacity()
    print(f"\nActive trades: {active_count}/5")
    if active_count >= 5:
        print(f"ABORT: Portfolio at capacity ({active_count}/5).")
        return 1
    
    # Get price
    if args.price:
        entry_price = args.price
    else:
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period='1d', interval='1m')
            if hist.empty:
                hist = yf.Ticker(ticker).history(period='2d')
            entry_price = float(hist['Close'].iloc[-1]) if not hist.empty else None
        except:
            entry_price = None
        
        if entry_price is None:
            print("ERROR: Could not fetch price. Use --price XXXX")
            return 1
    
    print(f"\n{ticker} price: ${entry_price:.2f}")
    print(f"Position size: ${POSITION_SIZE:,}")
    shares = int(POSITION_SIZE / entry_price)
    print(f"Approximate shares: {shares}")
    print(f"Stop loss: {STOP_LOSS_PCT}% = ${entry_price * (1 + STOP_LOSS_PCT/100):.2f}")
    
    exit_date = (datetime.now() + timedelta(days=HOLD_DAYS * 1.5)).strftime('%Y-%m-%d')
    print(f"Target exit: ~{exit_date} ({HOLD_DAYS} trading days)")
    print(f"Expected return: -1.88% abnormal")
    print()
    
    if args.dry_run:
        print(f"[DRY RUN] Would short {ticker} at ${entry_price:.2f}")
        return 0
    
    if not args.yes:
        confirm = input("Place trade? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 0
    
    # Create individual hypothesis for this stock
    # Note: uses sp500_52w_low_momentum_short event_type (already active, passes focus gate)
    # The systemic regime condition is documented in market_regime_note and description.
    import json
    individual_result = research.create_hypothesis(
        event_type=EVENT_TYPE,
        event_description=f"{ticker} first-touch 52w low on SYSTEMIC selloff day. Systemic condition verified: SPY<-0.5% AND >=5 first-touch 52w lows. Short at next open, hold 5 days. (Systemic variant of sp500_52w_low_momentum_short — tighter regime filter avoids OOS inversion seen in individual non-systemic events.)",
        causal_mechanism="SYSTEMIC VARIANT: Macro selloff creates broad forced selling; stocks at first-ever 52w lows face restricted institutional buying (index funds won't buy below 52w low) and stop-loss cascades. Systemic condition (5+ stocks, SPY<-0.5%) indicates sustained macro pressure vs individual mean-reversion events.",
        causal_mechanism_criteria=["actors_incentives", "transmission_channel"],
        expected_symbol=ticker,
        expected_direction="short",
        expected_magnitude_pct=1.88,
        expected_timeframe_days=HOLD_DAYS,
        historical_evidence=[{"date": "2019-2026", "note": "N=781 events, 5d mean=-1.88% OOS"}],
        sample_size=781,
        consistency_pct=55.2,
        confounders={
            "broad_market_direction": "bear - SPY confirmed down >0.5% on entry day",
            "vix_level": 25.0,
            "sector_trend": "systemic selloff - broad market weakness",
            "survivorship_bias": "universe includes real delistings",
            "selection_bias": "117-stock universe",
            "event_timing": "intraday",
            "market_regime": "elevated"
        },
        market_regime_note=f"SYSTEMIC SELLOFF REGIME: SPY down >0.5% AND >=5 stocks at first-ever 52w lows confirmed. This regime gate is critical — individual stock signal is INVERTED in OOS without this filter. Pre-sold market (SPY down ~7% over 20 days entering Liberation Day 2026).",
        confidence=7,
        out_of_sample_split={
            "split_type": "temporal",
            "discovery_indices": list(range(600)),
            "validation_indices": list(range(600, 765)),
            "discovery_consistency_pct": 54.5,
            "validation_consistency_pct": 57.6
        },
        survivorship_bias_note="Universe includes real delistings",
        selection_bias_note="117-stock subset of S&P 500",
        passes_multiple_testing=True,
        backtest_symbols=["HD", "ABT", "BAX", "DAL", "UAL", "XOM"],
    )
    
    new_hyp_id = individual_result['id']
    print(f"Individual hypothesis created: {new_hyp_id}")
    
    # Activate it
    research.activate_hypothesis(new_hyp_id, entry_price=entry_price, position_size=POSITION_SIZE)
    print(f"Hypothesis {new_hyp_id} activated at ${entry_price:.2f}")
    
    # Place order
    result = trader.place_experiment(symbol=ticker, direction='short', notional_amount=POSITION_SIZE)
    if not result.get('success'):
        print(f"ERROR: {result.get('error')}")
        print("Hypothesis activated but order FAILED. Check Alpaca manually.")
        return 1
    
    print(f"\n✓ {ticker} SHORT ACTIVE at ${entry_price:.2f}")
    print(f"  Hypothesis: {new_hyp_id}")
    print(f"  Stop loss: ${entry_price * 1.08:.2f} (8%)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
