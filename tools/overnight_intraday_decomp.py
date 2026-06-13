"""
overnight_intraday_decomp.py — Decompose daily total returns into the overnight
(close->open) and intraday (open->close) components, and evaluate the
overnight-only trading strategy net of transaction costs.

Background: A large literature (Cooper-Cliff-Gulen 2008; Lou-Polk-Skouras 2019;
Hendershott-Livdan-Rosch 2020) documents that US equities earn the bulk of their
total return overnight, while the intraday session is flat-to-negative. The effect
is strongest in small-caps. This tool quantifies the decomposition and runs a
net-of-cost tradeability check (an overnight-only strategy needs ~252 round-trips
per year, so turnover cost is the binding constraint).

Investigated 2026-06-13. Verdict recorded in knowledge base as
`overnight_intraday_return_decomposition_2026_06_13` (NO-GO for live deployment):
real and robust cross-sectionally, but a return-TIMING artifact for large-caps
(buy&hold dominates net of costs) and regime-dependent for small-caps (the IWM
overnight edge reversed in 2025-YTD2026).

Usage:
    python3 tools/overnight_intraday_decomp.py --tickers SPY,QQQ,IWM --start 2010-01-01
    python3 tools/overnight_intraday_decomp.py --tickers IWM --costs 0,1,2,3 --oos-start 2020-01-01
    python3 tools/overnight_intraday_decomp.py --tickers IWM --yearly
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

try:
    from tools.yfinance_utils import safe_download
except ModuleNotFoundError:  # allow running from inside tools/
    from yfinance_utils import safe_download


def decompose(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Return a DataFrame with overnight (on), intraday (id) and total (tot) daily returns.

    Uses auto_adjust=True so dividends/splits are handled consistently; on ex-div
    days the dividend is folded into the overnight component as a total-return effect
    rather than appearing as a spurious gap.
    """
    df = safe_download(ticker, start=start, end=end, auto_adjust=True)[["Open", "Close"]].dropna()
    on = df["Open"] / df["Close"].shift(1) - 1.0
    intraday = df["Close"] / df["Open"] - 1.0
    tot = df["Close"] / df["Close"].shift(1) - 1.0
    return pd.DataFrame({"on": on, "id": intraday, "tot": tot}).dropna()


def perf(s: pd.Series) -> dict:
    """Annualized return, vol, Sharpe (rf=0), t-stat of mean, max drawdown."""
    n = len(s)
    if n == 0:
        return dict(n=0, ann_ret=np.nan, ann_vol=np.nan, sharpe=np.nan, t=np.nan, maxdd=np.nan)
    mu, sd = s.mean(), s.std(ddof=1)
    ann_ret = (1 + s).prod() ** (252 / n) - 1
    ann_vol = sd * np.sqrt(252)
    sharpe = (mu / sd) * np.sqrt(252) if sd > 0 else np.nan
    t = mu / (sd / np.sqrt(n)) if sd > 0 else np.nan
    cum = (1 + s).cumprod()
    maxdd = (cum / cum.cummax() - 1).min()
    return dict(n=n, ann_ret=ann_ret, ann_vol=ann_vol, sharpe=sharpe, t=t, maxdd=maxdd)


def report(ticker: str, out: pd.DataFrame, costs_bps: list[float], oos_start: str) -> None:
    print(f"\n===== {ticker}  (n={len(out)}, {out.index[0].date()}..{out.index[-1].date()}) =====")
    windows = [("FULL", out.index == out.index)]
    if oos_start:
        windows += [("IS pre-" + oos_start, out.index < oos_start),
                    ("OOS " + oos_start + "+", out.index >= oos_start)]
    for label, mask in windows:
        sub = out[mask]
        if len(sub) == 0:
            continue
        bh = perf(sub["tot"])
        print(f"  [{label}] buy&hold: ann={bh['ann_ret']*100:6.2f}%  vol={bh['ann_vol']*100:4.1f}%  "
              f"Sharpe={bh['sharpe']:.2f}  maxDD={bh['maxdd']*100:6.1f}%")
        for comp, name in [("on", "overnight"), ("id", "intraday ")]:
            st = perf(sub[comp])
            print(f"      {name}: ann={st['ann_ret']*100:6.2f}%  vol={st['ann_vol']*100:4.1f}%  "
                  f"Sharpe={st['sharpe']:.2f}  t={st['t']:5.2f}  maxDD={st['maxdd']*100:6.1f}%")
        for c in costs_bps:
            net = sub["on"] - c / 1e4  # one round-trip per trading day
            st = perf(net)
            print(f"        overnight-only @ {c:.0f}bps RT: ann={st['ann_ret']*100:6.2f}%  "
                  f"Sharpe={st['sharpe']:.2f}  maxDD={st['maxdd']*100:6.1f}%")


def yearly(ticker: str, out: pd.DataFrame) -> None:
    print(f"\n=== {ticker} yearly (gross annualized) ===")
    for yr, g in out.groupby(out.index.year):
        on_a = (1 + g["on"]).prod() ** (252 / len(g)) - 1
        id_a = (1 + g["id"]).prod() ** (252 / len(g)) - 1
        print(f"  {yr}: overnight={on_a*100:7.2f}%  intraday={id_a*100:7.2f}%  "
              f"edge={ (on_a - id_a) * 100:7.2f}%")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tickers", default="SPY,QQQ,IWM,DIA", help="comma-separated tickers")
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default=None, help="default: today")
    p.add_argument("--costs", default="0,1,2", help="comma-separated round-trip costs in bps")
    p.add_argument("--oos-start", default="2020-01-01", help="IS/OOS split date ('' to disable)")
    p.add_argument("--yearly", action="store_true", help="also print per-year overnight/intraday")
    args = p.parse_args(argv)

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    costs = [float(x) for x in args.costs.split(",") if x.strip() != ""]
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    for tk in tickers:
        try:
            out = decompose(tk, args.start, end)
        except Exception as e:  # noqa: BLE001
            print(f"{tk}: ERROR {e}", file=sys.stderr)
            continue
        report(tk, out, costs, args.oos_start)
        if args.yearly:
            yearly(tk, out)


if __name__ == "__main__":
    main()
