"""Diagnose the worst-loss outlier in NT 10-Q IS sample to verify stop-loss behavior."""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
import db

db.init_db()

def sim(post, entry, horizon, stop_pct):
    stop = entry * (1 + stop_pct/100)
    for i in range(1, horizon+1):
        c = post[i]['close']
        if c >= stop:
            return ((entry-c)/entry*100, True, i, c)
    c = post[horizon]['close']
    return ((entry-c)/entry*100, False, horizon, c)

for task_id in ['T-8887e2ab', 'T-dda35472']:
    print(f'\n=== {task_id} outlier ranking (stop=5%, 3d horizon) ===')
    r = db.get_task_result(task_id)
    items = r['result']['individual_impacts']
    rows = []
    for it in items:
        post = it.get('post_prices', [])
        if len(post) < 4:
            continue
        entry = post[0]['close']
        ret, triggered, exit_day, exit_close = sim(post, entry, 3, 5.0)
        rows.append({'symbol': it['symbol'], 'date': it['event_date'], 'entry': entry,
                     'exit_close': exit_close, 'ret': ret, 'triggered': triggered, 'exit_day': exit_day,
                     'post': [(p['date'], p['close']) for p in post[:5]]})
    rows.sort(key=lambda x: x['ret'])
    print('Worst 5 losses (negative = short lost):')
    for r in rows[:5]:
        print(f'  {r["symbol"]:6s} {r["date"]}  entry={r["entry"]:.2f}  exit_d{r["exit_day"]}={r["exit_close"]:.2f}  ret={r["ret"]:+.2f}%  stop={r["triggered"]}')
        print(f'        prices: {r["post"]}')
    print('Best 5 wins (positive = short won):')
    for r in rows[-5:][::-1]:
        print(f'  {r["symbol"]:6s} {r["date"]}  entry={r["entry"]:.2f}  exit_d{r["exit_day"]}={r["exit_close"]:.2f}  ret={r["ret"]:+.2f}%  stop={r["triggered"]}')
