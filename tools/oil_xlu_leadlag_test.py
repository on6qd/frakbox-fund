"""Falsification test: is CL=F -> XLU lead-lag at the DISCOVERED lag (3) stable OOS?
Fix the lag, regress XLU return on lagged oil return + SPY control, split IS/OOS."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, '.')
from tools.timeseries import get_aligned_series

def ret(s): return np.log(s).diff()

start, end, oos_start = "2020-01-01", "2026-06-10", "2024-01-01"
al = get_aligned_series(["CL=F", "XLU", "SPY"], start=start, end=end)
oil, xlu, spy = al["CL=F"], al["XLU"], al["SPY"]

df = pd.DataFrame({"oil": ret(oil), "xlu": ret(xlu), "spy": ret(spy)}).dropna()

import statsmodels.api as sm
for lag in (1, 3):
    df[f"oil_l{lag}"] = df["oil"].shift(lag)

for label, sub in [("FULL", df), ("IS(2020-2023)", df[df.index < oos_start]),
                    ("OOS(2024+)", df[df.index >= oos_start])]:
    sub = sub.dropna()
    print(f"\n=== {label} n={len(sub)} ===")
    for lag in (1, 3):
        X = sm.add_constant(sub[[f"oil_l{lag}", "spy"]])
        m = sm.OLS(sub["xlu"], X).fit()
        b = m.params[f"oil_l{lag}"]; p = m.pvalues[f"oil_l{lag}"]
        print(f"  lag{lag}: beta_oil={b:+.4f} p={p:.4f} sig={'Y' if p<0.05 else 'n'}  R2={m.rsquared:.4f}")

# Simple tradeable signal: oil 3-day cumulative return > 0 -> long XLU next day, measure abnormal vs SPY
df["oil_3d"] = df["oil"].rolling(3).sum().shift(1)   # signal known at t-1
df["xlu_abn"] = df["xlu"] - df["spy"]
for label, sub in [("IS(2020-2023)", df[df.index < oos_start]), ("OOS(2024+)", df[df.index >= oos_start])]:
    sub = sub.dropna()
    longs = sub[sub["oil_3d"] > 0]["xlu_abn"]
    shorts = sub[sub["oil_3d"] <= 0]["xlu_abn"]
    print(f"\n[{label}] oil_up days: mean XLU abn ret = {longs.mean()*100:+.3f}% (n={len(longs)}); oil_down: {shorts.mean()*100:+.3f}% (n={len(shorts)})")
    from scipy import stats
    t,p = stats.ttest_ind(longs, shorts, equal_var=False)
    print(f"           spread t={t:.2f} p={p:.4f}")
