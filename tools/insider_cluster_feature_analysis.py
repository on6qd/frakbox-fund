"""
Insider Cluster Feature Analysis
=================================
Analyzes what features predict successful insider buying clusters.

Uses SEC EDGAR quarterly Form 4 bulk data (cached pickles).
For each cluster (3+ unique insiders buying within 14 days of same issuer):
  - Computes features: cluster_size, total_value, ceo_cfo_present, prior_drawdown
  - Computes VIX at cluster date
  - Computes 5-day post-event abnormal return (vs SPY)
  - Outputs feature importance analysis

Usage:
  python tools/insider_cluster_feature_analysis.py [--start-year 2020] [--end-year 2025]
"""

import pickle
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.yfinance_utils import safe_download

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/sec_form4_cache')

CEO_CFO_KEYWORDS = [
    "chief executive", "ceo", "chief financial", "cfo",
    "president and ceo", "president, ceo", "president & ceo",
    "c.e.o", "chief exec"
]

def is_ceo_cfo(title_str):
    """Check if reporting owner title indicates CEO or CFO."""
    if not title_str or pd.isna(title_str):
        return False
    title_lower = str(title_str).lower()
    return any(kw in title_lower for kw in CEO_CFO_KEYWORDS)


def load_quarter(year, quarter):
    """Load a single quarter of Form 4 data."""
    fname = f"{year}q{quarter}_form345.pkl"
    fpath = os.path.join(CACHE_DIR, fname)
    if not os.path.exists(fpath):
        return None
    with open(fpath, 'rb') as f:
        return pickle.load(f)


def find_clusters(start_year=2020, end_year=2025, min_insiders=3, window_days=14, min_value=50000):
    """Find all insider buying clusters from quarterly data."""
    all_purchases = []

    for year in range(start_year, end_year + 1):
        for q in range(1, 5):
            data = load_quarter(year, q)
            if data is None:
                continue

            subs = data['submissions']
            trans = data['nonderiv_trans']
            owners = data['reporting_owners']

            # Filter to purchases
            purchases = trans[trans['TRANS_CODE'] == 'P'].copy()
            if purchases.empty:
                continue

            # Merge with submissions for ticker info
            purchases = purchases.merge(
                subs[['ACCESSION_NUMBER', 'ISSUERTRADINGSYMBOL', 'ISSUERNAME', 'ISSUERCIK', 'FILING_DATE']],
                on='ACCESSION_NUMBER', how='left'
            )

            # Merge with owners for title info
            purchases = purchases.merge(
                owners[['ACCESSION_NUMBER', 'RPTOWNERCIK', 'RPTOWNERNAME', 'RPTOWNER_TITLE']],
                on='ACCESSION_NUMBER', how='left'
            )

            # Clean up
            purchases['TRANS_DATE'] = pd.to_datetime(purchases['TRANS_DATE'], format='%d-%b-%Y', errors='coerce')
            purchases['TRANS_SHARES'] = pd.to_numeric(purchases['TRANS_SHARES'], errors='coerce')
            purchases['TRANS_PRICEPERSHARE'] = pd.to_numeric(purchases['TRANS_PRICEPERSHARE'], errors='coerce')
            purchases['dollar_value'] = purchases['TRANS_SHARES'] * purchases['TRANS_PRICEPERSHARE']

            # Filter valid
            valid = purchases.dropna(subset=['TRANS_DATE', 'ISSUERTRADINGSYMBOL', 'RPTOWNERCIK'])
            valid = valid[valid['dollar_value'] > 0]

            all_purchases.append(valid)
            print(f"  Loaded {year}Q{q}: {len(valid)} purchases", file=sys.stderr)

    if not all_purchases:
        return []

    df = pd.concat(all_purchases, ignore_index=True)
    df = df.sort_values('TRANS_DATE')
    print(f"\nTotal purchases: {len(df)}", file=sys.stderr)

    # Group by ticker and find clusters within window
    clusters = []
    for ticker, group in df.groupby('ISSUERTRADINGSYMBOL'):
        if pd.isna(ticker) or str(ticker).strip() == '':
            continue

        group = group.sort_values('TRANS_DATE')
        dates = group['TRANS_DATE'].dropna().unique()

        if len(dates) == 0:
            continue

        # Sliding window: for each date, look forward window_days
        used_dates = set()
        for i, anchor_date in enumerate(sorted(dates)):
            if anchor_date in used_dates:
                continue

            window_end = anchor_date + pd.Timedelta(days=window_days)
            window_rows = group[(group['TRANS_DATE'] >= anchor_date) & (group['TRANS_DATE'] <= window_end)]

            # Count unique insiders
            unique_insiders = window_rows['RPTOWNERCIK'].nunique()
            if unique_insiders < min_insiders:
                continue

            total_value = window_rows['dollar_value'].sum()
            if total_value < min_value:
                continue

            # Mark dates as used
            for d in window_rows['TRANS_DATE'].unique():
                used_dates.add(d)

            # Check CEO/CFO presence
            has_ceo_cfo = any(is_ceo_cfo(t) for t in window_rows['RPTOWNER_TITLE'].dropna())

            # Cluster date = last filing date in window
            cluster_date = window_rows['TRANS_DATE'].max()
            issuer_name = window_rows['ISSUERNAME'].iloc[0] if 'ISSUERNAME' in window_rows.columns else '?'

            # Get insider names and titles
            insider_details = []
            for _, row in window_rows.drop_duplicates('RPTOWNERCIK').iterrows():
                insider_details.append({
                    'name': str(row.get('RPTOWNERNAME', '?')),
                    'title': str(row.get('RPTOWNER_TITLE', '?')),
                    'is_ceo_cfo': is_ceo_cfo(row.get('RPTOWNER_TITLE'))
                })

            clusters.append({
                'ticker': str(ticker).strip(),
                'issuer_name': str(issuer_name),
                'cluster_date': cluster_date,
                'n_insiders': unique_insiders,
                'total_value': total_value,
                'has_ceo_cfo': has_ceo_cfo,
                'n_transactions': len(window_rows),
                'insiders': insider_details
            })

    print(f"\nClusters found: {len(clusters)}", file=sys.stderr)
    return clusters


