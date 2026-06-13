#!/usr/bin/env python3
"""Expand SEO bought-deal backtest to 2020-2024 (and optionally 2025).

Steps:
1. Re-scan all years 2020-2024 using cached EDGAR indexes
2. Merge and deduplicate
3. Resolve CIK -> ticker (use existing cache, add new)
4. Filter to large-cap (>$500M)
5. Backtest: full set and IS/OOS temporal split
6. Print compact JSON summary

Usage: python tools/expand_seo_backtest.py
"""
import sys
import json
import time
import os
import pickle
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import yfinance as yf
import pandas as pd

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
DATA_DIR = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))

# ── Step 1: Scan all years ──────────────────────────────────────────────────
from tools.seo_bought_deal_scanner import find_bought_deals, resolve_ticker

print("=== SCANNING 2020-2024 FOR BOUGHT DEALS ===")
all_deals = find_bought_deals(years=[2020, 2021, 2022, 2023, 2024])
print(f"\nTotal raw bought deals found: {len(all_deals)}")

# Save expanded raw
raw_path = DATA_DIR / 'seo_bought_deals_raw_expanded.json'
with open(raw_path, 'w') as f:
    json.dump(all_deals, f, indent=2, default=str)
print(f"Saved raw -> {raw_path}")

# ── Step 2: Resolve tickers ─────────────────────────────────────────────────
cache_path = DATA_DIR / 'seo_cik_ticker_cache.json'
if cache_path.exists():
    with open(cache_path) as f:
        ticker_cache = json.load(f)
    print(f"\nLoaded CIK cache ({len(ticker_cache)} entries)")
else:
    ticker_cache = {}

unique_ciks = list({d['cik'] for d in all_deals})
new_ciks = [c for c in unique_ciks if c not in ticker_cache]
print(f"CIKs to resolve: {len(new_ciks)} new out of {len(unique_ciks)} total")

for i, cik in enumerate(new_ciks):
    if i % 50 == 0 and i > 0:
        print(f"  Progress: {i}/{len(new_ciks)}")
    ticker_cache[cik] = resolve_ticker(cik)
    time.sleep(0.12)

with open(cache_path, 'w') as f:
    json.dump(ticker_cache, f)
print(f"Cache now has {len(ticker_cache)} entries")

# Attach tickers
resolved = []
for d in all_deals:
    t = ticker_cache.get(d['cik'])
    if t:
        deal = dict(d)
        deal['ticker'] = t
        resolved.append(deal)

print(f"Resolved to tickers: {len(resolved)}")

# ── Step 3: Market cap filter ───────────────────────────────────────────────
mc_cache_path = DATA_DIR / 'seo_mc_cache.json'
if mc_cache_path.exists():
    with open(mc_cache_path) as f:
        mc_cache = json.load(f)
else:
    mc_cache = {}

all_tickers = list({d['ticker'] for d in resolved})
new_tickers = [t for t in all_tickers if t not in mc_cache]
print(f"\nMarket cap lookups needed: {len(new_tickers)} new out of {len(all_tickers)} total")

for i, tkr in enumerate(new_tickers):
    if i % 50 == 0 and i > 0:
        print(f"  Progress: {i}/{len(new_tickers)}")
    try:
        info = yf.Ticker(tkr).info
        mc_cache[tkr] = info.get('marketCap', 0) or 0
    except Exception:
        mc_cache[tkr] = 0
    time.sleep(0.05)

with open(mc_cache_path, 'w') as f:
    json.dump(mc_cache, f)

filtered = [d for d in resolved if mc_cache.get(d['ticker'], 0) >= 500_000_000]
print(f"Large-cap (>$500M): {len(filtered)}")

# ── Step 4: Deduplicate (same ticker + same month) ──────────────────────────
seen = set()
deduped = []
for d in sorted(filtered, key=lambda x: x['announcement_date']):
    key = (d['ticker'], d['announcement_date'][:7])
    if key not in seen:
        seen.add(key)
        deduped.append(d)

