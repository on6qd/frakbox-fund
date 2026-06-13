"""
GLD tariff extended backtest — finds additional tariff escalation dates
and measures 20d abnormal returns vs SPY.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from scipy import stats
from tools.yfinance_utils import safe_download

# ---- Known existing events (from hypothesis) ----
# Returns: [+2.16, -6.28, +6.84, +7.79, +6.42, +7.01, +9.34]
existing_returns = [2.16, -6.28, 6.84, 7.79, 6.42, 7.01, 9.34]

# ---- Additional candidate tariff escalation dates ----
# Sources: Bush 2002 steel, Obama 2009 tires, various 2018-2019, 2025 episodes
# Using next trading day after announcement for entry
candidate_events = [
    # NOTE: GLD launched 2004-11-18. All events must be >= 2004-12-01.
    # Obama tire tariffs
    ("2009-09-11", "Obama imposes 35% tariff on Chinese tires (Section 421)"),
    # Obama solar/steel
    ("2012-03-20", "US imposes duties on Chinese solar panels (first round)"),
    ("2014-12-16", "US imposes antidumping duties on Chinese solar panels (2nd)"),
    # Additional 2018 escalations not in original set
    ("2018-03-22", "Trump signs Section 301 memo — $60B China tariffs announced"),
    ("2018-06-15", "USTR publishes $34B China tariff list effective July 6"),
    ("2018-09-17", "Trump announces $200B China tariff list at 10%"),
    # 2019 escalations (if not already in existing set)
    ("2019-05-10", "US raises tariffs on $200B Chinese goods 10% to 25%"),
    ("2019-08-05", "Trump 10% tariffs on remaining $300B Chinese imports"),
    ("2019-12-13", "Phase 1 deal announced — partial tariff rollback"),  # note: reduction, include as control
    # 2025 escalations (additional)
    ("2025-01-20", "Trump inauguration — executive orders on tariff expansion"),
    ("2025-03-04", "25% tariffs on Canada/Mexico take effect"),
    ("2025-03-12", "Additional steel/aluminum tariffs 25% effective"),
]

def get_20d_abnormal_return(ticker, event_date, window=20):
    """Returns (abnormal_ret, gld_ret, spy_ret) or None if data missing."""
    import pandas as pd
    from datetime import datetime, timedelta

    dt = pd.Timestamp(event_date)
    # Fetch 60 calendar days of data to ensure we get 20 trading days
    start = dt - pd.Timedelta(days=5)  # small buffer before
    end = dt + pd.Timedelta(days=60)

    try:
        gld = safe_download("GLD", start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), auto_adjust=True)
    except Exception:
        return None
    try:
        spy = safe_download("SPY", start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), auto_adjust=True)
    except Exception:
        return None

    if gld is None or spy is None or len(gld) < 5 or len(spy) < 5:
        return None

    # Find first trading day on or after event date
    gld_dates = gld.index
    spy_dates = spy.index

    entry_dates = gld_dates[gld_dates >= dt]
    if len(entry_dates) < window + 1:
        return None

    entry = entry_dates[0]

    # Get entry prices (open of that day = after-hours announcement entry)
    try:
        gld_entry = float(gld.loc[entry, 'Open']) if 'Open' in gld.columns else float(gld.loc[entry].iloc[0])
        spy_entry = float(spy.loc[entry, 'Open']) if 'Open' in spy.columns else float(spy.loc[entry].iloc[0])
    except:
        return None

    # Exit: close 20 trading days later
    idx = list(gld_dates).index(entry)
    if idx + window >= len(gld_dates):
        return None

    exit_date = gld_dates[idx + window]

    # Find matching spy exit
    spy_exit_dates = spy_dates[spy_dates >= exit_date]
    if len(spy_exit_dates) == 0:
        return None
    spy_exit = spy_exit_dates[0]

    try:
        gld_exit = float(gld.loc[exit_date, 'Close']) if 'Close' in gld.columns else float(gld.loc[exit_date].iloc[-1])
        spy_exit_price = float(spy.loc[spy_exit, 'Close']) if 'Close' in spy.columns else float(spy.loc[spy_exit].iloc[-1])
    except:
        return None

    gld_ret = (gld_exit / gld_entry - 1) * 100
    spy_ret = (spy_exit_price / spy_entry - 1) * 100
    abnormal = gld_ret - spy_ret

    return abnormal, gld_ret, spy_ret, str(entry.date()), str(exit_date.date())


print("=" * 65)
print("GLD TARIFF EXTENDED BACKTEST — 20d Abnormal Returns")
print("=" * 65)
print(f"\nExisting events (n={len(existing_returns)}): {existing_returns}")
print(f"Existing mean: {np.mean(existing_returns):.2f}%")

# One-sample t-test on existing data
t_stat, p_val = stats.ttest_1samp(existing_returns, 0)
print(f"Existing t-test: t={t_stat:.3f}, p={p_val:.4f} (two-sided)")

print("\n--- Testing additional candidate events ---\n")

new_results = []
for date, desc in candidate_events:
    result = get_20d_abnormal_return("GLD", date)
    if result is None:
        print(f"  {date}  SKIP (no data)  — {desc}")
        continue
    abnormal, gld_ret, spy_ret, entry_d, exit_d = result
    status = "INCLUDE" if True else ""
    print(f"  {date}  abnormal={abnormal:+.2f}%  (GLD={gld_ret:+.2f}%, SPY={spy_ret:+.2f}%)  entry={entry_d}")
    print(f"    {desc}")
    new_results.append({
        "date": date,
        "desc": desc,
        "abnormal": abnormal,
        "gld_ret": gld_ret,
        "spy_ret": spy_ret,
        "entry": entry_d
    })

print("\n" + "=" * 65)
print("COMBINED ANALYSIS")
print("=" * 65)

if new_results:
    new_returns = [r["abnormal"] for r in new_results]
    all_returns = existing_returns + new_returns

    print(f"\nNew events found: n={len(new_results)}")
    print(f"New returns: {[f'{r:+.2f}' for r in new_returns]}")
    print(f"\nCombined dataset: n={len(all_returns)}")
    print(f"Combined returns: {[f'{r:+.2f}' for r in all_returns]}")
    print(f"Combined mean: {np.mean(all_returns):.2f}%")
    print(f"Combined std: {np.std(all_returns, ddof=1):.2f}%")

    # One-sample t-test (H0: mean = 0)
    t_stat2, p_val2 = stats.ttest_1samp(all_returns, 0)
    print(f"\nOne-sample t-test (H0: mean=0):")
    print(f"  t = {t_stat2:.3f}, p = {p_val2:.4f} (two-sided)")
    print(f"  p < 0.01? {'YES' if p_val2 < 0.01 else 'NO'}")
    print(f"  p < 0.05? {'YES' if p_val2 < 0.05 else 'NO'}")

    # Wilcoxon signed-rank (non-parametric, robust to outliers)
    if len(all_returns) >= 5:
        try:
            w_stat, w_p = stats.wilcoxon(all_returns, alternative='greater')
            print(f"\nWilcoxon signed-rank (one-sided, H1: median > 0):")
            print(f"  W = {w_stat:.1f}, p = {w_p:.4f}")
            print(f"  p < 0.01? {'YES' if w_p < 0.01 else 'NO'}")
        except Exception as e:
            print(f"  Wilcoxon failed: {e}")

    # Direction stats
    direction = sum(1 for r in all_returns if r > 0.5) / len(all_returns) * 100
    print(f"\nDirection (>0.5%): {direction:.0f}%")

    # Binomial test for direction
    n_pos = sum(1 for r in all_returns if r > 0.5)
    binom_p = stats.binomtest(n_pos, len(all_returns), 0.5, alternative='greater').pvalue
    print(f"Binomial test (p(direction=positive)>50%): p={binom_p:.4f}")

    print("\n--- EVENT BREAKDOWN (new events) ---")
    for r in new_results:
        tag = "+" if r["abnormal"] > 0.5 else ("-" if r["abnormal"] < -0.5 else "~")
        print(f"  [{tag}] {r['date']}  {r['abnormal']:+.2f}%  {r['desc'][:55]}")

    # Outlier check
    mean_all = np.mean(all_returns)
    std_all = np.std(all_returns, ddof=1)
    outliers = [r for r in all_returns if abs(r - mean_all) > 2 * std_all]
    if outliers:
        print(f"\nOutlier check (>2 std): {[f'{r:+.2f}' for r in outliers]}")
        trimmed = [r for r in all_returns if abs(r - mean_all) <= 2 * std_all]
        t3, p3 = stats.ttest_1samp(trimmed, 0)
        print(f"Trimmed t-test (n={len(trimmed)}): t={t3:.3f}, p={p3:.4f}")
    else:
        print("\nNo outliers (>2 std) detected.")
else:
    print("No new results retrieved.")

print("\nDone.")
