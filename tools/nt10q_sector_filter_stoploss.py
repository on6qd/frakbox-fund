"""
NT 10-Q late filing short — sector filter + stop-loss refinement.

Hypothesis: The NT 10-Q discovery (71% neg @3d, Wilcoxon p=0.008) is untradeable
due to fat tails (max outlier +215.6%). Filtering out biotech/pharma AND applying
a 5% stop-loss should convert the directional signal into a tradeable strategy.

Pre-registered thresholds:
- Mean abnormal return must be <= -0.5% at 3d (after costs it should be viable)
- Direction-correct rate (negative) must be >=55% at 3d
- Both IS (T-8887e2ab) and OOS (T-dda35472) samples must pass independently
- Must be significant (Wilcoxon p<0.05) in OOS sample
"""
import json
import sys
import os
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))  # project root for db
import db
import yfinance_utils

# Bright-line sector classification
BIOTECH_HEALTHCARE_FLAGS = {
    # known biotech / pharma / small healthcare tickers from the dataset
}


_SECTOR_CACHE = {}


def get_sector(symbol):
    """Fetch yfinance sector + industry. Return (sector, industry) or (None, None)."""
    if symbol in _SECTOR_CACHE:
        return _SECTOR_CACHE[symbol]
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        sec = info.get('sector')
        ind = info.get('industry')
        _SECTOR_CACHE[symbol] = (sec, ind)
        return (sec, ind)
    except Exception as e:
        print(f'  WARN: sector fetch failed for {symbol}: {e}', file=sys.stderr)
        _SECTOR_CACHE[symbol] = (None, None)
        return (None, None)


def simulate_short_with_stop(post_prices, entry_close, horizon_days, stop_pct=5.0):
    """
    Simulate short position with close-based stop-loss.
    Entry = event-day close. If subsequent close rises >= stop_pct above entry,
    exit at that day's close (stop triggered). Otherwise exit at horizon close.

    Returns: (return_pct, stop_triggered_bool, exit_day_index)
    Return is the RAW return of the short (positive = short profit).
    """
    # post_prices[0] is typically the event-day close (the entry price)
    # We enter short at entry_close (T=0) and exit at T+horizon_days
    # post_prices should have horizon+1 entries (T, T+1, ..., T+horizon)
    if len(post_prices) < horizon_days + 1:
        return (None, None, None)

    stop_price = entry_close * (1.0 + stop_pct / 100.0)
    for i in range(1, horizon_days + 1):
        close_i = post_prices[i].get('close')
        if close_i is None:
            return (None, None, None)
        if close_i >= stop_price:
            # Stop triggered at this day's close.
            # Short return = (entry - exit) / entry * 100
            r = (entry_close - close_i) / entry_close * 100.0
            return (r, True, i)
    # No stop triggered — exit at horizon close
    exit_close = post_prices[horizon_days].get('close')
    if exit_close is None:
        return (None, None, None)
    r = (entry_close - exit_close) / entry_close * 100.0
    return (r, False, horizon_days)


