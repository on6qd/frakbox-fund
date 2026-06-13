"""
Backtest: Material Weakness 8-K Disclosures — Short Signal Analysis
=====================================================================
Tests whether stocks underperform SPY in the 5 trading days following
an 8-K filing that discloses a material weakness in ICFR.

Data source: SEC EDGAR full-text search API (efts.sec.gov)
Period: 2021-01-01 to 2025-12-31
Universe: Large-cap only (>$1B market cap)
"""

import sys
import os
import json
import re
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.yfinance_utils import safe_download, get_close_prices
from tools.largecap_filter import get_market_cap, _load_market_cap_cache, _save_market_cap_cache

# ── Config ──────────────────────────────────────────────────────────────────
EDGAR_HEADERS = {
    'User-Agent': 'frakbox_fund_bot research@example.com',
    'Accept': 'application/json',
}
MIN_MARKET_CAP_M = 1_000   # $1B+
HOLD_DAYS = 5               # trading days after filing
HOLD_DAYS_10 = 10           # also track 10-day window
MAX_PAGES = 10              # 100 results/page → up to 1000 results from EDGAR

# ── Step 1: Fetch EDGAR filings ──────────────────────────────────────────────
print("=" * 70)
print("STEP 1: Fetching material weakness 8-K filings from EDGAR")
print("=" * 70)

all_hits = []
for page in range(MAX_PAGES):
    from_val = page * 100
    url = (
        'https://efts.sec.gov/LATEST/search-index?q=%22material+weakness%22'
        '&forms=8-K&dateRange=custom&startdt=2021-01-01&enddt=2025-12-31'
        f'&from={from_val}'
    )
    r = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
    if r.status_code != 200:
        print(f"  Error {r.status_code} at page {page}, stopping.")
        break
    data = r.json()
    hits = data.get('hits', {}).get('hits', [])
    if not hits:
        break
    all_hits.extend(hits)
    total = data['hits']['total']['value']
    print(f"  Page {page+1}/{MAX_PAGES}: fetched {len(hits)} hits, running total={len(all_hits)} / {total}")
    if len(all_hits) >= total:
        break
    time.sleep(0.3)  # be polite to EDGAR

print(f"\nTotal filings fetched: {len(all_hits)}")

# ── Step 2: Parse — extract ticker + filing date ─────────────────────────────
print("\n" + "=" * 70)
print("STEP 2: Parsing filings and extracting tickers")
print("=" * 70)

def extract_ticker(display_names):
    """Extract first exchange-traded ticker from EDGAR display_names field."""
    if not display_names:
        return None, None
    name_str = display_names[0] if isinstance(display_names, list) else display_names
    # Tickers appear as (TICKER) — uppercase 1-5 chars, before (CIK ...)
    tickers = re.findall(r'\(([A-Z]{1,5})\)', name_str)
    company = name_str.split('  (')[0].strip()
    # Filter out state abbreviations and other false positives
    state_abbrevs = {'CA', 'NY', 'TX', 'FL', 'MA', 'DE', 'WA', 'IL', 'NJ', 'OH', 'PA', 'GA', 'NC', 'VA', 'CO', 'AZ', 'MD', 'MN', 'MI', 'TN', 'IN', 'MO', 'OR', 'CT', 'UT', 'NV', 'SC', 'AL', 'KY', 'LA', 'OK', 'AR', 'MS', 'KS', 'NE', 'ID', 'WI', 'WV', 'RI', 'HI', 'AK', 'ND', 'SD', 'MT', 'WY', 'VT', 'NH', 'ME', 'NM', 'IA'}
    valid = [t for t in tickers if t not in state_abbrevs and len(t) >= 1]
    if valid:
        return company, valid[0]
    return company, None

records = []
seen = set()  # deduplicate (ticker, file_date)
for h in all_hits:
    src = h['_source']
    company, ticker = extract_ticker(src.get('display_names', []))
    file_date = src.get('file_date', '')
    items = src.get('items', [])
    form = src.get('form', '')

    if not ticker or not file_date:
        continue

    key = (ticker, file_date)
    if key in seen:
        continue
    seen.add(key)

    records.append({
        'company': company,
        'ticker': ticker,
        'file_date': file_date,
        'items': items,
        'form': form,
    })

