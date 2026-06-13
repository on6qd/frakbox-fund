#!/usr/bin/env python3
"""SEO bought-deal backtest pipeline

Resolves CIKs to tickers, filters to large-cap, deduplicates, then backtests
the short-side signal (bought deals suppress price -2 to -3% abnormal).

Usage: python tools/run_seo_backtest.py
"""
import sys
import json
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import yfinance as yf
import pandas as pd
from pathlib import Path

HEADERS = {"User-Agent": "financial-researcher research@example.com"}

# ── Load raw deals ────────────────────────────────────────────────────────────
raw_path = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/seo_bought_deals_raw.json'))
with open(raw_path) as f:
    raw_deals = json.load(f)

print(f"Loaded {len(raw_deals)} raw bought deals")

# ── CIK → ticker resolution ───────────────────────────────────────────────────
def resolve_ticker(cik: str) -> str | None:
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tickers = data.get('tickers', [])
            if tickers:
                return tickers[0]
    except Exception:
        pass
    return None

cache_path = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/seo_cik_ticker_cache.json'))
if cache_path.exists():
    with open(cache_path) as f:
        ticker_cache = json.load(f)
    print(f"Loaded existing CIK cache ({len(ticker_cache)} entries)")
else:
    ticker_cache = {}

new_lookups = 0
print("Resolving tickers (~1 min for uncached CIKs)...")
for deal in raw_deals:
    cik = deal['cik']
    if cik not in ticker_cache:
        ticker = resolve_ticker(cik)
        ticker_cache[cik] = ticker
        new_lookups += 1
        time.sleep(0.11)  # stay well under SEC rate limit

if new_lookups:
    with open(cache_path, 'w') as f:
        json.dump(ticker_cache, f)
    print(f"  Resolved {new_lookups} new CIKs and saved cache")

resolved_count = sum(1 for v in ticker_cache.values() if v)
print(f"Resolved {resolved_count} tickers out of {len(ticker_cache)}")

# Attach tickers to deals
resolved_deals = []
for deal in raw_deals:
    ticker = ticker_cache.get(deal['cik'])
    if ticker:
        deal = dict(deal)  # don't mutate original
        deal['ticker'] = ticker
        resolved_deals.append(deal)

print(f"Deals with tickers: {len(resolved_deals)}")

# ── Large-cap filter (>$500M) ─────────────────────────────────────────────────
mc_cache_path = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/seo_mc_cache.json'))
if mc_cache_path.exists():
    with open(mc_cache_path) as f:
        mc_cache = json.load(f)
    print(f"Loaded market-cap cache ({len(mc_cache)} entries)")
else:
    mc_cache = {}

print("Filtering by market cap...")
all_tickers = list({d['ticker'] for d in resolved_deals})
for tkr in all_tickers:
    if tkr not in mc_cache:
        try:
            info = yf.Ticker(tkr).info
            mc = info.get('marketCap', 0) or 0
            mc_cache[tkr] = mc
        except Exception:
            mc_cache[tkr] = 0
        time.sleep(0.05)

with open(mc_cache_path, 'w') as f:
    json.dump(mc_cache, f)

filtered_deals = [d for d in resolved_deals if mc_cache.get(d['ticker'], 0) >= 500_000_000]
print(f"Large-cap deals (>$500M): {len(filtered_deals)}")

# ── Deduplicate: same ticker + same month ─────────────────────────────────────
seen: set = set()
deduped = []
for deal in filtered_deals:
    key = (deal['ticker'], deal['announcement_date'][:7])
    if key not in seen:
        seen.add(key)
        deduped.append(deal)

print(f"After deduplication: {len(deduped)}")

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/seo_bought_deals_filtered.json'), 'w') as f:
    json.dump(deduped, f, indent=2)
print("Saved filtered list -> data/seo_bought_deals_filtered.json")

# ── Backtest helper ───────────────────────────────────────────────────────────
import market_data
import pickle


