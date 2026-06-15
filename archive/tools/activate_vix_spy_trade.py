"""
VIX Spike Recovery SPY Long Trade Activation Script
=====================================================
Run this at market open of the NEXT TRADING DAY after VIX closes above 30.

HYPOTHESIS: b63a0168 (vix_spike_above_30_spy_long, 20d hold)

SIGNAL: When VIX closes above 30 (first close in 30-day cluster), buy SPY at
next open and hold 20 trading days.

BACKTEST (N=54 events, 2000-2026):
  - 5d: mean=+1.19%, direction=69%, p=0.012 (significant p<0.05)
  - 20d: mean=+1.69%, direction=67%, p=0.045 (significant p<0.05)
  - PASSES multiple testing (2+ horizons at p<0.05)
  - Validation 2015-2026 (N=19): 20d mean=+2.92%, dir=79%, p=0.027

TRIGGER CONDITIONS:
  1. VIX CLOSED above 30 yesterday (must be CLOSE, not intraday)
  2. VIX close>30 is the FIRST in a 30-day window (not in an ongoing episode)
  3. Portfolio capacity < 5

ABORT CONDITIONS:
  - VIX only spiked intraday, did NOT close above 30
  - Already in an ongoing VIX>30 episode (within 30 days of prior event)
  - Portfolio at 5/5 capacity

Usage:
  python tools/activate_vix_spy_trade.py [--dry-run] [--yes]
"""

import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db

HYPOTHESIS_ID = 'b63a0168'
POSITION_SIZE = 5000
HOLD_DAYS = 20
STOP_LOSS_PCT = 10.0
TAKE_PROFIT_PCT = 8.0  # Capture if market recovers sharply early


