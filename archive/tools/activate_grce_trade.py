"""
GRCE (Grace Therapeutics) FDA PDUFA Trade Activation Script
============================================================
Run this the MORNING AFTER the FDA decision on April 23, 2026 (or as soon as
the market opens on the day of the rejection, if announced AH/pre-mkt).

HYPOTHESIS: 0c2e58cf (fda_clinical_rejection_short)

SIGNAL: GRCE NDA for GTx-104 (IV nimodipine, aSAH = aneurysmal subarachnoid hemorrhage)
PDUFA date: April 23, 2026 (Thursday).
FIRST NDA — no prior CRL. Drug approved for aSAH prevention.
KEY RISK: 2:1 mortality imbalance in STRIVE-ON trial (8 deaths drug vs 4 placebo).
  FDA may cite safety (mortality) OR efficacy concerns in a CRL.
Market cap: ~$72M. Single-pipeline. No backup product.

BACKTEST (N=14 fda_clinical_rejection_short/clinical_efficacy_failure events, 2019-2026):
  - 3d avg abnormal return: -24.1% (range: -15.3% to -34.7%)
  - Direction: 92.9% negative
  - OOS 2022-2026: 87.5% negative

ENTRY CONDITIONS:
  1. FDA issued CRL or rejection (NOT approval)
  2. GRCE opened DOWN on crash day
  3. Crash is due to CLINICAL/EFFICACY/SAFETY reasons
  4. GRCE dropped >= 40% from prior close
  5. Portfolio has capacity (<5 active positions)

ABORT CONDITIONS:
  - FDA APPROVED (we only short on rejection)
  - Crash < 40% (not a clean signal - could reverse)
  - Crash > 85% (panic levels, may reverse sharply next day)
  - CRL was for CMC/manufacturing reasons only (not efficacy/safety)
  - GRCE stock declined >20% in 10 days before April 23 (signal already priced)

DATES:
  - PDUFA: April 23, 2026 (Thursday)
  - FDA typically acts on or before PDUFA date
  - If announced AH on April 22 or April 23: enter at April 24 open (Friday)
  - If announced April 23 pre-mkt: enter at April 23 open

Usage:
    python tools/activate_grce_trade.py [--crash-pct 55.0] [--dry-run] [--yes]

Examples:
    # Pre-market announcement, 62% drop, standard run
    python tools/activate_grce_trade.py --crash-pct 62.0

    # Dry run to check conditions
    python tools/activate_grce_trade.py --crash-pct 62.0 --dry-run

    # After hours announcement, known crash
    python tools/activate_grce_trade.py --crash-pct 45.0 --yes
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
import research
import trader
import db


HYPOTHESIS_ID = '0c2e58cf'
POSITION_SIZE = 5000
STOP_LOSS_PCT = 10
HOLD_DAYS = 3
MIN_CRASH_PCT = 40.0  # must crash at least 40% to enter
MAX_CRASH_PCT = 85.0  # above 85% = panic reversal risk, abort
MAX_PRE_EVENT_DECLINE_PCT = 20.0  # abort if stock already dropped >20% in 10td pre-PDUFA
SYMBOL = 'GRCE'


def check_pre_event_decline(crash_pct=None):
    """
    Hard-coded version of the doc abort rule at the top of this file.
    Uses 30-day max drawdown from peak (NOT rolling 10td window), so it
    catches leaks that happened more than 10 days ago but still have the
    information priced in.

    If the drawdown from the 30-day peak (excluding today's crash) exceeds
    MAX_PRE_EVENT_DECLINE_PCT, the rejection is already priced in and the
    activator should abort.
    """
    try:
        from tools.yfinance_utils import safe_download
    except ImportError:
        return True, None, "cannot import yfinance_utils — skipping pre-decline check"
    try:
        from datetime import date, timedelta
        end = date.today() + timedelta(days=1)
        start = end - timedelta(days=60)
        df = safe_download(SYMBOL, start=start.isoformat(), end=end.isoformat())
        if df is None or len(df) < 12:
            return True, None, "insufficient history (<12 bars)"
        closes = df['Close'].dropna().values.flatten()
        if len(closes) < 12:
            return True, None, "insufficient lookback"
        # Exclude the latest bar if crash_pct is provided (that's the crash day).
        # Otherwise use all bars (simulation/dry-run).
        if crash_pct is not None and crash_pct > 0 and len(closes) >= 2:
            pre_crash = closes[:-1]
            anchor = float(pre_crash[-1])  # prior close
        else:
            pre_crash = closes
            anchor = float(pre_crash[-1])
        peak_30d = float(max(pre_crash[-30:]))
        drawdown_pct = (anchor / peak_30d - 1.0) * 100.0
        if drawdown_pct < -MAX_PRE_EVENT_DECLINE_PCT:
            return False, drawdown_pct, (
                f"PRE-EVENT CONTAMINATION: {SYMBOL} prior close ${anchor:.2f} is "
                f"{drawdown_pct:+.1f}% below 30d peak (${peak_30d:.2f}). Leak/priced-in "
                f"rejection — expected post-event abnormal drop will be much smaller "
                f"than backtest. Do NOT short a pre-leaked event."
            )
        return True, drawdown_pct, (
            f"30d drawdown from peak: {drawdown_pct:+.1f}% "
            f"(peak ${peak_30d:.2f} -> prior ${anchor:.2f}) — clean"
        )
    except Exception as e:
        return True, None, f"pre-decline check error: {e} — allowing"


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


def get_current_price(ticker):
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period='1d', interval='1m')
        if hist.empty:
            hist = yf.Ticker(ticker).history(period='2d')
        return float(hist['Close'].iloc[-1]) if not hist.empty else None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description='Activate GRCE FDA rejection short trade')
    parser.add_argument('--crash-pct', type=float, required=False,
                        help='Observed crash percentage (e.g. 55.0 for -55%)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()

    db.init_db()

    print("=" * 65)
    print("GRCE (Grace Therapeutics) FDA REJECTION SHORT")
    print(f"Hypothesis: {HYPOTHESIS_ID} (fda_clinical_rejection_short)")
    print("PDUFA: April 23, 2026")
    print("=" * 65)
    print()

    # --- Pre-flight checks ---
    print("PRE-FLIGHT CHECKS:")
    print(f"1. Did FDA issue a CRL/rejection? (NOT approval)")
    print(f"2. Was rejection for clinical/safety/efficacy reasons?")
    print(f"3. Did GRCE crash ≥{MIN_CRASH_PCT}%?")
    print()

    # Get crash percentage
    crash_pct = args.crash_pct
    if crash_pct is None:
        entry_price = get_current_price('GRCE')
        if entry_price:
            print(f"Current GRCE price: ${entry_price:.2f}")
            print("(Compare to prior close to estimate crash %)")
        if not args.yes:
            crash_input = input("Enter observed crash % (e.g. 55.0 for -55%): ").strip()
            crash_pct = float(crash_input)
        else:
            print("ERROR: --crash-pct required when using --yes without interactive mode")
            return 1

    # Validate crash
    print(f"\nObserved crash: -{crash_pct:.1f}%")
    if crash_pct < MIN_CRASH_PCT:
        print(f"ABORT: Crash only {crash_pct:.1f}% — need ≥{MIN_CRASH_PCT}% for clean signal.")
        print(f"  Reason: Small crashes may reverse. Signal validated on 40%+ crashes only.")
        return 1

    if crash_pct > MAX_CRASH_PCT:
        print(f"ABORT: Crash of {crash_pct:.1f}% is panic-level (>{MAX_CRASH_PCT}%).")
        print(f"  Reason: Extreme crashes risk next-day reversal (short squeeze).")
        print(f"  Consider smaller position or skip.")
        if not args.yes:
            confirm = input("Override panic check and proceed anyway? (yes/no): ").strip().lower()
            if confirm != 'yes':
                return 0

    print(f"✓ Crash condition met ({crash_pct:.1f}% ≥ {MIN_CRASH_PCT}%)")

    # Pre-event contamination check
    ok, decline_pct, msg = check_pre_event_decline(crash_pct=crash_pct)
    print(f"\nPre-event decline check: {msg}")
    if not ok:
        print(f"ABORT: {msg}")
        return 1

    # Portfolio capacity
    active_count = check_capacity()
    print(f"\nActive trades: {active_count}/5")
    if active_count >= 5:
        print(f"ABORT: Portfolio at capacity ({active_count}/5).")
        return 1

    # Get current price
    entry_price = get_current_price('GRCE')
    if entry_price is None:
        print("ERROR: Could not fetch GRCE price. Check ticker.")
        return 1

    print(f"\nGRCE current price: ${entry_price:.2f}")
    shares = int(POSITION_SIZE / entry_price)
    print(f"Position size: ${POSITION_SIZE:,}")
    print(f"Approximate shares: {shares}")
    print(f"Stop loss: {STOP_LOSS_PCT}% = ${entry_price * (1 + STOP_LOSS_PCT/100):.2f}")

    exit_date = (datetime.now() + timedelta(days=HOLD_DAYS * 1.5)).strftime('%Y-%m-%d')
    print(f"Target exit: ~{exit_date} ({HOLD_DAYS} trading days)")
    print(f"Expected return: -20% abnormal over 3 days (N=14, 92.9% direction)")
    print()

    if args.dry_run:
        print(f"[DRY RUN] Would short GRCE at ${entry_price:.2f}")
        print(f"[DRY RUN] Hypothesis {HYPOTHESIS_ID} would be activated")
        return 0

    if not args.yes:
        confirm = input("Place GRCE short trade? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            return 0

    # Activate hypothesis
    print(f"\nActivating hypothesis {HYPOTHESIS_ID}...")
    research.activate_hypothesis(
        HYPOTHESIS_ID,
        entry_price=entry_price,
        position_size=POSITION_SIZE,
    )
    print(f"✓ Hypothesis {HYPOTHESIS_ID} activated at ${entry_price:.2f}")

    # Place short order
    result = trader.place_experiment(
        symbol='GRCE',
        direction='short',
        notional_amount=POSITION_SIZE,
    )

    if not result.get('success'):
        print(f"\nERROR: Order failed — {result.get('error')}")
        print("Hypothesis activated but order FAILED. Check Alpaca manually.")
        return 1

    print(f"\n✓ GRCE SHORT ACTIVE at ${entry_price:.2f}")
    print(f"  Hypothesis: {HYPOTHESIS_ID}")
    print(f"  Position: ${POSITION_SIZE:,} / {shares} shares")
    print(f"  Stop loss: ${entry_price * (1 + STOP_LOSS_PCT/100):.2f} (+{STOP_LOSS_PCT}%)")
    print(f"  Target exit: {exit_date} ({HOLD_DAYS} trading days)")
    print()
    print("NEXT STEPS:")
    print(f"  1. Monitor for stop loss (${entry_price * (1 + STOP_LOSS_PCT/100):.2f})")
    print(f"  2. Close position on {exit_date} at market open")
    print(f"  3. Record outcome: python3 tools/record_hypothesis_close.py --id {HYPOTHESIS_ID}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
