"""Quantify gold→XLRE lag regression coefficients with DGS10 control."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_aligned_returns
import pandas as pd
import numpy as np
import statsmodels.api as sm
from datetime import datetime

# Fetch data
ids = ['GC=F', 'XLRE', 'SPY', 'FRED:DGS10']
start = '2020-01-01'
end = '2026-04-15'
oos_start = '2023-01-01'

rets = get_aligned_returns(ids, start=start, end=end)

# Build lagged features
df = pd.DataFrame({
    'XLRE': rets['XLRE'],
    'XLRE_lag1': rets['XLRE'].shift(1),
    'GC_lag1': rets['GC=F'].shift(1),
    'GC_lag2': rets['GC=F'].shift(2),
    'SPY': rets['SPY'],
    'DGS10': rets.get('FRED:DGS10', rets.get('DGS10', pd.Series(0, index=rets.index))),
}).dropna()

# Split IS/OOS
is_df = df[df.index < oos_start]
oos_df = df[df.index >= oos_start]

# IS regression
X_is = sm.add_constant(is_df[['XLRE_lag1', 'GC_lag1', 'GC_lag2', 'SPY', 'DGS10']])
y_is = is_df['XLRE']
model_is = sm.OLS(y_is, X_is).fit()

print("=== IN-SAMPLE REGRESSION (2020 - 2022) ===")
print(f"N = {len(is_df)}")
print(f"R² = {model_is.rsquared:.4f}, Adj R² = {model_is.rsquared_adj:.4f}")
print()
for var in ['GC_lag1', 'GC_lag2']:
    coef = model_is.params[var]
    pval = model_is.pvalues[var]
    print(f"  {var}: coef={coef:.6f}, t={model_is.tvalues[var]:.2f}, p={pval:.4f}")
    # Economic interpretation: if gold moves 1 std dev, how much does XLRE move?
    gold_daily_vol = rets['GC=F'].std()
    print(f"    → 1% gold move → {coef*100:.3f} bps XLRE at lag")
    print(f"    → 1σ gold move ({gold_daily_vol*100:.2f}%) → {coef * gold_daily_vol * 10000:.2f} bps XLRE")

print()

# OOS regression (fit IS model, apply to OOS)
X_oos = sm.add_constant(oos_df[['XLRE_lag1', 'GC_lag1', 'GC_lag2', 'SPY', 'DGS10']])
y_oos = oos_df['XLRE']
model_oos = sm.OLS(y_oos, X_oos).fit()

print("=== OUT-OF-SAMPLE REGRESSION (2023 - present) ===")
print(f"N = {len(oos_df)}")
print(f"R² = {model_oos.rsquared:.4f}")
for var in ['GC_lag1', 'GC_lag2']:
    coef = model_oos.params[var]
    pval = model_oos.pvalues[var]
    print(f"  {var}: coef={coef:.6f}, t={model_oos.tvalues[var]:.2f}, p={pval:.4f}")
    print(f"    → 1% gold move → {coef*100:.3f} bps XLRE at lag")

print()

# Trading strategy simulation: buy XLRE when gold up > 1%, sell after 2 days
print("=== STRATEGY SIMULATION (OOS only) ===")
gold_rets = rets['GC=F']
xlre_rets = rets['XLRE']
spy_rets = rets['SPY']

trades = []
for threshold in [0.5, 1.0, 1.5, 2.0]:
    signals = gold_rets[gold_rets.index >= oos_start]
    signal_dates = signals[signals > threshold/100].index
    
    trade_returns = []
    for dt in signal_dates:
        # Buy XLRE next day, hold 2 days
        pos = xlre_rets.index.get_loc(dt)
        if pos + 3 < len(xlre_rets):
            ret_1d = xlre_rets.iloc[pos + 1]
            ret_2d = xlre_rets.iloc[pos + 1] + xlre_rets.iloc[pos + 2]
            spy_1d = spy_rets.iloc[pos + 1]
            spy_2d = spy_rets.iloc[pos + 1] + spy_rets.iloc[pos + 2]
            trade_returns.append({
                '1d_raw': ret_1d * 100,
                '2d_raw': ret_2d * 100,
                '1d_abnormal': (ret_1d - spy_1d) * 100,
                '2d_abnormal': (ret_2d - spy_2d) * 100,
            })
    
    if trade_returns:
        avg_1d_abn = np.mean([t['1d_abnormal'] for t in trade_returns])
        avg_2d_abn = np.mean([t['2d_abnormal'] for t in trade_returns])
        avg_2d_raw = np.mean([t['2d_raw'] for t in trade_returns])
        pct_pos = np.mean([1 if t['2d_abnormal'] > 0 else 0 for t in trade_returns]) * 100
        print(f"  Threshold >{threshold}%: n={len(trade_returns)}, 1d_abn={avg_1d_abn:+.3f}%, 2d_abn={avg_2d_abn:+.3f}%, 2d_raw={avg_2d_raw:+.3f}%, dir={pct_pos:.0f}%")
    else:
        print(f"  Threshold >{threshold}%: n=0")

