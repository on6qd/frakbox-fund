"""
Segment historical insider-cluster events by distance-from-52-week-high at the
cluster date, to test whether extreme-drawdown clusters (stocks at/near their
52-week low, e.g. FISV at -73%) earn the same +abnormal_5d as typical clusters.

Motivation: the live FISV signal (6 insiders incl. CFO, 2026-06-17) fires while
the stock sits AT its 52-week low (-73% from high). The evaluator's drawdown gate
only looks back 20 days (recent dip = conviction). This script checks the
52-WEEK structural-drawdown dimension the validation never isolated.
"""
import sys
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

df = pd.read_csv('data/clusters_with_roles_full.csv')
df['cluster_date'] = pd.to_datetime(df['cluster_date'])

tickers = sorted(df['ticker'].unique())
hist = {}

def fetch(t):
    try:
        h = yf.Ticker(t).history(start='2020-01-01', end='2026-01-01', auto_adjust=True)
        if len(h) > 0:
            h.index = h.index.tz_localize(None)
            return t, h['Close']
    except Exception:
        pass
    return t, None

with ThreadPoolExecutor(max_workers=8) as ex:
    for t, s in ex.map(fetch, tickers):
        hist[t] = s

drawdowns = []
for _, row in df.iterrows():
    s = hist.get(row['ticker'])
    if s is None:
        drawdowns.append(np.nan); continue
    cd = row['cluster_date']
    window = s[(s.index <= cd) & (s.index > cd - pd.Timedelta(days=365))]
    if len(window) < 30:
        drawdowns.append(np.nan); continue
    cur = window.iloc[-1]
    peak = window.max()
    drawdowns.append(100.0 * (cur / peak - 1.0))

df['dd_52w'] = drawdowns
n_ok = df['dd_52w'].notna().sum()
print(f"computed 52w drawdown for {n_ok}/{len(df)} events")

buckets = [(-1000,-50,'<=-50% (near 52w low, FISV-like)'),
           (-50,-30,'-50..-30%'),
           (-30,-15,'-30..-15%'),
           (-15,-5,'-15..-5%'),
           (-5,1000,'>-5% (near highs)')]

print(f"\n{'bucket':<34}{'n':>5}{'mean_5d':>9}{'median':>8}{'pos%':>7}")
print('-'*63)
sub = df[df['dd_52w'].notna()]
for lo, hi, name in buckets:
    b = sub[(sub['dd_52w'] > lo) & (sub['dd_52w'] <= hi)]
    if len(b) == 0:
        print(f"{name:<34}{0:>5}"); continue
    print(f"{name:<34}{len(b):>5}{b.abnormal_5d.mean():>9.2f}{b.abnormal_5d.median():>8.2f}{100*(b.abnormal_5d>0).mean():>7.1f}")

# CEO/CFO subset (FISV has CFO)
print("\n--- CEO/CFO present subset ---")
cc = sub[(sub['has_ceo']==1)|(sub['has_cfo']==1)]
for lo, hi, name in buckets:
    b = cc[(cc['dd_52w'] > lo) & (cc['dd_52w'] <= hi)]
    if len(b)==0:
        print(f"{name:<34}{0:>5}"); continue
    print(f"{name:<34}{len(b):>5}{b.abnormal_5d.mean():>9.2f}{b.abnormal_5d.median():>8.2f}{100*(b.abnormal_5d>0).mean():>7.1f}")

# Statistical test: extreme-dd vs rest
from scipy import stats
extreme = sub[sub['dd_52w'] <= -50]['abnormal_5d']
rest = sub[sub['dd_52w'] > -50]['abnormal_5d']
if len(extreme) >= 5:
    t, p = stats.ttest_ind(extreme, rest, equal_var=False)
    print(f"\nextreme(<=-50%) n={len(extreme)} mean={extreme.mean():.2f} pos={100*(extreme>0).mean():.1f}%")
    print(f"rest          n={len(rest)} mean={rest.mean():.2f} pos={100*(rest>0).mean():.1f}%")
    print(f"Welch t-test extreme vs rest: t={t:.2f} p={p:.4f}")
    # one-sample: is extreme bucket significantly != 0?
    t1, p1 = stats.ttest_1samp(extreme, 0)
    print(f"extreme bucket vs 0: t={t1:.2f} p={p1:.4f}")

df[['ticker','cluster_date','n_insiders','has_ceo','has_cfo','dd_52w','abnormal_5d']].to_csv('data/cluster_dd52w_segmentation.csv', index=False)
print("\nsaved data/cluster_dd52w_segmentation.csv")
