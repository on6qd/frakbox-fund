"""Compute pre-announce 5d abnormal run-up for S&P 500 additions and test
whether it predicts the post-announce fade. Priority #2/#3 from 2026-06-11 handoff.

Usage: python3 tools/sp500_preannounce_runup.py
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import get_close_prices
import pandas as pd

EVENTS = json.load(open('/tmp/sp500_events.json')) if os.path.exists('/tmp/sp500_events.json') else []
# strip the duplicate N= line issue: file is pure json
if isinstance(EVENTS, dict):
    EVENTS = EVENTS.get('events', [])


def pre_announce_abnormal(symbol, date_str, lookback=5):
    """Abnormal return from close[D-lookback] to close[D] vs SPY.
    D is the announcement date (after-hours), so run-up INTO announce includes close of D."""
    import datetime as dt
    d = pd.Timestamp(date_str)
    start = (d - pd.Timedelta(days=lookback + 20)).strftime('%Y-%m-%d')
    end = (d + pd.Timedelta(days=3)).strftime('%Y-%m-%d')
    try:
        sym_px = get_close_prices(symbol, start, end)
        spy_px = get_close_prices('SPY', start, end)
    except Exception as e:
        return None, f'fetch_err:{e}'
    if sym_px is None or spy_px is None or len(sym_px) < lookback + 1:
        return None, 'insufficient'
    # squeeze one-column DataFrame -> Series
    if hasattr(sym_px, 'columns'):
        sym_px = sym_px.iloc[:, 0]
    if hasattr(spy_px, 'columns'):
        spy_px = spy_px.iloc[:, 0]
    # align to trading days <= D
    sym_px = sym_px[sym_px.index <= d]
    spy_px = spy_px[spy_px.index <= d]
    if len(sym_px) < lookback + 1 or len(spy_px) < lookback + 1:
        return None, 'insufficient_after_align'
    s_now = sym_px.iloc[-1]
    s_then = sym_px.iloc[-(lookback + 1)]
    b_now = spy_px.iloc[-1]
    b_then = spy_px.iloc[-(lookback + 1)]
    sym_ret = (s_now / s_then - 1) * 100
    spy_ret = (b_now / b_then - 1) * 100
    return round(float(sym_ret - spy_ret), 2), 'ok'


def main():
    rows = []
    for e in EVENTS:
        sym, date = e['symbol'], e['date']
        post5 = e.get('abnormal_5d')
        post1 = e.get('abnormal_1d')
        pre5, status = pre_announce_abnormal(sym, date)
        rows.append({'symbol': sym, 'date': date, 'pre5': pre5,
                     'post1': post1, 'post5': post5, 'status': status})
        print(f"{sym:6} {date} pre5={pre5}  post1={post1}  post5={post5}  [{status}]")

    df = pd.DataFrame(rows)
    valid = df.dropna(subset=['pre5', 'post5'])
    print(f"\n=== N valid = {len(valid)} / {len(df)} ===")
    if len(valid) < 5:
        return
    r = valid['pre5'].corr(valid['post5'])
    print(f"Pearson r(pre5, post5) = {r:.3f}")
    rs = valid['pre5'].corr(valid['post5'], method='spearman')
    print(f"Spearman r(pre5, post5) = {rs:.3f}")

    # high vs low runup
    hi = valid[valid['pre5'] > 15]
    lo = valid[valid['pre5'] <= 15]
    print(f"\nHIGH pre5>15% (n={len(hi)}): mean post5 = {hi['post5'].mean():.2f}  "
          f"[{', '.join(hi['symbol'])}]")
    print(f"LOW  pre5<=15% (n={len(lo)}): mean post5 = {lo['post5'].mean():.2f}")

    # alt threshold 10
    hi10 = valid[valid['pre5'] > 10]
    lo10 = valid[valid['pre5'] <= 10]
    print(f"\nHIGH pre5>10% (n={len(hi10)}): mean post5 = {hi10['post5'].mean():.2f}")
    print(f"LOW  pre5<=10% (n={len(lo10)}): mean post5 = {lo10['post5'].mean():.2f}")

    # temporal split
    valid = valid.copy()
    valid['yr'] = valid['date'].str[:4].astype(int)
    early = valid[valid['yr'] < 2023]
    recent = valid[valid['yr'] >= 2023]
    print(f"\n--- TEMPORAL ---")
    print(f"PRE-2023 (n={len(early)}): mean pre5={early['pre5'].mean():.2f} mean post5={early['post5'].mean():.2f}")
    print(f"2023+    (n={len(recent)}): mean pre5={recent['pre5'].mean():.2f} mean post5={recent['post5'].mean():.2f}")

    df.to_json('/tmp/sp500_runup_results.json', orient='records')
    print("\nsaved /tmp/sp500_runup_results.json")


if __name__ == '__main__':
    main()
