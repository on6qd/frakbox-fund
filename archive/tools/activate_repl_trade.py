"""
REPL (Replimune) FDA PDUFA Trade Activation Script
====================================================
Run this the MORNING AFTER the FDA decision on April 10, 2026 (or as soon as
the market opens on the day of the rejection, if announced AH/pre-mkt).

HYPOTHESES:
  - 5f805860 (fda_clinical_rejection_short): If REPL drops 40-55%, enter SHORT
  - d302c84b (clinical_efficacy_failure_short): If REPL drops >55%, enter SHORT

SIGNAL: REPL BLA resubmission for RP1 (vusolimogene oderparepvec) + nivolumab
in advanced melanoma. PDUFA date: April 10, 2026.
Prior CRL (July 2025): EFFICACY reasons — FDA questioned heterogeneity and trial design.
Resubmission did NOT run a new confirmatory trial — added analysis to same data.

BACKTEST (N=6 fda_clinical_rejection_short events, 2019-2024):
  - 3d avg abnormal return: -24.1% (from -15.3% to -34.7%)
  - Direction: 100% negative
  - OOS validation: 100% negative (all 3 OOS events)

BACKTEST (N=14 clinical_efficacy_failure_short events, 2019-2026):
  - 3d avg abnormal return: -27.5%
  - Direction: 92.9% negative
  - OOS 2022-2026: 87.5% negative

ENTRY CONDITIONS:
  1. FDA issued CRL or rejection (NOT approval)
  2. REPL opened DOWN on crash day
  3. Crash is due to CLINICAL/EFFICACY reasons (not CMC/manufacturing)
  4. REPL dropped ≥40% from prior close
  5. Portfolio has capacity (<5 active positions)

DECISION TREE:
  If crash_pct > 55%: use hypothesis d302c84b (larger effect expected)
  If crash_pct 40-55%: SKIP — REPL bounced after July 2025 CRL (prior pattern).
    Only enter >55% to avoid repeat bounce risk.

ABORT CONDITIONS:
  - FDA APPROVED (we only short on rejection)
  - Crash < 40% (not a clean signal - could reverse)
  - Crash > 85% (panic levels, may reverse sharply Monday)
  - CRL was for CMC/manufacturing reasons only (not efficacy)
  - Pre-announced: REPL stock declined >20% in 10 days before April 10

DATES:
  - PDUFA: April 10, 2026 (Friday)
  - FDA typically acts on or before PDUFA date
  - If announced AH Thursday April 9: enter at April 10 (Friday) open
  - If announced AH Friday April 10: enter at Monday April 13 open
  - If announced April 10 pre-mkt: enter at April 10 open
  - NOTE: April 10 is FRIDAY. AH announcements mean Monday entry (2 weekend days of info diffusion)

Usage:
  python tools/activate_repl_trade.py [--dry-run] [--price XXXX] [--prior-close XXXX]
"""

import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import trader
import db


# Hypothesis IDs
HYP_CRL_SHORT = '5f805860'     # fda_clinical_rejection_short (40-55% crash)
HYP_EFFICACY_SHORT = 'd302c84b'  # clinical_efficacy_failure_short (>55% crash)

POSITION_SIZE = 5000
HOLD_DAYS = 3
STOP_LOSS_PCT = 15.0       # Wider stop — biotech can recover sharply
TAKE_PROFIT_PCT = 25.0     # Capture 25% gain early
SYMBOL = 'REPL'

PDUFA_DATE = '2026-04-10'
MAX_PRE_EVENT_DECLINE_PCT = 20.0  # Abort if stock already dropped >20% in 10td before PDUFA


def get_repl_price():
    """Get REPL current price."""
    try:
        import yfinance as yf
        hist = yf.Ticker(SYMBOL).history(period='2d')
        if len(hist) >= 2:
            return float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
        elif not hist.empty:
            return float(hist['Close'].iloc[-1]), None
    except Exception as e:
        print(f"  Warning: could not get {SYMBOL} price: {e}")
    return None, None


