"""
Proper investigation of DXY<100 -> international ETF signal.

Controls for:
1. Abnormal returns (subtract SPY)
2. OOS split (discovery pre-2022, OOS 2022+)
3. VIX regime decomposition (known failure mode of DXY>100 dead ends)
4. DXY momentum contamination (is signal driven by persistent trend?)

Horizons tested: 5d, 10d, 20d.
"""
import sys
import os
import pandas as pd
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download


def fetch_series(ticker, start, end):
    df = safe_download(ticker, start=start, end=end, auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    # Prefer Adj Close, fallback to Close
    if 'Adj Close' in df.columns:
        s = df['Adj Close']
    else:
        s = df['Close']
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s.name = ticker
    return s


def find_threshold_crossings(dxy, threshold=100.0, direction='below', cluster_days=30):
    """First close below threshold, gated to 1 per cluster_days window."""
    below = dxy < threshold
    prev_above = dxy.shift(1) >= threshold
    first_cross = below & prev_above
    # enforce cluster
    crossings = []
    last_date = None
    for d, fire in first_cross.items():
        if not fire:
            continue
        if last_date is None or (d - last_date).days >= cluster_days:
            crossings.append(d)
            last_date = d
    return crossings


def forward_abnormal_returns(dates, target, benchmark, horizons=(5, 10, 20)):
    """For each date, compute (target_ret - benchmark_ret) over each horizon."""
    results = {h: [] for h in horizons}
    for d in dates:
        idx = target.index.searchsorted(d)
        if idx >= len(target.index):
            continue
        entry_idx = idx  # close of trigger day
        for h in horizons:
            exit_idx = entry_idx + h
            if exit_idx >= len(target.index):
                continue
            t_ret = target.iloc[exit_idx] / target.iloc[entry_idx] - 1
            b_ret = benchmark.iloc[exit_idx] / benchmark.iloc[entry_idx] - 1
            results[h].append({
                'date': target.index[entry_idx],
                'target_ret': float(t_ret),
                'benchmark_ret': float(b_ret),
                'abnormal_ret': float(t_ret - b_ret),
            })
    return results


def summarize(observations, label):
    if len(observations) < 3:
        return f"{label}: n={len(observations)} (too few)"
    abn = np.array([o['abnormal_ret'] for o in observations])
    raw = np.array([o['target_ret'] for o in observations])
    t, p = stats.ttest_1samp(abn, 0)
    pos_rate = float((abn > 0).mean())
    return {
        'label': label,
        'n': len(observations),
        'mean_abn_pct': float(abn.mean() * 100),
        'median_abn_pct': float(np.median(abn) * 100),
        'mean_raw_pct': float(raw.mean() * 100),
        't_stat': float(t),
        'p_value': float(p),
        'pos_rate': pos_rate,
    }


def run_investigation(target_ticker):
    print(f"\n{'='*70}\nTARGET: {target_ticker}\n{'='*70}")
    start = '1999-01-01'
    end = '2026-04-16'
    dxy = fetch_series('DX-Y.NYB', start, end)
    target = fetch_series(target_ticker, start, end)
    spy = fetch_series('SPY', start, end)
    vix = fetch_series('^VIX', start, end)
    if dxy is None or target is None or spy is None:
        print("Data fetch failed")
        return
    # Align
    common = dxy.index.intersection(target.index).intersection(spy.index).intersection(vix.index)
    dxy = dxy.reindex(common)
    target = target.reindex(common)
    spy = spy.reindex(common)
    vix = vix.reindex(common)
    crossings = find_threshold_crossings(dxy, threshold=100.0, direction='below', cluster_days=30)
    print(f"DXY<100 first-cross events (30d cluster): n={len(crossings)}")
    if len(crossings) < 3:
        return
    # All observations
    horizons = (5, 10, 20)
    obs = forward_abnormal_returns(crossings, target, spy, horizons)
    print("\n--- FULL SAMPLE (abnormal vs SPY) ---")
    for h in horizons:
        print(summarize(obs[h], f"{h}d full"))
    # Discovery / OOS split
    disc_cut = pd.Timestamp('2022-01-01')
    print("\n--- DISCOVERY (<2022) ---")
    for h in horizons:
        disc = [o for o in obs[h] if o['date'] < disc_cut]
        print(summarize(disc, f"{h}d disc"))
    print("\n--- OOS (>=2022) ---")
    for h in horizons:
        oos = [o for o in obs[h] if o['date'] >= disc_cut]
        print(summarize(oos, f"{h}d oos"))
    # VIX regime decomposition (was the killer for DXY>100)
    print("\n--- VIX STRATIFICATION (20d horizon) ---")
    vix_map = {}
    for d in crossings:
        idx = vix.index.searchsorted(d)
        if idx < len(vix):
            vix_map[d] = float(vix.iloc[idx])
    obs20 = obs[20]
    for o in obs20:
        o['vix'] = vix_map.get(o['date'], np.nan)
    high = [o for o in obs20 if o.get('vix', 0) >= 25]
    low = [o for o in obs20 if o.get('vix', 0) < 25]
    print(summarize(high, "VIX>=25"))
    print(summarize(low, "VIX<25"))
    # DXY momentum contamination check: was DXY already trending down?
    print("\n--- DXY MOMENTUM CHECK (20d horizon) ---")
    for o in obs20:
        d = o['date']
        idx = dxy.index.searchsorted(d)
        if idx - 20 >= 0:
            dxy_20d_prior = dxy.iloc[idx] / dxy.iloc[idx - 20] - 1
            o['dxy_prior_20d'] = float(dxy_20d_prior)
        if idx + 20 < len(dxy):
            dxy_fwd = dxy.iloc[idx + 20] / dxy.iloc[idx] - 1
            o['dxy_fwd_20d'] = float(dxy_fwd)
    # Split by DXY trend prior
    trending_down = [o for o in obs20 if o.get('dxy_prior_20d', 0) < -0.02]
    stable_or_up = [o for o in obs20 if o.get('dxy_prior_20d', 0) >= -0.02]
    print(summarize(trending_down, "DXY was already trending down >2% prior 20d"))
    print(summarize(stable_or_up, "DXY stable or up prior 20d"))
    # Persistence: did DXY continue down during the trade window?
    if obs20:
        persist = np.array([o.get('dxy_fwd_20d', np.nan) for o in obs20])
        persist = persist[~np.isnan(persist)]
        print(f"\nDXY forward 20d (avg): {persist.mean()*100:.2f}% (n={len(persist)})")
        print(f"DXY continued falling in {(persist < 0).mean()*100:.0f}% of observations")
    # List all events
    print("\n--- EVENT LIST (date, abn_ret 20d, vix, dxy_prior, dxy_fwd) ---")
    for o in obs20[-15:]:  # last 15 for space
        print(f"  {o['date'].date()}  abn={o['abnormal_ret']*100:+.2f}%  vix={o.get('vix', np.nan):.1f}  "
              f"dxy_prior={o.get('dxy_prior_20d', np.nan)*100:+.2f}%  dxy_fwd={o.get('dxy_fwd_20d', np.nan)*100:+.2f}%")


if __name__ == '__main__':
    for t in ['EWJ', 'EWG', 'EEM']:
        run_investigation(t)