def get_vix_history(start_date, end_date):
    """Get VIX daily close prices."""
    vix = safe_download('^VIX', start=start_date, end=end_date)
    if vix is not None and not vix.empty:
        return vix['Close']
    return pd.Series(dtype=float)


def compute_returns(clusters, hold_days=5):
    """Compute post-event returns for each cluster."""
    # Get unique tickers
    tickers = list(set(c['ticker'] for c in clusters))
    print(f"\nFetching prices for {len(tickers)} tickers + SPY + ^VIX...", file=sys.stderr)

    # Get date range
    min_date = min(c['cluster_date'] for c in clusters) - pd.Timedelta(days=30)
    max_date = max(c['cluster_date'] for c in clusters) + pd.Timedelta(days=30)

    # Fetch SPY benchmark
    spy = safe_download('SPY', start=min_date.strftime('%Y-%m-%d'), end=max_date.strftime('%Y-%m-%d'))
    spy_close = spy['Close'] if spy is not None and not spy.empty else pd.Series(dtype=float)

    # Fetch VIX
    vix = safe_download('^VIX', start=min_date.strftime('%Y-%m-%d'), end=max_date.strftime('%Y-%m-%d'))
    vix_close = vix['Close'] if vix is not None and not vix.empty else pd.Series(dtype=float)

    # Fetch all stock prices in batches
    price_cache = {}
    batch_size = 20
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        for ticker in batch:
            try:
                df = safe_download(ticker, start=min_date.strftime('%Y-%m-%d'), end=max_date.strftime('%Y-%m-%d'))
                if df is not None and not df.empty:
                    price_cache[ticker] = df['Close']
            except Exception:
                pass
        if i > 0 and i % 100 == 0:
            print(f"  Fetched {i}/{len(tickers)} tickers", file=sys.stderr)

    print(f"  Price data for {len(price_cache)}/{len(tickers)} tickers", file=sys.stderr)

    # Compute returns for each cluster
    results = []
    for c in clusters:
        ticker = c['ticker']
        cdate = c['cluster_date']

        if ticker not in price_cache:
            continue

        prices = price_cache[ticker]

        # Find entry date (next trading day after cluster date)
        future_prices = prices[prices.index >= cdate]
        if len(future_prices) < hold_days + 1:
            continue

        entry_price = future_prices.iloc[0]
        exit_price = future_prices.iloc[min(hold_days, len(future_prices)-1)]

        raw_return = (exit_price - entry_price) / entry_price * 100

        # SPY return for same period
        spy_future = spy_close[spy_close.index >= cdate]
        if len(spy_future) >= hold_days + 1:
            spy_entry = spy_future.iloc[0]
            spy_exit = spy_future.iloc[min(hold_days, len(spy_future)-1)]
            spy_return = (spy_exit - spy_entry) / spy_entry * 100
        else:
            spy_return = 0.0

        abnormal_return = raw_return - spy_return

        # VIX at cluster date
        vix_at_cluster = None
        vix_near = vix_close[vix_close.index <= cdate]
        if not vix_near.empty:
            vix_at_cluster = float(vix_near.iloc[-1])

        # Prior 20-day drawdown
        prior_prices = prices[prices.index < cdate].tail(20)
        if len(prior_prices) >= 5:
            prior_drawdown = (prior_prices.iloc[-1] - prior_prices.iloc[0]) / prior_prices.iloc[0] * 100
        else:
            prior_drawdown = None

        results.append({
            'ticker': ticker,
            'issuer_name': c['issuer_name'],
            'cluster_date': cdate.strftime('%Y-%m-%d'),
            'n_insiders': c['n_insiders'],
            'total_value': round(c['total_value'], 2),
            'has_ceo_cfo': c['has_ceo_cfo'],
            'n_transactions': c['n_transactions'],
            'vix_at_cluster': round(vix_at_cluster, 2) if vix_at_cluster else None,
            'prior_20d_return_pct': round(prior_drawdown, 2) if prior_drawdown is not None else None,
            'raw_return_5d_pct': round(float(raw_return), 2),
            'spy_return_5d_pct': round(float(spy_return), 2),
            'abnormal_return_5d_pct': round(float(abnormal_return), 2),
            'is_winner': float(abnormal_return) > 0.5  # >0.5% threshold
        })

    return results