print(f"Deduplicated: {len(deduped)}")

# Year distribution
from collections import Counter
year_dist = Counter(d['announcement_date'][:4] for d in deduped)
print(f"By year: {dict(sorted(year_dist.items()))}")

# Save expanded filtered
with open(DATA_DIR / 'seo_bought_deals_filtered_expanded.json', 'w') as f:
    json.dump(deduped, f, indent=2)

# ── Step 5: Backtest ────────────────────────────────────────────────────────
import market_data

def run_bt(events, label):
    """Run backtest and return result dict."""
    event_dates = [
        {'symbol': d['ticker'], 'date': d['announcement_date'],
         'timing': 'after_hours', 'entry_price': 'open'}
        for d in events
    ]
    if len(event_dates) > 250:
        event_dates = event_dates[:250]

    result = market_data.measure_event_impact(
        event_dates=event_dates,
        benchmark='SPY',
        sector_etf=None,
        estimate_costs=True,
        event_type='secondary_equity_offering',
    )
    # Compact summary
    summary = {
        'label': label,
        'n': result['events_measured'],
    }
    for h in ['1d', '3d', '5d', '10d']:
        avg = result.get(f'avg_abnormal_{h}')
        pos = result.get(f'positive_rate_abnormal_{h}')
        p = result.get(f'wilcoxon_p_abnormal_{h}')
        if avg is not None:
            summary[h] = {'avg': round(avg, 2), 'neg_rate': round(100 - pos, 1), 'p': round(p, 4)}

    summary['passes_mt'] = result.get('passes_multiple_testing', False)
    return result, summary


# Full set
print("\n=== FULL SET BACKTEST ===")
result_full, sum_full = run_bt(deduped, "FULL 2020-2024")
print(json.dumps(sum_full, indent=2))

# Same-day subset
same_day = [d for d in deduped if d.get('days_between', 999) == 0]
if len(same_day) >= 20:
    print(f"\n=== SAME-DAY SUBSET ({len(same_day)} events) ===")
    result_sd, sum_sd = run_bt(same_day, "SAME-DAY ONLY")
    print(json.dumps(sum_sd, indent=2))
else:
    sum_sd = None
    print(f"\nSame-day subset too small: {len(same_day)}")

# Temporal split: IS = 2020-2022, OOS = 2023-2024
is_events = [d for d in deduped if d['announcement_date'] < '2023-01-01']
oos_events = [d for d in deduped if d['announcement_date'] >= '2023-01-01']

if len(is_events) >= 20 and len(oos_events) >= 10:
    print(f"\n=== IN-SAMPLE 2020-2022 ({len(is_events)} events) ===")
    _, sum_is = run_bt(is_events, "IS 2020-2022")
    print(json.dumps(sum_is, indent=2))

    print(f"\n=== OUT-OF-SAMPLE 2023-2024 ({len(oos_events)} events) ===")
    _, sum_oos = run_bt(oos_events, "OOS 2023-2024")
    print(json.dumps(sum_oos, indent=2))
else:
    print(f"\nIS: {len(is_events)}, OOS: {len(oos_events)} — not enough for temporal split")
    sum_is = None
    sum_oos = None

# ── Step 6: Final summary ──────────────────────────────────────────────────
print("\n" + "="*70)
print("=== FINAL SUMMARY ===")
print("="*70)
print(json.dumps({
    'full': sum_full,
    'same_day': sum_sd,
    'in_sample': sum_is if sum_is else "N/A",
    'out_of_sample': sum_oos if sum_oos else "N/A",
    'year_distribution': dict(sorted(year_dist.items())),
}, indent=2))

# Save results
with open(DATA_DIR / 'seo_expanded_backtest_results.json', 'w') as f:
    json.dump({
        'full': sum_full,
        'same_day': sum_sd,
        'in_sample': sum_is,
        'out_of_sample': sum_oos,
        'year_distribution': dict(sorted(year_dist.items())),
    }, f, indent=2)

print("\nDone. Results saved to data/seo_expanded_backtest_results.json")