def check_pre_event_decline(prior_close=None, crash_pct=None):
    """
    Abort rule: if REPL is already >MAX_PRE_EVENT_DECLINE_PCT below its 30-day
    peak going INTO the FDA decision, the signal is contaminated (pre-leak has
    priced in most of the rejection). Enter only on clean surprises.

    Uses 30-day max drawdown from peak so leaks older than 10 days still trip
    the check.

    The "exclude crash bar" heuristic only applies when today's date is >=
    PDUFA_DATE. Otherwise we are running pre-event, so the most recent bar is
    pre-event price action (potentially leak), not the FDA crash itself, and
    excluding it would mask contamination. (Bug found 2026-04-09: pre-event
    leak of -24.6% on Apr 8 was being excluded as "the crash" the day before
    PDUFA.)

    Returns (ok: bool, drawdown_pct: float | None, message: str).
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
        # Only exclude the latest bar as "the crash" when we are AT or AFTER the
        # PDUFA date. Before PDUFA the latest bar is pre-event price action and
        # MUST count toward the contamination check.
        try:
            from datetime import date as _date
            today = _date.today()
            pdufa = _date.fromisoformat(PDUFA_DATE)
            is_post_event = today >= pdufa
        except Exception:
            is_post_event = True  # default to legacy behavior on parse error
        if is_post_event and crash_pct is not None and abs(crash_pct) > 10 and len(closes) >= 2:
            pre_crash = closes[:-1]
        else:
            pre_crash = closes
        # When pre-event, the "anchor" is the latest available close (today's
        # pre-event price), not whatever the caller passed as prior_close (which
        # in pre-event mode is yesterday-vs-day-before-yesterday — meaningless).
        if not is_post_event:
            anchor = float(pre_crash[-1])
        else:
            anchor = float(prior_close) if prior_close is not None else float(pre_crash[-1])
        peak_30d = float(max(pre_crash[-30:]))
        drawdown_pct = (anchor / peak_30d - 1.0) * 100.0
        mode = "post-event" if is_post_event else "pre-event"
        if drawdown_pct < -MAX_PRE_EVENT_DECLINE_PCT:
            return False, drawdown_pct, (
                f"PRE-EVENT CONTAMINATION ({mode} check): {SYMBOL} anchor "
                f"${anchor:.2f} is {drawdown_pct:+.1f}% below 30d peak "
                f"(${peak_30d:.2f}). Rejection is likely priced in — expected "
                f"post-event abnormal drop will be much smaller than backtest. "
                f"Do NOT short a pre-leaked event."
            )
        return True, drawdown_pct, (
            f"30d drawdown from peak ({mode}): {drawdown_pct:+.1f}% "
            f"(peak ${peak_30d:.2f} -> anchor ${anchor:.2f}) — clean"
        )
    except Exception as e:
        return True, None, f"pre-decline check error: {e} — allowing"


def check_portfolio_capacity():
    """Check if we have room for another position."""
    try:
        positions = trader.get_api().list_positions()
        # Alpaca Position objects expose attributes (not dict) — qty is a string
        active = len([p for p in positions if float(getattr(p, 'qty', 0) or 0) != 0])
        print(f"  Active positions: {active}/5")
        return active < 5
    except Exception as e:
        print(f"  Warning: could not check positions: {e}")
        return True


def classify_crash(crash_pct):
    """Classify crash and return appropriate hypothesis."""
    abs_crash = abs(crash_pct)
    if abs_crash >= 85:
        return None, "ABORT: crash >85% (panic zone, potential reversal)"
    elif abs_crash >= 55:
        return HYP_EFFICACY_SHORT, f"STRONG: crash={abs_crash:.1f}% (>55%) -> use clinical_efficacy_failure_short"
    elif abs_crash >= 40:
        return None, f"SKIP: crash={abs_crash:.1f}% (40-55%). REPL bounced after July 2025 CRL — prior pattern suggests 40-55% crashes reverse. Only enter >55%."
    else:
        return None, f"ABORT: crash={abs_crash:.1f}% (<40%) — below threshold, signal too weak"


def main():
    parser = argparse.ArgumentParser(description='Activate REPL FDA rejection short trade')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without trading')
    parser.add_argument('--price', type=float, help='Current REPL price (crash day open/close)')
    parser.add_argument('--prior-close', type=float, help='REPL prior close (before FDA decision)')
    parser.add_argument('--crl-type', choices=['clinical', 'cmc', 'both'],
                        help='Type of CRL: clinical (bad), cmc (abort), both (use clinical)')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    print(f"\n=== REPL FDA Rejection Short Activation ===")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"PDUFA: {PDUFA_DATE}")
    print()

    # Get prices
    current_price, prior_close = get_repl_price()

    if args.price:
        current_price = args.price
    if args.prior_close:
        prior_close = args.prior_close

    if not current_price:
        print("ERROR: Could not get REPL price. Provide with --price flag.")
        return 1

    print(f"REPL current price: ${current_price:.2f}")

    if prior_close:
        crash_pct = (current_price / prior_close - 1) * 100
        print(f"REPL prior close: ${prior_close:.2f}")
        print(f"Crash: {crash_pct:+.1f}%")
    else:
        print("WARNING: No prior close available. Cannot compute crash %. Provide with --prior-close.")
        crash_pct = None

    # Check CRL type
    if args.crl_type:
        crl_type = args.crl_type
        if crl_type == 'cmc':
            print("\nABORT: CRL was CMC/manufacturing only.")
            print("  This signal requires CLINICAL/EFFICACY rejection.")
            print("  Historical: CMC CRLs show reversal (like RCKT 2024: recovered from -20%)")
            return 1
        print(f"CRL type: {crl_type} → qualifies for short")

    # Pre-event contamination check (coded-enforced version of doc rule)
    ok, decline_pct, msg = check_pre_event_decline(prior_close=prior_close, crash_pct=crash_pct)
    print(f"\nPre-event decline check: {msg}")
    if not ok:
        print(f"ABORT: {msg}")
        return 1

    # Classify crash and select hypothesis
    if crash_pct is not None:
        hyp_id, message = classify_crash(crash_pct)
        print(f"\nSignal classification: {message}")

        if not hyp_id:
            return 1

        # Verify hypothesis status
        hyp = db.get_hypothesis_by_id(hyp_id)
        if not hyp:
            print(f"ERROR: Hypothesis {hyp_id} not found!")
            return 1
        if hyp['status'] != 'pending':
            print(f"ERROR: Hypothesis {hyp_id} status is '{hyp['status']}', expected 'pending'")
            return 1
        print(f"Hypothesis: {hyp_id} ({hyp['event_type']}) status={hyp['status']} ✓")

        # For d302c84b (TBD symbol), we need to update to REPL first
        if hyp.get('expected_symbol') == 'TBD' or not hyp.get('expected_symbol'):
            print(f"  Updating hypothesis symbol from TBD to REPL")
            db.update_hypothesis_fields(hyp_id, expected_symbol=SYMBOL)
    else:
        print("\nWARNING: Cannot determine crash %. Defaulting to fda_clinical_rejection_short.")
        hyp_id = HYP_CRL_SHORT

    # Check portfolio capacity
    if not check_portfolio_capacity():
        print(f"\nABORT: Portfolio at maximum capacity (5/5 positions).")
        return 1

    # Check VIX
    try:
        import yfinance as yf
        vix = yf.Ticker('^VIX').history(period='1d')
        current_vix = float(vix['Close'].iloc[-1]) if not vix.empty else None
        if current_vix:
            print(f"VIX: {current_vix:.1f}")
            if current_vix > 60:
                print(f"ABORT: VIX={current_vix:.1f} > 60. Circuit breaker risk.")
                return 1
    except Exception as e:
        print(f"Warning: could not check VIX: {e}")

    # Calculate shares
    shares = int(POSITION_SIZE / current_price)
    actual_position = shares * current_price

    print(f"\nTrade plan:")
    print(f"  Symbol: {SYMBOL}")
    print(f"  Direction: SHORT")
    print(f"  Shares: {shares} @ ${current_price:.2f} = ${actual_position:.0f}")
    print(f"  Stop loss: {STOP_LOSS_PCT}% (stop at ${current_price * (1 + STOP_LOSS_PCT/100):.2f})")
    print(f"  Take profit: {TAKE_PROFIT_PCT}% (target ${current_price * (1 - TAKE_PROFIT_PCT/100):.2f})")
    print(f"  Hold: {HOLD_DAYS} trading days")

    if args.dry_run:
        print(f"\n[DRY RUN] Would short {shares} shares of {SYMBOL} at ${current_price:.2f}")
        return 0

    if not args.yes:
        confirm = input(f"\nConfirm SHORT {shares} shares of {SYMBOL}? [y/N] ")
        if confirm.lower() != 'y':
            print("Aborted.")
            return 0

    # Set trigger to immediate
    db.update_hypothesis_fields(hyp_id,
        trigger='immediate',
        trigger_position_size=POSITION_SIZE,
        trigger_stop_loss_pct=STOP_LOSS_PCT,
        trigger_take_profit_pct=TAKE_PROFIT_PCT,
        expected_symbol=SYMBOL,
    )

    # Execute trade
    result = trader.place_order(
        symbol=SYMBOL,
        side='sell',  # short
        qty=shares,
        hypothesis_id=hyp_id,
        stop_loss_pct=STOP_LOSS_PCT,
        take_profit_pct=TAKE_PROFIT_PCT,
    )

    if result:
        print(f"\n✓ SHORT {shares} shares of {SYMBOL} placed successfully")
        print(f"  Order ID: {result.get('order_id', 'unknown')}")

        db.log_trade(
            type='activate',
            hypothesis_id=hyp_id,
            symbol=SYMBOL,
            direction='short',
            entry_price=current_price,
            position_size=POSITION_SIZE,
            order_id=result.get('order_id'),
            trigger_type='manual_fda_rejection'
        )
    else:
        print(f"\nERROR: Trade placement failed.")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
