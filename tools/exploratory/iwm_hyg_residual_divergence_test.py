"""
Test: IWM/HYG residual divergence trigger — definitive dispositive test.

Hypothesis: When IWM's residual from a rolling regression on HYG (controlling
for SPY) diverges by >2σ from expected, IWM mean-reverts over the following 5
days.

Per exposure_to_event_conversion_meta_finding: prior threshold-on-daily-return
and threshold-on-factor-momentum approaches both failed. This is a final
residual-based test. If this also fails, we close the category and write a
canonical rule.
"""

import sys
sys.path.insert(0, "/Users/frakbox/Bots/financial_researcher")
import numpy as np
import pandas as pd
from tools.yfinance_utils import get_close_prices

START = "2010-01-01"
END = "2026-04-22"
OOS_SPLIT = "2022-01-01"
ROLLING_WINDOW = 60   # days for rolling regression
Z_THRESHOLD = 2.0
HORIZONS = [1, 3, 5]

print(f"Fetching IWM, HYG, SPY from {START} to {END}...")
px = get_close_prices(["IWM", "HYG", "SPY"], start=START, end=END)
px = px.dropna()
ret = px.pct_change().dropna()
print(f"Data: {len(ret)} days, from {ret.index[0].date()} to {ret.index[-1].date()}")

# Rolling regression: IWM_ret ~ HYG_ret + SPY_ret, compute residual each day
# Use rolling window of ROLLING_WINDOW days ending at t-1 to predict day t (no lookahead)
residuals = []
resid_std = []
dates = []
y = ret["IWM"].values
X1 = ret["HYG"].values
X2 = ret["SPY"].values
idx = ret.index

for t in range(ROLLING_WINDOW, len(ret)):
    # Use last ROLLING_WINDOW days ending at t-1
    win_y = y[t - ROLLING_WINDOW : t]
    win_X = np.column_stack([np.ones(ROLLING_WINDOW), X1[t - ROLLING_WINDOW : t], X2[t - ROLLING_WINDOW : t]])
    # OLS fit
    try:
        beta, *_ = np.linalg.lstsq(win_X, win_y, rcond=None)
    except np.linalg.LinAlgError:
        continue
    # Predict day t
    pred_t = beta[0] + beta[1] * X1[t] + beta[2] * X2[t]
    resid_t = y[t] - pred_t
    # Rolling std of residuals in the window (as the normalizing constant)
    win_resid = win_y - (beta[0] + beta[1] * X1[t - ROLLING_WINDOW : t] + beta[2] * X2[t - ROLLING_WINDOW : t])
    std_t = win_resid.std(ddof=1)
    residuals.append(resid_t)
    resid_std.append(std_t)
    dates.append(idx[t])

df = pd.DataFrame({"date": dates, "resid": residuals, "resid_std": resid_std}).set_index("date")
df["z"] = df["resid"] / df["resid_std"]

# Now measure forward returns
spy_ret = ret["SPY"]
iwm_ret = ret["IWM"]

def forward_abnormal(t_idx, h):
    """Forward IWM return minus SPY return over h days starting day t+1 (i.e. entry next day open)."""
    pos = idx.get_loc(t_idx)
    if pos + h + 1 > len(idx):
        return np.nan
    iwm_cum = (1 + iwm_ret.iloc[pos + 1 : pos + 1 + h]).prod() - 1
    spy_cum = (1 + spy_ret.iloc[pos + 1 : pos + 1 + h]).prod() - 1
    return iwm_cum - spy_cum

def analyze(subset_df, label):
    pos_trigs = subset_df[subset_df["z"] > Z_THRESHOLD]    # IWM outperformed -> expect underperform next
    neg_trigs = subset_df[subset_df["z"] < -Z_THRESHOLD]   # IWM underperformed -> expect outperform next
    print(f"\n=== {label} ===")
    print(f"Sample: {subset_df.index[0].date()} to {subset_df.index[-1].date()}, n={len(subset_df)}")
    print(f"Positive-resid triggers (z>{Z_THRESHOLD}): n={len(pos_trigs)} (expect mean-reversion DOWN)")
    print(f"Negative-resid triggers (z<-{Z_THRESHOLD}): n={len(neg_trigs)} (expect mean-reversion UP)")

    for h in HORIZONS:
        pos_fwd = np.array([forward_abnormal(d, h) for d in pos_trigs.index])
        pos_fwd = pos_fwd[~np.isnan(pos_fwd)]
        neg_fwd = np.array([forward_abnormal(d, h) for d in neg_trigs.index])
        neg_fwd = neg_fwd[~np.isnan(neg_fwd)]

        # Expected direction: pos_trigs should be NEGATIVE, neg_trigs should be POSITIVE
        from scipy import stats
        pos_t, pos_p = stats.ttest_1samp(pos_fwd, 0) if len(pos_fwd) > 2 else (np.nan, np.nan)
        neg_t, neg_p = stats.ttest_1samp(neg_fwd, 0) if len(neg_fwd) > 2 else (np.nan, np.nan)

        pos_dir = (pos_fwd < 0).mean() * 100 if len(pos_fwd) else np.nan
        neg_dir = (neg_fwd > 0).mean() * 100 if len(neg_fwd) else np.nan

        print(f"  h={h}d | pos_trigs: mean={pos_fwd.mean()*100:.2f}% dir_down={pos_dir:.0f}% t={pos_t:.2f} p={pos_p:.3f} n={len(pos_fwd)}")
        print(f"          neg_trigs: mean={neg_fwd.mean()*100:.2f}% dir_up={neg_dir:.0f}% t={neg_t:.2f} p={neg_p:.3f} n={len(neg_fwd)}")

# Full sample
analyze(df, "FULL SAMPLE")

# IS (before OOS split)
is_df = df.loc[df.index < OOS_SPLIT]
analyze(is_df, f"IN-SAMPLE (pre-{OOS_SPLIT})")

# OOS
oos_df = df.loc[df.index >= OOS_SPLIT]
analyze(oos_df, f"OUT-OF-SAMPLE (post-{OOS_SPLIT})")

print("\n=== SUCCESS CRITERIA ===")
print(f"For signal to be tradeable:")
print(f"  - Expected return |>=0.5%| at h=5")
print(f"  - p < 0.05 in OOS period")
print(f"  - Direction >55% in expected direction")
print(f"  - Consistent sign IS and OOS")
