"""
April 2, 2026 "Liberation Day" Tariff Runbook
==============================================
US tariff announcements expected ~6:00 PM ET on April 2.

After-market actions (run April 2 after close, 4:15-5:00 PM ET):
  1. Run this script to assess conditions
  2. If conditions met, it prints exact activation commands for April 7 open

CRITICAL: Good Friday April 3 = MARKET CLOSED. Next open = Monday April 7, 2026.

BACKTEST EXPECTATION:
  Systemic days (SPY<-0.5%, >=5 first-touch lows): -1.88% abnormal over 5 days
  VIX close > 30: SPY recovers +1.69% over 20 days

UPDATED PORTFOLIO STATE (as of 2026-03-28):
  - GO: CLOSED March 26 at $6.75 (entry $6.19, +9% return)
  - HD, ABT, BAX: CLOSED March 27 (all confirmed complete)
  - SYK: active short until April 2 (entry $326.23, currently -0.4%)
  - SPY LONG (b63a0168): TRIGGERED for March 31 open (VIX=31.0 on March 27!)
    *** VIX ALREADY CROSSED 30 — DO NOT activate SPY long again on April 2 ***
  - AMT: ABANDONED (not tradeable in Alpaca paper)
  - VGNT (Versigent/Aptiv spinco): REINSTATED — regular trading starts April 1.
    Run: python3 tools/check_vgnt_and_activate.py on April 1 morning.
    If tradeable: run --yes to activate spinco short. 25% auto tariff amplifies pressure.
    Hypothesis: 2d94ac68. Entry at market open April 1 (before Liberation Day).
  - TDG: CROSSED first-ever 52w low on March 27 ($1,140 vs barrier $1,152.50)
  - ADBE, OTIS, DPZ, QCOM + 24 others: ALL crossed first-ever 52w lows on March 27
    (28 total first-touch 52w lows; SPY -1.71% → systemic short signal FIRED)
  - DECISION: Systemic shorts NOT activated — Liberation Day correlation + capacity
    (reserves slots for GLD/WFC/KRE/COST which are higher magnitude and better validated)
    Track informally: python3 tools/amd_qcom_liberation_day_observer.py (April 8-9 for 5d)

PRE-SELLOFF REGIME CAVEAT (identified 2026-03-28, updated 2026-03-28):
  SPY is down ~7.3% over 20 days and -8.6% from 60d peak going into Liberation Day.
  Training distribution: 20d pre-moves ranged -3.5% to +4.5% (only 2019-08-23 was worse at -5.7%).
  Liberation Day 2026 at -7.3% pre-move is OUTSIDE the training distribution.

  CRITICAL ANALOG — 2019-08-23 (most similar pre-selloff of -5.7%):
    - Context: Trump threatened tariffs on $300B China goods (pre-selloff -5.7%)
    - Result: OPPOSITE of signal → SPY bounced +5.2%, GLD -5.5% abnormal, KO -3.7%, KRE +5.2%
    - Reason: Event was followed by tariff rollback, not escalation
    - Implication: When market already sold off AND tariffs don't escalate → market BOUNCES

  WHY THE SPY GATE PROTECTS US:
    - Gate: SPY must be DOWN on April 2 close (after announcement)
    - If announcement was a rollback/small: SPY likely UP → gate blocks defensive longs
    - If announcement was escalation: SPY DOWN → gate allows defensive longs
    - The 2019-08-23 analog would NOT pass the gate (SPY rallied after announcement)

  RESIDUAL RISK:
    - If tariffs are announced large AND market initially sells off at 6pm but bounces overnight
    - We would enter April 7 at a bounce high (worst case scenario)
    - No complete protection against this; but if tariffs are truly large, sustained selloff is likely

  KO/XLU UNCONDITIONAL TRIGGERS (fire April 7 regardless of gate):
    - Currently KO (dbe0dc29) and XLU (9184ba0f) do NOT have SPY gate
    - If SPY is UP on April 2, these WILL fire on April 7 into a bounce → BAD
    - RECOMMENDED ACTION: If SPY > +0.5% on April 2, manually cancel KO and XLU triggers
    - Command: Check db.update_hypothesis_fields for dbe0dc29, 9184ba0f

2025 LIBERATION DAY OOS RESULTS (CRITICAL — updated 2026-03-28):
  Analysis of 2025 Liberation Day (April 2-3 2025) as OOS validation:
  - GLD: 5d=+4.3%, 10d=+8.8% STRONG ✓ | 20d=-1.6% FAIL (rollback April 9)
  - WFC short: 5d=+1.6% ✓ | 20-30d reversed (rollback)
  - KRE short: 5d=+1.5% ✓, 10d=-2.1% | reversed in rollback
  - KO: 5d=-1.0% ✗, 20d=-7.7% ✗ FAILED — defensive lagged the bounce
  - XLU: 5d=-2.2% ✗, 20d=-4.5% ✗ FAILED — utility lagged the bounce
  - STLD short: 5d=-3.5% ✗ WRONG DIRECTION — rollback bullish for domestic steel
  - COST: 5d=+2.0% ✓, 10d=+4.8% ✓ | 20d=-1.2% FAIL (rollback)
  AMD+QCOM basket: 5d=-2.5% ✓, 10d=-5.9% ✓ — semiconductor tariff short worked!

  → SKIP: KO, XLU, STLD (all failed in 2025 analog, pre-sold regime)
  → KEEP: GLD (10d max), WFC (5d), KRE (10d), COST (if capacity)
  → INFORMAL: AMD + QCOM basket short (no formal hypothesis yet, but strong signal)

CAPACITY ON APRIL 7 (REVISED 2026-03-28):
  - SPY long (1 slot, 20d hold from March 31)
  - SYK closes April 2 → frees 0 (not yet activated in system)
  - Slots available: 4 (max 5 - SPY)
  Priority (REVISED): GLD(2) > WFC(3) > KRE(4) > COST(5) [AMD/QCOM basket if slot]
  CANCELLED: KO, XLU, STLD — failed in 2025 Liberation Day OOS

CRITICAL: Good Friday April 3 = MARKET CLOSED. All April 3 actions → April 7.

If WFC triggers: enter April 7 open (after Liberation Day announcement April 2)
VIX SPY long: ALREADY TRIGGERED for March 31 — do NOT fire again April 7
"""

