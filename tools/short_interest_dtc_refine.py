"""
Refinement of the settled short-interest anomaly: test the EXTREME most-shorted tail
(top DTC decile/ventile short side) and a LONGER 21-day horizon (APR used ~monthly).
Reuses cached FINRA cross-sections; caches prices to disk.
"""
import os, sys, json, warnings
import numpy as np, pandas as pd
from datetime import datetime, timedelta
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.finra_short_interest import settlement_dates, fetch_cross_section
from tools.yfinance_utils import safe_download
from scipy import stats as st

DISSEM_LAG_DAYS = 9
OOS_START = "2024-01-01"
DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def load_universe():
    u = json.load(open(os.path.join(DATA, "sp500_universe.json")))
    return sorted(set(u["tickers"]))


def get_prices(tickers):
    cache = os.path.join(DATA, "si_prices_cache.parquet")
    if os.path.exists(cache):
        return pd.read_parquet(cache)
    df = safe_download(tickers, start="2020-12-01", end="2026-06-13", auto_adjust=True, progress=False)
    cc = [c for c in df.columns if c.startswith("Close_")]
    close = df[cc].copy(); close.columns = [c[6:] for c in cc]
    close = close.dropna(how="all")
    close.to_parquet(cache)
    return close


def build_panel(universe, prices, hold):
    pidx = prices.index
    dates = settlement_dates("2021-01-01", "2026-06-01")
    rows = []
    for sd in dates:
        et = (datetime.strptime(sd, "%Y-%m-%d") + timedelta(days=DISSEM_LAG_DAYS)).strftime("%Y-%m-%d")
        pos = pidx.searchsorted(pd.Timestamp(et))
        if pos >= len(pidx) or pos + hold >= len(pidx):
            continue
        entry_day, exit_day = pidx[pos], pidx[pos + hold]
        try:
            cs = fetch_cross_section(sd)
        except Exception:
            continue
        cs = cs[cs.index.isin(universe)]
        pe, px = prices.loc[entry_day], prices.loc[exit_day]
        for tk in cs.index:
            a, b = pe.get(tk), px.get(tk)
            if pd.isna(a) or pd.isna(b) or a <= 0:
                continue
            rows.append({"settlement": sd, "entry": entry_day, "ticker": tk,
                         "dtc": cs.loc[tk, "days_to_cover"], "fwd": (b/a)-1})
    return pd.DataFrame(rows)


def tail_test(df, hold, top_frac, label):
    d = df.dropna(subset=["dtc", "fwd"]).copy()
    d["abn"] = d["fwd"] - d.groupby("settlement")["fwd"].transform("mean")
    per = []
    for sd, g in d.groupby("settlement"):
        if len(g) < 30:
            continue
        k = max(1, int(len(g) * top_frac))
        gs = g.sort_values("dtc")
        top = gs.tail(k)["abn"].mean()    # most shorted
        bot = gs.head(k)["abn"].mean()    # least shorted
        per.append({"entry": g["entry"].iloc[0], "top_abn": top, "bot_abn": bot,
                    "ls": bot - top})
    p = pd.DataFrame(per)
    print(f"\n--- {label}: hold={hold}d, extreme {int(top_frac*100)}% tail ---")
    for lbl, sub in [("FULL", p), ("IS<2024", p[p.entry < pd.Timestamp(OOS_START)]),
                     ("OOS>=24", p[p.entry >= pd.Timestamp(OOS_START)])]:
        # short-side: does most-shorted tail underperform (top_abn < 0)?
        x = sub["top_abn"].dropna().values
        ls = sub["ls"].dropna().values
        if len(x) < 5:
            print(f"  {lbl}: n={len(x)} too few"); continue
        ts, ps = st.ttest_1samp(x, 0)
        tl, pl = st.ttest_1samp(ls, 0)
        print(f"  {lbl}: n={len(x):3d} | most-shorted abn={np.mean(x)*100:+.3f}% "
              f"(t={ts:+.2f} p={ps:.3f}) | LS={np.mean(ls)*100:+.3f}% (t={tl:+.2f} p={pl:.3f}) "
              f"dir={(ls>0).mean()*100:.0f}%")


if __name__ == "__main__":
    uni = load_universe()
    prices = get_prices(uni)
    print(f"prices {prices.shape}", flush=True)
    for hold in [10, 21]:
        df = build_panel(uni, prices, hold)
        print(f"\n==== HOLD {hold}d: {len(df)} obs ====", flush=True)
        tail_test(df, hold, 0.10, "decile")
        tail_test(df, hold, 0.05, "ventile")
