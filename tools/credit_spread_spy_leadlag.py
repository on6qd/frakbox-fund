"""HYG-LQD credit-spread → SPY lead-lag test.

The HYG-LQD spread isolates credit risk by removing rate-duration component.
Hypothesis: credit-spread widening (HYG underperforming LQD) leads SPY weakness 1-5d.

Test: Granger causality of HYG_ret - LQD_ret on SPY_ret, with cross-correlation
sanity check (per granger_false_positive_xcorr_sanity_check rule).
"""

import sys
sys.path.insert(0, '.')
from tools.yfinance_utils import safe_download
import pandas as pd
import numpy as np
from statsmodels.tsa.stattools import grangercausalitytests
from scipy import stats

START = "2010-01-01"
END   = "2026-04-19"
OOS   = "2020-01-01"
MAX_LAGS = 5

# Fetch
hyg = safe_download("HYG", start=START, end=END)['Close'].squeeze()
lqd = safe_download("LQD", start=START, end=END)['Close'].squeeze()
spy = safe_download("SPY", start=START, end=END)['Close'].squeeze()
df = pd.concat({'hyg': hyg, 'lqd': lqd, 'spy': spy}, axis=1).dropna()
df['ret_hyg'] = df['hyg'].pct_change()
df['ret_lqd'] = df['lqd'].pct_change()
df['ret_spy'] = df['spy'].pct_change()
df['credit_spread_ret'] = df['ret_hyg'] - df['ret_lqd']  # positive = credit risk-on
df = df.dropna()

print(f"=== HYG-LQD credit spread → SPY lead-lag ===")
print(f"Period: {df.index[0].date()} to {df.index[-1].date()}, n={len(df)}")
print()

# In-sample (pre-OOS)
is_df  = df[df.index < OOS]
oos_df = df[df.index >= OOS]
print(f"IS window: {is_df.index[0].date()} to {is_df.index[-1].date()}, n={len(is_df)}")
print(f"OOS window: {oos_df.index[0].date()} to {oos_df.index[-1].date()}, n={len(oos_df)}")
print()

# Cross-correlation sanity check FIRST (per methodology rule)
print("=== Cross-correlation: credit_spread_ret(t-k) vs SPY_ret(t) ===")
for window_name, window_df in [('IS', is_df), ('OOS', oos_df)]:
    print(f"  {window_name}:")
    for lag in range(1, MAX_LAGS+1):
        # Pearson corr between credit_spread shifted forward by `lag` and SPY today
        xcorr = window_df['credit_spread_ret'].shift(lag).corr(window_df['ret_spy'])
        # Note: positive xcorr at lag k means: spread widening k days ago -> SPY down today
        # Wait. credit_spread_ret = HYG - LQD. positive = credit improving. If credit IMPROVING leads SPY UP, xcorr positive.
        # So if credit-leads-equity is real, xcorr at lag 1+ should be POSITIVE
        print(f"    lag {lag}: corr={xcorr:+.4f}")

# Granger causality test
def run_granger(window_df, label):
    series = window_df[['ret_spy', 'credit_spread_ret']].dropna()
    if len(series) < 100:
        print(f"  {label}: too few obs (n={len(series)})")
        return None
    try:
        results = grangercausalitytests(series, maxlag=MAX_LAGS)
        best = None
        for lag, res in results.items():
            f, p = res[0]['ssr_ftest'][0], res[0]['ssr_ftest'][1]
            if best is None or p < best[2]:
                best = (lag, f, p)
        print(f"  {label}: best lag={best[0]}, F={best[1]:.3f}, p={best[2]:.4f}")
        return best
    except Exception as e:
        print(f"  {label}: error {e}")
        return None

print()
print("=== Granger causality: credit_spread_ret → SPY ===")
is_best  = run_granger(is_df,  "IS")
oos_best = run_granger(oos_df, "OOS")

# Verdict
print()
print("=== VERDICT ===")
if is_best and oos_best:
    is_sig  = is_best[2] < 0.05
    oos_sig = oos_best[2] < 0.05
    if is_sig and oos_sig:
        # Check sign consistency via xcorr
        is_xcorr  = np.mean([is_df['credit_spread_ret'].shift(k).corr(is_df['ret_spy']) for k in range(1, MAX_LAGS+1)])
        oos_xcorr = np.mean([oos_df['credit_spread_ret'].shift(k).corr(oos_df['ret_spy']) for k in range(1, MAX_LAGS+1)])
        same_sign = (is_xcorr * oos_xcorr) > 0
        print(f"  IS p={is_best[2]:.4f}, OOS p={oos_best[2]:.4f}, both significant.")
        print(f"  Avg xcorr(1..5): IS={is_xcorr:+.4f}, OOS={oos_xcorr:+.4f}, same sign={same_sign}")
        if same_sign and abs(is_xcorr) > 0.05 and abs(oos_xcorr) > 0.05:
            print("  → POTENTIAL VALID LEAD (proceed to event-conversion design)")
        else:
            print("  → DEAD END: significant Granger but xcorr too weak / sign-flips OOS")
    elif is_sig and not oos_sig:
        print(f"  → DEAD END: IS-only signal (p={is_best[2]:.4f}) does not hold OOS (p={oos_best[2]:.4f})")
    elif not is_sig:
        print(f"  → NULL: not significant in-sample (p={is_best[2]:.4f})")

# Bonus: same-day correlation (well-known: credit and equity move together same-day)
print()
print(f"=== Same-day corr (sanity, expected positive ~0.4-0.6) ===")
print(f"  IS:  corr(credit_spread_ret, ret_spy) = {is_df['credit_spread_ret'].corr(is_df['ret_spy']):+.4f}")
print(f"  OOS: corr(credit_spread_ret, ret_spy) = {oos_df['credit_spread_ret'].corr(oos_df['ret_spy']):+.4f}")
