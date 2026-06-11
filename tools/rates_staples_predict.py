import numpy as np, pandas as pd
from timeseries import get_aligned_series

START="2010-01-01"; END="2026-06-10"; OOS="2024-01-01"
df = get_aligned_series(["XLP","SPY","FRED:DGS10"], START, END)
df = df.rename(columns={"FRED:DGS10":"DGS10"}).dropna()
# yield change (bps) over past M days; XLP forward N-day ABNORMAL return (vs SPY)
for M,N in [(5,5),(5,3),(3,3),(10,5),(1,1)]:
    d = df.copy()
    d["dy"] = d["DGS10"] - d["DGS10"].shift(M)           # past M-day yield change (pct pts)
    d["xlp_fwd"] = d["XLP"].shift(-N)/d["XLP"] - 1
    d["spy_fwd"] = d["SPY"].shift(-N)/d["SPY"] - 1
    d["abn"] = d["xlp_fwd"] - d["spy_fwd"]
    d = d.dropna()
    isd = d[d.index < OOS]; oos = d[d.index >= OOS]
    # regression abn ~ dy (in-sample)
    x = isd["dy"].values; y = isd["abn"].values
    b1,b0 = np.polyfit(x,y,1)
    corr = np.corrcoef(x,y)[0,1]
    # OOS sign agreement
    xo=oos["dy"].values; yo=oos["abn"].values
    b1o,_=np.polyfit(xo,yo,1)
    print(f"M={M} N={N} | IS n={len(isd)} slope={b1:.4f} corr={corr:+.3f} | OOS n={len(oos)} slope={b1o:.4f} sign_match={np.sign(b1)==np.sign(b1o)}")
