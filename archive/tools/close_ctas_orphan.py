"""
Close CTAS Orphan Position
==========================
CTAS was shorted on 2026-03-23 as part of sp500_52w_low_momentum_short.
Hypothesis cc88dd18 was completed on 2026-03-24, but the Alpaca position
was NOT closed (orphan position). This script closes it.

Run at market open (9:30 AM ET):
  python tools/close_ctas_orphan.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trader
import yfinance as yf
from datetime import datetime

SYMBOL = 'CTAS'
ENTRY_PRICE = 179.30  # SHORT entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    print(f"=== CTAS Orphan Position Closer ===")
    print(f"Entry: SHORT {SYMBOL} @ ${ENTRY_PRICE}")

    # Get current price
    current_price = trader.get_current_price(SYMBOL)
    if not current_price:
        ticker = yf.Ticker(SYMBOL)
        hist = ticker.history(period='1d')
        if not hist.empty:
            current_price = float(hist['Close'].iloc[-1])

    if not current_price:
        print("ERROR: Could not get current price")
        return 1

    position_return_pct = (ENTRY_PRICE - current_price) / ENTRY_PRICE * 100
    print(f"Current price: ${current_price:.2f}")
    print(f"Return (short): {position_return_pct:+.2f}%")

    # Check position exists in Alpaca
    try:
        positions = trader.get_account_summary().get('positions', [])
        ctas_pos = next((p for p in positions if p['symbol'] == SYMBOL), None)
        if not ctas_pos:
            print("CTAS position not found in Alpaca - already closed?")
            return 0
        print(f"Alpaca: {ctas_pos['qty']} shares, side={ctas_pos['side']}, PnL={ctas_pos['unrealized_pl']:.2f}")
    except Exception as e:
        print(f"Warning: could not check Alpaca position: {e}")

    if args.dry_run:
        print("[DRY RUN] Would close CTAS position")
        return 0

    print("Closing CTAS position...")
    result = trader.close_position(SYMBOL)
    if result.get('success'):
        print(f"SUCCESS: Closed {SYMBOL} @ ${result.get('exit_price', current_price):.2f}")
        print(f"Note: Hypothesis cc88dd18 already marked completed - no DB update needed")
    else:
        print(f"FAILED: {result.get('error', 'unknown error')}")
        return 1

    return 0


if __name__ == '__main__':
    from config import load_env
    load_env()
    sys.exit(main())