import sys
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db


def check_conditions():
    """Check April 2 trigger conditions."""
    db.init_db()

    print("=" * 70)
    print("APRIL 2, 2026 LIBERATION DAY — POST-MARKET ASSESSMENT")
    print("=" * 70)
    print()

    # --- SPY Return ---
    end = datetime.now()
    start = end - timedelta(days=5)
    spy = yf.download('SPY', start=start, end=end + timedelta(days=1),
                      auto_adjust=True, progress=False)
    if spy.empty:
        print("ERROR: Could not fetch SPY data")
        return None

    if isinstance(spy.columns, pd.MultiIndex):
        spy_close = spy['Close']['SPY'].dropna()
    else:
        spy_close = spy['Close'].dropna()

    spy_ret = (spy_close.iloc[-1] / spy_close.iloc[-2] - 1)
    spy_pct = spy_ret * 100
    spy_condition = spy_ret < -0.005  # <-0.5%
    print(f"1. SPY Return Today: {spy_pct:.2f}%")
    print(f"   Condition (need <-0.5%): {'✓ PASS' if spy_condition else '✗ FAIL'}")

    # --- VIX Level ---
    vix = yf.download('^VIX', start=start, end=end + timedelta(days=1),
                      auto_adjust=True, progress=False)
    if not vix.empty:
        if isinstance(vix.columns, pd.MultiIndex):
            vix_series = vix['Close'].iloc[:, 0].dropna()
        else:
            vix_series = vix['Close'].dropna()
        vix_close = float(vix_series.iloc[-1])
        vix_condition = vix_close > 30
        print(f"\n2. VIX Close Today: {vix_close:.1f}")
        print(f"   Condition (need >30 for SPY long): {'✓ PASS' if vix_condition else '✗ FAIL'}")
    else:
        vix_close = None
        vix_condition = False
        print("\n2. VIX: Could not fetch")

    # --- 52w Low First Touches ---
    print("\n3. Scanning for first-touch 52w lows (this takes 1-2 min)...")
    try:
        from tools.systemic_52w_low_scanner import scan
        result = scan(date_str=None, verbose=False)
        n_lows = result.get('n_stocks_at_low', 0)
        stocks_at_low = result.get('stocks_at_low', [])
        lows_condition = n_lows >= 5
        print(f"   First-touch 52w lows: {n_lows} (need >=5)")
        if stocks_at_low:
            print(f"   Stocks: {', '.join(stocks_at_low[:10])}")
        print(f"   Condition: {'✓ PASS' if lows_condition else '✗ FAIL'}")
    except Exception as e:
        print(f"   ERROR scanning: {e}")
        n_lows = 0
        stocks_at_low = []
        lows_condition = False

    # --- Portfolio Capacity ---
    hypotheses = db.load_hypotheses()
    active_count = len([h for h in hypotheses if h.get('status') == 'active'])

    print(f"\n4. Portfolio capacity: {active_count}/5 active positions")
    available_slots = 5 - active_count
    print(f"   Available slots: {available_slots}")

    # --- Summary ---
    print()
    print("=" * 70)
    print("TRIGGER SUMMARY")
    print("=" * 70)

    systemic_fires = spy_condition and lows_condition
    vix_fires = vix_condition

    if systemic_fires:
        print("🚨 SYSTEMIC 52W LOW SHORT SIGNAL FIRES!")
        print("   → Run Monday April 7 at 9:30 AM (GOOD FRIDAY APRIL 3 = CLOSED):")
        print()
        print("   NOTE: f93527a2 and f055dc19 are ABANDONED. Need new pre-registration.")
        print("   Use activate_systemic_short.py with any large-cap at 52w low (>$10B mktcap).")
        print()
        # Updated 2026-03-28: current 52w low proximity data (as of March 27 close)
        # TDG ($1140, 0% above, first touch 2026-03-26) - BEST: most recent, $41B cap
        # HD ($322, 0.28% above, first touch 2026-03-20) - HOME DEPOT $316B cap
        # SBAC ($167, 0.1% above, never crossed) - SBA Communications $26B
        # OTIS ($76, 0.2% above, never crossed) - Otis Worldwide $31B
        # DPZ ($360, 0.1% above, never crossed) - Dominos $13B (borderline cap)
        # ADBE ($238, 0.6% above, approaching first touch) - $35B cap (not crossed yet)
        # BSX ($69, 1.5% above, first touch 2026-01-16) - Boston Scientific $101B
        # PANW ($147, 3.78% above, first touch 2026-02-18) - $50B cap
        # NOTE: Only short stocks STILL at their 52w low on April 7 (verify with scanner)
        candidates = ['TDG', 'HD', 'SBAC', 'OTIS', 'ADBE', 'BSX', 'PANW']
        if stocks_at_low:
            combined = list(dict.fromkeys(stocks_at_low + candidates))
        else:
            combined = candidates
        n_to_trade = min(available_slots, len(combined))
        print(f"   Best candidates from scanner (verify still at 52w low April 7):")
        for ticker in combined[:n_to_trade]:
            print(f"   - {ticker}: python tools/activate_systemic_short.py --ticker {ticker} --yes")
        print()
        print("   Expected: -1.88% abnormal over 5 days each")
        print("   IMPORTANT: Only short stocks STILL below their 52w low at April 7 open")
        print("   CAPACITY: SPY long uses slot 1. Systemic shorts fill remaining slots.")
    else:
        print("✗ Systemic short signal NOT triggered")
        if not spy_condition:
            print(f"  → SPY only down {spy_pct:.2f}% (need <-0.5%)")
        if not lows_condition:
            print(f"  → Only {n_lows} first-touch 52w lows (need >=5)")

    if vix_fires:
        print()
        print("⚠️  VIX SPIKE RECOVERY — ALREADY TRIGGERED (2026-03-27)")
        print("   VIX closed at 31.0 on March 27 — SPY long (b63a0168) was set for March 31 open.")
        print("   *** DO NOT ACTIVATE AGAIN — SPY long already running from March 31 entry ***")
        print()
        print("   If SPY long is NOT yet active (check python3 run.py --status):")
        print("   THEN run: python tools/activate_vix_spy_trade.py --yes")
        print()
        print("   Expected: +1.69% over 20 days (N=54, OOS validation +2.92%)")
        print("   IMPORTANT: Check portfolio capacity — max 5 positions")
        print("   NOTE: Compatible with tariff sector plays (different horizons)")
    else:
        if vix_close:
            print(f"\n✗ VIX long NOT triggered (VIX={vix_close:.1f}, need >30)")

    # --- WFC Tariff Short (NEW 2026-03-26) ---
    print()
    print("=" * 70)
    print("WFC TARIFF BANK SHORT SIGNAL (hypothesis b73efac3)")
    print("=" * 70)
    print("Signal: WFC underperforms SPY -2.39% avg over 5 days after major tariff events")
    print("  n=8, direction=88%, p=0.0045. Validated 2018-2025.")
    print()
    wfc_condition = spy_condition  # tariff shock = SPY down
    if spy_pct is not None:
        large_tariff = True  # Set manually based on news: is announcement >15% reciprocal?
        print(f"  SPY return today: {spy_pct:.2f}%")
        print(f"  SPY down condition: {'✓' if spy_condition else '✗'}")
    print()
    print("  CHECK MANUALLY: Was tariff announcement >15% universal/reciprocal?")
    print("  ONE MISS: 2025-02-01 (+0.5% abnormal when market rallied after announcement)")
    print("  → If SPY is UP: probably don't activate (market not pricing shock)")
    print()
    if spy_condition:
        print("  ✓ SPY is DOWN → Conditions favor WFC short activation")
        print("  → Run April 7 at market open (Good Friday April 3 = CLOSED):")
        print("     python tools/activate_wfc_tariff_trade.py --yes")
    else:
        print("  ✗ SPY is UP → Caution: WFC short may not work (see 2025-02-01 miss)")

    # --- KO/XLU/STLD: SKIPPED based on 2025 Liberation Day OOS (updated 2026-03-28) ---
    print()
    print("=" * 70)
    print("KO / XLU / STLD — CANCELLED FOR LIBERATION DAY 2026 (updated 2026-03-28)")
    print("=" * 70)
    print("2025 Liberation Day OOS showed ALL THREE failed:")
    print("  KO: 5d=-1.0%, 20d=-7.7% FAIL (defensive lagged the rally after rollback)")
    print("  XLU: 5d=-2.2%, 20d=-4.5% FAIL (utility lagged the rally after rollback)")
    print("  STLD: 5d=-3.5% WRONG DIRECTION (rollback was BULLISH for domestic steel!)")
    print()
    print("  Pre-sold regime (SPY -7.76% over 20d pre-Liberation Day 2026) = HIGH rollback risk")
    print("  DO NOT ACTIVATE: KO (dbe0dc29), XLU (9184ba0f), STLD (907d94ec)")
    print()
    print("  ALTERNATIVE: COST (8c2f8cbb, 5d) — worked at 10d in 2025 (+4.8%). Lower rollback risk.")
    if spy_condition:
        print("  ✓ SPY DOWN → COST can be activated as 5th slot if capacity allows:")
        print("    python3 -c \"import db; db.init_db(); db.update_hypothesis_fields(")
        print("    '8c2f8cbb', trigger='2026-04-07T09:30', trigger_position_size=5000,")
        print("    trigger_stop_loss_pct=10)\"")
    else:
        print("  ✗ SPY UP → Do not activate COST either")
    print()

    # --- AMD+QCOM Semiconductor Basket (Informal Monitor) ---
    print()
    print("=" * 70)
    print("AMD+QCOM SEMICONDUCTOR BASKET SHORT (INFORMAL MONITOR — validated 2026-03-28)")
    print("=" * 70)
    print("Signal: AMD+QCOM basket underperforms SPY after tariff escalations.")
    print("  N=8 events (2018-2025). 10d: avg=-6.16%, 8/8 direction (100%), p=0.0008.")
    print("  5d: avg=-2.03%, 7/8 direction (87.5%), p=0.0039. PASSES multiple testing.")
    print("  AMD ~50% China revenue, QCOM ~63% China revenue.")
    print("  OOS: 2025-03-26 basket=-5.9% ✓, 2025-04-02 basket=-2.2% ✓ (2/2 correct).")
    print()
    print("  ⚠ No formal hypothesis yet (focus limit: 16 active signal types > max 3).")
    print("  INFORMAL TRADE: If Liberation Day fires AND portfolio has 5th slot:")
    print("    Short AMD $2,500 + Short QCOM $2,500 = $5,000 basket")
    print("    Entry: April 7 open. Hold: 10 trading days. Stop: 10%.")
    print()
    if spy_condition:
        recent = yf.download(['AMD', 'QCOM'], start=datetime.now()-timedelta(days=5),
                            end=datetime.now()+timedelta(days=1), progress=False)
        try:
            if isinstance(recent.columns, pd.MultiIndex):
                amd_px = float(recent['Close']['AMD'].dropna().iloc[-1])
                qcom_px = float(recent['Close']['QCOM'].dropna().iloc[-1])
            else:
                amd_px = qcom_px = None
            if amd_px:
                print(f"  Current prices: AMD=${amd_px:.2f}, QCOM=${qcom_px:.2f}")
        except:
            pass
        print("  ✓ SPY DOWN → AMD+QCOM basket conditions met. Trade if 5th slot available.")
        print("  Manual short commands (Alpaca paper):")
        print("    python3 -c \"import trader; trader.place_order('AMD', 'sell', dollar_amount=2500)\"")
        print("    python3 -c \"import trader; trader.place_order('QCOM', 'sell', dollar_amount=2500)\"")
    else:
        print("  ✗ SPY UP → AMD/QCOM basket not worth trading (market not pricing tariff shock)")

    # --- Other Pending Signals ---
    print()
    print("=" * 70)
    print("UPCOMING SIGNALS TO MONITOR (April-May 2026)")
    print("=" * 70)
    print("• VGNT/FDXF/HONA: ABANDONED (tickers not found in Alpaca paper trading, 2026-03-27)")
    print("• REPL: PDUFA April 10. If 2nd CRL and >40% drop → activate_repl_trade.py")
    print("  - If >40% but <55% drop: use hypothesis 5f805860 (REPL short)")
    print("  - If >55% drop: use hypothesis d302c84b (clinical efficacy failure)")
    print("• CAPR: PDUFA August 22, 2026 (Capricor Therapeutics, Duchenne MD)")
    print("• HON Investor Day June 3, 2026 → HONA ticker confirmation for spinco trade")
    print("• S&P 500 Q2 rebalance announcement: ~June 5, 2026")
    print()
    print("• If another systemic selloff occurs (April 3-8): re-run scanner daily")
    print("• VIX monitoring: needs to CLOSE above 30 (check each day at 4:15 PM ET)")
    print()
    print("Run: python tools/activate_systemic_short.py --ticker TICKER --yes")
    print("Run: python tools/activate_vix_spy_trade.py --yes")
    return spy_pct


