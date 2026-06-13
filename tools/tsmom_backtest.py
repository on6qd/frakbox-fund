"""Time-series (absolute) momentum backtest.

Tests the Moskowitz-Ooi-Pedersen (2012) signal: the SIGN of an asset's own
trailing 12-month return predicts its next-month return. This is distinct from
cross-sectional momentum (rank winners vs losers) which is already a recorded
dead end in this system.

Construction (monthly, no lookahead):
  - month-end close prices (auto-adjusted for splits/divs)
  - signal at month t = sign(return from t-12 to t)   [in-sample info only]
  - position over month t+1 = signal                  [traded the FOLLOWING month]
  - long/short portfolio = equal-weight mean of per-asset (signal_t * ret_{t+1})
  - long-only timing variant = max(signal,0) * ret_{t+1}, vs buy-and-hold

Usage:
  python3 tools/tsmom_backtest.py --lookback 12 --oos-start 2018-01-01
"""
import argparse
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tools.yfinance_utils import get_close_prices

DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "EFA", "EEM",      # equity (US large/tech/small, dev intl, EM)
    "TLT", "IEF", "LQD", "HYG",             # bonds (long/int treasury, IG, HY)
    "GLD", "DBC", "USO", "UNG", "XLE",      # commodities/energy
]


def annualized_stats(monthly_ret: pd.Series):
    r = monthly_ret.dropna()
    n = len(r)
    if n < 2:
        return dict(n=n, mean_ann=np.nan, vol_ann=np.nan, sharpe=np.nan,
                    t_stat=np.nan, hit_rate=np.nan)
    mean_m = r.mean()
    vol_m = r.std(ddof=1)
    t_stat = mean_m / (vol_m / np.sqrt(n)) if vol_m > 0 else np.nan
    return dict(
        n=n,
        mean_ann=mean_m * 12,
        vol_ann=vol_m * np.sqrt(12),
        sharpe=(mean_m / vol_m) * np.sqrt(12) if vol_m > 0 else np.nan,
        t_stat=t_stat,
        hit_rate=(r > 0).mean(),
    )


def run(universe, lookback, oos_start, start="2006-01-01", end="2026-06-13"):
    px = get_close_prices(universe, start=start, end=end)
    px = px.dropna(axis=1, how="all")
    # month-end last price
    m = px.resample("ME").last()
    ret = m.pct_change()                       # month t return
    # signal at end of month t = sign of trailing `lookback`-month return
    trail = m / m.shift(lookback) - 1.0
    signal = np.sign(trail)
    # position for month t+1 = signal at end of t  -> shift signal forward 1
    pos = signal.shift(1)                      # no lookahead
    fwd = ret                                  # ret aligns to the month being held

    ls = (pos * fwd)                           # long/short per asset
    lo = (pos.clip(lower=0) * fwd)             # long-only timing per asset

    # require valid signal; equal-weight across assets that have a position
    ls_port = ls.mean(axis=1, skipna=True)
    lo_port = lo.mean(axis=1, skipna=True)
    bh_port = fwd.mean(axis=1, skipna=True)    # buy-and-hold equal weight

    # restrict to rows where at least half the universe has a signal
    valid = pos.notna().sum(axis=1) >= max(3, len(px.columns) // 2)
    ls_port, lo_port, bh_port = ls_port[valid], lo_port[valid], bh_port[valid]

    def split(s):
        return s[s.index < oos_start], s[s.index >= oos_start]

    out = {}
    for name, s in [("LS", ls_port), ("LongOnly", lo_port), ("BuyHold", bh_port)]:
        is_s, oos_s = split(s)
        out[name] = {"IS": annualized_stats(is_s), "OOS": annualized_stats(oos_s),
                     "ALL": annualized_stats(s)}

    # per-asset long/short ALL-sample t-stats
    per_asset = {}
    for c in ls.columns:
        st = annualized_stats(ls[c][valid])
        per_asset[c] = st
    return out, per_asset, m.index.min(), m.index.max()


def fmt(d):
    return (f"n={d['n']:>4} mean={d['mean_ann']*100:6.2f}%/yr vol={d['vol_ann']*100:5.1f}% "
            f"sharpe={d['sharpe']:5.2f} t={d['t_stat']:5.2f} hit={d['hit_rate']*100:4.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=12)
    ap.add_argument("--oos-start", default="2018-01-01")
    ap.add_argument("--start", default="2006-01-01")
    ap.add_argument("--end", default="2026-06-13")
    ap.add_argument("--universe", default=",".join(DEFAULT_UNIVERSE))
    args = ap.parse_args()
    uni = [u.strip() for u in args.universe.split(",") if u.strip()]
    out, per_asset, dmin, dmax = run(uni, args.lookback, args.oos_start, args.start, args.end)
    print(f"=== TSMOM lookback={args.lookback}m  universe={len(uni)}  "
          f"data {dmin.date()}..{dmax.date()}  OOS>={args.oos_start} ===")
    for name in ["LS", "LongOnly", "BuyHold"]:
        print(f"\n[{name}]")
        for seg in ["IS", "OOS", "ALL"]:
            print(f"  {seg:4} {fmt(out[name][seg])}")
    print("\n[Per-asset LS (ALL sample)]")
    for c, st in sorted(per_asset.items(), key=lambda kv: -(kv[1]['t_stat'] if not np.isnan(kv[1]['t_stat']) else -9)):
        print(f"  {c:5} {fmt(st)}")


if __name__ == "__main__":
    main()
