"""
VGNT Spinco Monitoring & Activation Script
==========================================
VGNT = Versigent, Aptiv EDS spinoff (automotive wiring/connectors)
Regular trading starts: April 1, 2026
Hypothesis ID: 2d94ac68 (spinco_institutional_selling_short)

Run this script on:
  - March 31 (Monday): Check if VGNT is in Alpaca yet
  - April 1 (Tuesday) morning: Confirm VGNT tradeable, update trigger to open entry
  - April 1 (Tuesday): Verify trade executed

Usage:
  python3 tools/check_vgnt_and_activate.py          # check status
  python3 tools/check_vgnt_and_activate.py --yes    # force activate if ready
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import trader

HYPOTHESIS_ID = '2d94ac68'
TICKER = 'VGNT'

def main():
    force = '--yes' in sys.argv
    db.init_db()
    
    print("=" * 60)
    print("VGNT SPINCO MONITORING — Versigent (Aptiv EDS)")
    print("=" * 60)
    
    # Check hypothesis status
    h = db.get_hypothesis_by_id(HYPOTHESIS_ID)
    if not h:
        print("ERROR: Hypothesis not found")
        return
    print(f"Hypothesis {HYPOTHESIS_ID[:8]}: status={h['status']}, trigger={h.get('trigger','None')}")
    
    # Check if VGNT is available in Alpaca
    try:
        api = trader.get_api()
        asset = api.get_asset(TICKER)
        tradeable = asset.tradable
        status = asset.status
        exchange = getattr(asset, 'exchange', 'unknown')
        print(f"\nVGNT in Alpaca: tradable={tradeable}, status={status}, exchange={exchange}")
        
        if not tradeable:
            print("\nVGNT is listed but NOT TRADEABLE. Wait until it becomes tradeable.")
            return
    except Exception as e:
        print(f"\nVGNT NOT YET AVAILABLE in Alpaca: {e}")
        print("\nVGNT.WI (when-issued) may still be the only available ticker.")
        try:
            asset_wi = api.get_asset('VGNT.WI')
            print(f"VGNT.WI: tradable={asset_wi.tradable}, status={asset_wi.status}")
        except:
            print("VGNT.WI also not found")
        print("\nACTION: Wait until April 1 for regular trading to begin.")
        return
    
    # Get current price
    try:
        quote = api.get_latest_trade(TICKER)
        current_price = float(quote.price)
        print(f"Current VGNT price: ${current_price:.2f}")
    except Exception as e:
        print(f"Could not get VGNT price: {e}")
        current_price = None
    
    # Check if we should activate
    print("\nBacktest signal: -5.69% abnormal return over 5d (73.9% direction, N=25)")
    print("Key OOS: VSNT -17.5% 5d, LION -19.9% 5d")
    print("Tariff context: 25% auto tariff amplifies institutional selling on auto supplier spinco")
    
    if h['status'] == 'pending':
        print(f"\nHypothesis is PENDING with trigger={h.get('trigger','None')}")
        
        if force:
            # Update trigger to next market open for best entry
            print("\nForce-activating: Setting trigger to next_market_open")
            db.update_hypothesis_fields(HYPOTHESIS_ID,
                trigger='next_market_open',
                notes=h.get('notes','') + f'\n2026-04-01: VGNT now tradeable in Alpaca. Trigger updated to next_market_open for immediate short entry.'
            )
            print("Trigger updated. Trade_loop will execute at next open.")
        else:
            print("\nVGNT is tradeable. Run with --yes to activate at next market open.")
    elif h['status'] == 'active':
        print(f"\nHypothesis already ACTIVE. Trade in progress.")
    else:
        print(f"\nHypothesis status: {h['status']}. No action needed.")
    
    print("\n" + "=" * 60)
    print("Liberation Day Note: If April 2 includes auto tariff escalation,")
    print("VGNT (automotive wiring supplier) may see additional pressure.")
    print("VGNT is Aptiv EDS, a component supplier to auto OEMs worldwide.")
    print("=" * 60)

if __name__ == '__main__':
    main()
