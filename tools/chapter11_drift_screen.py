"""Chapter 11 post-filing drift screen via Tiingo (handles delisted/OTC 'Q' tickers).
Entry at first available close on/after filing date; hold 5d and 20d; abnormal vs SPY.
"""
import os, requests, sys
from datetime import datetime, timedelta

KEY = os.getenv("TIINGO_API_KEY")

def fetch(sym, start, end):
    r = requests.get(f"https://api.tiingo.com/tiingo/daily/{sym}/prices",
                     params={"startDate": start, "endDate": end, "token": KEY}, timeout=25)
    if r.status_code != 200:
        return None
    d = r.json()
    if not d:
        return None
    return [(row["date"][:10], row["close"]) for row in d]

# (label, list of candidate tickers original+Q, filing_date)
EVENTS = [
    ("Hertz",      ["HTZGQ","HTZ"],         "2020-05-22"),
    ("Chesapeake", ["CHKAQ","CHK"],         "2020-06-28"),
    ("Frontier",   ["FTRCQ","FTR"],         "2020-04-14"),
    ("Valaris",    ["VALPQ","VAL"],         "2020-08-19"),
    ("TuesdayMrn", ["TUEMQ","TUES"],        "2020-05-27"),
    ("WashPrime",  ["WPGGQ","WPG"],         "2021-06-13"),
    ("Revlon",     ["REVRQ","REV"],         "2022-06-15"),
    ("CoreSci",    ["CORZQ","CORZ"],        "2022-12-21"),
    ("Avaya",      ["AVYAQ","AVYA"],        "2023-02-14"),
    ("BedBath",    ["BBBYQ"],               "2023-04-23"),
    ("PartyCity",  ["PRTYQ","PRTY"],        "2023-01-17"),
    ("Yellow",     ["YELLQ","YELL"],        "2023-08-06"),
    ("WeWork",     ["WEWKQ","WE"],          "2023-11-06"),
    ("RiteAid",    ["RADCQ","RAD"],         "2023-10-15"),
    ("SmileDirect",["SDCCQ","SDC"],         "2023-09-29"),
    ("Lordstown",  ["RIDEQ","RIDE"],        "2023-06-27"),
    ("Spirit",     ["SAVEQ","SAVE"],        "2024-11-18"),
    ("Express",    ["EXPRQ","EXPR"],        "2024-04-22"),
    ("Fisker",     ["FSRNQ","FSR"],         "2024-06-17"),
    ("BigLots",    ["BIGGQ","BIG"],         "2024-09-09"),
    ("Tupperware", ["TUPBQ","TUP"],         "2024-09-17"),
    ("Enviva",     ["EVAVQ","EVA"],         "2024-03-13"),
]

def ret_at(series, entry_idx, h):
    if entry_idx + h >= len(series):
        return None
    p0 = series[entry_idx][1]; p1 = series[entry_idx + h][1]
    if p0 <= 0: return None
    return (p1 / p0 - 1) * 100

# benchmark SPY map (date->close)
spy = dict(fetch("SPY", "2020-01-01", "2025-06-01") or [])

rows = []
for label, tickers, fdate in EVENTS:
    start = (datetime.strptime(fdate, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
    end = (datetime.strptime(fdate, "%Y-%m-%d") + timedelta(days=45)).strftime("%Y-%m-%d")
    series = None; used = None
    for t in tickers:
        series = fetch(t, start, end)
        if series and len(series) >= 6:
            used = t; break
    if not series:
        rows.append((label, "NODATA", fdate, None, None, None, None)); continue
    # entry: first close on/after filing date
    eidx = next((i for i, (d, _) in enumerate(series) if d >= fdate), None)
    if eidx is None:
        rows.append((label, used, fdate, "no_entry", None, None, None)); continue
    edate = series[eidx][0]
    r5 = ret_at(series, eidx, 5); r20 = ret_at(series, eidx, 20)
    # SPY abnormal
    def spy_ret(h):
        if r5 is None and h == 5: return None
        try:
            ed = datetime.strptime(edate, "%Y-%m-%d")
            # find spy close nearest edate and edate+~(h*1.4 cal days)
            s0 = next((spy[k] for k in sorted(spy) if k >= edate), None)
            target = (ed + timedelta(days=int(h*1.45))).strftime("%Y-%m-%d")
            s1 = next((spy[k] for k in sorted(spy) if k >= target), None)
            if s0 and s1: return (s1/s0-1)*100
        except Exception:
            return None
        return None
    a5 = (r5 - spy_ret(5)) if (r5 is not None and spy_ret(5) is not None) else None
    a20 = (r20 - spy_ret(20)) if (r20 is not None and spy_ret(20) is not None) else None
    rows.append((label, used, edate, round(r5,1) if r5 is not None else None,
                 round(r20,1) if r20 is not None else None,
                 round(a5,1) if a5 is not None else None,
                 round(a20,1) if a20 is not None else None))

print(f"{'name':12} {'tick':7} {'entry':10} {'r5':>7} {'r20':>7} {'abn5':>7} {'abn20':>7}")
for r in rows:
    print(f"{r[0]:12} {str(r[1]):7} {str(r[2]):10} {str(r[3]):>7} {str(r[4]):>7} {str(r[5]):>7} {str(r[6]):>7}")

def stats(idx, lo=None, hi=None):
    vals = []
    for r in rows:
        if r[idx] is None: continue
        y = int(r[2][:4]) if r[2] and len(str(r[2]))>=4 and str(r[2])[0]=='2' else None
        if lo and (y is None or y < lo): continue
        if hi and (y is None or y > hi): continue
        vals.append(r[idx])
    if not vals: return None
    n=len(vals); mean=sum(vals)/n; neg=sum(1 for v in vals if v<0)
    return n, round(mean,2), round(neg/n*100), sorted(vals)

print("\nABN5  all:", stats(5))
print("ABN20 all:", stats(6))
print("ABN20 discovery(<=2023):", stats(6, hi=2023))
print("ABN20 OOS(2024):", stats(6, lo=2024))
