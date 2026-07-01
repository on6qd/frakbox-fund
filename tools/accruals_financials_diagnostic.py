"""
accruals_financials_diagnostic.py

Frontier 290775e5: is the INVERTED accruals sign (+6.3%/z, high-accrual beats
low-accrual — opposite of Sloan 1996) a FINANCIALS-not-excluded / definitional
artifact rather than survivorship?

Survivorship-via-distress-delisting was refuted 2026-06-28 (Form-25 delisting is
flat across accruals quintiles). The leading remaining explanation: Sloan (1996)
explicitly excludes financials because accruals = (NetIncome - OperatingCashFlow)
/ Assets is not meaningful for banks/insurers/REITs (their OCF and balance-sheet
mechanics differ fundamentally). If financials contaminate the XBRL-frames
universe and cluster in the extreme accruals quintiles, they can drive the
inverted L/S sign.

This tool tests the PREMISE of that hypothesis without needing to re-fetch
returns (the return pipeline + cache were lost on a prior branch):

  1. Pull SEC XBRL frames for NetIncomeLoss, OperatingCashFlow, Assets, FY2013-19.
  2. Build accruals = (NI - OCF)/Assets per CIK-fiscal-year, within-FY quintiles.
  3. Classify each CIK as financial (SIC 6000-6999) via SEC submissions (cached).
  4. Measure whether financials are over-represented in the extreme quintiles
     (Q0/Q4 — the L/S legs) and whether their accruals values are systematically
     more extreme than non-financials.

Reuses tools/__init__.py curl_cffi shim indirectly (SEC calls use urllib, no
yfinance needed here).

Usage:
  python3 tools/accruals_financials_diagnostic.py frames      # step 1-2, cache accruals panel
  python3 tools/accruals_financials_diagnostic.py sic          # step 3, cache SIC per CIK (slow)
  python3 tools/accruals_financials_diagnostic.py analyze      # step 4, print diagnostic
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
PANEL_PATH = os.path.join(CACHE_DIR, "accruals_frames_panel.json")
SIC_PATH = os.path.join(CACHE_DIR, "sec_sic_by_cik.json")

UA = {"User-Agent": "frakbox research bart.de.lepeleer@gmail.com"}
FISCAL_YEARS = list(range(2013, 2020))  # FY2013-2019 inclusive (matches n=33046 work)

# Concept fallbacks: SEC tags vary across filers/years.
OCF_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]


def _get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def _frames(concept, cy, instant=False):
    # Flow concepts (income, cash flow) use CY{year}; balance-sheet instant
    # concepts (Assets) use CY{year}Q4I (point-in-time, ~Dec 31).
    period = f"CY{cy}Q4I" if instant else f"CY{cy}"
    url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"
    d = _get(url)
    if not d or "data" not in d:
        return {}
    # Keep one value per CIK (frames CY already dedups to the annual value).
    out = {}
    for row in d["data"]:
        out[int(row["cik"])] = row["val"]
    return out


def build_panel():
    os.makedirs(CACHE_DIR, exist_ok=True)
    panel = []  # list of dicts: cik, fy, ni, ocf, assets, accruals
    for fy in FISCAL_YEARS:
        ni = _frames("NetIncomeLoss", fy)
        assets = _frames("Assets", fy, instant=True)
        ocf = {}
        for tag in OCF_TAGS:
            f = _frames(tag, fy)
            for cik, v in f.items():
                ocf.setdefault(cik, v)  # first tag wins
        common = set(ni) & set(assets) & set(ocf)
        for cik in common:
            a = assets[cik]
            if not a or a <= 0:
                continue
            acc = (ni[cik] - ocf[cik]) / a
            panel.append({"cik": cik, "fy": fy, "ni": ni[cik],
                          "ocf": ocf[cik], "assets": a, "accruals": acc})
        print(f"FY{fy}: ni={len(ni)} ocf={len(ocf)} assets={len(assets)} "
              f"-> common={len(common)}")
    json.dump(panel, open(PANEL_PATH, "w"))
    ciks = sorted({r["cik"] for r in panel})
    print(f"panel rows={len(panel)} unique_ciks={len(ciks)} -> {PANEL_PATH}")
    return panel


def fetch_sic(limit=None, workers=8):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    panel = json.load(open(PANEL_PATH))
    ciks = sorted({r["cik"] for r in panel})
    sic = {}
    if os.path.exists(SIC_PATH):
        sic = json.load(open(SIC_PATH))
    todo = [c for c in ciks if str(c) not in sic]
    if limit:
        todo = todo[:limit]
    print(f"fetching SIC for {len(todo)} CIKs (have {len(sic)}) with {workers} workers")

    def one(cik):
        c10 = str(cik).zfill(10)
        d = _get(f"https://data.sec.gov/submissions/CIK{c10}.json")
        return str(cik), {
            "sic": (d or {}).get("sic") or "",
            "sicDescription": (d or {}).get("sicDescription") or "",
        }

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c for c in todo}
        for fut in as_completed(futs):
            k, v = fut.result()
            sic[k] = v
            done += 1
            if done % 500 == 0:
                json.dump(sic, open(SIC_PATH, "w"))
                print(f"  {done}/{len(todo)} cached", flush=True)
    json.dump(sic, open(SIC_PATH, "w"))
    print(f"SIC cached for {len(sic)} CIKs -> {SIC_PATH}")


def _quintiles(vals):
    """Return within-list quintile index 0..4 for each value (rank-based)."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    q = [0] * len(vals)
    n = len(vals)
    for rank, idx in enumerate(order):
        q[idx] = min(4, int(5 * rank / n))
    return q


