"""
MAX / lottery-demand effect backtest (Bali, Cakici & Whitelaw 2011).

Hypothesis: stocks with the highest maximum daily returns over the prior month
(lottery-like payoffs) earn LOWER subsequent returns. The tradeable expression
is long the low-MAX quintile, short the high-MAX quintile (Q1 - Q5 > 0), or a
single-leg short of the high-MAX quintile vs the equal-weight universe.

This is a properly *rebalanced* cross-sectional test (the generic
causal_tests.test_cross_section only does a single static sort over the whole
sample, which is not a valid factor backtest). Each month-end we rank the
universe by MAX measured over the trailing window, form equal-weight quintile
portfolios, hold them the following month, then repeat.

Universe note: a fixed present-day liquid universe carries survivorship bias.
For a SHORT-the-high-MAX strategy this is conservative — the worst lottery
stocks that crashed and delisted are excluded, which understates (not inflates)
the short leg's profitability. A dead-end result is therefore robust to it.

Usage:
    python3 tools/max_lottery_backtest.py --max-n 5 --quantiles 5 \
        --start 2013-12-01 --oos-start 2021-01-01
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tools.yfinance_utils import get_close_prices

# A diverse, liquid universe that traded continuously since ~2013: low-vol
# staples/utilities through high-vol growth/biotech/consumer-discretionary, so
# that MAX has real cross-sectional dispersion while every name stays tradeable.
UNIVERSE = [
    # Mega-cap tech / comm
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "ADBE", "CRM", "ORCL", "CSCO",
    "INTC", "AMD", "QCOM", "TXN", "AVGO", "MU", "NFLX", "PYPL", "IBM", "HPQ",
    # Consumer discretionary (incl. higher-vol)
    "TSLA", "HD", "LOW", "NKE", "SBUX", "MCD", "TGT", "BKNG", "MAR", "GM",
    "F", "EBAY", "ROST", "TJX", "YUM", "DHI", "LEN", "WYNN", "RCL", "CCL",
    # Consumer staples (low vol)
    "PG", "KO", "PEP", "WMT", "COST", "CL", "MDLZ", "MO", "PM", "KMB",
    "GIS", "K", "HSY", "SYY", "KR",
    # Health care (incl. biotech vol)
    "JNJ", "PFE", "MRK", "ABBV", "TMO", "ABT", "LLY", "BMY", "AMGN", "GILD",
    "BIIB", "REGN", "VRTX", "ISRG", "CVS", "UNH", "MDT", "DHR",
    # Financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "AXP", "BLK", "SCHW", "USB",
    "PNC", "COF", "MET", "PRU", "TRV",
    # Industrials / energy / materials / utilities
    "BA", "CAT", "DE", "HON", "GE", "MMM", "UPS", "FDX", "LMT", "RTX",
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "FCX", "NEM", "NUE", "DOW",
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL",
]


def compute_max_factor(rets_window: pd.DataFrame, max_n: int) -> pd.Series:
    """MAX = mean of the top `max_n` daily returns in the window, per stock."""
    out = {}
    for col in rets_window.columns:
        s = rets_window[col].dropna()
        if len(s) < 15:  # need most of a month of observations
            continue
        topn = s.nlargest(max_n)
        if len(topn) < max_n:
            continue
        out[col] = float(topn.mean())
    return pd.Series(out, name=f"MAX{max_n}")


def annualize_t(monthly: pd.Series) -> dict:
    m = monthly.dropna()
    if len(m) < 6:
        return {"n": len(m), "mean_monthly_pct": None, "t": None, "p": None,
                "annual_pct": None, "sharpe": None}
    from scipy import stats as sps
    t, p = sps.ttest_1samp(m.values, 0.0)
    mean = float(m.mean())
    sd = float(m.std(ddof=1))
    return {
        "n": int(len(m)),
        "mean_monthly_pct": round(mean * 100, 4),
        "t": round(float(t), 3),
        "p": round(float(p), 5),
        "annual_pct": round(mean * 12 * 100, 3),
        "sharpe": round(mean / sd * np.sqrt(12), 3) if sd > 0 else None,
    }


def run(start: str, end: str, max_n: int, n_quantiles: int,
        oos_start: str, lookback: int) -> dict:
    tickers = sorted(set(UNIVERSE))
    closes = get_close_prices(tickers + ["SPY"], start=start, end=end)
    closes = closes.dropna(how="all")
    # Drop names with too little history
    closes = closes.loc[:, closes.notna().sum() > 252 * 5]
    spy = closes["SPY"]
    universe_cols = [c for c in closes.columns if c != "SPY"]
    rets = closes[universe_cols].pct_change()
    spy_ret = spy.pct_change()

    # Month-end rebalance dates = last actual TRADING day in each calendar month
    s = pd.Series(rets.index, index=rets.index)
    month_ends = s.groupby([rets.index.year, rets.index.month]).last()
    month_ends = pd.DatetimeIndex(sorted(month_ends.values))
    spread_ls, short_leg_abn, long_leg_abn = {}, {}, {}
    quintile_monthly = {q: {} for q in range(n_quantiles)}

    idx = rets.index
    for me in month_ends:
        # formation window = trailing `lookback` trading days up to & incl. me
        window = rets.loc[:me].tail(lookback)
        if len(window) < int(lookback * 0.7):
            continue
        factor = compute_max_factor(window, max_n)
        if len(factor) < n_quantiles * 4:
            continue
        try:
            qlabels = pd.qcut(factor.rank(method="first"), n_quantiles, labels=False)
        except ValueError:
            continue

        # holding window = next month (me, me+1month]
        future = idx[(idx > me)]
        nxt = month_ends[month_ends > me]
        if len(nxt) == 0:
            continue
        hold_end = nxt[0]
        hold = future[future <= hold_end]
        if len(hold) < 10:
            continue

        # forward simple return per stock over the holding month
        hold_rets = (closes.loc[hold, universe_cols].iloc[-1] /
                     closes.loc[me, universe_cols] - 1.0)
        bench = float((spy.loc[hold].iloc[-1] / spy.loc[me]) - 1.0)
        univ_ew = float(hold_rets.reindex(factor.index).dropna().mean())

        q_ret = {}
        for q in range(n_quantiles):
            names = factor.index[qlabels == q]
            r = hold_rets.reindex(names).dropna()
            if len(r) == 0:
                continue
            q_ret[q] = float(r.mean())
            quintile_monthly[q][hold_end] = float(r.mean())

        if 0 in q_ret and (n_quantiles - 1) in q_ret:
            lo, hi = q_ret[0], q_ret[n_quantiles - 1]
            spread_ls[hold_end] = lo - hi            # long low-MAX, short high-MAX
            short_leg_abn[hold_end] = univ_ew - hi   # short high-MAX vs EW universe
            long_leg_abn[hold_end] = lo - univ_ew    # long low-MAX vs EW universe
            _ = bench  # market context retained; spread is market-neutral

    spread = pd.Series(spread_ls).sort_index()
    short_abn = pd.Series(short_leg_abn).sort_index()
    long_abn = pd.Series(long_leg_abn).sort_index()

    def split(s):
        return {
            "full": annualize_t(s),
            "is": annualize_t(s[s.index < oos_start]),
            "oos": annualize_t(s[s.index >= oos_start]),
        }

    q_summary = {}
    for q in range(n_quantiles):
        qs = pd.Series(quintile_monthly[q]).sort_index()
        a = annualize_t(qs)
        q_summary[f"Q{q}"] = {"annual_pct": a["annual_pct"], "n": a["n"],
                              "sharpe": a["sharpe"]}

    return {
        "config": {"max_n": max_n, "n_quantiles": n_quantiles, "lookback": lookback,
                   "oos_start": oos_start, "n_universe": len(universe_cols),
                   "start": start, "end": end},
        "quintiles": q_summary,
        "long_short_Q0_minus_Qhi": split(spread),
        "short_high_max_vs_ew": split(short_abn),
        "long_low_max_vs_ew": split(long_abn),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-12-01")
    ap.add_argument("--end", default="2026-06-13")
    ap.add_argument("--max-n", type=int, default=5)
    ap.add_argument("--quantiles", type=int, default=5)
    ap.add_argument("--oos-start", default="2021-01-01")
    ap.add_argument("--lookback", type=int, default=21)
    args = ap.parse_args()
    res = run(args.start, args.end, args.max_n, args.quantiles,
              args.oos_start, args.lookback)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
