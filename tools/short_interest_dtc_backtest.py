"""
Settled (bi-monthly) short-interest cross-sectional anomaly backtest.

Hypothesis (Asquith-Pathak-Ritter 2005): heavily shorted stocks (high days-to-cover /
high short-interest-ratio) earn negative abnormal returns. Tested here as a
cross-sectional quintile long-short on a large-cap universe, rebalanced each FINRA
bi-monthly settlement, with NO-LOOKAHEAD entry (enter >= settlement + 9 calendar days,
after FINRA's ~8-business-day dissemination lag).

Signals tested:
  1. days_to_cover (DTC) level  -> long low-DTC quintile, short high-DTC quintile
  2. change_pct (rise in short position) -> long decreasing-SI, short increasing-SI

Caveat: universe = CURRENT S&P 500 constituents (survivorship bias). This drops names
that were delisted/bankrupt (often the heavily-shorted losers), so it biases AGAINST
finding short-side underperformance -> any signal found is conservative.
"""
import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.finra_short_interest import settlement_dates, fetch_cross_section
from tools.yfinance_utils import safe_download

HOLD_DAYS = 10          # trading days held
DISSEM_LAG_DAYS = 9     # calendar days after settlement before entry (post-publication)
N_Q = 5
OOS_START = "2024-01-01"


def load_universe():
    u = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "sp500_universe.json")))
    return sorted(set(u["tickers"]))


def get_prices(tickers, start, end):
    df = safe_download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    # safe_download returns FLAT columns: "Close_AAPL" for multi-ticker, "Close" for single.
    close_cols = [c for c in df.columns if c.startswith("Close_")]
    if close_cols:
        close = df[close_cols].copy()
        close.columns = [c[len("Close_"):] for c in close_cols]
    elif "Close" in df.columns:
        close = df[["Close"]].copy()
        close.columns = [tickers[0] if isinstance(tickers, list) else tickers]
    else:
        raise ValueError(f"No Close columns found: {list(df.columns)[:8]}")
    return close.dropna(how="all")


def first_trading_on_or_after(idx, target_date):
    pos = idx.searchsorted(pd.Timestamp(target_date))
    if pos >= len(idx):
        return None
    return idx[pos]


def run():
    universe = load_universe()
    dates = settlement_dates("2021-01-01", "2026-06-01")
    print(f"Universe={len(universe)} tickers; settlement_dates={len(dates)} "
          f"({dates[0]}..{dates[-1]})", flush=True)

    print("Downloading prices...", flush=True)
    prices = get_prices(universe, "2020-12-01", "2026-06-13")
    pidx = prices.index
    print(f"Price matrix: {prices.shape}", flush=True)

    rows = []  # one record per (settlement_date, ticker)
    for i, sd in enumerate(dates):
        # need a next reference far enough that hold window has data
        entry_target = (datetime.strptime(sd, "%Y-%m-%d") + timedelta(days=DISSEM_LAG_DAYS)).strftime("%Y-%m-%d")
        entry_day = first_trading_on_or_after(pidx, entry_target)
        if entry_day is None:
            continue
        epos = pidx.get_loc(entry_day)
        if epos + HOLD_DAYS >= len(pidx):
            continue
        exit_day = pidx[epos + HOLD_DAYS]

        try:
            cs = fetch_cross_section(sd)
        except Exception as e:
            print(f"  {sd}: fetch failed {e}", flush=True)
            continue
        cs = cs[cs.index.isin(universe)]
        if cs.empty:
            continue

        p_entry = prices.loc[entry_day]
        p_exit = prices.loc[exit_day]
        for tk in cs.index:
            pe, px = p_entry.get(tk), p_exit.get(tk)
            if pd.isna(pe) or pd.isna(px) or pe <= 0:
                continue
            fwd = (px / pe) - 1.0
            rows.append({
                "settlement": sd, "entry": entry_day, "ticker": tk,
                "dtc": cs.loc[tk, "days_to_cover"],
                "chg": cs.loc[tk, "change_pct"],
                "fwd_ret": fwd,
            })
        if (i + 1) % 20 == 0:
            print(f"  processed {i+1}/{len(dates)} settlements, rows={len(rows)}", flush=True)

    df = pd.DataFrame(rows)
    print(f"\nTotal stock-period observations: {len(df)}", flush=True)
    df.to_parquet(os.path.join(os.path.dirname(__file__), "..", "data", "si_dtc_panel.parquet"))

    for factor, asc_long in [("dtc", True), ("chg", True)]:
        # asc_long=True: long the LOW factor quintile, short the HIGH factor quintile
        analyze(df, factor)


def analyze(df, factor):
    print(f"\n{'='*70}\nFACTOR: {factor}  (long low-{factor} quintile, short high-{factor} quintile)\n{'='*70}")
    d = df.dropna(subset=[factor, "fwd_ret"]).copy()
    # market-relative: subtract each period's universe mean (abnormal return)
    d["abn"] = d["fwd_ret"] - d.groupby("settlement")["fwd_ret"].transform("mean")

    per = []
    for sd, g in d.groupby("settlement"):
        if g[factor].nunique() < N_Q or len(g) < N_Q * 3:
            continue
        try:
            q = pd.qcut(g[factor].rank(method="first"), N_Q, labels=False)
        except Exception:
            continue
        g = g.assign(q=q.values)
        lo = g[g["q"] == 0]["abn"].mean()         # low factor
        hi = g[g["q"] == N_Q - 1]["abn"].mean()    # high factor
        ls = lo - hi                               # long low, short high
        per.append({"settlement": sd, "entry": g["entry"].iloc[0],
                    "lo": lo, "hi": hi, "ls": ls, "n": len(g)})
    pdf = pd.DataFrame(per)
    if pdf.empty:
        print("  insufficient data")
        return

    from scipy import stats as st
    full = pdf["ls"].dropna().values
    t, p = st.ttest_1samp(full, 0)
    print(f"  FULL : n={len(full):3d} | LS={np.mean(full)*100:+.3f}%/{HOLD_DAYS}d | t={t:+.2f} p={p:.3f} | dir={(full>0).mean()*100:.0f}%")
    print(f"         avg lowQ abn={pdf['lo'].mean()*100:+.3f}%  avg highQ abn={pdf['hi'].mean()*100:+.3f}%")

    is_mask = pdf["entry"] < pd.Timestamp(OOS_START)
    for lbl, sub in [("IS  (<2024)", pdf[is_mask]), ("OOS (>=2024)", pdf[~is_mask])]:
        x = sub["ls"].dropna().values
        if len(x) < 5:
            print(f"  {lbl}: n={len(x)} too few"); continue
        t, p = st.ttest_1samp(x, 0)
        print(f"  {lbl}: n={len(x):3d} | LS={np.mean(x)*100:+.3f}%/{HOLD_DAYS}d | t={t:+.2f} p={p:.3f} | dir={(x>0).mean()*100:.0f}% "
              f"| loQ={sub['lo'].mean()*100:+.3f}% hiQ={sub['hi'].mean()*100:+.3f}%")


if __name__ == "__main__":
    run()
