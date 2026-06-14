#!/usr/bin/env python3
"""
SEO bought-deal short: does pre-announcement run-up condition the post-deal drop?

Validated signal (knowledge: seo_bought_deal_short): short at next open after the
424B prospectus filing, hold 5 trading days. Pooled 5d median ~-2.9%, neg_rate ~63%.

Refinement hypothesis: companies that issue equity "into strength" (large positive
run-up into the deal) drop harder than those issuing while flat/down -- the offering
caps an overextended rally and signals management views the stock as fully valued.

Test: prior 20-trading-day return (ending the close before the filing) vs SPY-adjusted
5d abnormal return (enter next open after filing, hold 5 trading days). Tercile split
+ OLS. No EDGAR; cached event dates + yfinance only.
"""
import csv
import sys
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, ".")
import tools.yfinance_utils as yf

EVENTS_CSV = "data/seo_events_filtered.csv"
RUNUP_WINDOW = 20   # trading days
HOLD = 5            # trading days


def load_events():
    out = []
    with open(EVENTS_CSV) as f:
        for row in csv.DictReader(f):
            out.append((row["ticker"].strip(), row["file_date"].strip()))
    return out


def main():
    events = load_events()
    print(f"Loaded {len(events)} SEO bought-deal events from {EVENTS_CSV}")

    # SPY benchmark, downloaded once over full span
    dates = sorted(d for _, d in events)
    spy = yf.safe_download("SPY", "2019-11-01", "2026-06-13")
    spy_close = spy["Close"]
    spy_open = spy["Open"]

    rows = []
    skipped = 0
    for tk, fd in events:
        try:
            start = (pd.Timestamp(fd) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
            end = (pd.Timestamp(fd) + pd.Timedelta(days=20)).strftime("%Y-%m-%d")
            df = yf.safe_download(tk, start, end)
            if df is None or df.empty or "Open" not in df.columns:
                skipped += 1
                continue
            close = df["Close"].dropna()
            opn = df["Open"].dropna()
            idx = close.index
            fdt = pd.Timestamp(fd)
            # filing-day position = first trading day >= file_date
            pos = idx.searchsorted(fdt)
            if pos >= len(idx):
                skipped += 1
                continue
            d0 = pos  # filing trading day
            # need RUNUP_WINDOW+1 days before d0 and HOLD+1 days at/after d0+1
            if d0 - RUNUP_WINDOW - 1 < 0 or d0 + 1 + HOLD >= len(idx):
                skipped += 1
                continue

            runup = float(close.iloc[d0 - 1] / close.iloc[d0 - 1 - RUNUP_WINDOW] - 1)

            entry_date = idx[d0 + 1]
            exit_date = idx[d0 + HOLD]  # 5 trading days after filing day (entry+4 closes)
            entry_px = float(opn.iloc[d0 + 1])
            exit_px = float(close.iloc[d0 + HOLD])
            stock_ret = exit_px / entry_px - 1

            # SPY over the same calendar window (entry open -> exit close)
            try:
                sp_entry = float(spy_open.loc[entry_date])
                sp_exit = float(spy_close.loc[exit_date])
                spy_ret = sp_exit / sp_entry - 1
            except KeyError:
                spy_ret = 0.0
            abret = stock_ret - spy_ret  # long-side abnormal return

            rows.append(dict(ticker=tk, file_date=fd, runup=runup,
                             stock_5d=stock_ret, abret_5d=abret,
                             short_pnl=-abret))
        except Exception as e:
            skipped += 1
            continue

    df = pd.DataFrame(rows)
    n = len(df)
    print(f"Backtested N={n} (skipped {skipped} for missing/short price history)\n")
    if n < 30:
        print("Insufficient sample.")
        return

    # ---- Pooled sanity check (should reproduce validated short) ----
    print("=== POOLED (sanity vs validated signal) ===")
    print(f"  5d abnormal return: mean={df.abret_5d.mean()*100:.2f}%  "
          f"median={df.abret_5d.median()*100:.2f}%  "
          f"neg_rate={(df.abret_5d<0).mean()*100:.1f}%  "
          f"wilcoxon_p={stats.wilcoxon(df.abret_5d)[1]:.4f}")

    # ---- Tercile split on run-up ----
    df["tercile"] = pd.qcut(df.runup, 3, labels=["low", "mid", "high"])
    print("\n=== BY PRE-ANNOUNCEMENT RUN-UP TERCILE (20d return) ===")
    for t in ["low", "mid", "high"]:
        g = df[df.tercile == t]
        ru = g.runup
        ab = g.abret_5d
        try:
            wp = stats.wilcoxon(ab)[1]
        except Exception:
            wp = float("nan")
        print(f"  {t:>4}: n={len(g):>3}  runup[{ru.min()*100:6.1f}%,{ru.max()*100:6.1f}%] "
              f"med={ru.median()*100:5.1f}%  ||  abret5d mean={ab.mean()*100:6.2f}% "
              f"median={ab.median()*100:6.2f}% neg={ (ab<0).mean()*100:4.1f}% p={wp:.3f}")

    lo = df[df.tercile == "low"].abret_5d
    hi = df[df.tercile == "high"].abret_5d
    mw = stats.mannwhitneyu(hi, lo, alternative="less")  # high run-up MORE negative?
    print(f"\n  Mann-Whitney (high run-up abret < low run-up abret): U-p={mw.pvalue:.4f}")

    # ---- OLS: abret_5d ~ runup ----
    x = df.runup.values
    yv = df.abret_5d.values
    slope, intercept, r, p, se = stats.linregress(x, yv)
    print(f"\n=== OLS abret_5d ~ runup ===")
    print(f"  slope={slope:.4f} (p={p:.4f}), r={r:.3f}, intercept={intercept*100:.2f}%")
    print(f"  => a +10% run-up shifts 5d abret by {slope*0.10*100:.2f}pp")

    # ---- Short-side tradability of high run-up subset ----
    print("\n=== SHORT P&L of HIGH run-up subset (the proposed gate) ===")
    g = df[df.tercile == "high"]
    sp = g.short_pnl
    print(f"  n={len(g)} mean={sp.mean()*100:.2f}% median={sp.median()*100:.2f}% "
          f"win_rate={(sp>0).mean()*100:.1f}% p={stats.wilcoxon(sp)[1]:.4f}")


if __name__ == "__main__":
    main()
