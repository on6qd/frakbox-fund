"""
SPY index-level short-term reversal after N consecutive down days.

HYPOTHESIS (pre-registered):
  After SPY closes DOWN for N consecutive sessions, short-term overreaction
  produces a positive bounce the next session.
  Entry: open of day T+1 (no lookahead — signal known at close of T).
  Hold : to close of T+1 (1-day). Return = (close_{T+1} - open_{T+1}) / open_{T+1}.
  Also report close-to-close next-day for comparison.

  Direction: LONG.

SUCCESS CRITERIA (set BEFORE looking at results):
  - Conditional next-day mean (open->close) > +0.15% AND > unconditional by >=0.10%
  - p < 0.05 (two-sided t-test vs 0)
  - direction (share positive) > 52%
  - SAME sign in recent subset (2015+ and 2020+) — no regime flip
  - Not a pure-crisis artifact (still positive after excluding 2008-09-01..2009-06-30
    and 2020-02-15..2020-04-30)

Samples: full 2000-2026, modern 2010+, recent 2015+, recent2 2020+.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools  # curl_cffi shim
from tools.yfinance_utils import safe_download
import numpy as np
import pandas as pd
from scipy import stats

df = safe_download("SPY", start="2000-01-01", end="2026-07-07")
# normalize columns
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
df = df.rename(columns=str.title)
o, c = df["Open"].astype(float), df["Close"].astype(float)
ret_cc = c.pct_change()                       # close-to-close
oc = (c - o) / o                              # open-to-close (same day)
down = (ret_cc < 0).astype(int)

# consecutive down-day count ending at each day t (based on close-to-close)
streak = np.zeros(len(down), dtype=int)
vals = down.values
for i in range(len(vals)):
    if vals[i] == 1:
        streak[i] = (streak[i-1] + 1) if i > 0 else 1
    else:
        streak[i] = 0
streak = pd.Series(streak, index=down.index)

idx = df.index
def subset_mask(start=None):
    return pd.Series(True, index=idx) if start is None else (idx >= pd.Timestamp(start))

crisis = ((idx >= "2008-09-01") & (idx <= "2009-06-30")) | \
         ((idx >= "2020-02-15") & (idx <= "2020-04-30"))
crisis = pd.Series(crisis, index=idx)

# unconditional baselines
def summarize(vals):
    vals = vals.dropna()
    n = len(vals)
    if n < 20:
        return dict(n=n, mean=float('nan'), p=float('nan'), dir=float('nan'))
    t, p = stats.ttest_1samp(vals, 0.0)
    return dict(n=n, mean=vals.mean()*100, p=p, dir=(vals > 0).mean()*100)

print("SPY rows:", len(df), "| range", idx.min().date(), "->", idx.max().date())
uncond_oc = oc.dropna()
print(f"\nUNCONDITIONAL open->close: n={len(uncond_oc)} mean={uncond_oc.mean()*100:.3f}%  dir={(uncond_oc>0).mean()*100:.1f}%")

# For each N, signal fires at close of day t when streak[t]>=N; enter next day.
# next-day open->close return is oc shifted by -1 aligned to signal day t.
oc_next = oc.shift(-1)
cc_next = ret_cc.shift(-1)

for N in [2, 3, 4, 5]:
    # FIRST-TOUCH only: streak reaches EXACTLY N -> one independent event per down-run.
    # (streak>=N overlaps: days 4,5,6 of one run all count, autocorrelated.)
    sig = (streak == N)
    print(f"\n===== streak == {N} (first-touch, independent events) =====")
    for label, start in [("full2000", None), ("modern2010", "2010-01-01"),
                          ("recent2015", "2015-01-01"), ("recent2020", "2020-01-01")]:
        m = sig & subset_mask(start)
        s_oc = summarize(oc_next[m])
        s_cc = summarize(cc_next[m])
        # excl crisis
        s_oc_nc = summarize(oc_next[m & ~crisis])
        print(f"  [{label:11}] O->C n={s_oc['n']:4d} mean={s_oc['mean']:+.3f}% p={s_oc['p']:.3f} dir={s_oc['dir']:.1f}% "
              f"| C->C mean={s_cc['mean']:+.3f}% p={s_cc['p']:.3f} "
              f"| exCrisis mean={s_oc_nc['mean']:+.3f}% p={s_oc_nc['p']:.3f} n={s_oc_nc['n']}")
