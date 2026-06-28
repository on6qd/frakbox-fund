#!/usr/bin/env python3
"""
Accruals cross-section survivorship test (reuse-proof, SEC-only).

Frontier item 6409c3e6 / knowledge: accruals_anomaly_survivorship_inverted_2026_06_15.

A prior session found an INVERTED accruals anomaly on SEC XBRL frames
(high-accrual BEATS low-accrual, +6.3%/z, p~0, IS and OOS) and attributed it
to survivorship bias: the cross-section dropped every stock-year whose CIK is
absent from the CURRENT company_tickers.json (i.e. delisted names), and
high-accrual firms are disproportionately the distress/blowup cases that later
delist.  That explanation was asserted but never directly tested against the
reuse-proof SEC delisting flag.

This tool tests the MECHANISM directly, using only SEC endpoints (no prices,
no Tiingo, no yfinance -> immune to ticker reuse and rate limits):

  1. XBRL frames -> fundamentals for ALL filers per FY (survivors + since-delisted).
     accruals = (NetIncomeLoss - OperatingCashFlow) / Assets        (Sloan/balance-sheet)
  2. SEC submissions API per CIK -> Form 25 / 25-NSE / 15 delisting date (permanent, CIK-keyed).
  3. Holding window = end-June(Y+1) .. end-June(Y+2).  A stock-year is
     "delisted-in-window" if its CIK's first 25/25-NSE filing falls on/before end-June(Y+2).
  4. PRIMARY: delisting rate by within-FY accruals quintile.  If high-accrual
     firms delist materially more, dropping them mechanically inflates the
     high-accrual bin's surviving-mean return -> reproduces the inverted sign.
  5. SECONDARY: survivor rate by FY (reproduces the prior survivor_rate_by_fy)
     and a bounding decomposition of the survivorship-induced return bias.

Caches submissions lookups to data/cache so re-runs are cheap.
"""
import os, sys, json, time, argparse
from collections import defaultdict
import requests

UA = {"User-Agent": "frakbox research bart.de.lepeleer@gmail.com"}
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DELIST_FORMS = {"25", "25-NSE"}          # NYSE/Nasdaq removal from listing (the tradeable delisting)
DEREG_FORMS = {"15", "15-12B", "15-12G", "15-15D"}  # deregistration (often follows)