print(f"Unique (ticker, date) pairs: {len(records)}")

# ── Step 3: Filter to large-cap ──────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"STEP 3: Filtering to large-cap (>${MIN_MARKET_CAP_M/1000:.0f}B market cap)")
print("=" * 70)

cache = _load_market_cap_cache()
unique_tickers = list({r['ticker'] for r in records})
print(f"Checking market caps for {len(unique_tickers)} unique tickers...")

uncached = [t for t in unique_tickers if t not in cache]
print(f"  {len(uncached)} tickers not in cache, fetching from yfinance...")

for i, ticker in enumerate(uncached):
    get_market_cap(ticker, cache)
    if (i + 1) % 20 == 0:
        print(f"  ... {i+1}/{len(uncached)}")
        _save_market_cap_cache(cache)
        time.sleep(0.5)

_save_market_cap_cache(cache)

largecap_records = [
    r for r in records
    if cache.get(r['ticker']) is not None and cache[r['ticker']] >= MIN_MARKET_CAP_M
]

print(f"\nAfter large-cap filter (>$1B): {len(largecap_records)} events")
print(f"  (excluded {len(records) - len(largecap_records)} small/micro-cap or delisted)")

# Show size distribution
cap_buckets = {'Mega (>$50B)': 0, 'Large ($10-50B)': 0, 'Mid ($1-10B)': 0}
for r in largecap_records:
    cap = cache.get(r['ticker'], 0) or 0
    if cap >= 50_000:
        cap_buckets['Mega (>$50B)'] += 1
    elif cap >= 10_000:
        cap_buckets['Large ($10-50B)'] += 1
    else:
        cap_buckets['Mid ($1-10B)'] += 1

print("\nCap distribution:")
for k, v in cap_buckets.items():
    print(f"  {k}: {v} events")

# ── Step 4: Compute abnormal returns ────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 4: Computing 5-day and 10-day abnormal returns")
print("=" * 70)

results = []
errors = []

# We'll batch download SPY once per period, then look up per event
# For efficiency, collect all needed date ranges first
sorted_events = sorted(largecap_records, key=lambda x: x['file_date'])

print(f"Processing {len(sorted_events)} events...")
print("(downloading price data — this may take ~60-90 seconds)\n")

