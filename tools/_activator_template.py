"""
CANONICAL ACTIVATOR TEMPLATE (2026-04-09)
==========================================
Copy this file when creating a new event-driven activator script.
This template encodes every guardrail the research agent has learned to the
2026-04-09 date. Deviating from the pattern must be justified in writing.

Mandatory guardrails:
1. Capacity check (max 5 active trades) — uses MAX(hypothesis DB, Alpaca positions)
2. Pre-event contamination check — aborts if stock is >20% below 30d peak
   heading into the event (catches priced-in signals / leaked news)
3. Signal precondition check — the actual signal trigger (52w low breach,
   earnings miss abnormal, etc.)
4. Catastrophic-move abort — never enter on >15% gap moves (too anomalous)
5. Dry-run and --yes support
6. Activate-then-order sequencing: hypothesis must be activated BEFORE order
7. Fail loudly if order fails after activation

Do not copy the DEPRECATED activator scripts (pg/pgr/sbac/syk/khc/adbe/amt/dpz/
otis) — they predate the shared pre-event helper and are marked DEPRECATED.

Usage:
    1. Copy this file to tools/activate_<symbol>_trade.py
    2. Update SYMBOL, HYPOTHESIS_ID, EVENT_DATE, signal thresholds
    3. Update the signal precondition block (replace 52w_low example)
    4. Run dry-run first: python tools/activate_<symbol>_trade.py --dry-run
"""
from __future__ import annotations
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import trader
import research
from tools.pre_event_contamination import check_pre_event_contamination


# ---- TEMPLATE CONSTANTS — REPLACE THESE ---------------------------------
SYMBOL = "XXX"
HYPOTHESIS_ID = "template"
EVENT_DATE = "2026-01-01"       # ISO date of catalyst (earnings, PDUFA, ...)
POSITION_SIZE = 5000            # $5k per experiment (fund-wide constant)
STOP_LOSS_PCT = 8.0
TAKE_PROFIT_PCT = 15.0
DIRECTION = "short"             # or "long"

# Example: 52w-low catalyst short precondition
SIGNAL_52W_LOW = None           # e.g. 137.62; set to None to skip
MIN_CATALYST_ABN_PCT = 2.0      # Minimum abnormal decline on catalyst day
MAX_CATASTROPHIC_MOVE_PCT = 15.0 # Never enter on >15% single-bar moves
# ------------------------------------------------------------------------


def get_current_price(symbol: str) -> float | None:
    """Fetch live price; returns None on failure (caller must abort)."""
    try:
        from tools.yfinance_utils import safe_download
        import pandas as pd
        from datetime import date, timedelta
        df = safe_download(symbol, start=(date.today()-timedelta(days=3)).isoformat(),
                           end=(date.today()+timedelta(days=1)).isoformat(),
                           interval='1d')
        if df is not None and not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception as e:
        print(f"  Warning: price fetch failed: {e}")
    return None


def get_spy_change_pct() -> float:
    """SPY % change vs prior close, for abnormal-return calculation."""
    try:
        from tools.yfinance_utils import get_close_prices
        from datetime import date, timedelta
        s = get_close_prices('SPY', (date.today()-timedelta(days=10)).isoformat(),
                             (date.today()+timedelta(days=1)).isoformat())
        if s is not None and len(s) >= 2:
            prior = float(s.iloc[-2])
            current = float(s.iloc[-1])
            return (current - prior) / prior * 100
    except Exception as e:
        print(f"  Warning: SPY change fetch failed: {e}")
    return 0.0


def check_capacity() -> int:
    """Max of hypothesis-DB active count and Alpaca position count."""
    db.init_db()
    hyps = db.load_hypotheses()
    hyp_count = sum(1 for h in hyps if h.get('status') == 'active')
    try:
        alpaca_count = len(trader.get_positions())
    except Exception:
        alpaca_count = 0
    if alpaca_count > hyp_count:
        print(f"  [WARNING] Alpaca has {alpaca_count} positions but hypothesis DB has {hyp_count}")
    return max(hyp_count, alpaca_count)


