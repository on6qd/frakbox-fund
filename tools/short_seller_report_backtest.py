#!/usr/bin/env python3
"""
Short Seller Report Post-Announcement Drift Backtest

Tests whether stocks targeted by activist short sellers (Hindenburg, Muddy Waters)
continue to decline AFTER the initial report-day drop.

Entry: next-day open after report publication (we can't trade same-day)
Benchmark: SPY
Horizons: 1d, 3d, 5d, 10d, 20d abnormal returns

Hypothesis: Post-report drift of -2% to -5% abnormal over 5-10 days.
Causal mechanism: (1) New investigation findings take days to fully digest,
(2) Institutional selling cascades as compliance reviews trigger,
(3) Short interest increases create borrowing pressure.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import market_data
from tools.yfinance_utils import safe_download

# ── Compiled short seller report events (US-listed, 2020-2025) ─────────────
# Source: Hindenburg Research, Muddy Waters Research websites + Wikipedia
# Date = report publication date. Entry = next-day open.

EVENTS = [
    # Hindenburg Research
    {"symbol": "NKLA", "date": "2020-09-10", "source": "Hindenburg"},
    {"symbol": "CLOV", "date": "2021-02-04", "source": "Hindenburg"},
    {"symbol": "ORA",  "date": "2021-03-01", "source": "Hindenburg"},
    {"symbol": "RIDE", "date": "2021-03-12", "source": "Hindenburg"},
    {"symbol": "DKNG", "date": "2021-06-15", "source": "Hindenburg"},
    {"symbol": "SQ",   "date": "2023-03-23", "source": "Hindenburg"},
    {"symbol": "IEP",  "date": "2023-05-02", "source": "Hindenburg"},
    {"symbol": "FRHC", "date": "2023-09-26", "source": "Hindenburg"},
    {"symbol": "SMCI", "date": "2024-08-27", "source": "Hindenburg"},
    # Muddy Waters Research
    {"symbol": "EHTH", "date": "2020-04-08", "source": "MuddyWaters"},
    {"symbol": "NNOX", "date": "2020-09-22", "source": "MuddyWaters"},
    {"symbol": "MPLN", "date": "2020-11-11", "source": "MuddyWaters"},
    {"symbol": "YY",   "date": "2020-11-18", "source": "MuddyWaters"},
    {"symbol": "XL",   "date": "2021-03-03", "source": "MuddyWaters"},
    {"symbol": "LMND", "date": "2021-05-13", "source": "MuddyWaters"},
    {"symbol": "DNMR", "date": "2021-09-15", "source": "MuddyWaters"},
    {"symbol": "BEKE", "date": "2021-12-16", "source": "MuddyWaters"},
    {"symbol": "HASI", "date": "2022-07-12", "source": "MuddyWaters"},
    {"symbol": "DLO",  "date": "2022-11-16", "source": "MuddyWaters"},
    {"symbol": "KDNY", "date": "2023-05-16", "source": "MuddyWaters"},
    {"symbol": "ELF",  "date": "2024-11-20", "source": "MuddyWaters"},
    {"symbol": "FTAI", "date": "2025-01-15", "source": "MuddyWaters"},
    {"symbol": "APP",  "date": "2025-03-27", "source": "MuddyWaters"},
]

# Filter: only events where we can measure at least 10d of post-report data
# (cutoff: need at least 14 calendar days before today)
from datetime import datetime, timedelta
CUTOFF = datetime(2026, 3, 20)  # ~3 weeks ago
events_for_backtest = [
    {"symbol": e["symbol"], "date": e["date"]}
    for e in EVENTS
    if datetime.strptime(e["date"], "%Y-%m-%d") < CUTOFF
]

print(f"Events for backtest: {len(events_for_backtest)}")
print("Symbols:", [e["symbol"] for e in events_for_backtest])

# ── Run backtest ───────────────────────────────────────────────────────────
# entry_price="open" means we enter at the NEXT day's open after the event
result = market_data.measure_event_impact(
    event_dates=events_for_backtest,
    benchmark="SPY",
    entry_price="open",
    estimate_costs=True,
)

# ── Print summary ──────────────────────────────────────────────────────────
print("\n=== SHORT SELLER REPORT POST-DRIFT BACKTEST ===")
print(f"Events measured: {result.get('events_measured', 'N/A')}")
print(f"Data quality warning: {result.get('data_quality_warning', 'None')}")
print(f"Passes multiple testing: {result.get('passes_multiple_testing', 'N/A')}")

for horizon in ['1d', '3d', '5d', '10d', '20d']:
    avg_key = f'avg_abnormal_{horizon}'
    med_key = f'median_abnormal_{horizon}'
    pos_key = f'positive_rate_abnormal_{horizon}'
    p_key = f'wilcoxon_p_abnormal_{horizon}'
    raw_key = f'avg_raw_{horizon}'

    avg = result.get(avg_key, 'N/A')
    med = result.get(med_key, 'N/A')
    pos = result.get(pos_key, 'N/A')
    p = result.get(p_key, 'N/A')
    raw = result.get(raw_key, 'N/A')

    avg_s = f"{avg:.2f}%" if isinstance(avg, (int, float)) else avg
    med_s = f"{med:.2f}%" if isinstance(med, (int, float)) else med
    pos_s = f"{pos:.1f}%" if isinstance(pos, (int, float)) else pos
    p_s = f"{p:.4f}" if isinstance(p, (int, float)) else p
    raw_s = f"{raw:.2f}%" if isinstance(raw, (int, float)) else raw

    print(f"  {horizon}: avg_abn={avg_s}, med={med_s}, neg_rate={100-float(pos):.1f}% (pos={pos_s}), wilcoxon_p={p_s}, raw={raw_s}")

# Bootstrap CI for 5d
ci_key = 'bootstrap_ci_abnormal_5d'
ci = result.get(ci_key)
if ci:
    print(f"\n  5d bootstrap CI: [{ci.get('ci_lower', 'N/A'):.2f}%, {ci.get('ci_upper', 'N/A'):.2f}%], excludes_zero={ci.get('ci_excludes_zero', 'N/A')}")

# Per-event breakdown
print("\n=== PER-EVENT RESULTS ===")
for evt in result.get('individual_impacts', []):
    sym = evt.get('symbol', '?')
    abn5 = evt.get('abnormal_5d', 'N/A')
    abn10 = evt.get('abnormal_10d', 'N/A')
    abn5_s = f"{abn5:.1f}%" if isinstance(abn5, (int, float)) else abn5
    abn10_s = f"{abn10:.1f}%" if isinstance(abn10, (int, float)) else abn10
    print(f"  {sym} ({evt.get('event_date', '?')}): 5d_abn={abn5_s}, 10d_abn={abn10_s}")

# Save full result
print(f"\nFull result keys: {list(result.keys())[:15]}")
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/short_seller_backtest_result.json'), 'w') as f:
    json.dump(result, f, indent=2, default=str)
print("Saved to data/short_seller_backtest_result.json")
