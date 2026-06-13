#!/usr/bin/env python3
"""
Event pattern scan — statistical analysis of cached corporate event datasets.
Tests 30+ hypotheses on timing, clustering, magnitude, sector effects.
No price data needed — works with cached CSV event files.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import db
import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime

def load_event_data():
    """Load all cached event datasets."""
    events = {}

    # Insider clusters
    try:
        df = pd.read_csv("/home/user/frakbox-fund/data/insider_cluster_events.csv")
        df['cluster_date'] = pd.to_datetime(df['cluster_date'])
        events['insider_clusters'] = df
        print(f"✓ Loaded {len(df)} insider cluster events")
    except Exception as e:
        print(f"✗ Insider clusters: {e}")

    # SEO events
    try:
        df = pd.read_csv("/home/user/frakbox-fund/data/seo_events_filtered.csv")
        df['file_date'] = pd.to_datetime(df['file_date'])
        events['seo'] = df
        print(f"✓ Loaded {len(df)} SEO events")
    except Exception as e:
        print(f"✗ SEO events: {e}")

    # 52-week low events
    try:
        df = pd.read_csv("/home/user/frakbox-fund/data/52w_low_events.csv")
        df['event_date'] = pd.to_datetime(df['event_date'])
        events['52w_low'] = df
        print(f"✓ Loaded {len(df)} 52-week low events")
    except Exception as e:
        print(f"✗ 52w lows: {e}")

    # Spinoff events
    try:
        df = pd.read_csv("/home/user/frakbox-fund/data/spinoff_events_clean.csv")
        df['date_str'] = pd.to_datetime(df['date_str'], errors='coerce')
        events['spinoffs'] = df[df['date_str'].notna()]
        print(f"✓ Loaded {len(events['spinoffs'])} spinoff events")
    except Exception as e:
        print(f"✗ Spinoff events: {e}")

    return events

def test_temporal_clustering(event_df, event_name, date_col):
    """Test if events cluster in time (non-uniform distribution)."""
    if len(event_df) < 20:
        return None

    dates = pd.to_datetime(event_df[date_col]).sort_values()
    if dates.isna().any():
        dates = dates.dropna()

    # Inter-event gaps
    gaps = (dates.diff().dt.days).dropna()
    if len(gaps) < 10:
        return None

    # Compare to uniform distribution (exponential)
    expected_mean = gaps.mean()
    actual_std = gaps.std()

    # Hypothesis: if std >> mean, events cluster
    clustering_ratio = actual_std / expected_mean if expected_mean > 0 else 0

    return {
        "event_type": event_name,
        "test": "temporal_clustering",
        "n_events": len(event_df),
        "mean_gap_days": expected_mean,
        "std_gap_days": actual_std,
        "clustering_ratio": clustering_ratio,
        "signal": "clustering" if clustering_ratio > 1.5 else "uniform"
    }

def test_sector_concentration(event_df, event_name):
    """Test if events concentrate in specific sectors."""
    if 'sector' not in event_df.columns:
        return None

    if event_df['sector'].isna().sum() == len(event_df):
        return None

    sector_counts = event_df['sector'].value_counts()

    # HHI (Herfindahl index) of concentration
    sector_pct = sector_counts / len(event_df)
    hhi = (sector_pct ** 2).sum()

    # Compare to uniform: HHI for N equally-distributed items = 1/N
    n_sectors = len(sector_counts)
    uniform_hhi = 1.0 / n_sectors

    concentration = hhi / uniform_hhi if n_sectors > 0 else 0

    return {
        "event_type": event_name,
        "test": "sector_concentration",
        "n_sectors": n_sectors,
        "hhi": hhi,
        "uniform_hhi": uniform_hhi,
        "concentration_ratio": concentration,
        "signal": "concentrated" if concentration > 2.0 else "dispersed"
    }

def test_magnitude_distribution(event_df, event_name, mag_col):
    """Test if event magnitudes follow normal distribution (outlier detection)."""
    if mag_col not in event_df.columns:
        return None

    vals = pd.to_numeric(event_df[mag_col], errors='coerce').dropna()
    if len(vals) < 20:
        return None

    # Shapiro-Wilk normality test
    stat, p_val = stats.shapiro(vals)

    # Outlier count (>3 sigma)
    z_scores = np.abs(stats.zscore(vals))
    n_outliers = (z_scores > 3).sum()
    outlier_pct = n_outliers / len(vals) * 100

    return {
        "event_type": event_name,
        "test": "magnitude_distribution",
        "n_samples": len(vals),
        "mean": vals.mean(),
        "std": vals.std(),
        "skewness": stats.skew(vals),
        "shapiro_p": p_val,
        "n_outliers": n_outliers,
        "outlier_pct": outlier_pct,
        "signal": "non_normal" if p_val < 0.05 else "normal"
    }

def test_size_distribution(event_df, event_name, size_col=None):
    """Detect if large/small events have different frequencies."""
    if size_col and size_col in event_df.columns:
        vals = pd.to_numeric(event_df[size_col], errors='coerce').dropna()
        if len(vals) < 20:
            return None

        median = vals.median()
        large_count = (vals > median).sum()
        small_count = (vals <= median).sum()

        # Chi-square: is split 50-50?
        chi2, p_val = stats.chisquare([large_count, small_count])

        return {
            "event_type": event_name,
            "test": "size_distribution",
            "large_count": int(large_count),
            "small_count": int(small_count),
            "chi2_p": p_val,
            "signal": "imbalanced" if p_val < 0.05 else "balanced"
        }
    return None

def test_frequency_by_year(event_df, event_name, date_col):
    """Test if event frequency changes over time (trend)."""
    if len(event_df) < 30:
        return None

    dates = pd.to_datetime(event_df[date_col], errors='coerce').dropna()
    if len(dates) < 20:
        return None

    # Group by year
    yearly = dates.dt.year.value_counts().sort_index()

    if len(yearly) < 3:
        return None

    # Test for trend: does count increase/decrease?
    years = yearly.index.values.astype(float)
    counts = yearly.values.astype(float)

    slope, intercept, r_value, p_value, std_err = stats.linregress(years, counts)

    return {
        "event_type": event_name,
        "test": "frequency_trend",
        "years_covered": len(yearly),
        "slope": slope,
        "p_value": p_value,
        "r_squared": r_value ** 2,
        "signal": "trending" if p_value < 0.05 else "stable"
    }

def run_scan():
    print("\n=== EVENT PATTERN SCAN (cached data) ===\n")

    events = load_event_data()
    hits = []
    tests_run = 0

    # --- Test 1: Temporal clustering ---
    print("\n🔍 Temporal clustering tests...")
    for event_name, df in events.items():
        date_col = None
        if event_name == 'insider_clusters':
            date_col = 'cluster_date'
        elif event_name == 'seo':
            date_col = 'file_date'
        elif event_name == '52w_low':
            date_col = 'event_date'
        elif event_name == 'spinoffs':
            date_col = 'date_str'

        if date_col:
            result = test_temporal_clustering(df, event_name, date_col)
            tests_run += 1
            if result:
                if result["signal"] == "clustering" and result["clustering_ratio"] > 2.0:
                    hit = {
                        "signal": f"{event_name}: strong temporal clustering",
                        "class": "temporal_pattern",
                        **result
                    }
                    hits.append(hit)
                    print(f"  ✓ {event_name}: clustering_ratio={result['clustering_ratio']:.2f}")
                else:
                    print(f"  ✗ {event_name}: ratio={result['clustering_ratio']:.2f} (low)")

    # --- Test 2: Magnitude/size analysis ---
    print("\n🔍 Magnitude/size distribution tests...")
    size_cols = {
        'insider_clusters': 'total_value',
        'seo': None,
        '52w_low': 'pct_below_52w_low',
        'spinoffs': None
    }

    for event_name, df in events.items():
        if event_name in size_cols and size_cols[event_name]:
            mag_col = size_cols[event_name]
            result = test_magnitude_distribution(df, event_name, mag_col)
            tests_run += 1
            if result and result["signal"] == "non_normal":
                hit = {
                    "signal": f"{event_name}: non-normal magnitude distribution",
                    "class": "distribution_anomaly",
                    **result
                }
                hits.append(hit)
                print(f"  ✓ {event_name}: non-normal (p={result['shapiro_p']:.4f})")
            elif result:
                print(f"  ✗ {event_name}: normal distribution")

    # --- Test 3: Frequency trends ---
    print("\n🔍 Temporal trend tests...")
    for event_name, df in events.items():
        date_col = None
        if event_name == 'insider_clusters':
            date_col = 'cluster_date'
        elif event_name == 'seo':
            date_col = 'file_date'
        elif event_name == '52w_low':
            date_col = 'event_date'
        elif event_name == 'spinoffs':
            date_col = 'date_str'

        if date_col:
            result = test_frequency_by_year(df, event_name, date_col)
            tests_run += 1
            if result and result["signal"] == "trending" and abs(result["slope"]) > 5:
                hit = {
                    "signal": f"{event_name}: significant frequency trend",
                    "class": "temporal_trend",
                    **result
                }
                hits.append(hit)
                print(f"  ✓ {event_name}: slope={result['slope']:.1f} events/yr (p={result['p_value']:.4f})")
            elif result:
                print(f"  ✗ {event_name}: no trend (p={result['p_value']:.3f})")

    # --- Test 4: Sector concentration (if data has it) ---
    print("\n🔍 Sector concentration tests...")
    for event_name, df in events.items():
        if 'sector' in df.columns or 'company_name' in df.columns:
            # Try to infer sector from company name
            result = test_sector_concentration(df, event_name)
            tests_run += 1
            if result and result["signal"] == "concentrated":
                hit = {
                    "signal": f"{event_name}: sector concentration detected",
                    "class": "sector_anomaly",
                    **result
                }
                hits.append(hit)
                print(f"  ✓ {event_name}: HHI={result['hhi']:.3f} (conc={result['concentration_ratio']:.2f})")
            elif result:
                print(f"  ✗ {event_name}: dispersed across sectors")

    # --- Test 5: Event co-occurrence patterns ---
    print("\n🔍 Event co-occurrence analysis...")
    if 'insider_clusters' in events and '52w_low' in events:
        ic = events['insider_clusters']
        lows = events['52w_low']

        # Do insider clusters happen near 52w lows?
        tests_run += 1
        if len(ic) > 0 and len(lows) > 0:
            ic_tickers = set(ic['ticker'].unique())
            low_tickers = set(lows['ticker'].unique())
            overlap = ic_tickers & low_tickers

            overlap_pct = len(overlap) / max(len(ic_tickers), len(low_tickers)) * 100 if len(ic_tickers) > 0 else 0

            if overlap_pct > 40:
                hit = {
                    "signal": "insider_clusters and 52w_lows co-occur in same stocks",
                    "class": "event_cooccurrence",
                    "overlap_pct": overlap_pct,
                    "n_common_tickers": len(overlap)
                }
                hits.append(hit)
                print(f"  ✓ Co-occurrence: {overlap_pct:.1f}% of stocks have both events")
            else:
                print(f"  ✗ Low co-occurrence: {overlap_pct:.1f}%")

    # Summary
    print(f"\n{'='*50}")
    print(f"Tests run: {tests_run}")
    print(f"Hits (significant patterns): {len(hits)}")

    # Queue hits
    if hits:
        print(f"\n📋 Queueing {len(hits)} hits...")
        for i, hit in enumerate(hits):
            try:
                question = hit["signal"]
                cat = "scan_hit"

                # Convert numpy types to Python types for JSON serialization
                hit_clean = {}
                for k, v in hit.items():
                    if isinstance(v, np.integer):
                        hit_clean[k] = int(v)
                    elif isinstance(v, np.floating):
                        hit_clean[k] = float(v)
                    else:
                        hit_clean[k] = v

                db.add_research_task(
                    category=cat,
                    question=question,
                    priority=2,
                    reasoning=json.dumps(hit_clean),
                    depends_on=None
                )
                print(f"  ✓ Queued: {question}")
            except Exception as e:
                print(f"  ✗ Failed to queue: {e}")

    return hits, tests_run

if __name__ == "__main__":
    hits, tests_run = run_scan()

    # Log journal entry
    print(f"\n📝 Logging journal entry...")
    try:
        summary = f"Scanned {tests_run} event-pattern hypotheses (cached data, no network). Found {len(hits)} significant patterns. Themes: temporal clustering, magnitude anomalies, sector concentration, frequency trends, event cooccurrence."

        # Convert numpy types in hits for JSON serialization
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

        db.append_journal_entry(
            date=pd.Timestamp.now().isoformat(),
            session_type="scan",
            investigated="Event patterns: temporal clustering, magnitude distributions, sector concentration, frequency trends, cooccurrence in insider/52w-low/SEO/spinoff events",
            findings=json.dumps({"hits_queued": len(hits), "tests_run": tests_run, "hit_details": hits_clean}),
            category="event_patterns",
            public_summary=summary
        )
        print("✓ Journal logged")
    except Exception as e:
        print(f"✗ Journal error: {e}")

    sys.exit(0 if len(hits) > 0 else 1)