def main() -> int:
    parser = argparse.ArgumentParser(description=f'Activate {SYMBOL} {DIRECTION} trade')
    parser.add_argument('--dry-run', action='store_true', help='Simulate without placing order')
    parser.add_argument('--price', type=float, default=None, help='Override entry price')
    parser.add_argument('--prior-close', type=float, default=None, help='Override prior close')
    parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    print("=" * 65)
    print(f"{SYMBOL} {DIRECTION.upper()} ACTIVATION — hyp {HYPOTHESIS_ID}")
    print("=" * 65)
    print(f"Event date: {EVENT_DATE}")

    # 1. Capacity check
    active_count = check_capacity()
    print(f"Active trades: {active_count}/5")
    if active_count >= 5:
        print("ABORT: Portfolio at capacity")
        return 1

    # 2. Entry price
    if args.price is not None:
        entry_price = args.price
        print(f"Entry (override): ${entry_price:.2f}")
    else:
        entry_price = get_current_price(SYMBOL)
        if entry_price is None:
            print("ABORT: could not fetch live price; pass --price manually")
            return 1
        print(f"Entry (live): ${entry_price:.2f}")

    # 3. PRE-EVENT CONTAMINATION CHECK — REQUIRED for every event-driven short.
    #    Catches cases where the stock has already priced in the bad news.
    ok, drawdown_pct, msg = check_pre_event_contamination(
        SYMBOL, event_date=EVENT_DATE,
        prior_close=args.prior_close,
    )
    print(f"Pre-event contamination: {msg}")
    if not ok:
        print(f"ABORT: {msg}")
        return 1

    # 4. Signal precondition (example: 52w low breach)
    if SIGNAL_52W_LOW is not None:
        if DIRECTION == 'short' and entry_price >= SIGNAL_52W_LOW:
            print(f"ABORT: {SYMBOL} ${entry_price:.2f} >= 52w low ${SIGNAL_52W_LOW:.2f} — signal not triggered")
            return 1

    # 5. Catastrophic move check
    if args.prior_close:
        gap_pct = (entry_price - args.prior_close) / args.prior_close * 100
        if abs(gap_pct) > MAX_CATASTROPHIC_MOVE_PCT:
            print(f"ABORT: catastrophic move {gap_pct:+.1f}% — manual review required")
            return 1

    # 6. Abnormal return check (replaces with signal-specific threshold)
    spy_pct = get_spy_change_pct()
    if args.prior_close:
        gap_pct = (entry_price - args.prior_close) / args.prior_close * 100
        abn = gap_pct - spy_pct
        print(f"Abnormal: {abn:+.2f}% (gap {gap_pct:+.2f}%, SPY {spy_pct:+.2f}%)")
        if DIRECTION == 'short' and abn > -MIN_CATALYST_ABN_PCT:
            print(f"WARNING: abnormal above -{MIN_CATALYST_ABN_PCT}% threshold")
            if not args.yes:
                if input("Proceed anyway? (yes/no): ").strip().lower() != 'yes':
                    return 0

    if args.dry_run:
        print(f"[DRY RUN] Would activate {HYPOTHESIS_ID} at ${entry_price:.2f}")
        return 0

    # 7. Confirmation
    if not args.yes:
        if input("Place trade? (yes/no): ").strip().lower() != 'yes':
            return 0

    # 8. Activate hypothesis first — if this fails, do NOT place order
    try:
        research.activate_hypothesis(HYPOTHESIS_ID,
                                     entry_price=entry_price,
                                     position_size=POSITION_SIZE)
    except Exception as e:
        print(f"ERROR activating hypothesis: {e}")
        return 1

    # 9. Place order
    try:
        result = trader.place_experiment(
            symbol=SYMBOL, direction=DIRECTION, notional_amount=POSITION_SIZE,
        )
        if not result.get('success'):
            print(f"ERROR placing order: {result.get('error')}")
            print("Hypothesis was activated but order FAILED. Manual reconcile required.")
            return 1
    except Exception as e:
        print(f"ERROR placing order: {e}")
        return 1

    print("TRADE ACTIVE")
    return 0


if __name__ == '__main__':
    sys.exit(main())