def analyze():
    import statistics
    panel = json.load(open(PANEL_PATH))
    sic = json.load(open(SIC_PATH)) if os.path.exists(SIC_PATH) else {}

    def is_fin(cik):
        s = sic.get(str(cik), {}).get("sic", "")
        try:
            return 6000 <= int(s) <= 6999
        except (ValueError, TypeError):
            return False

    # Assign within-FY quintiles.
    by_fy = {}
    for r in panel:
        by_fy.setdefault(r["fy"], []).append(r)
    rows = []
    for fy, rs in by_fy.items():
        qs = _quintiles([r["accruals"] for r in rs])
        for r, q in zip(rs, qs):
            rows.append({**r, "q": q, "fin": is_fin(r["cik"])})

    have_sic = [r for r in rows if str(r["cik"]) in sic]
    fin_share = sum(r["fin"] for r in have_sic) / max(1, len(have_sic))
    print(f"rows={len(rows)} with_sic={len(have_sic)} "
          f"overall_financials_share={fin_share:.3f}")

    print("\nFinancials share by accruals quintile (Q0=lowest accruals):")
    for q in range(5):
        qr = [r for r in have_sic if r["q"] == q]
        fs = sum(r["fin"] for r in qr) / max(1, len(qr))
        print(f"  Q{q}: n={len(qr):5d}  financials_share={fs:.3f}  "
              f"(vs base {fin_share:.3f}, ratio {fs/max(1e-9,fin_share):.2f}x)")

    fin_acc = [r["accruals"] for r in have_sic if r["fin"]]
    non_acc = [r["accruals"] for r in have_sic if not r["fin"]]
    def summ(x):
        x = sorted(x)
        n = len(x)
        return (statistics.mean(x), statistics.median(x),
                x[int(0.05*n)], x[int(0.95*n)])
    fm = summ(fin_acc); nm = summ(non_acc)
    print(f"\naccruals (NI-OCF)/Assets distribution:")
    print(f"  financials    n={len(fin_acc):5d} mean={fm[0]:+.4f} med={fm[1]:+.4f} "
          f"p05={fm[2]:+.4f} p95={fm[3]:+.4f}")
    print(f"  non-financial n={len(non_acc):5d} mean={nm[0]:+.4f} med={nm[1]:+.4f} "
          f"p05={nm[2]:+.4f} p95={nm[3]:+.4f}")

    # How much of the extreme-quintile mass is financial?
    extreme = [r for r in have_sic if r["q"] in (0, 4)]
    mid = [r for r in have_sic if r["q"] in (1, 2, 3)]
    ext_fs = sum(r["fin"] for r in extreme)/max(1,len(extreme))
    mid_fs = sum(r["fin"] for r in mid)/max(1,len(mid))
    print(f"\nfinancials share: extreme quintiles(Q0,Q4)={ext_fs:.3f} "
          f"vs middle(Q1-3)={mid_fs:.3f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    if cmd == "frames":
        build_panel()
    elif cmd == "sic":
        lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
        fetch_sic(lim)
    elif cmd == "analyze":
        analyze()
    else:
        print(__doc__)