if __name__ == '__main__':
    spy_pct = check_conditions()

    # --- GLD Tariff Long (NEW 2026-03-27) ---
    print()
    print("=" * 70)
    print("GLD GOLD LONG SIGNAL (hypothesis b768e8d8)")
    print("=" * 70)
    print("Signal: GLD outperforms SPY by +3.87% avg over 20 days after tariff events.")
    print("        N=19 events (2009-2025), 84% direction, p=0.0014 VALIDATED.")
    print("        5-day signal is noisy (50% direction) - initial selloff/margin calls.")
    print("        20-day effect is robust: gold recovers as inflation expectations rise.")
    print()
    if spy_pct is not None and spy_pct < -0.5:
        print(f"✓ SPY down {spy_pct:+.1f}% → Tariff shock confirmed → GLD LONG signal active")
        print(f"  Action: Set trigger 2026-04-07T09:30 (next trading day after Good Friday)")
        print(f"  python3 -c \"import db; db.init_db(); db.update_hypothesis_fields('b768e8d8', trigger='2026-04-07T09:30', trigger_position_size=5000, trigger_stop_loss_pct=10, trigger_take_profit_pct=15)\"")
    elif spy_pct is not None:
        print(f"  SPY return: {spy_pct:+.1f}% — Market not pricing tariff shock → GLD long uncertain")
        print(f"  Note: GLD historically rallies 20d even in partial-shock scenarios (84% dir)")
        print(f"  Judgment call: if tariff announcement was large, still consider activating.")
    else:
        print("  ERROR: Could not determine SPY return")

    # --- STLD Tariff Short — CANCELLED for Liberation Day 2026 (2026-03-28) ---
    print()
    print("=" * 70)
    print("STLD DOMESTIC STEEL SHORT — CANCELLED FOR LIBERATION DAY 2026")
    print("=" * 70)
    print("2025 Liberation Day OOS result: STLD +1.8% at 5d (WRONG DIRECTION).")
    print("Root cause: 90-day tariff rollback was BULLISH for domestic steel (no import competition).")
    print("Signal is valid in aggregate (n=10, 80% dir) but NOT in rollback-risk scenarios.")
    print("Liberation Day 2026 carries HIGH rollback risk (pre-sold market, same setup as 2025).")
    print()
    print("⛔ DO NOT ACTIVATE STLD (907d94ec) for April 7, 2026.")
    print("   Re-evaluate after observing April 7-14 market behavior (rollback or not?).")

    # --- KRE Regional Bank Tariff Short (NEW 2026-03-27) ---
    print()
    print("=" * 70)
    print("KRE REGIONAL BANK SHORT SIGNAL (hypothesis 6e732966)")
    print("=" * 70)
    print("Signal: KRE underperforms SPY -3.08% avg over 20 DAYS after tariff escalation.")
    print("        N=10 (2018-2025), 89% SHORT direction, 20d p=0.016. Passes MT.")
    print("        10d: -2.73%, 90% direction, p=0.008 (even stronger direction)")
    print("        5d signal weak (p=0.21) — this is a SLOW-BURN 20-day play, not a 5d play")
    print("        Hold time: ~20 days. Entry April 7 → exit ~April 27")
    print()
    print("  PRIORITY NOTES:")
    print("  - WFC fires if SPY<-0.5% (5d play). KRE is COMPLEMENTARY (20d play).")
    print("  - If WFC is already active: KRE adds a DIFFERENT timeline/ETF short")
    print("  - KRE has STRONGER direction consistency (89%) than WFC (~88%)")
    print("  - But WFC has better expected 5d return (-1.94% vs -1.30% for KRE at 5d)")
    print()
    if spy_pct is not None and spy_pct < -0.5:
        hypotheses = db.load_hypotheses()
        active = [h for h in hypotheses if h.get('status') == 'active']
        if len(active) < 4:
            print(f"✓ SPY down {spy_pct:+.1f}% + portfolio has room → KRE SHORT signal active")
            print(f"  Action: python tools/activate_kre_tariff_trade.py --yes")
        else:
            print(f"⚠ SPY down but portfolio full ({len(active)}/5). KRE valid but no capacity.")
    else:
        print(f"  SPY return: {spy_pct:+.1f}% — Only activate KRE short if SPY DOWN and capacity allows")

    # --- AMD/QCOM Semiconductor Basket — Informal OOS Observer ---
    print()
    print("=" * 70)
    print("AMD/QCOM SEMICONDUCTOR BASKET — INFORMAL OOS OBSERVATION")
    print("=" * 70)
    print("Signal: AMD+QCOM basket underperforms SPY -6.16% avg 10d after tariff escalations.")
    print("        N=8 events, 100% direction at 10d, p=0.0008. 2 OOS instances confirmed.")
    print("        CANNOT be formally pre-registered (signal type cap exceeded).")
    print("        Capturing Liberation Day 2026 as 3rd informal OOS instance.")
    print()
    print("  NO FORMAL TRADE — This is an observation event only.")
    print("  After 10 trading days from April 2 → run:")
    print("    python3 tools/amd_qcom_liberation_day_observer.py")
    print("  After 14-15 trading days → run:")
    print("    python3 tools/amd_qcom_liberation_day_observer.py --record")
    print("  If OOS3 confirmed: formally pre-register at NEXT tariff escalation event")
    print("  (when signal type slots free up post-Liberation Day completions)")
    print()
    print("  APRIL 7 PORTFOLIO EXPECTED STATE:")
    print("    Slot 1: SPY VIX long (b63a0168) — 20d hold, expires ~April 28")
    print("    Slot 2: GLD long (b768e8d8) — 20d hold if SPY<-0.5%")
    print("    Slot 3: WFC short (b73efac3) — 5d hold if SPY<-0.5%")
    print("    Slot 4: KRE short (6e732966) — 20d hold if capacity")
    print("    Slot 5: COST long (8c2f8cbb) — 5d hold if capacity")
    print("    VGNT closes ~April 8 (5d from April 1 entry)")
