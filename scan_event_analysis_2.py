#!/usr/bin/env python3
"""
Event analysis scan 2 — deeper statistical tests on event characteristics.
Tests correlation patterns, magnitude effects, and frequency anomalies.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import db
import pandas as pd
import numpy as np
from scipy import stats

def test_magnitude_percentiles(event_df, event_name, mag_col):
    """Test if events cluster at specific magnitude levels."""
    if mag_col not in event_df.columns or len(event_df) < 20:
        return None

    vals = pd.to_numeric(event_df[mag_col], errors='coerce').dropna()
    if len(vals) < 15:
        return None

    # Split into percentile bins
    percentiles = [10, 25, 50, 75, 90]
    bins = np.percentile(vals, percentiles)

    # Count events in each decile
    decile_counts = []
    for i in range(10):
        lower = np.percentile(vals, i * 10)
        upper = np.percentile(vals, (i + 1) * 10)
        count = len(vals[(vals >= lower) & (vals <= upper)])
        decile_counts.append(count)

    # Chi-square test for uniform distribution
    expected = sum(decile_counts) / 10
    chi2, p_val = stats.chisquare(decile_counts, [expected] * 10)

    return {
        "event_type": event_name,
        "test": "magnitude_percentiles",
        "chi2": chi2,
        "p_value": p_val,
        "decile_variance": np.std(decile_counts),
        "signal": "clustered" if p_val < 0.05 else "dispersed"
    }

def test_ticker_concentration(event_df, event_name):
    """Test if events concentrate in a few tickers (Pareto effect)."""
    if 'ticker' not in event_df.columns or len(event_df) < 20:
        return None

    ticker_counts = event_df['ticker'].value_counts()

    # Top 10% of tickers
    n_top_10 = max(1, len(ticker_counts) // 10)
    top_10_pct = ticker_counts.head(n_top_10).sum() / len(event_df) * 100

    # Pareto principle: top 20% should have ~80%
    n_top_20 = max(1, len(ticker_counts) // 5)
    top_20_pct = ticker_counts.head(n_top_20).sum() / len(event_df) * 100

    return {
        "event_type": event_name,
        "test": "ticker_concentration",
        "n_unique_tickers": len(ticker_counts),
        "top_10_pct": top_10_pct,
        "top_20_pct": top_20_pct,
        "herfindahl": (ticker_counts / len(event_df)).pow(2).sum(),
        "signal": "concentrated" if top_20_pct > 60 else "dispersed"
    }

def test_event_gap_distribution(event_df, event_name, date_col):
    """Test if inter-event gaps follow log-normal or exponential."""
    if len(event_df) < 30:
        return None

    dates = pd.to_datetime(event_df[date_col], errors='coerce').dropna().sort_values()
    gaps = dates.diff().dt.days.dropna()

    if len(gaps) < 15:
        return None

    # Fit to exponential
    lambda_param = 1.0 / gaps.mean()
    expected_exp = stats.expon.ppf(np.arange(1, len(gaps) + 1) / (len(gaps) + 1), scale=1/lambda_param)

    # KS test
    ks_stat, ks_p = stats.kstest(gaps, stats.expon(scale=1/lambda_param).cdf)

    return {
        "event_type": event_name,
        "test": "gap_distribution",
        "mean_gap": gaps.mean(),
        "std_gap": gaps.std(),
        "ks_statistic": ks_stat,
        "ks_p_value": ks_p,
        "signal": "non_exponential" if ks_p < 0.05 else "exponential"
    }

def test_event_seasonality(event_df, event_name, date_col):
    """Test if events cluster by month or quarter."""
    if len(event_df) < 30:
        return None

    dates = pd.to_datetime(event_df[date_col], errors='coerce').dropna()

    # Count by month
    by_month = dates.dt.month.value_counts()
    month_counts = [by_month.get(i, 0) for i in range(1, 13)]

    if len(month_counts) < 12 or sum(month_counts) < 30:
        return None

    # Chi-square for uniform distribution
    expected = sum(month_counts) / 12
    chi2, p_val = stats.chisquare(month_counts, [expected] * 12)

    # Count by quarter
    by_q = dates.dt.quarter.value_counts()
    q_counts = [by_q.get(i, 0) for i in range(1, 5)]
    expected_q = sum(q_counts) / 4
    chi2_q, p_q = stats.chisquare(q_counts, [expected_q] * 4)

    return {
        "event_type": event_name,
        "test": "seasonality",
        "monthly_chi2_p": p_val,
        "quarterly_chi2_p": p_q,
        "best_month": np.argmax(month_counts) + 1,
        "worst_month": np.argmin(month_counts) + 1,
        "signal": "seasonal" if p_val < 0.10 else "uniform"
    }

def run_scan():
    print("\n=== EVENT ANALYSIS SCAN 2 ===\n")

    # Load events
    events = {}
    try:
        events['insider'] = pd.read_csv("/home/user/frakbox-fund/data/insider_cluster_events.csv")
        events['insider']['cluster_date'] = pd.to_datetime(events['insider']['cluster_date'])
    except:
        pass

    try:
        events['seo'] = pd.read_csv("/home/user/frakbox-fund/data/seo_events_filtered.csv")
        events['seo']['file_date'] = pd.to_datetime(events['seo']['file_date'])
    except:
        pass

    try:
        events['52w_low'] = pd.read_csv("/home/user/frakbox-fund/data/52w_low_events.csv")
        events['52w_low']['event_date'] = pd.to_datetime(events['52w_low']['event_date'])
    except:
        pass

    try:
        events['spinoff'] = pd.read_csv("/home/user/frakbox-fund/data/spinoff_events_clean.csv")
        events['spinoff']['date_str'] = pd.to_datetime(events['spinoff']['date_str'], errors='coerce')
    except:
        pass

    hits = []
    tests_run = 0

    # --- Magnitude percentile tests ---
    print("🔍 Magnitude distribution tests...")
    mag_tests = [
        ('insider', 'total_value'),
        ('52w_low', 'pct_below_52w_low')
    ]

    for evt_name, mag_col in mag_tests:
        if evt_name in events:
            result = test_magnitude_percentiles(events[evt_name], evt_name, mag_col)
            tests_run += 1
            if result and result['signal'] == 'clustered':
                hit = {
                    "signal": f"{evt_name}: magnitude clustering at specific levels",
                    "class": "magnitude_anomaly",
                    **result
                }
                hits.append(hit)
                print(f"  ✓ {evt_name}: chi2_p={result['p_value']:.4f}, decile_var={result['decile_variance']:.1f}")
            else:
                print(f"  ✗ {evt_name}: uniform distribution")

    # --- Ticker concentration tests ---
    print("\n🔍 Ticker concentration tests...")
    for evt_name in events.keys():
        result = test_ticker_concentration(events[evt_name], evt_name)
        tests_run += 1
        if result:
            if result['signal'] == 'concentrated':
                hit = {
                    "signal": f"{evt_name}: Pareto concentration in tickers",
                    "class": "ticker_concentration",
                    **result
                }
                hits.append(hit)
                print(f"  ✓ {evt_name}: top_20%={result['top_20_pct']:.1f}%, HHI={result['herfindahl']:.4f}")
            else:
                print(f"  ✗ {evt_name}: dispersed across tickers")

    # --- Gap distribution tests ---
    print("\n🔍 Inter-event gap tests...")
    gap_date_cols = {
        'insider': 'cluster_date',
        'seo': 'file_date',
        '52w_low': 'event_date',
        'spinoff': 'date_str'
    }

    for evt_name, date_col in gap_date_cols.items():
        if evt_name in events:
            result = test_event_gap_distribution(events[evt_name], evt_name, date_col)
            tests_run += 1
            if result:
                if result['signal'] == 'non_exponential':
                    hit = {
                        "signal": f"{evt_name}: non-exponential inter-event gaps",
                        "class": "gap_anomaly",
                        **result
                    }
                    hits.append(hit)
                    print(f"  ✓ {evt_name}: KS_p={result['ks_p_value']:.4f}, mean_gap={result['mean_gap']:.1f}d")
                else:
                    print(f"  ✗ {evt_name}: exponential gaps (random Poisson)")

    # --- Seasonality tests ---
    print("\n🔍 Seasonality tests...")
    for evt_name, date_col in gap_date_cols.items():
        if evt_name in events:
            result = test_event_seasonality(events[evt_name], evt_name, date_col)
            tests_run += 1
            if result and result['signal'] == 'seasonal':
                hit = {
                    "signal": f"{evt_name}: seasonal clustering",
                    "class": "seasonality",
                    **result
                }
                hits.append(hit)
                print(f"  ✓ {evt_name}: monthly_p={result['monthly_chi2_p']:.4f}, best_m={result['best_month']}")
            else:
                if result:
                    print(f"  ✗ {evt_name}: uniform seasonality (p={result['monthly_chi2_p']:.3f})")

    print(f"\n{'='*50}")
    print(f"Tests run: {tests_run}")
    print(f"Hits: {len(hits)}")

    # Queue hits
    if hits:
        print(f"\n📋 Queueing {len(hits)} hits...")
        for hit in hits:
            try:
                hit_clean = {}
                for k, v in hit.items():
                    if isinstance(v, np.integer):
                        hit_clean[k] = int(v)
                    elif isinstance(v, np.floating):
                        hit_clean[k] = float(v)
                    else:
                        hit_clean[k] = v

                db.add_research_task(
                    category="scan_hit",
                    question=hit["signal"],
                    priority=2,
                    reasoning=json.dumps(hit_clean),
                    depends_on=None
                )
                print(f"  ✓ Queued: {hit['signal'][:60]}")
            except Exception as e:
                print(f"  ✗ Failed: {e}")

    # Log journal
    try:
        hits_clean = []
        for hit in hits:
            hit_clean = {}
            for k, v in hit.items():
                if isinstance(v, np.integer):
                    hit_clean[k] = int(v)
                elif isinstance(v, np.floating):
                    hit_clean[k] = float(v)
                else:
                    hit_clean[k] = v
            hits_clean.append(hit_clean)

        summary = f"Scanned {tests_run} event-analysis tests (magnitude, tickers, gaps, seasonality). Found {len(hits)} patterns."
        db.append_journal_entry(
            date=pd.Timestamp.now().isoformat(),
            session_type="scan",
            investigated="Event characteristics: magnitude clustering, ticker concentration, gap distributions, seasonality",
            findings=json.dumps({"hits_queued": len(hits), "tests_run": tests_run}),
            category="event_analysis_2",
            public_summary=summary
        )
        print("✓ Journal logged")
    except Exception as e:
        print(f"✗ Journal: {e}")

    return len(hits), tests_run

if __name__ == "__main__":
    hits, tests_run = run_scan()
    sys.exit(0 if hits > 0 else 1)
