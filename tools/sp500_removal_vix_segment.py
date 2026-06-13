"""
SP500 Removal Backtest — VIX Segmentation Analysis
Segments removal events by VIX level at announcement date.
Computes mean abnormal return, direction %, and p-value for VIX>25 vs VIX<25.
"""

import json
import sys
import os
from datetime import datetime, timedelta
import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download

RESULTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools/sp500_removal_backtest_results.json')
VIX_THRESHOLD = 25
HORIZONS = [1, 3, 5, 10, 20]


def fetch_vix_closes(start_date: str, end_date: str) -> dict:
    """Download VIX closes for date range. Returns {date_str: vix_close}."""
    df = safe_download('^VIX', start=start_date, end=end_date)
    if df is None or df.empty:
        return {}
    # Handle MultiIndex columns from yfinance
    if hasattr(df.columns, 'levels'):
        df.columns = df.columns.get_level_values(0)
    close_col = 'Close' if 'Close' in df.columns else df.columns[0]
    result = {}
    for idx, row in df.iterrows():
        date_str = str(idx)[:10]
        result[date_str] = float(row[close_col])
    return result


def get_vix_for_event(event_date: str, vix_data: dict) -> float | None:
    """Get VIX close on the event date, or look back up to 5 days if missing (holiday/weekend)."""
    dt = datetime.strptime(event_date, '%Y-%m-%d')
    for offset in range(6):
        candidate = (dt - timedelta(days=offset)).strftime('%Y-%m-%d')
        if candidate in vix_data:
            return vix_data[candidate]
    return None


def direction_pct(values, short_signal=True):
    """% of events where short signal was correct (negative abnormal return)."""
    if not values:
        return None
    if short_signal:
        correct = sum(1 for v in values if v < -0.005)  # >0.5% negative = directionally correct short
    else:
        correct = sum(1 for v in values if v > 0.005)
    return correct / len(values) * 100


def ttest(values):
    """One-sample t-test: H0 = mean = 0. Returns (t_stat, p_value, mean, n)."""
    if len(values) < 3:
        return None, None, np.mean(values) if values else None, len(values)
    t, p = stats.ttest_1samp(values, 0)
    return float(t), float(p), float(np.mean(values)), len(values)


