"""Test if BTC weekend/overnight returns predict MSTR Monday open-to-close drift.

Hypothesis: BTC trades 24/7, MSTR only 9:30-16:00 ET. When BTC moves significantly
from Friday close to Monday open, MSTR gaps at open but may under-react or over-react.
If MSTR continues to drift in the direction of the BTC move through Monday close,
that's a tradeable signal (buy/short at open, exit at close).

Approach:
- For each Monday, compute BTC return from Friday 16:00 UTC to Monday 14:30 UTC (approx MSTR open)
  -- approximated as BTC Friday close -> Monday close return (both daily bars)
- Measure MSTR Monday open-to-close return
- Regress MSTR open-to-close return on BTC weekend return
- Slope > 0 and significant means BTC weekend move predicts intraday MSTR drift
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from scipy import stats

START = "2023-01-01"
END = "2026-04-17"

# Fetch MSTR OHLC
mstr = yf.download("MSTR", start=START, end=END, auto_adjust=False, progress=False)
if isinstance(mstr.columns, pd.MultiIndex):
    mstr.columns = mstr.columns.get_level_values(0)

# Fetch BTC (trades 24/7 — daily bar covers full day)
btc = yf.download("BTC-USD", start=START, end=END, auto_adjust=False, progress=False)
if isinstance(btc.columns, pd.MultiIndex):
    btc.columns = btc.columns.get_level_values(0)

# MSTR open-to-close
mstr["o2c_return"] = (mstr["Close"] / mstr["Open"]) - 1.0
mstr["dow"] = mstr.index.dayofweek  # 0=Monday

# PROPER BTC weekend return: Friday close -> Sunday close
# Sunday's daily BTC bar Close = Monday 00:00 UTC = Sunday 20:00 ET
# That is BEFORE MSTR Monday 9:30 ET open, so it is non-contaminated.
btc_close = btc["Close"]

# For each Monday in MSTR, get the BTC close from previous Friday AND previous Sunday
def btc_weekend_return(monday_date, btc_close):
    # Find Friday (2 calendar days before Monday)
    friday = monday_date - pd.Timedelta(days=3)
    sunday = monday_date - pd.Timedelta(days=1)
    # BTC index lookups (use asof for missing days)
    try:
        fri_price = btc_close.asof(friday)
        sun_price = btc_close.asof(sunday)
        if pd.isna(fri_price) or pd.isna(sun_price) or fri_price == 0:
            return np.nan
        return (sun_price / fri_price) - 1.0
    except Exception:
        return np.nan

is_monday = (mstr["dow"] == 0)
monday_df = mstr[is_monday].copy()
monday_df["btc_weekend_ret"] = [btc_weekend_return(d, btc_close) for d in monday_df.index]

print(f"\n=== BTC Weekend -> MSTR Monday Open-to-Close ===")
print(f"N Mondays: {len(monday_df.dropna())}")
print(f"Date range: {monday_df.index[0].date()} to {monday_df.index[-1].date()}")

# Drop NaN
df = monday_df[["o2c_return", "btc_weekend_ret"]].dropna()

# Regress
slope, intercept, r_value, p_value, std_err = stats.linregress(df["btc_weekend_ret"], df["o2c_return"])
print(f"\nIn-sample (all Mondays):")
print(f"  Slope (MSTR o2c per 1% BTC weekend): {slope:.4f}")
print(f"  R²: {r_value**2:.4f}")
print(f"  p-value: {p_value:.4g}")
print(f"  Intercept: {intercept:.4g}")
print(f"  Correlation: {r_value:.4f}")

# Split IS/OOS
is_df = df[df.index < "2025-01-01"]
oos_df = df[df.index >= "2025-01-01"]

if len(is_df) >= 30:
    is_s, is_i, is_r, is_p, _ = stats.linregress(is_df["btc_weekend_ret"], is_df["o2c_return"])
    print(f"\nIS (2023-2024, n={len(is_df)}): slope={is_s:.4f}, R²={is_r**2:.4f}, p={is_p:.4g}")

if len(oos_df) >= 30:
    oos_s, oos_i, oos_r, oos_p, _ = stats.linregress(oos_df["btc_weekend_ret"], oos_df["o2c_return"])
    print(f"OOS (2025+, n={len(oos_df)}): slope={oos_s:.4f}, R²={oos_r**2:.4f}, p={oos_p:.4g}")

# Threshold analysis: large BTC moves
print(f"\n=== Threshold Analysis: Large BTC Weekend Moves ===")
for thresh in [0.03, 0.05, 0.07]:
    up = df[df["btc_weekend_ret"] > thresh]
    dn = df[df["btc_weekend_ret"] < -thresh]
    if len(up) >= 5:
        print(f"BTC weekend > +{thresh*100:.0f}%: n={len(up)}, MSTR o2c mean={up['o2c_return'].mean()*100:.3f}%, pos_rate={(up['o2c_return']>0).mean()*100:.1f}%")
    if len(dn) >= 5:
        print(f"BTC weekend < -{thresh*100:.0f}%: n={len(dn)}, MSTR o2c mean={dn['o2c_return'].mean()*100:.3f}%, pos_rate={(dn['o2c_return']>0).mean()*100:.1f}%")

# Compare to non-Monday baseline (weekday MSTR o2c)
non_monday = mstr[~is_monday]["o2c_return"].dropna()
print(f"\n=== Baseline ===")
print(f"MSTR o2c all days mean: {mstr['o2c_return'].dropna().mean()*100:.3f}%, std: {mstr['o2c_return'].dropna().std()*100:.2f}%")
print(f"MSTR o2c Mondays mean:  {monday_df['o2c_return'].dropna().mean()*100:.3f}%, std: {monday_df['o2c_return'].dropna().std()*100:.2f}%")
print(f"MSTR o2c non-Mondays mean: {non_monday.mean()*100:.3f}%, std: {non_monday.std()*100:.2f}%")