def get_current_vix():
    """Get yesterday's VIX close."""
    try:
        import yfinance as yf
        vix = yf.Ticker('^VIX')
        hist = vix.history(period='3d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2]) if len(hist) > 1 else None
    except Exception as e:
        print(f"Warning: could not get VIX: {e}")
    return None, None


def get_spy_price():
    """Get current SPY price."""
    try:
        import yfinance as yf
        spy = yf.Ticker('SPY')
        hist = spy.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        hist = spy.history(period='2d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Warning: could not get SPY price: {e}")
    return None


def check_capacity():
    """Check current portfolio capacity."""
    db.init_db()
    hypotheses = db.load_hypotheses()
    hyp_active = [h for h in hypotheses if h.get('status') == 'active']
    hyp_count = len(hyp_active)
    try:
        api = trader.get_api()
        alpaca_count = len(api.list_positions())
    except Exception:
        alpaca_count = 0
    return max(hyp_count, alpaca_count)


def check_vix_episode_status():
    """Check if VIX>30 was already ongoing (within last 30 days)."""
    try:
        import yfinance as yf
        vix = yf.Ticker('^VIX')
        hist = vix.history(period='35d')
        if hist.empty:
            return False, None
        
        # Find the last time VIX crossed above 30 before yesterday
        hist_list = [(date, close) for date, close in zip(hist.index, hist['Close'])]
        last_date = hist_list[-1][0]  # Yesterday (or today's data)
        
        # Check if any prior close in last 30 days was also >30
        prior_vix_above_30 = [(d, c) for d, c in hist_list[:-1] if c > 30]
        
        if prior_vix_above_30:
            last_prior = prior_vix_above_30[-1]
            days_since = (last_date - last_prior[0]).days
            if days_since <= 30:
                return True, last_prior  # Already in an episode
        
        return False, None
    except Exception as e:
        print(f"Warning: could not check VIX episode: {e}")
    return False, None


def main():
    parser = argparse.ArgumentParser(description='Activate VIX spike recovery SPY long trade')
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without placing actual order')
    parser.add_argument('--yes', action='store_true',
                        help='Skip confirmation prompt')
    parser.add_argument('--vix-close', type=float, default=None,
                        help='Override VIX close value (default: fetch live)')
    args = parser.parse_args()

    print("=" * 65)
    print("VIX SPIKE RECOVERY — SPY LONG ACTIVATION")
    print("=" * 65)
    print(f"Hypothesis: {HYPOTHESIS_ID} (vix_spike_above_30_spy_long, {HOLD_DAYS}d hold)")
    print()

    db.init_db()

    # Check hypothesis status
    hyp = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not hyp:
        print(f"ERROR: Hypothesis {HYPOTHESIS_ID} not found in database!")
        return 1
    if hyp.get('status') != 'pending':
        print(f"ERROR: Hypothesis status is '{hyp.get('status')}', expected 'pending'.")
        return 1

    # Capacity check
    active_count = check_capacity()
    print(f"Portfolio capacity: {active_count}/5")
    if active_count >= 5:
        print("ABORT: Portfolio at capacity. Close a trade first.")
        return 1

    # VIX check
    vix_close, vix_prev = get_current_vix()
    if args.vix_close:
        vix_close = args.vix_close
        print(f"  Using override VIX close: {vix_close}")
    
    print(f"VIX yesterday close: {vix_close:.1f}" if vix_close else "  WARNING: Could not fetch VIX")
    
    if vix_close and vix_close <= 30:
        print(f"\nABORT: VIX closed at {vix_close:.1f}, which is AT OR BELOW 30.")
        print("       Signal only fires when VIX CLOSES above 30.")
        return 1

    # Check if this is a new episode (not within 30d of prior VIX>30)
    in_episode, prior_event = check_vix_episode_status()
    if in_episode:
        print(f"\nWARNING: VIX>30 already occurred {prior_event[0].date()} (within 30 days).")
        print("         This may be a CONTINUATION of an existing episode, not a new spike.")
        print("         Per methodology, we only trade the FIRST event in each 30-day cluster.")
        if not args.yes:
            resp = input("Continue anyway? (y/N): ").strip().lower()
            if resp != 'y':
                print("Aborted.")
                return 1

    # Get SPY price
    entry_price = get_spy_price()
    if entry_price is None:
        print("ERROR: Could not get SPY price. Aborting.")
        return 1
    
    print(f"SPY current price: ${entry_price:.2f}")

    # Calculate exit deadline (20 trading days ~ 28 calendar days)
    exit_date = (datetime.now() + timedelta(days=28)).strftime('%Y-%m-%dT15:50')

    print()
    print("=" * 65)
    print("TRADE SUMMARY")
    print("=" * 65)
    print(f"  Action: BUY SPY (long)")
    print(f"  Entry: ${entry_price:.2f} (market order)")
    print(f"  Position: ${POSITION_SIZE:,}")
    print(f"  Shares: ~{POSITION_SIZE/entry_price:.0f}")
    print(f"  Stop loss: {STOP_LOSS_PCT}%  (${entry_price*(1-STOP_LOSS_PCT/100):.2f})")
    print(f"  Take profit: {TAKE_PROFIT_PCT}%  (${entry_price*(1+TAKE_PROFIT_PCT/100):.2f})")
    print(f"  Exit deadline: {exit_date} (20 trading days)")
    print()
    print("  EXPECTED: +1.69% 20d (N=54), validation +2.92% 20d (N=19)")
    print(f"  VIX at entry: {vix_close:.1f}")

    if args.dry_run:
        print("\n[DRY RUN] — no trade placed.")
        return 0

    if not args.yes:
        resp = input("\nProceed with trade? (y/N): ").strip().lower()
        if resp != 'y':
            print("Aborted.")
            return 0

    # Step 1: Activate hypothesis (pre-registers entry price and stop)
    research.activate_hypothesis(
        HYPOTHESIS_ID,
        entry_price=entry_price,
        position_size=POSITION_SIZE,
        vix_level=vix_close,
        stop_loss_pct=STOP_LOSS_PCT,
        take_profit_pct=TAKE_PROFIT_PCT,
    )
    print(f"  Hypothesis {HYPOTHESIS_ID} activated at ${entry_price:.2f}")

    # Step 2: Place order via trader.py
    result = trader.place_experiment(
        symbol='SPY',
        direction='long',
        notional_amount=POSITION_SIZE,
    )
    if not result.get('success'):
        print(f"ERROR placing order: {result.get('error')}")
        print("Hypothesis was activated but order FAILED. Check Alpaca manually.")
        return 1
    print(f"\n✓ Order placed: BUY SPY @ market (${POSITION_SIZE:,})")
    print(f"  Order result: {result}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
