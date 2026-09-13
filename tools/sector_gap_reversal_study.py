"""
Idiosyncratic sector-ETF gap reversal study.

Hypothesis: when a SPDR sector ETF gaps at the open by a large amount RELATIVE to
SPY's gap (idiosyncratic sector shock), it reverses (overreaction) over the next
1-5 days. Test both directions; SPY-adjusted (abnormal) returns; survivorship-free
(fixed ETF universe). Canonical next-day-open convention reported alongside close-t entry.
"""
import warnings; warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
from scipy import stats
from tools.tiingo_cache import get_tiingo_cached

SECTORS = ["XLE","XLF","XLK","XLV","XLI","XLP","XLU","XLB","XLRE","XLC"]
START, END = "2010-01-01", "2025-12-31"
OOS_START = pd.Timestamp("2021-01-01")   # last 5y as out-of-sample

def load(sym):
    df = get_tiingo_cached(sym, START, END)
    if df is None or df.empty: return None
    return df[["Open","Close"]].astype(float).sort_index()

spy = load("SPY")
spy_gap = (spy["Open"] / spy["Close"].shift(1) - 1).rename("spy_gap")

rows = []
for sym in SECTORS:
    df = load(sym)
    if df is None:
        print("skip", sym); continue
    g = df["Open"] / df["Close"].shift(1) - 1        # overnight gap
    # forward returns (abnormal vs SPY)
    # entry at close_t, exit close_{t+h}
    fwd = {}
    for h in [1,3,5]:
        r = df["Close"].shift(-h) / df["Close"] - 1
        rs = spy["Close"].shift(-h) / spy["Close"] - 1
        fwd[f"ab_close_{h}"] = (r - rs)
    # entry at open_{t+1}, exit close_{t+h}  (canonical next-day-open)
    for h in [1,3,5]:
        r = df["Close"].shift(-h) / df["Open"].shift(-1) - 1
        rs = spy["Close"].shift(-h) / spy["Open"].shift(-1) - 1
        fwd[f"ab_ndo_{h}"] = (r - rs)
    d = pd.DataFrame({"gap":g, "spy_gap":spy_gap})
    for k,v in fwd.items(): d[k]=v
    d = d.dropna(subset=["gap","spy_gap"])
    # beta of sector gap on spy gap -> residual (idiosyncratic) gap
    b = np.polyfit(d["spy_gap"], d["gap"], 1)
    d["resid_gap"] = d["gap"] - (b[0]*d["spy_gap"] + b[1])
    d["sym"]=sym
    rows.append(d)

alld = pd.concat(rows)
print(f"Total obs: {len(alld)} across {alld['sym'].nunique()} ETFs")

def summ(mask, col, label):
    x = alld.loc[mask, col].dropna()
    if len(x)<30: return f"  {label:28s} n={len(x):4d} (too few)"
    t,p = stats.ttest_1samp(x,0)
    dirpos = (x>0).mean()
    return f"  {label:28s} n={len(x):4d} mean={x.mean()*100:+.2f}% dir+={dirpos*100:.0f}% p={p:.3f}"

# threshold: idiosyncratic gap beyond +/- 1.5%
for thr in [0.015, 0.02, 0.03]:
    up = alld["resid_gap"] >  thr
    dn = alld["resid_gap"] < -thr
    print(f"\n=== |resid_gap| > {thr*100:.1f}%   (up n={up.sum()}, down n={dn.sum()}) ===")
    for col in ["ab_close_1","ab_close_3","ab_close_5","ab_ndo_1","ab_ndo_3","ab_ndo_5"]:
        print("GAP-DOWN:"+summ(dn,col,col))
    print("  ---")
    for col in ["ab_close_1","ab_close_3","ab_close_5","ab_ndo_1","ab_ndo_3","ab_ndo_5"]:
        print("GAP-UP:  "+summ(up,col,col))

# OOS check at the most promising threshold (2%)
print("\n=== OOS split (thr=2%), next-day-open 3d ===")
thr=0.02
for lbl, m in [("IS (<2021)", alld.index<OOS_START), ("OOS (>=2021)", alld.index>=OOS_START)]:
    dn = m & (alld["resid_gap"]<-thr)
    up = m & (alld["resid_gap"]> thr)
    print(f" {lbl}: DOWN"+summ(dn,"ab_ndo_3","")+" | UP"+summ(up,"ab_ndo_3",""))
