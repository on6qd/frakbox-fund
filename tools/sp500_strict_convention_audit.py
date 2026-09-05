"""SP500 index-addition strict next-day-open convention audit.

Signal is announcement-anchored: enters at Open(announcement_day). S&P announces
quarterly rebalance additions AFTER close on the first Friday of the effective
month (verified Q1 2026 Mar 6, Q2 2026 Jun 5). So Open(announcement_day) is a
LOOKAHEAD -- it buys the morning before that evening's announcement.

Compare:
  LOOKAHEAD entry  = Open(ann_day)      [current backtest]
  STRICT entry     = Open(ann_day + 1)  [first tradeable moment after news]
Same exit (effective-date close) for both, so the only difference is entry timing.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd, numpy as np
from tools.yfinance_utils import safe_download

# (symbol, effective_date) quarterly rebalance adds 2023-2026 (survivors, Wikipedia)
EVENTS = [
    ("BG","2023-03-15"),("PODD","2023-03-15"),("FICO","2023-03-20"),
    ("PANW","2023-06-20"),("ABNB","2023-09-18"),("BX","2023-09-18"),
    ("JBL","2023-12-18"),("UBER","2023-12-18"),("BLDR","2023-12-18"),
    ("SMCI","2024-03-18"),("DECK","2024-03-18"),
    ("CRWD","2024-06-24"),("KKR","2024-06-24"),("GDDY","2024-06-24"),
    ("DELL","2024-09-23"),("ERIE","2024-09-23"),("PLTR","2024-09-23"),
    ("APO","2024-12-23"),("LII","2024-12-23"),("WDAY","2024-12-23"),
    ("TKO","2025-03-24"),("DASH","2025-03-24"),("EXE","2025-03-24"),("WSM","2025-03-24"),
    ("HOOD","2025-09-22"),("EME","2025-09-22"),("APP","2025-09-22"),
    ("CVNA","2025-12-22"),("FIX","2025-12-22"),("CRH","2025-12-22"),
    ("ECHO","2026-03-23"),("VRT","2026-03-23"),("COHR","2026-03-23"),("LITE","2026-03-23"),
    ("MRVL","2026-06-22"),("FLEX","2026-06-22"),
]

def first_friday(year, month):
    d = pd.Timestamp(year=year, month=month, day=1)
    while d.weekday() != 4:  # Friday
        d += pd.Timedelta(days=1)
    return d

def get_ohlc(sym, start, end):
    df = safe_download(sym, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or len(df)==0: return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df[["Open","Close"]].dropna()

rows=[]
for sym, eff in EVENTS:
    eff_ts = pd.Timestamp(eff)
    ann = first_friday(eff_ts.year, eff_ts.month)  # announcement day (after close)
    win_start = (ann - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    win_end   = (eff_ts + pd.Timedelta(days=6)).strftime("%Y-%m-%d")
    px = get_ohlc(sym, win_start, win_end)
    spy = get_ohlc("SPY", win_start, win_end)
    if px is None or spy is None:
        rows.append({"sym":sym,"eff":eff,"status":"no_data"}); continue
    idx = px.index.intersection(spy.index)
    px, spy = px.loc[idx], spy.loc[idx]
    trading = px.index
    # ann_day = first trading day >= ann
    after_ann = trading[trading >= ann]
    if len(after_ann) < 3: rows.append({"sym":sym,"eff":eff,"status":"no_ann"}); continue
    d0 = after_ann[0]                 # announcement day (lookahead entry open here)
    d1 = after_ann[1]                 # next trading day (strict entry open here)
    # exit = effective-date close (last trading day <= eff)
    on_or_before_eff = trading[trading <= eff_ts]
    if len(on_or_before_eff)==0: rows.append({"sym":sym,"eff":eff,"status":"no_exit"}); continue
    dexit = on_or_before_eff[-1]
    if dexit <= d1: rows.append({"sym":sym,"eff":eff,"status":"exit_too_early"}); continue

    def abn(entry_open_day):
        e_sym = px.loc[entry_open_day,"Open"]; e_spy = spy.loc[entry_open_day,"Open"]
        x_sym = px.loc[dexit,"Close"];         x_spy = spy.loc[dexit,"Close"]
        return ( (x_sym/e_sym-1) - (x_spy/e_spy-1) )*100

    look = abn(d0)   # lookahead: enter Open(ann_day)
    strict = abn(d1) # strict: enter Open(ann_day+1)
    # announcement pop forgone = Open(d0)->Open(d1) abnormal
    pop = ((px.loc[d1,"Open"]/px.loc[d0,"Open"]-1) - (spy.loc[d1,"Open"]/spy.loc[d0,"Open"]-1))*100
    rows.append({"sym":sym,"eff":eff,"ann":d0.strftime("%Y-%m-%d"),
                 "strict_entry":d1.strftime("%Y-%m-%d"),"exit":dexit.strftime("%Y-%m-%d"),
                 "look":round(look,2),"strict":round(strict,2),"pop":round(pop,2),"status":"ok"})

df = pd.DataFrame(rows)
ok = df[df.status=="ok"].copy()
ok["yr"]=ok["eff"].str[:4].astype(int)
pd.set_option("display.width",200,"display.max_rows",60)
print(ok[["sym","ann","strict_entry","exit","look","strict","pop"]].to_string(index=False))
print("\nfailed:", df[df.status!="ok"][["sym","eff","status"]].to_dict("records"))

def summ(label, s):
    print(f"{label:28} n={len(s):2d} mean={s.mean():+.2f}% median={s.median():+.2f}% pos={100*(s>0).mean():.0f}%")
print("\n=== FULL SAMPLE (2023-2026) ===")
summ("LOOKAHEAD (Open ann-day)", ok["look"]); summ("STRICT (Open ann-day+1)", ok["strict"]); summ("announcement pop forgone", ok["pop"])
rec=ok[ok.yr>=2024]
print("\n=== RECENT (2024-2026) ===")
summ("LOOKAHEAD", rec["look"]); summ("STRICT", rec["strict"]); summ("pop forgone", rec["pop"])
from scipy import stats
for lab,s in [("FULL",ok),("RECENT",rec)]:
    t,p=stats.ttest_1samp(s["strict"],0); print(f"{lab} strict t-test: t={t:.2f} p={p:.4f}")
