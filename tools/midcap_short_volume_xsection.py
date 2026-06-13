"""
Cross-sectional short-volume-ratio anomaly in mid-caps (Diether-Lee-Werner 2009).

Prior work (journal 2026-03-22/23) found the FINRA daily short-volume SPIKE
signal is a DEAD END for LARGE-CAPS (own-stock z-score, 1-day horizon, p>0.3):
MM/ETF-arb hedging swamps informed short selling in liquid large-caps.

This tool tests the UNTESTED, literature-motivated angle: a CROSS-SECTIONAL sort
on the daily short-volume ratio (SVR = ShortVolume/TotalVolume) restricted to
MID-CAPS (S&P 400), where the literature says short-volume predictability is
concentrated (smaller, harder-to-arbitrage names, less MM noise as a fraction).

Hypothesis: stocks in the HIGH short-volume quintile (Q5) underperform the LOW
quintile (Q1) over the next 5-10 trading days -> long-short Q1-Q5 > 0.

Design:
  - Universe: S&P 400 mid-cap constituents.
  - Non-overlapping formation dates every `step` trading days.
  - On each date, rank universe by SVR into quintiles. Forward abnormal return
    (stock - SPY), close[t] -> close[t+h]. Q1-Q5 long-short per period.
  - IS / OOS split. t-test + direction% across periods.
  - Variants: raw SVR cross-section, and detrended SVR (minus own 20d mean).

Data: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt (free).
"""
import argparse, json, os, sys, time
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scipy import stats

CACHE = '/tmp/finra_cache'
os.makedirs(CACHE, exist_ok=True)


def fetch_finra_day(date_str):
    f = os.path.join(CACHE, f'CNMS_{date_str}.parquet')
    if os.path.exists(f):
        try:
            return pd.read_parquet(f)
        except Exception:
            pass
    url = f'https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date_str}.txt'
    try:
        r = requests.get(url, headers={'User-Agent': 'research bart.de.lepeleer@gmail.com'}, timeout=20)
        if r.status_code != 200:
            return None
        rows = []
        for line in r.text.strip().split('\n')[1:]:
            p = line.strip().split('|')
            if len(p) < 5:
                continue
            try:
                sv = float(p[2]); tv = float(p[4])
                rows.append({'symbol': p[1], 'short_vol': sv, 'total_vol': tv})
            except (ValueError, IndexError):
                continue
        df = pd.DataFrame(rows)
        if not df.empty:
            try:
                df.to_parquet(f, index=False)
            except Exception:
                pass
        time.sleep(0.05)
        return df
    except Exception:
        return None


def build_svr_matrix(symbols, trading_days):
    """Return DataFrame indexed by date (Timestamp), columns=symbols, values=SVR."""
    symset = set(symbols)
    recs = {}
    for i, d in enumerate(trading_days):
        if i % 50 == 0:
            print(f'  finra {i}/{len(trading_days)}', file=sys.stderr)
        df = fetch_finra_day(d)
        if df is None or df.empty:
            continue
        df = df[(df['symbol'].isin(symset)) & (df['total_vol'] > 0)]
        if df.empty:
            continue
        svr = (df['short_vol'] / df['total_vol']).values
        recs[pd.Timestamp(d)] = dict(zip(df['symbol'].values, svr))
    mat = pd.DataFrame.from_dict(recs, orient='index').sort_index()
    return mat


