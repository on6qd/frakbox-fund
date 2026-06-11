import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
from scipy import stats
from tools.yfinance_utils import get_close_prices

coin = get_close_prices("COIN", period="max")
spy = get_close_prices("SPY", period="max")
vix = get_close_prices("^VIX", period="max")

df = pd.concat([coin.rename("COIN"), spy.rename("SPY"), vix.rename("VIX")], axis=1).dropna()
df["rc"] = df["COIN"].pct_change()
df["rs"] = df["SPY"].pct_change()
df = df.dropna()

beta, alpha, r, p, se = stats.linregress(df["rs"], df["rc"])
df["abn"] = df["rc"] - (alpha + beta*df["rs"])
df["vix_l"] = df["VIX"].shift(1)
df = df.dropna()
q1, q2 = df["vix_l"].quantile([0.333, 0.667])
def regime(v):
    return "low" if v<=q1 else ("mid" if v<=q2 else "high")
df["reg"] = df["vix_l"].apply(regime)

print(f"COIN market beta vs SPY: {beta:.2f} (r2={r**2:.2f}), n={len(df)}")
print(f"VIX terciles: low<={q1:.1f}, high>{q2:.1f}\n")
print("=== RAW COIN returns by regime ===")
for rg in ["low","mid","high"]:
    x = df[df["reg"]==rg]["rc"]; print(f"  {rg:4s}: mean={x.mean()*100:+.3f}%/day  n={len(x)}")
H,pK = stats.kruskal(df[df.reg=="low"].rc, df[df.reg=="mid"].rc, df[df.reg=="high"].rc)
print(f"  Kruskal H={H:.2f} p={pK:.4f}\n")
print("=== ABNORMAL (SPY-adjusted) COIN returns by regime ===")
for rg in ["low","mid","high"]:
    x = df[df["reg"]==rg]["abn"]; print(f"  {rg:4s}: mean={x.mean()*100:+.3f}%/day  n={len(x)}")
H2,pK2 = stats.kruskal(df[df.reg=="low"].abn, df[df.reg=="mid"].abn, df[df.reg=="high"].abn)
print(f"  Kruskal H={H2:.2f} p={pK2:.4f}")
tt = stats.ttest_ind(df[df.reg=="low"].abn, df[df.reg=="high"].abn)
print(f"  low-vs-high abnormal t={tt.statistic:.2f} p={tt.pvalue:.4f}")
print(f"  abnormal low {df[df.reg=='low'].abn.mean()*100:+.3f}% vs high {df[df.reg=='high'].abn.mean()*100:+.3f}%")
