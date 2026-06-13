#!/usr/bin/env python3
"""
Expanded Short Seller Report Post-Announcement Drift Backtest

Tests post-report drift across 4 short seller firms:
- Hindenburg Research (fraud/accounting focus)
- Muddy Waters Research (fraud/due diligence)
- Spruce Point Capital (forensic accounting)
- Kerrisdale / Wolfpack / Grizzly (mixed)

Entry: next-day open after report publication
Benchmark: SPY
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import market_data
import statistics
from datetime import datetime

EVENTS = [
    # ── Hindenburg Research ──────────────────────────────────────
    {"symbol": "NKLA", "date": "2020-09-10", "source": "Hindenburg"},
    {"symbol": "CLOV", "date": "2021-02-04", "source": "Hindenburg"},
    {"symbol": "ORA",  "date": "2021-03-01", "source": "Hindenburg"},
    {"symbol": "RIDE", "date": "2021-03-12", "source": "Hindenburg"},
    {"symbol": "DKNG", "date": "2021-06-15", "source": "Hindenburg"},
    {"symbol": "SQ",   "date": "2023-03-23", "source": "Hindenburg"},
    {"symbol": "IEP",  "date": "2023-05-02", "source": "Hindenburg"},
    {"symbol": "FRHC", "date": "2023-09-26", "source": "Hindenburg"},
    {"symbol": "SMCI", "date": "2024-08-27", "source": "Hindenburg"},

    # ── Muddy Waters Research ────────────────────────────────────
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

    # ── Spruce Point Capital (exact dates from website) ──────────
    {"symbol": "XYL",  "date": "2023-08-09", "source": "SprucePoint"},
    {"symbol": "IOT",  "date": "2023-09-21", "source": "SprucePoint"},
    {"symbol": "ROL",  "date": "2023-10-04", "source": "SprucePoint"},
    {"symbol": "ELF",  "date": "2023-11-09", "source": "SprucePoint"},  # note: MW also hit ELF in 2024
    {"symbol": "MSCI", "date": "2024-01-17", "source": "SprucePoint"},
    {"symbol": "ENFN", "date": "2024-03-14", "source": "SprucePoint"},
    {"symbol": "Z",    "date": "2024-03-05", "source": "SprucePoint"},
    {"symbol": "PWSC", "date": "2024-04-17", "source": "SprucePoint"},
    {"symbol": "BOOT", "date": "2024-05-08", "source": "SprucePoint"},
    {"symbol": "FND",  "date": "2024-07-01", "source": "SprucePoint"},
    {"symbol": "ZBRA", "date": "2024-08-08", "source": "SprucePoint"},
    {"symbol": "INTU", "date": "2024-09-20", "source": "SprucePoint"},
    {"symbol": "ERIE", "date": "2024-10-18", "source": "SprucePoint"},
    {"symbol": "PRCT", "date": "2025-01-16", "source": "SprucePoint"},
    {"symbol": "ROAD", "date": "2025-01-23", "source": "SprucePoint"},
    {"symbol": "DY",   "date": "2025-02-19", "source": "SprucePoint"},
    {"symbol": "RELY", "date": "2025-03-11", "source": "SprucePoint"},

    # ── Kerrisdale Capital ───────────────────────────────────────
    {"symbol": "MSTR", "date": "2024-03-28", "source": "Kerrisdale"},

    # ── Wolfpack Research ────────────────────────────────────────
    {"symbol": "MAX",  "date": "2024-06-24", "source": "Wolfpack"},

    # ── Grizzly Research ─────────────────────────────────────────
    {"symbol": "GCT",  "date": "2024-05-22", "source": "Grizzly"},
]

# Filter: need at least 10 trading days of post-event data
CUTOFF = datetime(2026, 3, 20)
events_for_backtest = [
    {"symbol": e["symbol"], "date": e["date"]}
    for e in EVENTS
    if datetime.strptime(e["date"], "%Y-%m-%d") < CUTOFF
]
source_lookup = {(e["symbol"], e["date"]): e["source"] for e in EVENTS}

print(f"Total events for backtest: {len(events_for_backtest)}")

# ── Run backtest ───────────────────────────────────────────────
result = market_data.measure_event_impact(
    event_dates=events_for_backtest,
    benchmark="SPY",
    entry_price="open",
    estimate_costs=True,
)

# ── Summary ────────────────────────────────────────────────────
N = result.get('events_measured', 0)
print(f"\n=== EXPANDED SHORT SELLER BACKTEST (N={N}) ===")
print(f"Passes multiple testing: {result.get('passes_multiple_testing', 'N/A')}")

for horizon in ['1d', '3d', '5d', '10d', '20d']:
    avg = result.get(f'avg_abnormal_{horizon}', 'N/A')
    med = result.get(f'median_abnormal_{horizon}', 'N/A')
    pos = result.get(f'positive_rate_abnormal_{horizon}', 'N/A')
    p = result.get(f'wilcoxon_p_abnormal_{horizon}', 'N/A')
    neg = 100 - float(pos) if isinstance(pos, (int, float)) else 'N/A'
    print(f"  {horizon}: avg_abn={avg:.2f}%, med={med:.2f}%, neg={neg:.0f}%, wilcoxon_p={p:.4f}")

ci = result.get('bootstrap_ci_abnormal_5d')
if ci:
    print(f"\n  5d CI: [{ci['ci_lower']:.2f}%, {ci['ci_upper']:.2f}%], excludes_zero={ci['ci_excludes_zero']}")

# ── By source ──────────────────────────────────────────────────
events = result.get('individual_impacts', [])
by_source = {}
for evt in events:
    key = source_lookup.get((evt['symbol'], evt.get('event_date', '')), 'Unknown')
    by_source.setdefault(key, []).append(evt)

print("\n=== BY SOURCE ===")
for src, evts in sorted(by_source.items()):
    abn5 = [e['abnormal_5d'] for e in evts if e.get('abnormal_5d') is not None]
    if not abn5: continue
    neg5 = sum(1 for x in abn5 if x < 0) / len(abn5) * 100
    avg5 = sum(abn5) / len(abn5)
    med5 = statistics.median(abn5)
    print(f"  {src} (n={len(abn5)}): avg_5d={avg5:.1f}%, med={med5:.1f}%, neg={neg5:.0f}%")

# ── Temporal split ─────────────────────────────────────────────
is_evts = [e for e in events if e.get('event_date', '') < '2023-06-01']
oos_evts = [e for e in events if e.get('event_date', '') >= '2023-06-01']

for label, group in [("IS < 2023-06", is_evts), ("OOS >= 2023-06", oos_evts)]:
    abn5 = [e['abnormal_5d'] for e in group if e.get('abnormal_5d') is not None]
    if not abn5: continue
    neg5 = sum(1 for x in abn5 if x < 0) / len(abn5) * 100
    avg5 = sum(abn5) / len(abn5)
    med5 = statistics.median(abn5)
    print(f"\n  {label} (n={len(abn5)}): avg_5d={avg5:.1f}%, med={med5:.1f}%, neg={neg5:.0f}%")

# ── Per-event (sorted by date) ─────────────────────────────────
print("\n=== PER-EVENT (sorted by date) ===")
sorted_events = sorted(events, key=lambda e: e.get('event_date', ''))
for evt in sorted_events:
    sym = evt.get('symbol', '?')
    src = source_lookup.get((sym, evt.get('event_date', '')), '?')
    a5 = evt.get('abnormal_5d')
    a10 = evt.get('abnormal_10d')
    a5s = f"{a5:+.1f}%" if isinstance(a5, (int,float)) else "N/A"
    a10s = f"{a10:+.1f}%" if isinstance(a10, (int,float)) else "N/A"
    marker = "*" if isinstance(a5, (int,float)) and a5 > 0 else " "
    print(f" {marker} {evt.get('event_date','?')} {sym:6s} [{src:12s}] 5d={a5s:>8s} 10d={a10s:>8s}")

# ── Save ───────────────────────────────────────────────────────
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/short_seller_expanded_backtest.json'), 'w') as f:
    json.dump(result, f, indent=2, default=str)
print("\nSaved to data/short_seller_expanded_backtest.json")
