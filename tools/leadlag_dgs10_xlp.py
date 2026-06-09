"""Tradeability test for DGS10 -> XLP lead-lag scan hit.

Granger significance tells us yield changes help predict XLP. This script asks
the only question that matters for trading: if we form a directional signal from
lagged DGS10 changes, does the resulting XLP position earn abnormal returns
(vs SPY) above 0.5% and transaction costs, consistently in IS and OOS?
"""
import sys
import numpy as np
import pandas as pd
from tools.timeseries import get_series  # unified fetcher

START = "2015-01-01"
END = "2026-06-09"
OOS_START = "2024-01-01"
import os
TARGET = os.environ.get("LL_TARGET","XLP")
HOLD = int(os.environ.get("LL_HOLD","4"))


def load():
    xlp = get_series(TARGET, START, END)
    spy = get_series("SPY", START, END)
    dgs = get_series("FRED:DGS10", START, END)
    df = pd.DataFrame({TARGET: xlp, "SPY": spy, "DGS10": dgs}).dropna()
    return df


def main():
    df = load()
    # daily yield change (in pct points) and daily returns
    df["dy"] = df["DGS10"].diff()
    df["t_ret"] = df[TARGET].pct_change()
    df["spy_ret"] = df["SPY"].pct_change()
    df["abn"] = df["t_ret"] - df["spy_ret"]  # abnormal vs SPY

    # forward HOLD-day abnormal return
    df["fwd_abn"] = (
        df["abn"].shift(-1).rolling(HOLD).sum().shift(-(HOLD - 1))
    )
    # signal: cumulative yield change over trailing 4 days (the lead window)
    df["sig"] = df["dy"].rolling(4).sum()
    d = df.dropna(subset=["sig", "fwd_abn"]).copy()

    for name, sub in [("ALL", d), ("IS", d[d.index < OOS_START]), ("OOS", d[d.index >= OOS_START])]:
        # predictive regression: fwd_abn ~ sig
        x = sub["sig"].values
        y = sub["fwd_abn"].values
        if len(x) < 30:
            print(f"{name}: n={len(x)} too small"); continue
        beta, alpha = np.polyfit(x, y, 1)
        corr = np.corrcoef(x, y)[0, 1]
        # trade rule: yield UP over 4d -> short XLP (expect underperformance)
        up = sub[sub["sig"] > 0.05]   # >5bp rise
        dn = sub[sub["sig"] < -0.05]  # >5bp fall
        # short when up: signal return = -fwd_abn ; long when dn: +fwd_abn
        short_ret = -up["fwd_abn"]
        long_ret = dn["fwd_abn"]
        combined = pd.concat([short_ret, long_ret])
        print(f"\n=== {name} (n={len(sub)}) ===")
        print(f"  predictive beta={beta:+.4f} corr={corr:+.3f}")
        print(f"  UP signal (yield+>5bp, n={len(up)}): short XLP mean abn={short_ret.mean()*100:+.3f}% winrate={(short_ret>0).mean()*100:.0f}%")
        print(f"  DN signal (yield-<5bp, n={len(dn)}): long  XLP mean abn={long_ret.mean()*100:+.3f}% winrate={(long_ret>0).mean()*100:.0f}%")
        print(f"  COMBINED rule mean abn/trade={combined.mean()*100:+.3f}% (n={len(combined)})")


if __name__ == "__main__":
    main()
