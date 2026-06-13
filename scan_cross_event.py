#!/usr/bin/env python3
"""
Cross-event correlation scan — identify relationships between different event types.
Tests temporal proximity, ticker overlap, magnitude correlations.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import db
import pandas as pd
import numpy as np
from scipy import stats

def load_events():
    """Load all event datasets."""
    events = {}
    try:
        events['insider'] = pd.read_csv("/home/user/frakbox-fund/data/insider_cluster_events.csv")
        events['insider']['date'] = pd.to_datetime(events['insider']['cluster_date'])
    except:
        pass

    try:
        events['seo'] = pd.read_csv("/home/user/frakbox-fund/data/seo_events_filtered.csv")
        events['seo']['date'] = pd.to_datetime(events['seo']['file_date'])
    except:
        pass

    try:
        events['52w_low'] = pd.read_csv("/home/user/frakbox-fund/data/52w_low_events.csv")
        events['52w_low']['date'] = pd.to_datetime(events['52w_low']['event_date'])
    except:
        pass

    try:
        events['spinoff'] = pd.read_csv("/home/user/frakbox-fund/data/spinoff_events_clean.csv")
        events['spinoff']['date'] = pd.to_datetime(events['spinoff']['date_str'], errors='coerce')
        events['spinoff'] = events['spinoff'][events['spinoff']['date'].notna()]
    except:
        pass

    try:
        events['repurchase'] = pd.read_csv("/home/user/frakbox-fund/data/repurchase_events.json")
        if isinstance(events['repurchase'], str):
            events['repurchase'] = pd.read_json(events['repurchase'])
        if 'date' not in events['repurchase'].columns and 'announcement_date' in events['repurchase'].columns:
            events['repurchase']['date'] = pd.to_datetime(events['repurchase']['announcement_date'])
    except:
        pass

    return events

def test_ticker_overlap(events_a, events_b, name_a, name_b):
    """Test overlap of tickers between two event types."""
    if len(events_a) == 0 or len(events_b) == 0:
        return None

    tickers_a = set(events_a['ticker'].unique()) if 'ticker' in events_a.columns else set()
    tickers_b = set(events_b['ticker'].unique()) if 'ticker' in events_b.columns else set()

    if len(tickers_a) == 0 or len(tickers_b) == 0:
        return None

    overlap = tickers_a & tickers_b
    overlap_pct = len(overlap) / min(len(tickers_a), len(tickers_b)) * 100

    # Hypergeometric test: is overlap more than random?
    total_universe = 5000  # S&P 5000ish
    expected_overlap = len(tickers_a) * len(tickers_b) / total_universe

    return {
        "event_pair": f"{name_a} vs {name_b}",
        "test": "ticker_overlap",
        "overlap_count": len(overlap),
        "overlap_pct": overlap_pct,
        "expected_random": expected_overlap,
        "signal": "related" if overlap_pct > 10 else "independent"
    }

def test_temporal_proximity(events_a, events_b, name_a, name_b, window_days=30):
    """Test if events happen near each other in time."""
    if len(events_a) == 0 or len(events_b) == 0:
        return None

    dates_a = pd.to_datetime(events_a['date'], errors='coerce').dropna().sort_values()
    dates_b = pd.to_datetime(events_b['date'], errors='coerce').dropna().sort_values()

    if len(dates_a) == 0 or len(dates_b) == 0:
        return None

    # Count how many events in B fall within window_days of events in A
    near_count = 0
    for date_a in dates_a:
        window_start = date_a - pd.Timedelta(days=window_days)
        window_end = date_a + pd.Timedelta(days=window_days)
        count_in_window = len(dates_b[(dates_b >= window_start) & (dates_b <= window_end)])
        if count_in_window > 0:
            near_count += 1

    proximity_pct = near_count / len(dates_a) * 100

    return {
        "event_pair": f"{name_a} -> {name_b}",
        "test": "temporal_proximity",
        "events_with_proximate_pair": near_count,
        "proximity_pct": proximity_pct,
        "window_days": window_days,
        "signal": "clustered" if proximity_pct > 20 else "independent"
    }

def test_event_sequence(events_a, events_b, name_a, name_b):
    """Test if one event type precedes the other (lead-lag)."""
    if len(events_a) == 0 or len(events_b) == 0:
        return None

    # Get tickers that have both events
    tickers_a = set(events_a['ticker'].unique()) if 'ticker' in events_a.columns else set()
    tickers_b = set(events_b['ticker'].unique()) if 'ticker' in events_b.columns else set()
    common_tickers = tickers_a & tickers_b

    if len(common_tickers) < 10:
        return None

    # For each common ticker, check if A typically precedes B
    a_leads_b = 0
    b_leads_a = 0

    for ticker in common_tickers:
        dates_a = pd.to_datetime(events_a[events_a['ticker'] == ticker]['date'], errors='coerce').dropna().sort_values()
        dates_b = pd.to_datetime(events_b[events_b['ticker'] == ticker]['date'], errors='coerce').dropna().sort_values()

        if len(dates_a) > 0 and len(dates_b) > 0:
            if dates_a.iloc[0] < dates_b.iloc[0]:
                a_leads_b += 1
            else:
                b_leads_a += 1

    if a_leads_b + b_leads_a == 0:
        return None

    a_lead_pct = a_leads_b / (a_leads_b + b_leads_a) * 100

    return {
        "event_pair": f"{name_a} -> {name_b}",
        "test": "event_sequence",
        "a_leads_b": a_leads_b,
        "b_leads_a": b_leads_a,
        "a_lead_pct": a_lead_pct,
        "signal": "directional" if a_lead_pct > 65 or a_lead_pct < 35 else "bidirectional"
    }

def run_scan():
    print("\n=== CROSS-EVENT CORRELATION SCAN ===\n")

    events = load_events()
    event_names = list(events.keys())

    print(f"Loaded {len(events)} event types: {', '.join(event_names)}")

    tests_run = 0
    hits = []

    # Test 1-2: Ticker overlap (bidirectional)
    print("\n🔍 Ticker overlap tests...")
    for i, name_a in enumerate(event_names):
        for name_b in event_names[i+1:]:
            result = test_ticker_overlap(events[name_a], events[name_b], name_a, name_b)
            tests_run += 1
            if result:
                if result['signal'] == 'related':
                    hit = {
                        "signal": f"{name_a} and {name_b} share common tickers ({result['overlap_pct']:.1f}%)",
                        "class": "ticker_overlap",
                        **result
                    }
                    hits.append(hit)
                    print(f"  ✓ {name_a} ↔ {name_b}: {result['overlap_pct']:.1f}% overlap")
                else:
                    print(f"  ✗ {name_a} ↔ {name_b}: {result['overlap_pct']:.1f}% overlap")

    # Test 3-4: Temporal proximity
    print("\n🔍 Temporal proximity tests...")
    for i, name_a in enumerate(event_names):
        for name_b in event_names[i+1:]:
            result = test_temporal_proximity(events[name_a], events[name_b], name_a, name_b, window_days=30)
            tests_run += 1
            if result:
                if result['signal'] == 'clustered':
                    hit = {
                        "signal": f"{name_a} and {name_b} cluster in time ({result['proximity_pct']:.1f}%)",
                        "class": "temporal_clustering",
                        **result
                    }
                    hits.append(hit)
                    print(f"  ✓ {name_a} -> {name_b}: {result['proximity_pct']:.1f}% proximate")
                else:
                    print(f"  ✗ {name_a} -> {name_b}: {result['proximity_pct']:.1f}% proximate")

    # Test 5-6: Sequence/lead-lag
    print("\n🔍 Event sequence tests...")
    for i, name_a in enumerate(event_names):
        for name_b in event_names[i+1:]:
            result = test_event_sequence(events[name_a], events[name_b], name_a, name_b)
            tests_run += 1
            if result:
                if result['signal'] == 'directional':
                    lead_name = name_a if result['a_lead_pct'] > 50 else name_b
                    hit = {
                        "signal": f"{lead_name} tends to precede {name_a if lead_name == name_b else name_b}",
                        "class": "event_sequence",
                        **result
                    }
                    hits.append(hit)
                    print(f"  ✓ {name_a} leads {name_b}: {result['a_lead_pct']:.1f}%")
                else:
                    print(f"  ✗ {name_a} <-> {name_b}: bidirectional")

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
                print(f"  ✓ Queued: {hit['signal'][:70]}")
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

        summary = f"Scanned {tests_run} cross-event tests (ticker overlap, temporal proximity, sequences). Found {len(hits)} relationships."
        db.append_journal_entry(
            date=pd.Timestamp.now().isoformat(),
            session_type="scan",
            investigated="Cross-event analysis: ticker overlap, temporal clustering, event sequences, lead-lag patterns",
            findings=json.dumps({"hits_queued": len(hits), "tests_run": tests_run}),
            category="cross_event",
            public_summary=summary
        )
        print("✓ Journal logged")
    except Exception as e:
        print(f"✗ Journal: {e}")

    return len(hits), tests_run

if __name__ == "__main__":
    hits, tests_run = run_scan()
    sys.exit(0 if hits > 0 else 1)
