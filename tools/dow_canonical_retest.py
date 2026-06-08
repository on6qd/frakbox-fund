"""Canonical retest for single-stock day-of-week scan hits.
Applies single_stock_calendar_anomaly_recency_rule_2026_04_21:
  (1) abnormal returns (subtract SPY), (2) recency subgroup, (3) benchmark parallel check.
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats
from tools.yfinance_utils import get_close_prices

DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']


def daily_ret(sym, start='2005-01-01', end='2026-06-08'):
    df = get_close_prices(sym, start=start, end=end)
    if isinstance(df, pd.DataFrame):
        s = df[sym] if sym in df.columns else df.iloc[:, 0]
    else:
        s = df
    s = pd.Series(s).dropna()
    s.index = pd.to_datetime(s.index)
    return s.pct_change().dropna() * 100  # percent


def analyze(sym, spy, label):
    r = daily_ret(sym)
    # align
    df = pd.DataFrame({'r': r, 'spy': spy}).dropna()
    df['abn'] = df['r'] - df['spy']
    df['dow'] = df.index.dayofweek
    df = df[df['dow'] < 5]
    print(f"\n===== {sym} ({label}) | n={len(df)} {df.index.min().date()}..{df.index.max().date()} =====")
    for col, name in [('r', 'RAW'), ('abn', 'ABNORMAL(vs SPY)')]:
        groups = [df[df['dow'] == d][col].values for d in range(5)]
        H, p = stats.kruskal(*groups)
        means = [g.mean() for g in groups]
        best = int(np.argmax(np.abs(means)))
        print(f"  {name}: Kruskal p={p:.4f} H={H:.2f} | means%: " +
              " ".join(f"{DOW[d]}={means[d]:+.3f}" for d in range(5)) +
              f" | extreme={DOW[best]}({means[best]:+.3f})")
    # recency split on ABNORMAL
    for yr in [2022]:
        recent = df[df.index.year >= yr]
        groups = [recent[recent['dow'] == d]['abn'].values for d in range(5)]
        if min(len(g) for g in groups) < 5:
            continue
        H, p = stats.kruskal(*groups)
        means = [g.mean() for g in groups]
        print(f"  RECENT>={yr} ABNORMAL: Kruskal p={p:.4f} | means%: " +
              " ".join(f"{DOW[d]}={means[d]:+.3f}" for d in range(5)) + f" (n={len(recent)})")


def main():
    spy = daily_ret('SPY')
    spy.name = 'spy'
    # SPY's own DOW (benchmark parallel check)
    sdf = pd.DataFrame({'spy': spy})
    sdf['dow'] = sdf.index.dayofweek
    sdf = sdf[sdf['dow'] < 5]
    groups = [sdf[sdf['dow'] == d]['spy'].values for d in range(5)]
    H, p = stats.kruskal(*groups)
    means = [g.mean() for g in groups]
    print(f"SPY benchmark DOW: Kruskal p={p:.4f} | means%: " +
          " ".join(f"{DOW[d]}={means[d]:+.3f}" for d in range(5)))
    for sym in sys.argv[1:] or ['AAPL', 'BABA']:
        analyze(sym, spy, 'canonical retest')


if __name__ == '__main__':
    main()