for i, event in enumerate(sorted_events):
    ticker = event['ticker']
    file_date_str = event['file_date']

    try:
        file_date = pd.Timestamp(file_date_str)
        # Fetch enough trading days: start from file_date, end 20 calendar days later
        start = file_date.strftime('%Y-%m-%d')
        end = (file_date + timedelta(days=30)).strftime('%Y-%m-%d')

        # Download stock + SPY together
        tickers_to_fetch = [ticker, 'SPY']
        closes = get_close_prices(tickers_to_fetch, start=start, end=end)

        if ticker not in closes.columns or 'SPY' not in closes.columns:
            errors.append({'ticker': ticker, 'date': file_date_str, 'error': 'Missing column'})
            continue

        # Find the first trading day ON OR AFTER the file date
        # (filing after market close → first available open is next day)
        valid_dates = closes.index[closes.index >= file_date]
        if len(valid_dates) < 2:
            errors.append({'ticker': ticker, 'date': file_date_str, 'error': 'Insufficient dates'})
            continue

        entry_date = valid_dates[0]

        # 5-day hold: entry on day 0 (open), exit at close of day 4 (0-indexed)
        if len(valid_dates) >= HOLD_DAYS + 1:
            exit_date_5 = valid_dates[HOLD_DAYS]
        elif len(valid_dates) >= 2:
            exit_date_5 = valid_dates[-1]
        else:
            errors.append({'ticker': ticker, 'date': file_date_str, 'error': 'Not enough trading days'})
            continue

        # 10-day hold
        if len(valid_dates) >= HOLD_DAYS_10 + 1:
            exit_date_10 = valid_dates[HOLD_DAYS_10]
        else:
            exit_date_10 = valid_dates[-1]

        # Calculate returns: (exit_price / entry_price - 1)
        stock_entry = closes.loc[entry_date, ticker]
        spy_entry = closes.loc[entry_date, 'SPY']

        stock_exit_5 = closes.loc[exit_date_5, ticker]
        spy_exit_5 = closes.loc[exit_date_5, 'SPY']

        stock_exit_10 = closes.loc[exit_date_10, ticker]
        spy_exit_10 = closes.loc[exit_date_10, 'SPY']

        stock_return_5 = (stock_exit_5 / stock_entry) - 1
        spy_return_5 = (spy_exit_5 / spy_entry) - 1
        abnormal_5 = stock_return_5 - spy_return_5

        stock_return_10 = (stock_exit_10 / stock_entry) - 1
        spy_return_10 = (spy_exit_10 / spy_entry) - 1
        abnormal_10 = stock_return_10 - spy_return_10

        cap = cache.get(ticker, 0) or 0
        if cap >= 50_000:
            cap_tier = 'Mega (>$50B)'
        elif cap >= 10_000:
            cap_tier = 'Large ($10-50B)'
        else:
            cap_tier = 'Mid ($1-10B)'

        results.append({
            'ticker': ticker,
            'company': event['company'],
            'file_date': file_date_str,
            'market_cap_m': round(cap, 0),
            'cap_tier': cap_tier,
            'items': ','.join(event['items']),
            'stock_return_5d': round(stock_return_5 * 100, 2),
            'spy_return_5d': round(spy_return_5 * 100, 2),
            'abnormal_5d': round(abnormal_5 * 100, 2),
            'stock_return_10d': round(stock_return_10 * 100, 2),
            'spy_return_10d': round(spy_return_10 * 100, 2),
            'abnormal_10d': round(abnormal_10 * 100, 2),
            'entry_date': str(entry_date.date()),
            'exit_date_5d': str(exit_date_5.date()),
            'exit_date_10d': str(exit_date_10.date()),
        })

        if (i + 1) % 10 == 0:
            print(f"  Processed {i+1}/{len(sorted_events)} events ({len(results)} successful, {len(errors)} errors)")

    except Exception as e:
        errors.append({'ticker': ticker, 'date': file_date_str, 'error': str(e)[:100]})

print(f"\nDone. Successful: {len(results)}, Errors: {len(errors)}")

# ── Step 5: Analysis ─────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 5: Analysis Results")
print("=" * 70)

df = pd.DataFrame(results)

if df.empty:
    print("ERROR: No results to analyze!")
    sys.exit(1)

# Save results
df.to_csv('/tmp/material_weakness_results.csv', index=False)
print(f"Results saved to /tmp/material_weakness_results.csv")

print(f"\n{'='*70}")
print(f"MATERIAL WEAKNESS 8-K SHORT SIGNAL BACKTEST RESULTS")
print(f"{'='*70}")
print(f"Period: 2021-2025 | Universe: Large-cap (>$1B) | Events analyzed: {len(df)}")
print()

# Overall 5-day stats
print("── 5-DAY ABNORMAL RETURN ──────────────────────────────────────────────")
n = len(df)
neg_pct_5 = (df['abnormal_5d'] < 0).mean() * 100
avg_abnormal_5 = df['abnormal_5d'].mean()
median_abnormal_5 = df['abnormal_5d'].median()
avg_stock_5 = df['stock_return_5d'].mean()
avg_spy_5 = df['spy_return_5d'].mean()
std_5 = df['abnormal_5d'].std()
t_stat_5 = avg_abnormal_5 / (std_5 / np.sqrt(n))

print(f"  N events with data:              {n}")
print(f"  % with negative abnormal return: {neg_pct_5:.1f}%")
print(f"  Average stock return (5d):       {avg_stock_5:+.2f}%")
print(f"  Average SPY return (5d):         {avg_spy_5:+.2f}%")
print(f"  Average abnormal return (5d):    {avg_abnormal_5:+.2f}%")
print(f"  Median abnormal return (5d):     {median_abnormal_5:+.2f}%")
print(f"  Std dev abnormal return (5d):    {std_5:.2f}%")
print(f"  t-statistic (vs 0):              {t_stat_5:.2f}")
print(f"  Interpretation:                  {'SIGNIFICANT' if abs(t_stat_5) > 1.96 else 'not significant'} at 95% CI")