def analyze(svr_mat, closes, spy, step, horizon, is_end, detrend=False):
    """Cross-sectional quintile long-short over non-overlapping periods."""
    if detrend:
        svr_mat = svr_mat - svr_mat.rolling(20, min_periods=10).mean()
    px_dates = closes.index
    periods = []
    svr_dates = [d for d in svr_mat.index if d in set(px_dates.normalize()) or d in px_dates]
    # align to price calendar
    common = sorted(set(svr_mat.index) & set(px_dates))
    for k in range(0, len(common) - 1, step):
        fdate = common[k]
        future = px_dates[px_dates > fdate]
        if len(future) < horizon:
            continue
        exitd = future[horizon - 1]
        row = svr_mat.loc[fdate].dropna()
        if len(row) < 25:
            continue
        # forward abnormal returns for ranked names
        valid = []
        for sym in row.index:
            try:
                p0 = closes.at[fdate, sym]; p1 = closes.at[exitd, sym]
                if not (np.isfinite(p0) and np.isfinite(p1)) or p0 <= 0:
                    continue
                r = (p1 / p0 - 1) * 100
                valid.append((sym, row[sym], r))
            except Exception:
                continue
        if len(valid) < 25:
            continue
        sp0 = spy.at[fdate]; sp1 = spy.at[exitd]
        spy_ret = (sp1 / sp0 - 1) * 100
        vdf = pd.DataFrame(valid, columns=['sym', 'svr', 'ret'])
        vdf['abn'] = vdf['ret'] - spy_ret
        vdf['q'] = pd.qcut(vdf['svr'].rank(method='first'), 5, labels=[1, 2, 3, 4, 5])
        qmeans = vdf.groupby('q')['abn'].mean()
        if 1 not in qmeans.index or 5 not in qmeans.index:
            continue
        periods.append({
            'date': fdate.strftime('%Y-%m-%d'),
            'is': fdate < pd.Timestamp(is_end),
            'q1': qmeans[1], 'q5': qmeans[5],
            'ls': qmeans[1] - qmeans[5],  # long Q1 (low SV) - short Q5 (high SV)
            'qall': [qmeans.get(j, np.nan) for j in [1, 2, 3, 4, 5]],
            'n': len(vdf),
        })
    return periods


def summarize(periods, label):
    if not periods:
        return {'label': label, 'n_periods': 0}
    ls = np.array([p['ls'] for p in periods])
    t, pval = stats.ttest_1samp(ls, 0)
    qstack = np.array([p['qall'] for p in periods], dtype=float)
    qmean = np.nanmean(qstack, axis=0)
    return {
        'label': label,
        'n_periods': len(periods),
        'ls_mean_pct': round(float(ls.mean()), 4),
        'ls_t': round(float(t), 3),
        'ls_p': round(float(pval), 4),
        'ls_direction_pct': round(float((ls > 0).mean() * 100), 1),
        'quintile_means': [round(float(x), 4) for x in qmean],  # Q1..Q5
        'q1_mean': round(float(qmean[0]), 4),
        'q5_mean': round(float(qmean[4]), 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2023-01-01')
    ap.add_argument('--is-end', default='2024-01-01')
    ap.add_argument('--end', default='2024-10-01')
    ap.add_argument('--horizon', type=int, default=5)
    ap.add_argument('--step', type=int, default=5)
    ap.add_argument('--detrend', action='store_true')
    ap.add_argument('--universe', default='/tmp/sp400.json')
    args = ap.parse_args()

    syms = json.load(open(args.universe))
    print(f'universe n={len(syms)}', file=sys.stderr)

    # price calendar from SPY
    spy_raw = yf.download('SPY', start=args.start, end=args.end, progress=False, auto_adjust=True)
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    spy = spy_raw['Close']
    trading_days = [d.strftime('%Y%m%d') for d in spy.index]

    print('building SVR matrix...', file=sys.stderr)
    svr_mat = build_svr_matrix(syms, trading_days)
    print(f'SVR matrix: {svr_mat.shape[0]} days x {svr_mat.shape[1]} syms', file=sys.stderr)

    print('downloading prices...', file=sys.stderr)
    px = yf.download(syms, start=args.start, end=args.end, progress=False, auto_adjust=True)['Close']
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)

    out = {'config': vars(args), 'svr_days': int(svr_mat.shape[0]), 'svr_syms': int(svr_mat.shape[1])}
    for detrend in ([False, True] if not args.detrend else [True]):
        periods = analyze(svr_mat, px, spy, args.step, args.horizon, args.is_end, detrend=detrend)
        tag = 'detrended' if detrend else 'raw'
        is_p = [p for p in periods if p['is']]
        oos_p = [p for p in periods if not p['is']]
        out[tag] = {
            'IS': summarize(is_p, f'{tag}_IS'),
            'OOS': summarize(oos_p, f'{tag}_OOS'),
            'ALL': summarize(periods, f'{tag}_ALL'),
        }
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