def analyze_features(results):
    """Analyze which features predict positive abnormal returns."""
    df = pd.DataFrame(results)
    if df.empty:
        return {}

    analysis = {
        'total_clusters': len(df),
        'avg_abnormal_return': round(df['abnormal_return_5d_pct'].mean(), 2),
        'median_abnormal_return': round(df['abnormal_return_5d_pct'].median(), 2),
        'positive_rate': round((df['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1),
        'features': {}
    }

    # 1. CEO/CFO presence
    ceo_yes = df[df['has_ceo_cfo'] == True]
    ceo_no = df[df['has_ceo_cfo'] == False]
    analysis['features']['ceo_cfo_presence'] = {
        'with_ceo_cfo': {
            'n': len(ceo_yes),
            'avg_abnormal': round(ceo_yes['abnormal_return_5d_pct'].mean(), 2) if len(ceo_yes) > 0 else None,
            'median_abnormal': round(ceo_yes['abnormal_return_5d_pct'].median(), 2) if len(ceo_yes) > 0 else None,
            'positive_rate': round((ceo_yes['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1) if len(ceo_yes) > 0 else None
        },
        'without_ceo_cfo': {
            'n': len(ceo_no),
            'avg_abnormal': round(ceo_no['abnormal_return_5d_pct'].mean(), 2) if len(ceo_no) > 0 else None,
            'median_abnormal': round(ceo_no['abnormal_return_5d_pct'].median(), 2) if len(ceo_no) > 0 else None,
            'positive_rate': round((ceo_no['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1) if len(ceo_no) > 0 else None
        }
    }

    # 2. Cluster size tiers
    for size_label, size_min, size_max in [('3_insiders', 3, 3), ('4_5_insiders', 4, 5), ('6plus_insiders', 6, 100)]:
        subset = df[(df['n_insiders'] >= size_min) & (df['n_insiders'] <= size_max)]
        if len(subset) > 0:
            analysis['features'][f'cluster_size_{size_label}'] = {
                'n': len(subset),
                'avg_abnormal': round(subset['abnormal_return_5d_pct'].mean(), 2),
                'median_abnormal': round(subset['abnormal_return_5d_pct'].median(), 2),
                'positive_rate': round((subset['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1)
            }

    # 3. VIX tiers
    vix_df = df.dropna(subset=['vix_at_cluster'])
    for vix_label, vix_min, vix_max in [('vix_lt15', 0, 15), ('vix_15_20', 15, 20), ('vix_20_25', 20, 25), ('vix_25_30', 25, 30), ('vix_gt30', 30, 100)]:
        subset = vix_df[(vix_df['vix_at_cluster'] >= vix_min) & (vix_df['vix_at_cluster'] < vix_max)]
        if len(subset) > 0:
            analysis['features'][f'vix_{vix_label}'] = {
                'n': len(subset),
                'avg_abnormal': round(subset['abnormal_return_5d_pct'].mean(), 2),
                'median_abnormal': round(subset['abnormal_return_5d_pct'].median(), 2),
                'positive_rate': round((subset['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1)
            }

    # 4. Dollar value tiers
    for val_label, val_min, val_max in [('under_500k', 0, 500000), ('500k_2m', 500000, 2000000), ('2m_10m', 2000000, 10000000), ('over_10m', 10000000, 1e15)]:
        subset = df[(df['total_value'] >= val_min) & (df['total_value'] < val_max)]
        if len(subset) > 0:
            analysis['features'][f'value_{val_label}'] = {
                'n': len(subset),
                'avg_abnormal': round(subset['abnormal_return_5d_pct'].mean(), 2),
                'median_abnormal': round(subset['abnormal_return_5d_pct'].median(), 2),
                'positive_rate': round((subset['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1)
            }

    # 5. Prior drawdown
    dd_df = df.dropna(subset=['prior_20d_return_pct'])
    for dd_label, dd_min, dd_max in [('prior_drop_gt10', -100, -10), ('prior_drop_5_10', -10, -5), ('prior_drop_lt5', -5, 0), ('prior_flat_or_up', 0, 100)]:
        subset = dd_df[(dd_df['prior_20d_return_pct'] >= dd_min) & (dd_df['prior_20d_return_pct'] < dd_max)]
        if len(subset) > 0:
            analysis['features'][f'drawdown_{dd_label}'] = {
                'n': len(subset),
                'avg_abnormal': round(subset['abnormal_return_5d_pct'].mean(), 2),
                'median_abnormal': round(subset['abnormal_return_5d_pct'].median(), 2),
                'positive_rate': round((subset['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1)
            }

    # 6. CEO/CFO × VIX interaction (most important for trade decisions)
    ceo_vix_low = df[(df['has_ceo_cfo'] == True) & (df['vix_at_cluster'].notna()) & (df['vix_at_cluster'] < 20)]
    ceo_vix_mid = df[(df['has_ceo_cfo'] == True) & (df['vix_at_cluster'].notna()) & (df['vix_at_cluster'] >= 20) & (df['vix_at_cluster'] < 25)]
    ceo_vix_high = df[(df['has_ceo_cfo'] == True) & (df['vix_at_cluster'].notna()) & (df['vix_at_cluster'] >= 25)]

    analysis['features']['ceo_cfo_x_vix'] = {}
    for label, subset in [('ceo_vix_lt20', ceo_vix_low), ('ceo_vix_20_25', ceo_vix_mid), ('ceo_vix_gte25', ceo_vix_high)]:
        if len(subset) > 0:
            analysis['features']['ceo_cfo_x_vix'][label] = {
                'n': len(subset),
                'avg_abnormal': round(subset['abnormal_return_5d_pct'].mean(), 2),
                'median_abnormal': round(subset['abnormal_return_5d_pct'].median(), 2),
                'positive_rate': round((subset['abnormal_return_5d_pct'] > 0.5).mean() * 100, 1)
            }

    # 7. Best/worst clusters
    top5 = df.nlargest(5, 'abnormal_return_5d_pct')[['ticker', 'cluster_date', 'n_insiders', 'has_ceo_cfo', 'vix_at_cluster', 'abnormal_return_5d_pct', 'total_value']].to_dict('records')
    bot5 = df.nsmallest(5, 'abnormal_return_5d_pct')[['ticker', 'cluster_date', 'n_insiders', 'has_ceo_cfo', 'vix_at_cluster', 'abnormal_return_5d_pct', 'total_value']].to_dict('records')
    analysis['top_5_winners'] = top5
    analysis['top_5_losers'] = bot5

    return analysis


def main():
    parser = argparse.ArgumentParser(description='Insider Cluster Feature Analysis')
    parser.add_argument('--start-year', type=int, default=2020)
    parser.add_argument('--end-year', type=int, default=2025)
    parser.add_argument('--hold-days', type=int, default=5)
    parser.add_argument('--min-insiders', type=int, default=3)
    parser.add_argument('--min-value', type=float, default=50000)
    parser.add_argument('--output', type=str, default='/tmp/insider_cluster_features.json')
    args = parser.parse_args()

    print(f"Finding clusters {args.start_year}-{args.end_year}, min {args.min_insiders} insiders, min ${args.min_value:,.0f}...", file=sys.stderr)
    clusters = find_clusters(
        start_year=args.start_year,
        end_year=args.end_year,
        min_insiders=args.min_insiders,
        min_value=args.min_value
    )

    if not clusters:
        print(json.dumps({"error": "No clusters found"}))
        return

    print(f"\nComputing {args.hold_days}d returns for {len(clusters)} clusters...", file=sys.stderr)
    results = compute_returns(clusters, hold_days=args.hold_days)

    print(f"\nAnalyzing features for {len(results)} clusters with valid price data...", file=sys.stderr)
    analysis = analyze_features(results)

    # Save full results
    output = {
        'analysis': analysis,
        'clusters': results
    }
    with open(args.output, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nFull results saved to {args.output}", file=sys.stderr)

    # Print summary to stdout
    print(json.dumps(analysis, indent=2, default=str))


if __name__ == '__main__':
    main()