def main():
    with open(RESULTS_PATH) as f:
        data = json.load(f)

    impacts = data['backtest_results']['individual_impacts']
    print(f"Total individual impact records: {len(impacts)}")

    # Date range for VIX download
    dates = [r['event_date'] for r in impacts]
    min_date = min(dates)
    max_date_dt = datetime.strptime(max(dates), '%Y-%m-%d') + timedelta(days=5)
    max_date = max_date_dt.strftime('%Y-%m-%d')
    print(f"Event date range: {min_date} to {max(dates)}")
    print(f"Fetching VIX data {min_date} to {max_date}...")

    vix_data = fetch_vix_closes(min_date, max_date)
    print(f"VIX data points fetched: {len(vix_data)}")

    # Attach VIX to each event
    enriched = []
    vix_missing = []
    for r in impacts:
        vix = get_vix_for_event(r['event_date'], vix_data)
        if vix is None:
            vix_missing.append(r['event_date'])
            continue
        r['vix_at_event'] = vix
        r['vix_group'] = 'high' if vix >= VIX_THRESHOLD else 'low'
        enriched.append(r)

    print(f"Events with VIX data: {len(enriched)}")
    if vix_missing:
        print(f"Events missing VIX (excluded): {vix_missing}")

    high_vix = [r for r in enriched if r['vix_group'] == 'high']
    low_vix = [r for r in enriched if r['vix_group'] == 'low']

    print(f"\n{'='*60}")
    print(f"VIX SEGMENTATION: threshold = {VIX_THRESHOLD}")
    print(f"  VIX >= {VIX_THRESHOLD} (high): n={len(high_vix)}")
    print(f"  VIX <  {VIX_THRESHOLD} (low):  n={len(low_vix)}")

    # Print VIX values for each event
    print(f"\n--- Event VIX values ---")
    for r in sorted(enriched, key=lambda x: x['event_date']):
        print(f"  {r['event_date']}  {r['symbol']:6s}  VIX={r['vix_at_event']:.1f}  [{r['vix_group'].upper()}]  "
              f"5d_abnormal={r['abnormal_5d']:+.2f}%")

    print(f"\n{'='*60}")
    print(f"RESULTS BY HORIZON (SHORT signal: negative abnormal return = correct)")
    print(f"{'='*60}")

    for h in HORIZONS:
        key = f'abnormal_{h}d'
        print(f"\n--- {h}-day horizon ---")
        for label, group in [('HIGH VIX (>=25)', high_vix), ('LOW VIX (<25)', low_vix), ('ALL', enriched)]:
            vals = [r[key] for r in group if key in r]
            t, p, mean, n = ttest(vals)
            # For a SHORT signal, "direction correct" means abnormal < -0.5%
            dir_pct = direction_pct(vals, short_signal=True)
            ci_note = ''
            if t is not None:
                ci_note = f'  t={t:+.2f}  p={p:.3f}'
            print(f"  {label:20s}  n={n:2d}  mean={mean:+.2f}%  dir_correct={dir_pct:.0f}%{ci_note}")

    # Summary table
    print(f"\n{'='*60}")
    print(f"SUMMARY TABLE (5-day abnormal return, short direction)")
    print(f"{'='*60}")
    print(f"{'Group':22s} {'n':>4} {'Mean':>8} {'Median':>8} {'Dir%':>6} {'t':>6} {'p':>7}")
    print(f"{'-'*60}")
    for label, group in [('HIGH VIX (>=25)', high_vix), ('LOW VIX (<25)', low_vix), ('ALL', enriched)]:
        vals_5d = [r['abnormal_5d'] for r in group if 'abnormal_5d' in r]
        t, p, mean, n = ttest(vals_5d)
        med = float(np.median(vals_5d)) if vals_5d else 0.0
        dp = direction_pct(vals_5d, short_signal=True)
        t_str = f'{t:+.2f}' if t is not None else 'n/a'
        p_str = f'{p:.3f}' if p is not None else 'n/a'
        print(f"{label:22s} {n:4d} {mean:+7.2f}% {med:+7.2f}% {dp:5.0f}% {t_str:>6} {p_str:>7}")

    # Also show 1d and 3d for VIX>25 vs VIX<25
    print(f"\n{'='*60}")
    print(f"HIGH VIX (>=25) DETAILED BREAKDOWN")
    print(f"{'='*60}")
    for h in HORIZONS:
        key = f'abnormal_{h}d'
        vals = [r[key] for r in high_vix if key in r]
        t, p, mean, n = ttest(vals)
        dp = direction_pct(vals)
        t_str = f'{t:+.2f}' if t is not None else 'n/a'
        p_str = f'{p:.3f}' if p is not None else 'n/a'
        print(f"  {h:2d}d:  n={n}  mean={mean:+.2f}%  dir={dp:.0f}%  t={t_str}  p={p_str}")

    print(f"\n{'='*60}")
    print(f"LOW VIX (<25) DETAILED BREAKDOWN")
    print(f"{'='*60}")
    for h in HORIZONS:
        key = f'abnormal_{h}d'
        vals = [r[key] for r in low_vix if key in r]
        t, p, mean, n = ttest(vals)
        dp = direction_pct(vals)
        t_str = f'{t:+.2f}' if t is not None else 'n/a'
        p_str = f'{p:.3f}' if p is not None else 'n/a'
        print(f"  {h:2d}d:  n={n}  mean={mean:+.2f}%  dir={dp:.0f}%  t={t_str}  p={p_str}")

    # Identify which HIGH VIX events drove the signal (or didn't)
    print(f"\n{'='*60}")
    print(f"HIGH VIX EVENTS — INDIVIDUAL 5d ABNORMAL RETURNS")
    print(f"{'='*60}")
    for r in sorted(high_vix, key=lambda x: x.get('abnormal_5d', 0)):
        a20 = f"{r['abnormal_20d']:+.2f}%" if 'abnormal_20d' in r else 'n/a'
        a10 = f"{r['abnormal_10d']:+.2f}%" if 'abnormal_10d' in r else 'n/a'
        print(f"  {r['event_date']}  {r['symbol']:6s}  VIX={r['vix_at_event']:.1f}  "
              f"5d={r.get('abnormal_5d', 0):+.2f}%  10d={a10}  20d={a20}")


if __name__ == '__main__':
    main()
