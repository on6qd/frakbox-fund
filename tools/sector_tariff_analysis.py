"""
Sector ETF performance after major US tariff escalation announcements.
Measures abnormal returns vs SPY across multiple horizons.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_data

TARIFF_DATES = [
    '2018-01-22',  # Solar/washing machine tariffs (first salvo)
    '2018-03-22',  # Steel/aluminum tariffs announced
    '2018-06-15',  # $50B China tariffs list published
    '2018-07-06',  # $34B China tariffs go into effect
    '2018-09-17',  # $200B China tariffs announced
    '2019-05-10',  # Tariff rate raised 10%->25% on $200B
    '2019-08-01',  # 10% tariff on remaining $300B announced
    '2025-01-20',  # Trump inauguration - tariff threats
    '2025-02-01',  # First actual tariffs: Canada/Mexico/China
    '2025-03-04',  # Escalation
]

ETFS = ['XLI', 'XLY', 'XLB', 'XLE', 'XLV', 'XLK', 'XLC']
ETF_NAMES = {
    'XLI': 'Industrials',
    'XLY': 'Consumer Discretionary',
    'XLB': 'Materials',
    'XLE': 'Energy',
    'XLV': 'Healthcare',
    'XLK': 'Technology',
    'XLC': 'Communication Services',
}

HORIZONS = ['1d', '3d', '5d', '10d', '20d']

# Known benchmark: WFC signal
WFC_SIGNAL = {'symbol': 'WFC', 'horizon': '5d', 'mean_abnormal': -1.9, 'p_value': 0.015}

print("=" * 70)
print("SECTOR ETF TARIFF ESCALATION ANALYSIS")
print(f"Dates: {len(TARIFF_DATES)} events | Horizons: {HORIZONS}")
print("Benchmark: SPY (abnormal returns = ETF - SPY)")
print("=" * 70)

results_summary = []

for etf in ETFS:
    print(f"\n--- {etf} ({ETF_NAMES[etf]}) ---")
    try:
        result = market_data.measure_event_impact(
            event_dates=TARIFF_DATES,
            symbol=etf,
            entry_price='open'
        )

        if result is None or 'error' in result:
            print(f"  ERROR: {result}")
            continue

        # The result dict has top-level keys plus per-horizon stats
        # Horizons are stored in result['stats'] or directly as result['1d'] etc.
        # Let's inspect the keys
        top_keys = list(result.keys())

        etf_summary = {
            'symbol': etf,
            'name': ETF_NAMES[etf],
            'horizons': {}
        }

        best_p = 1.0
        best_horizon = None
        horizons_under_05 = 0
        horizons_under_01 = 0

        # Extract per-event abnormal returns from individual_impacts
        individual_impacts = result.get('individual_impacts', [])
        n_events = result.get('events_measured', len(individual_impacts))

        for h in HORIZONS:
            mean_ab = result.get(f'avg_abnormal_{h}')
            p_val = result.get(f'p_value_abnormal_{h}')
            median_ab = result.get(f'median_abnormal_{h}')

            # Direction consistency from positive_rate (% positive)
            pct_positive = result.get(f'positive_rate_abnormal_{h}')
            pct_negative = (100.0 - pct_positive) if pct_positive is not None else None

            # Collect individual abnormal returns for this horizon
            ab_key = f'abnormal_{h}'
            individual = [imp.get(ab_key) for imp in individual_impacts if imp.get(ab_key) is not None]
            # Recompute pct_negative from raw individual if available
            if individual:
                pct_negative = sum(1 for x in individual if x < 0) / len(individual) * 100

            # Store
            etf_summary['horizons'][h] = {
                'mean_abnormal': mean_ab,
                'median_abnormal': median_ab,
                'p_value': p_val,
                'n': n_events,
                'pct_negative': pct_negative,
            }

            # Track best p-value
            if p_val is not None and p_val < best_p:
                best_p = p_val
                best_horizon = h

            if p_val is not None and p_val < 0.05:
                horizons_under_05 += 1
            if p_val is not None and p_val < 0.01:
                horizons_under_01 += 1

            direction_str = f"{pct_negative:.0f}% neg" if pct_negative is not None else "n/a"
            mean_str = f"{mean_ab:+.2f}%" if mean_ab is not None else "n/a"
            p_str = f"p={p_val:.4f}" if p_val is not None else "p=n/a"
            print(f"  {h:>4}: mean={mean_str:>8}  {direction_str:>10}  {p_str}  n={n_events}")

        # Multiple testing: 2+ horizons at p<0.05, OR 1+ at p<0.01
        passes_mt = (horizons_under_05 >= 2) or (horizons_under_01 >= 1)
        etf_summary['best_p'] = best_p
        etf_summary['best_horizon'] = best_horizon
        etf_summary['passes_mt'] = passes_mt
        etf_summary['horizons_under_05'] = horizons_under_05
        etf_summary['horizons_under_01'] = horizons_under_01

        mt_str = "PASSES" if passes_mt else "fails"
        print(f"  --> Best: {best_horizon}  p={best_p:.4f}  | Multiple testing: {mt_str} ({horizons_under_05} horizons p<0.05, {horizons_under_01} p<0.01)")

        results_summary.append(etf_summary)

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()

# Final ranked summary
print("\n" + "=" * 70)
print("RANKED SUMMARY — Best downward signals (for short thesis)")
print("Sorted by best p-value at any horizon")
print("=" * 70)

# Sort by best p-value
results_summary.sort(key=lambda x: x.get('best_p', 1.0))

print(f"\n{'ETF':<5} {'Name':<26} {'Best H':<8} {'Mean Abn%':<12} {'%Neg':<8} {'p-val':<10} {'MT'}")
print("-" * 80)

for r in results_summary:
    h = r.get('best_horizon')
    if h and h in r['horizons']:
        hdata = r['horizons'][h]
        mean_ab = hdata.get('mean_abnormal')
        pct_neg = hdata.get('pct_negative')
        p_val = hdata.get('p_value')
        n = hdata.get('n')

        mean_str = f"{mean_ab:+.2f}%" if mean_ab is not None else "n/a"
        neg_str = f"{pct_neg:.0f}%" if pct_neg is not None else "n/a"
        p_str = f"{p_val:.4f}" if p_val is not None else "n/a"
        mt_str = "YES" if r['passes_mt'] else "no"
        nh = r['horizons_under_05']
        print(f"{r['symbol']:<5} {r['name']:<26} {h:<8} {mean_str:<12} {neg_str:<8} {p_str:<10} {mt_str} ({nh} horizons<0.05)")
    else:
        print(f"{r['symbol']:<5} {r['name']:<26} {'?':<8} {'n/a':<12} {'n/a':<8} {'n/a':<10} {'no'}")

# Comparison to known signals
print("\n" + "=" * 70)
print("COMPARISON TO KNOWN SIGNALS")
print(f"  WFC  (Banks, 5d):     mean={WFC_SIGNAL['mean_abnormal']:+.1f}%  p={WFC_SIGNAL['p_value']:.4f}  [CONFIRMED]")
print(f"  XLF  (Financials, ?): mean=-1.0%  [contaminated, borderline]")
print(f"  SOXX (Semis, ?):      mean=-2.1%  [borderline underpowered]")
print(f"  XLU  (Utilities, 20d):mean=+3.9%  [long signal, borderline underpowered]")
print("=" * 70)
