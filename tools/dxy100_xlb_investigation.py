"""
DXY > 100 -> XLB long investigation.

Scan hit claimed: canonical pooled n=8 20d +2.63% p=0.0037, recent n=5 +3.05% p=0.0002, 100% positive.
Raw 20d +5.08% but SPY itself +4.36% on same events (beta contamination warning).

Tests required by methodology rules:
1. scan_hit_canonical_all_horizons_rule: check 1d/3d/5d/10d/20d
2. regime concentration check (dxy108_gld precedent - regime artifact)
3. orthogonality vs VIX>30 XLB_20d basket member
4. VIX stratification (DXY>100 may only work in high-VIX risk-off)
5. DXY persistence (is DXY still above 100 during trade window, or does it fall back?)
"""
import sys
import os
import pandas as pd
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download


def fetch(tk, start, end):
    df = safe_download(tk, start=start, end=end, auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    s = df['Adj Close'] if 'Adj Close' in df.columns else df['Close']
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s.name = tk
    return s


def first_cross_above(series, level, cluster_days=30):
    above = series > level
    prev_below = series.shift(1) <= level
    hits = above & prev_below
    events, last = [], None
    for d, fire in hits.items():
        if not fire:
            continue
        if last is None or (d - last).days >= cluster_days:
            events.append(d)
            last = d
    return events


def forward_abn(dates, tgt, bench, horizons):
    out = {h: [] for h in horizons}
    for d in dates:
        idx = tgt.index.searchsorted(d)
        if idx >= len(tgt):
            continue
        for h in horizons:
            ex = idx + h
            if ex >= len(tgt):
                continue
            tr = tgt.iloc[ex] / tgt.iloc[idx] - 1
            br = bench.iloc[ex] / bench.iloc[idx] - 1
            out[h].append({'date': tgt.index[idx], 'tgt': float(tr), 'bench': float(br), 'abn': float(tr - br)})
    return out


def sumstats(obs, label):
    if len(obs) < 3:
        return {'label': label, 'n': len(obs), 'status': 'too_few'}
    abn = np.array([o['abn'] for o in obs])
    raw = np.array([o['tgt'] for o in obs])
    t, p = stats.ttest_1samp(abn, 0)
    return {
        'label': label, 'n': len(obs),
        'mean_abn': float(abn.mean() * 100),
        'mean_raw': float(raw.mean() * 100),
        't': float(t), 'p': float(p),
        'pos_rate': float((abn > 0).mean()),
    }


def main():
    start, end = '1999-01-01', '2026-04-21'
    dxy = fetch('DX-Y.NYB', start, end)
    xlb = fetch('XLB', start, end)
    spy = fetch('SPY', start, end)
    vix = fetch('^VIX', start, end)
    common = dxy.index.intersection(xlb.index).intersection(spy.index).intersection(vix.index)
    dxy = dxy.reindex(common)
    xlb = xlb.reindex(common)
    spy = spy.reindex(common)
    vix = vix.reindex(common)

    events = first_cross_above(dxy, 100.0, 30)
    print(f"DXY first-cross above 100 (30d cluster): n={len(events)}")
    print(f"  earliest: {events[0].date() if events else 'n/a'}  latest: {events[-1].date() if events else 'n/a'}")

    horizons = [1, 3, 5, 10, 20]
    obs = forward_abn(events, xlb, spy, horizons)

    # All-horizons canonical retest
    print("\n=== ALL-HORIZONS SPY-ADJUSTED (pooled) ===")
    for h in horizons:
        r = sumstats(obs[h], f"XLB-SPY {h}d")
        print(f"  {h:3d}d  n={r['n']:2d}  mean_abn={r.get('mean_abn', 0):+.2f}%  t={r.get('t', 0):+.2f}  p={r.get('p', 1):.4f}  pos={r.get('pos_rate', 0)*100:.0f}%")

    # Also show SPY raw response (for context)
    print("\n=== SPY RAW (for context - is market just rising?) ===")
    for h in horizons:
        # just SPY's own return on the event dates
        spy_rets = [o['bench'] * 100 for o in obs[h]]
        if len(spy_rets) >= 3:
            t, p = stats.ttest_1samp(spy_rets, 0)
            print(f"  {h:3d}d  n={len(spy_rets):2d}  mean_spy={np.mean(spy_rets):+.2f}%  t={t:+.2f}  p={p:.4f}  pos={(np.array(spy_rets) > 0).mean()*100:.0f}%")

    # Regime (era) concentration check
    print("\n=== REGIME / ERA CONCENTRATION ===")
    print("All event dates with VIX, DXY prior-20d trend, XLB 20d abn:")
    o20 = obs[20]
    for o in o20:
        d = o['date']
        i = vix.index.searchsorted(d)
        v = float(vix.iloc[i]) if i < len(vix) else np.nan
        j = dxy.index.searchsorted(d)
        prior = (dxy.iloc[j] / dxy.iloc[j - 20] - 1) * 100 if j >= 20 else np.nan
        fwd = (dxy.iloc[j + 20] / dxy.iloc[j] - 1) * 100 if j + 20 < len(dxy) else np.nan
        print(f"  {d.date()}  vix={v:5.1f}  dxy_prior_20d={prior:+5.2f}%  dxy_fwd_20d={fwd:+5.2f}%  xlb_abn_20d={o['abn']*100:+6.2f}%")

    # Year concentration
    years = {}
    for o in o20:
        y = o['date'].year
        years.setdefault(y, []).append(o['abn'] * 100)
    print("\nPer-year distribution (20d):")
    for y in sorted(years):
        a = np.array(years[y])
        print(f"  {y}  n={len(a)}  mean_abn={a.mean():+.2f}%  range=[{a.min():+.2f}%, {a.max():+.2f}%]")

    # Leave-one-out robustness at 20d
    print("\n=== LEAVE-ONE-OUT ROBUSTNESS (20d) ===")
    abn_arr = np.array([o['abn'] * 100 for o in o20])
    for i in range(len(abn_arr)):
        loo = np.delete(abn_arr, i)
        t, p = stats.ttest_1samp(loo, 0)
        marker = '  '
        if p > 0.05:
            marker = '!!'
        print(f"  {marker} drop {o20[i]['date'].date()} -> n={len(loo)} mean={loo.mean():+.2f}% p={p:.4f}")

    # VIX stratification 20d
    print("\n=== VIX STRATIFICATION (20d) ===")
    hi = [o for o in o20 if (lambda d: float(vix.iloc[vix.index.searchsorted(d)]))(o['date']) >= 25]
    lo = [o for o in o20 if (lambda d: float(vix.iloc[vix.index.searchsorted(d)]))(o['date']) < 25]
    print(f"  VIX>=25: n={len(hi)}  mean_abn={np.mean([o['abn'] * 100 for o in hi]) if hi else 0:+.2f}%")
    print(f"  VIX <25: n={len(lo)}  mean_abn={np.mean([o['abn'] * 100 for o in lo]) if lo else 0:+.2f}%")

    # Discovery vs OOS split (2022 cut)
    print("\n=== IS/OOS TEMPORAL SPLIT (cut=2022-01-01) ===")
    cut = pd.Timestamp('2022-01-01')
    for h in horizons:
        disc = [o for o in obs[h] if o['date'] < cut]
        oos = [o for o in obs[h] if o['date'] >= cut]
        rd = sumstats(disc, f"disc{h}")
        ro = sumstats(oos, f"oos{h}")
        print(f"  {h:3d}d  disc n={rd['n']} mean={rd.get('mean_abn', 0):+.2f}% p={rd.get('p', 1):.4f}  |  oos n={ro['n']} mean={ro.get('mean_abn', 0):+.2f}% p={ro.get('p', 1):.4f}")


if __name__ == '__main__':
    main()