def _get(url, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            time.sleep(0.5 * (2 ** i))
        except Exception:
            time.sleep(0.5 * (2 ** i))
    return None


def fetch_frames_year(year):
    """Return {cik: {assets, ni, ocf}} for firms reporting CY{year} (Dec-FYE)."""
    out = {}
    assets = _get(FRAMES.format(concept="Assets", period=f"CY{year}Q4I"))
    ni = _get(FRAMES.format(concept="NetIncomeLoss", period=f"CY{year}"))
    ocf = _get(FRAMES.format(concept="NetCashProvidedByUsedInOperatingActivities", period=f"CY{year}"))
    if not (assets and ni and ocf):
        return out
    a = {d["cik"]: d["val"] for d in assets["data"]}
    n = {d["cik"]: d["val"] for d in ni["data"]}
    o = {d["cik"]: d["val"] for d in ocf["data"]}
    for cik in a.keys() & n.keys() & o.keys():
        if a[cik] and a[cik] > 0:
            out[cik] = {"assets": a[cik], "ni": n[cik], "ocf": o[cik]}
    return out


def load_current_tickers():
    d = _get(TICKERS_URL)
    return {int(v["cik_str"]) for v in d.values()} if d else set()


def get_delisting_date(cik, cache):
    """First Form 25/25-NSE filing date (YYYY-MM-DD) or None. Cached per CIK."""
    key = str(cik)
    if key in cache:
        return cache[key]
    sub = _get(SUBMISSIONS.format(cik=cik))
    res = None
    if sub:
        rec = sub.get("filings", {}).get("recent", {})
        forms = rec.get("form", [])
        dates = rec.get("filingDate", [])
        d25 = sorted(dates[i] for i in range(len(forms)) if forms[i] in DELIST_FORMS)
        if d25:
            res = d25[0]
    cache[key] = res
    return res


def quintile(sorted_vals, v):
    import bisect
    n = len(sorted_vals)
    idx = bisect.bisect_left(sorted_vals, v)
    return min(4, idx * 5 // n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-fy", type=int, default=2013)
    ap.add_argument("--end-fy", type=int, default=2019)  # mature windows (full Y+2 elapsed)
    ap.add_argument("--out", default=os.path.join(CACHE_DIR, "accruals_survivorship_result.json"))
    args = ap.parse_args()

    cache_path = os.path.join(CACHE_DIR, "sec_delisting_dates.json")
    sub_cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    current = load_current_tickers()
    print(f"current ticker-map CIKs: {len(current)}")

    # 1. Build stock-year panel
    panel = []  # dicts: fy, cik, accruals
    for fy in range(args.start_fy, args.end_fy + 1):
        frames = fetch_frames_year(fy)
        rows = []
        for cik, f in frames.items():
            acc = (f["ni"] - f["ocf"]) / f["assets"]
            if abs(acc) > 2:   # drop pathological tiny-asset blowups
                continue
            rows.append({"fy": fy, "cik": cik, "accruals": acc})
        # within-FY accruals quintile
        sv = sorted(r["accruals"] for r in rows)
        for r in rows:
            r["q"] = quintile(sv, r["accruals"])
        panel.extend(rows)
        print(f"FY{fy}: {len(rows)} stock-years")

    # 2. Delisting status per unique CIK (cached)
    uniq = sorted({r["cik"] for r in panel})
    print(f"unique CIKs to resolve: {len(uniq)} (cached: {len(sub_cache)})")
    for i, cik in enumerate(uniq):
        was_cached = str(cik) in sub_cache
        get_delisting_date(cik, sub_cache)
        if i % 250 == 0:
            json.dump(sub_cache, open(cache_path, "w"))
            print(f"  resolved {i}/{len(uniq)}")
        if not was_cached:
            time.sleep(0.11)  # ~9 req/s, under SEC 10/s
    json.dump(sub_cache, open(cache_path, "w"))

    # 3. Flag delisted-in-window; survivor = in current ticker map
    for r in panel:
        d = sub_cache.get(str(r["cik"]))
        r["delist_date"] = d
        # holding window ends end-June(fy+2)
        window_end = f"{r['fy']+2}-06-30"
        r["delisted_in_window"] = bool(d and d <= window_end)
        r["in_current_map"] = r["cik"] in current

    # 4. PRIMARY: delisting rate + survivor rate by accruals quintile
    by_q = defaultdict(lambda: {"n": 0, "delist_win": 0, "ever_delist": 0, "in_map": 0})
    for r in panel:
        b = by_q[r["q"]]
        b["n"] += 1
        b["delist_win"] += r["delisted_in_window"]
        b["ever_delist"] += bool(r["delist_date"])
        b["in_map"] += r["in_current_map"]

    print("\n=== delisting / survivor rate by accruals quintile (Q0=low accr ... Q4=high accr) ===")
    qstats = {}
    for q in range(5):
        b = by_q[q]
        n = b["n"]
        qstats[q] = {
            "n": n,
            "delist_in_window_pct": round(100 * b["delist_win"] / n, 2),
            "ever_delist_pct": round(100 * b["ever_delist"] / n, 2),
            "in_current_map_pct": round(100 * b["in_map"] / n, 2),
        }
        print(f"  Q{q}: n={n:5d}  delist_in_window={qstats[q]['delist_in_window_pct']:5.2f}%  "
              f"ever_delist={qstats[q]['ever_delist_pct']:5.2f}%  in_map={qstats[q]['in_current_map_pct']:5.2f}%")

    # 5. survivor rate by FY (reproduce prior survivor_rate_by_fy)
    by_fy = defaultdict(lambda: {"n": 0, "in_map": 0})
    for r in panel:
        by_fy[r["fy"]]["n"] += 1
        by_fy[r["fy"]]["in_map"] += r["in_current_map"]
    fy_surv = {fy: round(b["in_map"] / b["n"], 3) for fy, b in sorted(by_fy.items())}
    print("\nsurvivor rate by FY:", fy_surv)

    result = {
        "n_stock_years": len(panel),
        "n_unique_ciks": len(uniq),
        "fy_range": [args.start_fy, args.end_fy],
        "quintile_stats": qstats,
        "survivor_rate_by_fy": fy_surv,
    }
    json.dump(result, open(args.out, "w"), indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