def analyze_task_result(task_id, horizon=3, stop_pct=5.0, exclude_healthcare=True, min_price=0.0, min_dollar_vol=0.0):
    r = db.get_task_result(task_id)
    if r is None:
        print(f'Task {task_id} not found')
        return None
    items = r['result'].get('individual_impacts', [])
    print(f'\n=== Task {task_id} — {len(items)} events, horizon={horizon}d, stop={stop_pct}%, exclude_healthcare={exclude_healthcare}, min_price=${min_price} ===')

    # Classify each symbol
    unique_symbols = sorted(set(x['symbol'] for x in items))
    sectors = {}
    print(f'Fetching sectors for {len(unique_symbols)} symbols...')
    for s in unique_symbols:
        sectors[s] = get_sector(s)
        time.sleep(0.15)  # yfinance rate limit friendly
    # Print the classification
    healthcare = [s for s, sec in sectors.items() if sec[0] == 'Healthcare']
    print(f'Healthcare-classified: {len(healthcare)} / {len(unique_symbols)} — {healthcare}')

    # Run sims
    rows = []
    for it in items:
        symbol = it['symbol']
        sec = sectors.get(symbol, (None, None))
        is_healthcare = (sec[0] == 'Healthcare')
        if exclude_healthcare and is_healthcare:
            continue
        post = it.get('post_prices', [])
        entry = it.get('pre_event_price')  # this was close-mode
        # Actually entry_price_type may be close. Entry price is the event-day close.
        # pre_event_price is the previous close. The event day close is post_prices[0].
        if not post or len(post) < horizon + 1:
            continue
        event_close = post[0]['close']
        if event_close < min_price:
            continue
        avg_vol = it.get('avg_daily_volume') or 0
        pre_price = it.get('pre_event_price') or event_close
        dollar_vol = avg_vol * pre_price
        if dollar_vol < min_dollar_vol:
            continue
        ret_stop, triggered, exit_day = simulate_short_with_stop(post, event_close, horizon, stop_pct)
        # Benchmark (SPY)
        # No SPY post_prices here, but we have bench_Xd in the record
        bench_ret = it.get(f'bench_{horizon}d')
        # Build short abnormal: short raw return - (-bench) because short benefits when market drops
        # Actually: short abnormal = short_return + bench_return
        # short_return = (entry - exit)/entry, if market goes up +1%, short of market would lose 1%
        # abnormal_short = short_return - (-bench_ret) = short_return + bench_ret
        # Equivalently: short_abnormal = -(raw_return) - (-bench_ret) = -raw_return + bench_ret
        # Since our short return = -(raw return), short abnormal = short_return + bench_ret
        if ret_stop is None or bench_ret is None:
            continue
        short_abnormal = ret_stop + bench_ret  # if short profits and market up, we beat bench
        rows.append({
            'symbol': symbol,
            'event_date': it['event_date'],
            'sector': sec[0],
            'industry': sec[1],
            'event_close': event_close,
            'short_return': ret_stop,
            'short_abnormal': short_abnormal,
            'stop_triggered': triggered,
            'exit_day': exit_day,
            'raw_horizon': it.get(f'raw_{horizon}d'),
            'abnormal_horizon': it.get(f'abnormal_{horizon}d'),
        })

    n = len(rows)
    if n == 0:
        print('No events after filter')
        return None
    import statistics
    short_rets = [r['short_return'] for r in rows]
    short_abns = [r['short_abnormal'] for r in rows]
    stop_count = sum(1 for r in rows if r['stop_triggered'])
    pos_short = sum(1 for r in rows if r['short_return'] > 0)
    print(f'\nN after filter: {n}')
    print(f'  Short return mean={statistics.mean(short_rets):+.2f}%  median={statistics.median(short_rets):+.2f}%')
    print(f'  Short abnormal mean={statistics.mean(short_abns):+.2f}%  median={statistics.median(short_abns):+.2f}%')
    print(f'  Stop-outs: {stop_count}/{n} ({100*stop_count/n:.0f}%)')
    print(f'  Short wins: {pos_short}/{n} ({100*pos_short/n:.0f}%)')
    print(f'  Max short loss (stock pumped): {min(short_rets):+.2f}%')
    print(f'  Max short win: {max(short_rets):+.2f}%')
    # Wilcoxon on short_abnormal (H0: median=0, alternative: median>0 for profitable short)
    try:
        from scipy import stats as _s
        w = _s.wilcoxon(short_abns, alternative='greater')
        print(f'  Wilcoxon (short abnormal > 0): p={w.pvalue:.4f}')
        t = _s.ttest_1samp(short_abns, 0.0, alternative='greater')
        print(f'  t-test (short abnormal > 0): t={t.statistic:.2f}, p={t.pvalue:.4f}')
    except Exception as e:
        print(f'  stat test failed: {e}')
    return rows


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--task-id', required=True)
    p.add_argument('--horizon', type=int, default=3)
    p.add_argument('--stop-pct', type=float, default=5.0)
    p.add_argument('--no-healthcare-filter', action='store_true')
    p.add_argument('--include-all', action='store_true', help='also run without any filter/stop for baseline')
    p.add_argument('--min-price', type=float, default=0.0)
    p.add_argument('--min-dollar-vol', type=float, default=0.0)
    args = p.parse_args()
    db.init_db()
    if args.include_all:
        print('### BASELINE: no filter, no stop ###')
        analyze_task_result(args.task_id, horizon=args.horizon, stop_pct=999.0, exclude_healthcare=False, min_price=0.0)
        print('\n### STOP ONLY ###')
        analyze_task_result(args.task_id, horizon=args.horizon, stop_pct=args.stop_pct, exclude_healthcare=False, min_price=0.0)
        print('\n### FILTER + STOP ###')
        analyze_task_result(args.task_id, horizon=args.horizon, stop_pct=args.stop_pct, exclude_healthcare=not args.no_healthcare_filter, min_price=0.0)
        if args.min_price > 0 or args.min_dollar_vol > 0:
            print(f'\n### FILTER + STOP + MIN_PRICE ${args.min_price} + MIN_$VOL ${args.min_dollar_vol:,.0f} ###')
            analyze_task_result(args.task_id, horizon=args.horizon, stop_pct=args.stop_pct,
                                exclude_healthcare=not args.no_healthcare_filter, min_price=args.min_price,
                                min_dollar_vol=args.min_dollar_vol)
    else:
        analyze_task_result(args.task_id, horizon=args.horizon, stop_pct=args.stop_pct,
                            exclude_healthcare=not args.no_healthcare_filter, min_price=args.min_price,
                            min_dollar_vol=args.min_dollar_vol)
