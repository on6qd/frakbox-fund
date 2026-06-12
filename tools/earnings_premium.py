"""Earnings Announcement Premium test (Frazzini & Lamont 2007).

For a universe of large-caps, fetch historical earnings dates, map each to its
'announcement trading day' (AMC release -> next session, BMO -> same session),
and measure abnormal return (stock - SPY) over the announcement window.

Outputs compact summary stats only. Full per-event rows stored to CSV for audit.
"""
import sys, warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

UNIVERSE = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","JPM","BAC","WFC","XOM",
            "CVX","JNJ","PFE","UNH","HD","WMT","PG","KO","PEP","DIS",
            "NKE","CAT","BA","INTC","CSCO","ORCL","CRM","MCD","VZ","T"]

def get_announce_days(sym):
    """Return list of (announce_trading_date pd.Timestamp normalized, raw_ts)."""
    try:
        df = yf.Ticker(sym).get_earnings_dates(limit=40)
    except Exception as e:
        return []
    if df is None or len(df) == 0:
        return []
    out = []
    for ts in df.index:
        hour = ts.hour
        d = pd.Timestamp(ts.date())
        # AMC: released after close -> reaction next session. BMO: same session.
        amc = hour >= 16
        out.append((d, amc))
    return out

def main():
    syms = UNIVERSE
    start = "2019-11-01"; end = "2026-06-12"
    px = yf.download(syms + ["SPY"], start=start, end=end, auto_adjust=True,
                     progress=False)["Close"]
    px = px.dropna(how="all")
    idx = px.index  # trading days
    spy = px["SPY"]

    rows = []
    for sym in syms:
        if sym not in px.columns:
            continue
        s = px[sym]
        for d, amc in get_announce_days(sym):
            # find announcement trading day position
            # the session that first incorporates the news
            loc = idx.searchsorted(d)
            if loc >= len(idx):
                continue
            sess = idx[loc] if idx[loc] >= d else (idx[loc+1] if loc+1 < len(idx) else None)
            # idx.searchsorted gives first idx >= d
            ad = idx[loc] if loc < len(idx) else None
            if ad is None:
                continue
            if amc:
                # reaction is the NEXT session after release day d
                # if d is a trading day, ad == d (or first >= d); reaction = session after ad
                p = idx.searchsorted(d, side="right")  # first session strictly after d
                if p >= len(idx): continue
                ad = idx[p]
            apos = idx.get_loc(ad)
            if apos < 1 or apos + 1 >= len(idx):
                continue
            e0 = idx[apos-1]   # entry: close day before announcement session
            e1 = idx[apos+1]   # exit: close day after announcement session
            try:
                r_stock = s.loc[e1]/s.loc[e0] - 1
                r_spy = spy.loc[e1]/spy.loc[e0] - 1
                # announcement-day-only abnormal
                r_stock_d = s.loc[ad]/s.loc[idx[apos-1]] - 1
                r_spy_d = spy.loc[ad]/spy.loc[idx[apos-1]] - 1
            except KeyError:
                continue
            if pd.isna(r_stock) or pd.isna(r_spy):
                continue
            rows.append({"sym":sym,"announce_day":ad,"year":ad.year,
                         "abn_window":(r_stock-r_spy),
                         "abn_day":(r_stock_d-r_spy_d)})
    df = pd.DataFrame(rows)
    df.to_csv("tools/earnings_premium_events.csv", index=False)

    def report(sub, label):
        n=len(sub)
        if n<5:
            print(f"{label:20} n={n} (too few)"); return
        for col in ["abn_window","abn_day"]:
            x=sub[col].values*100
            t,p=stats.ttest_1samp(x,0)
            print(f"{label:20} {col:11} n={n:4d} mean={x.mean():+.3f}% "
                  f"med={np.median(x):+.3f}% pos={ (x>0).mean()*100:4.1f}% "
                  f"t={t:+.2f} p={p:.4f}")

    print(f"TOTAL events: {len(df)}  symbols={df['sym'].nunique()}")
    report(df, "ALL 2020-2026")
    report(df[df.year<=2022], "DISCOVERY 20-22")
    report(df[df.year>=2023], "OOS 23-26")
    print("\nPer-year mean abn_window (%):")
    print((df.groupby('year')['abn_window'].mean()*100).round(3).to_string())

if __name__=="__main__":
    main()
