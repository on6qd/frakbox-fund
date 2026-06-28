#!/usr/bin/env python3
"""Robustness cuts on the cached accruals-survivorship panel (no new network).

Tests the prior session's stated mechanism for the inverted accruals anomaly:
  "high-accrual firms are disproportionately the distress/blowup cases that later
   delist, so dropping them mechanically inverts the accruals->return sign."

Uses the reuse-proof Form-25 cache built by resolve_delisting_dates.py.
"""
import os, sys, json, math
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.accruals_survivorship_test import fetch_frames_year, load_current_tickers, CACHE_DIR

cache = json.load(open(os.path.join(CACHE_DIR, "sec_delisting_dates.json")))
current = load_current_tickers()

panel = []
for fy in range(2013, 2020):
    rows = []
    for cik, f in fetch_frames_year(fy).items():
        acc = (f["ni"] - f["ocf"]) / f["assets"]
        if abs(acc) > 2:
            continue
        rows.append({"fy": fy, "cik": cik, "acc": acc})
    sv = sorted(r["acc"] for r in rows)
    n = len(sv)
    import bisect
    for r in rows:
        idx = bisect.bisect_left(sv, r["acc"])
        r["dec"] = min(9, idx * 10 // n)
        r["q"] = min(4, idx * 5 // n)
        d = cache.get(str(r["cik"]))
        r["form25"] = bool(d)
        r["delist_win"] = bool(d and d <= f"{fy+2}-06-30")
        r["in_map"] = r["cik"] in current
    panel.extend(rows)

N = len(panel)
print(f"panel stock-years: {N}")

# Decile delisting-in-window + survivor rate
print("\n=== by accruals DECILE (D0=lowest/most-negative accr ... D9=highest) ===")
by = defaultdict(lambda: {"n":0,"dw":0,"f25":0,"map":0,"acc":0.0})
for r in panel:
    b = by[r["dec"]]; b["n"]+=1; b["dw"]+=r["delist_win"]; b["f25"]+=r["form25"]; b["map"]+=r["in_map"]; b["acc"]+=r["acc"]
for d in range(10):
    b=by[d]; n=b["n"]
    print(f"  D{d}: meanAcc={b['acc']/n:+.3f}  delist_in_window={100*b['dw']/n:5.2f}%  ever25={100*b['f25']/n:5.2f}%  survivor={100*b['map']/n:5.2f}%")

# 2-proportion z-test: Q4 (high acc) vs Q0 (low acc) delist-in-window
def prop_z(x1,n1,x2,n2):
    p1,p2=x1/n1,x2/n2; p=(x1+x2)/(n1+n2)
    se=math.sqrt(p*(1-p)*(1/n1+1/n2))
    z=(p1-p2)/se if se else 0
    return p1,p2,z
q={qq:{"n":0,"dw":0} for qq in range(5)}
for r in panel:
    q[r["q"]]["n"]+=1; q[r["q"]]["dw"]+=r["delist_win"]
p4,p0,z=prop_z(q[4]["dw"],q[4]["n"],q[0]["dw"],q[0]["n"])
print(f"\nQ4(high-acc) delist={100*p4:.2f}% vs Q0(low-acc) delist={100*p0:.2f}%  diff={100*(p4-p0):+.2f}pp  z={z:.2f}")

# Characterize the DROPPED (not in current map) population by quintile:
# of firms absent from the price universe, what fraction are Form-25 distress
# delistings vs other absence (M&A / going-private / rename)?
print("\n=== among DROPPED (absent from current ticker map), share that filed Form 25 ===")
drop = defaultdict(lambda: {"dropped":0,"f25":0})
for r in panel:
    if not r["in_map"]:
        drop[r["q"]]["dropped"]+=1
        drop[r["q"]]["f25"]+=r["form25"]
for qq in range(5):
    b=drop[qq]; d=b["dropped"]
    print(f"  Q{qq}: dropped={d:5d}  of-which-Form25={100*b['f25']/d:5.2f}%  (rest = M&A/going-private/rename)")

# Bounding the survivorship bias direction:
# If dropped Form-25 firms are losers (~-100%) and the panel only sees survivors,
# the bias to a quintile's measured mean return is ~ +droprate*f25share*(survmean+1).
# The KEY question is whether this bias is LARGER for high-acc (Q4) than low-acc (Q0).
print("\n=== survivorship upward-bias proxy = drop_rate * form25_share (per quintile) ===")
for qq in range(5):
    n=q[qq]["n"]; d=drop[qq]["dropped"]; f=drop[qq]["f25"]
    print(f"  Q{qq}: drop_rate={100*d/n:5.2f}%  form25_share_of_dropped={100*f/max(d,1):5.2f}%  bias_proxy={100*f/n:5.2f}%")
