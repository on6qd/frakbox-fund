#!/usr/bin/env python3
"""Parallel SEC Form 25/25-NSE delisting-date resolver (cache builder).

Reads the accruals panel CIK universe, fetches each CIK's submissions once via a
thread pool (~9 req/s, under SEC's 10/s cap), and writes data/cache/sec_delisting_dates.json
mapping str(cik) -> first 25/25-NSE filing date (YYYY-MM-DD) or null.
Reuse-proof: keyed by permanent CIK, immune to ticker reuse. Resumable via cache.
"""
import os, sys, json, time, threading
from concurrent.futures import ThreadPoolExecutor
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.accruals_survivorship_test import fetch_frames_year, CACHE_DIR, DELIST_FORMS, UA

SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
cache_path = os.path.join(CACHE_DIR, "sec_delisting_dates.json")
cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
lock = threading.Lock()

# Build universe
uniq = set()
for fy in range(2013, 2020):
    f = fetch_frames_year(fy)
    uniq |= {c for c, d in f.items() if abs((d["ni"] - d["ocf"]) / d["assets"]) <= 2}
todo = sorted(c for c in uniq if str(c) not in cache)
print(f"universe {len(uniq)} | cached {len(cache)} | to fetch {len(todo)}")

_sess = threading.local()
def sess():
    if not hasattr(_sess, "s"):
        _sess.s = requests.Session(); _sess.s.headers.update(UA)
    return _sess.s

done = [0]
def fetch(cik):
    res = None
    for i in range(3):
        try:
            r = sess().get(SUB.format(cik=cik), timeout=30)
            if r.status_code == 200:
                rec = r.json().get("filings", {}).get("recent", {})
                forms, dates = rec.get("form", []), rec.get("filingDate", [])
                d25 = sorted(dates[j] for j in range(len(forms)) if forms[j] in DELIST_FORMS)
                res = d25[0] if d25 else None
                break
            elif r.status_code == 404:
                break
            time.sleep(0.3 * (2 ** i))
        except Exception:
            time.sleep(0.3 * (2 ** i))
    with lock:
        cache[str(cik)] = res
        done[0] += 1
        if done[0] % 500 == 0:
            json.dump(cache, open(cache_path, "w"))
            print(f"  {done[0]}/{len(todo)}")
    time.sleep(0.05)

with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(fetch, todo))
json.dump(cache, open(cache_path, "w"))
print(f"done. cache size {len(cache)}")