def print_results(result: dict, label: str) -> None:
    print(f"\n{'='*60}")
    print(f"=== {label} ===")
    print(f"{'='*60}")
    print(f"Events measured : {result['events_measured']}")
    print(f"Entry price mode: {result['entry_price_mode']}")
    print(f"\n--- Abnormal Returns (vs SPY) ---")
    for horizon in ['1d', '3d', '5d', '10d']:
        avg = result.get(f'avg_abnormal_{horizon}')
        pos = result.get(f'positive_rate_abnormal_{horizon}')
        std = result.get(f'stdev_abnormal_{horizon}')
        p   = result.get(f'wilcoxon_p_abnormal_{horizon}')
        if avg is None:
            print(f"  {horizon}: N/A")
        else:
            print(f"  {horizon}: avg={avg:+.2f}%  neg_rate={100-pos:.0f}%  stdev={std:.2f}%  wilcoxon_p={p:.4f}")

    print(f"\nPasses multiple testing : {result.get('passes_multiple_testing')}")
    print(f"Data quality warning    : {result.get('data_quality_warning')}")
    print(f"Avg transaction cost    : {result.get('avg_estimated_cost_pct', 'N/A')}")

    print("\n--- Bootstrap 95% CIs ---")
    for horizon in ['1d', '3d', '5d']:
        ci = result.get(f'bootstrap_ci_abnormal_{horizon}', {})
        if ci:
            lo = ci.get('ci_lower', float('nan'))
            hi = ci.get('ci_upper', float('nan'))
            excl = ci.get('ci_excludes_zero', 'N/A')
            print(f"  {horizon}: [{lo:+.2f}%, {hi:+.2f}%]  excludes_zero={excl}")


def run_backtest(deals: list, label: str, outfile: str) -> dict:
    event_dates = [
        {
            'symbol': d['ticker'],
            'date': d['announcement_date'],
            'timing': 'after_hours',
            'entry_price': 'open',
        }
        for d in deals
    ]

    # Cap at 200 events; enough for power, avoids very long runtimes
    if len(event_dates) > 200:
        print(f"Capping at 200 events (have {len(event_dates)})")
        event_dates = event_dates[:200]

    print(f"\nRunning backtest: {label} ({len(event_dates)} events)...")
    result = market_data.measure_event_impact(
        event_dates=event_dates,
        benchmark='SPY',
        sector_etf=None,       # offerings span many sectors — skip sector adj
        estimate_costs=True,
        event_type='secondary_equity_offering',
    )

    print_results(result, label)

    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), f'data/{outfile}'), 'wb') as f:
        pickle.dump(result, f)
    print(f"Saved pickle -> data/{outfile}")

    return result


# ── Full backtest ─────────────────────────────────────────────────────────────
result_all = run_backtest(deduped, "ALL FILTERED DEALS", "seo_bought_deal_backtest_all.pkl")

# ── Verdict ───────────────────────────────────────────────────────────────────
passes     = result_all.get('passes_multiple_testing', False)
avg_1d     = result_all.get('avg_abnormal_1d', 0) or 0
avg_3d     = result_all.get('avg_abnormal_3d', 0) or 0
pos_rate_1d = result_all.get('positive_rate_abnormal_1d', 50) or 50

print("\n=== VERDICT (Full Set) ===")
if passes and avg_1d < -1.0 and pos_rate_1d < 50:
    print("SIGNAL DETECTED - worth forming hypothesis")
elif passes:
    print("PARTIAL SIGNAL - passes multiple testing but direction/magnitude unclear")
else:
    print("NO SIGNAL - does not pass multiple testing")

# ── Same-day only backtest ────────────────────────────────────────────────────
same_day = [d for d in deduped if d.get('days_between', 999) == 0]
print(f"\nSame-day events (8-K and 424B4 same date): {len(same_day)}")

if avg_1d < -1.0 or avg_3d < -1.0:
    print("Signal looks real — running same-day sub-backtest for cleaner signal...")
    result_sd = run_backtest(same_day, "SAME-DAY EVENTS ONLY", "seo_bought_deal_backtest_sameday.pkl")

    passes_sd  = result_sd.get('passes_multiple_testing', False)
    avg_1d_sd  = result_sd.get('avg_abnormal_1d', 0) or 0
    pos_sd     = result_sd.get('positive_rate_abnormal_1d', 50) or 50

    print("\n=== VERDICT (Same-Day) ===")
    if passes_sd and avg_1d_sd < -1.0 and pos_sd < 50:
        print("SAME-DAY SIGNAL CONFIRMED - cleanest subset still shows effect")
    elif avg_1d_sd < avg_1d:
        print("Same-day is even stronger - dates are well-matched")
    else:
        print("Same-day does NOT strengthen signal - possible date noise in full set")
else:
    print("Full-set signal too weak (<-1% at 1d) - skipping same-day sub-backtest")
    print("NOTE: Near-zero returns suggest 8-K filings are not the announcement events.")
    print("      Likely capturing post-announcement drift events, not the announcement itself.")

print("\nDone.")
