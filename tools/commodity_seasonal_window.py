"""
Commodity seasonal *window* backtester with spot-vs-ETF roll-drag decomposition.

Generalizes the natural-gas seasonality finding (2026-06-13): a commodity seasonal
is only *tradeable* if a roll-paying ETF actually captures the spot move. Natgas
failed because its seasonal sits in the contango/storage-build phase (UNG paid the
gain back as roll cost). This tool tests any (start_mmdd -> end_mmdd) window on a
spot continuous future AND its tradeable ETF, and reports the roll drag between them.

A seasonal is tradeable only when BOTH:
  - spot window return is significant (p<0.05) with high win rate, AND
  - the ETF captures most of it (roll_drag small/neutral) and is itself significant.

Usage:
    python3 tools/commodity_seasonal_window.py --spot RB=F --etf UGA \
        --start 02-01 --end 04-30 --oos-start-year 2018
"""

import sys
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from scipy.stats import ttest_1samp

try:
    from tools.yfinance_utils import safe_download
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from tools.yfinance_utils import safe_download


def _mmdd(year, mmdd):
    return f"{year}-{mmdd}"


def window_returns(close, start_mmdd, end_mmdd):
    """Per-year buy-at-start / sell-at-end returns (in %).

    Buys at first trading day on/after start_mmdd, sells at first trading day
    on/after end_mmdd of the same calendar year. Returns list of dicts.
    """
    rows = []
    years = sorted(close.index.year.unique())
    for yr in years:
        s = close[_mmdd(yr, start_mmdd):_mmdd(yr, end_mmdd)]
        if len(s) < 5:
            continue
        # require the window to actually span ~the intended period
        entry = s.iloc[0]
        exit_ = s.iloc[-1]
        if entry <= 0 or np.isnan(entry) or np.isnan(exit_):
            continue
        rows.append({
            "year": yr,
            "entry_date": s.index[0].strftime("%Y-%m-%d"),
            "exit_date": s.index[-1].strftime("%Y-%m-%d"),
            "ret_pct": (exit_ / entry - 1) * 100,
        })
    return rows


def _summ(rows, label):
    rets = np.array([r["ret_pct"] for r in rows])
    if len(rets) < 3:
        return {"label": label, "n": len(rets), "error": "too few years"}
    t, p = ttest_1samp(rets, 0)
    return {
        "label": label,
        "n": len(rets),
        "mean_pct": round(rets.mean(), 2),
        "median_pct": round(np.median(rets), 2),
        "std_pct": round(rets.std(), 2),
        "win_rate": round((rets > 0).mean() * 100, 1),
        "t_stat": round(float(t), 3),
        "p_value": round(float(p), 4),
    }


def seasonal_window(spot, etf, start_mmdd, end_mmdd, years=20, oos_start_year=None):
    """Run the window backtest on spot + ETF and decompose roll drag.

    Returns a dict with spot/etf summaries (full, IS, OOS) and per-year roll drag.
    """
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")

    out = {"spot": spot, "etf": etf, "window": f"{start_mmdd}->{end_mmdd}"}

    spot_df = safe_download(spot, start=start, end=end)
    spot_rows = window_returns(spot_df["Close"], start_mmdd, end_mmdd) if not spot_df.empty else []
    out["spot_full"] = _summ(spot_rows, f"{spot} full")

    etf_rows = []
    if etf:
        etf_df = safe_download(etf, start=start, end=end)
        etf_rows = window_returns(etf_df["Close"], start_mmdd, end_mmdd) if not etf_df.empty else []
        out["etf_full"] = _summ(etf_rows, f"{etf} full")

    # roll drag = spot_ret - etf_ret per matched year
    etf_by_year = {r["year"]: r["ret_pct"] for r in etf_rows}
    drag = []
    for r in spot_rows:
        if r["year"] in etf_by_year:
            drag.append({"year": r["year"],
                         "spot_pct": round(r["ret_pct"], 2),
                         "etf_pct": round(etf_by_year[r["year"]], 2),
                         "drag_pct": round(r["ret_pct"] - etf_by_year[r["year"]], 2)})
    out["roll_drag_per_year"] = drag
    if drag:
        d = np.array([x["drag_pct"] for x in drag])
        out["roll_drag_mean_pct"] = round(d.mean(), 2)
        out["etf_capture_ratio"] = (round(np.mean([x["etf_pct"] for x in drag]) /
                                          np.mean([x["spot_pct"] for x in drag]), 2)
                                    if np.mean([x["spot_pct"] for x in drag]) != 0 else None)

    if oos_start_year:
        out["spot_IS"] = _summ([r for r in spot_rows if r["year"] < oos_start_year], f"{spot} IS<{oos_start_year}")
        out["spot_OOS"] = _summ([r for r in spot_rows if r["year"] >= oos_start_year], f"{spot} OOS>={oos_start_year}")
        if etf:
            out["etf_IS"] = _summ([r for r in etf_rows if r["year"] < oos_start_year], f"{etf} IS<{oos_start_year}")
            out["etf_OOS"] = _summ([r for r in etf_rows if r["year"] >= oos_start_year], f"{etf} OOS>={oos_start_year}")

    return out


def _print(out):
    print(f"=== Seasonal window {out['window']} | spot={out['spot']} etf={out['etf']} ===")
    for k in ["spot_full", "etf_full", "spot_IS", "spot_OOS", "etf_IS", "etf_OOS"]:
        if k in out:
            s = out[k]
            if "error" in s:
                print(f"  {s['label']}: {s['error']} (n={s['n']})")
            else:
                print(f"  {s['label']:<22} n={s['n']:>2} mean={s['mean_pct']:+6.2f}% "
                      f"med={s['median_pct']:+6.2f}% win={s['win_rate']:>5.1f}% "
                      f"t={s['t_stat']:>6.2f} p={s['p_value']:.4f}")
    if "roll_drag_mean_pct" in out:
        print(f"  ROLL DRAG mean (spot-etf) = {out['roll_drag_mean_pct']:+.2f}%  "
              f"ETF capture ratio = {out.get('etf_capture_ratio')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot", required=True, help="Spot/continuous future, e.g. RB=F")
    ap.add_argument("--etf", default=None, help="Tradeable ETF, e.g. UGA")
    ap.add_argument("--start", required=True, help="Window start MM-DD")
    ap.add_argument("--end", required=True, help="Window end MM-DD")
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--oos-start-year", type=int, default=None)
    a = ap.parse_args()
    out = seasonal_window(a.spot, a.etf, a.start, a.end, years=a.years, oos_start_year=a.oos_start_year)
    _print(out)