print()
print("── 10-DAY ABNORMAL RETURN ─────────────────────────────────────────────")
neg_pct_10 = (df['abnormal_10d'] < 0).mean() * 100
avg_abnormal_10 = df['abnormal_10d'].mean()
median_abnormal_10 = df['abnormal_10d'].median()
avg_stock_10 = df['stock_return_10d'].mean()
avg_spy_10 = df['spy_return_10d'].mean()
std_10 = df['abnormal_10d'].std()
t_stat_10 = avg_abnormal_10 / (std_10 / np.sqrt(n))

print(f"  % with negative abnormal return: {neg_pct_10:.1f}%")
print(f"  Average stock return (10d):      {avg_stock_10:+.2f}%")
print(f"  Average SPY return (10d):        {avg_spy_10:+.2f}%")
print(f"  Average abnormal return (10d):   {avg_abnormal_10:+.2f}%")
print(f"  Median abnormal return (10d):    {median_abnormal_10:+.2f}%")
print(f"  Std dev abnormal return (10d):   {std_10:.2f}%")
print(f"  t-statistic (vs 0):              {t_stat_10:.2f}")
print(f"  Interpretation:                  {'SIGNIFICANT' if abs(t_stat_10) > 1.96 else 'not significant'} at 95% CI")

print()
print("── BY MARKET CAP TIER ─────────────────────────────────────────────────")
for tier in ['Mega (>$50B)', 'Large ($10-50B)', 'Mid ($1-10B)']:
    sub = df[df['cap_tier'] == tier]
    if len(sub) == 0:
        continue
    n_sub = len(sub)
    neg_pct = (sub['abnormal_5d'] < 0).mean() * 100
    avg_abn = sub['abnormal_5d'].mean()
    print(f"  {tier}:")
    print(f"    N={n_sub}, neg_pct={neg_pct:.1f}%, avg_abnormal_5d={avg_abn:+.2f}%")

print()
print("── TOP 10 LARGEST NEGATIVE REACTIONS (5-day) ─────────────────────────")
top_neg = df.nsmallest(10, 'abnormal_5d')[['ticker', 'company', 'file_date', 'market_cap_m', 'stock_return_5d', 'spy_return_5d', 'abnormal_5d']]
print(top_neg.to_string(index=False))

print()
print("── ALL LARGE-CAP EVENTS ───────────────────────────────────────────────")
display_cols = ['ticker', 'company', 'file_date', 'market_cap_m', 'items', 'stock_return_5d', 'spy_return_5d', 'abnormal_5d', 'abnormal_10d']
# Truncate company to 30 chars for display
df_display = df[display_cols].copy()
df_display['company'] = df_display['company'].str[:30]
print(df_display.to_string(index=False))

print()
print("── ERROR SUMMARY ──────────────────────────────────────────────────────")
print(f"  Total errors: {len(errors)}")
if errors:
    # Group errors by type
    error_types = {}
    for e in errors:
        err_msg = e['error'][:50]
        error_types[err_msg] = error_types.get(err_msg, 0) + 1
    for msg, count in sorted(error_types.items(), key=lambda x: -x[1])[:5]:
        print(f"  '{msg}': {count} occurrences")

print()
print("=" * 70)
print("HYPOTHESIS VERDICT")
print("=" * 70)
if neg_pct_5 > 55 and avg_abnormal_5 < -1.0:
    verdict = "SUPPORTED — majority negative drift with meaningful magnitude"
elif neg_pct_5 > 55 or avg_abnormal_5 < -1.0:
    verdict = "WEAK SUPPORT — directional but not both criteria met"
elif neg_pct_5 < 45 and avg_abnormal_5 > 0:
    verdict = "REJECTED — no consistent negative drift"
else:
    verdict = "INCONCLUSIVE — mixed evidence"

print(f"  Verdict: {verdict}")
print(f"  Signal direction: {neg_pct_5:.1f}% of events negative (need >55% for signal)")
print(f"  Signal magnitude: {avg_abnormal_5:+.2f}% avg abnormal return (need <-1% for signal)")
print(f"  Statistical significance: t={t_stat_5:.2f} ({'YES' if abs(t_stat_5) > 1.96 else 'NO'} at 95% CI)")
