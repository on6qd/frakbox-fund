#!/usr/bin/env python3
"""
accruals_returns_test.py — DEFINITIVE test of the *inverted* accruals cross-section.

Background
----------
The classic Sloan (1996) accruals anomaly: firms with HIGH balance-sheet accruals
earn LOW future returns (short high-accrual, long low-accrual). Prior work in this
system on the SEC XBRL-frames universe found the OPPOSITE sign — high-accrual firms
BEAT low-accrual firms by ~+6.3% per unit accruals-z (an *inverted* spread). The
frontier hypothesis: that inversion is an artifact of financial firms (SIC 6000-6999)
being 2-4x over-concentrated in the high-accrual leg. Prediction: excluding financials
(and applying a price>$5 screen) collapses the inverted spread toward 0 or flips it to
the Sloan-negative sign.

Method
------
Accruals (cash-flow definition, Sloan/Hribar-Collins):
    ACC_t = (NetIncome_t - CashFlowFromOps_t) / avg(Assets_t, Assets_{t-1})
Sort firms into quintiles on ACC each fiscal year FY2013..FY2019. Form equal-weighted
portfolios at end-June of Y+1 (ensures the 10-K is public) and hold to end-June of Y+2.
Report the High-minus-Low (Q5-Q1) spread. Sloan predicts NEGATIVE; the inverted result
was POSITIVE.

Data
----
  * SEC XBRL frames API (one call = all filers for a concept/period) — cached to disk.
  * CIK->ticker from data/cache/sec_company_tickers.json.
  * Prices from yfinance (needs tools/__init__.py curl_cffi safari shim).
  * SIC per CIK from SEC submissions API — cached to disk.

Run:  python3 tools/accruals_returns_test.py --build       # fetch + cache everything
      python3 tools/accruals_returns_test.py --analyze     # print spreads
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from collections import defaultdict

import numpy as np
import pandas as pd

import tools  # noqa: F401  -- installs the curl_cffi safari shim
from tools.yfinance_utils import safe_download

UA = {"User-Agent": "frakbox-research bart.de.lepeleer@gmail.com"}
CACHE = "data/cache"
FRAMES_DIR = os.path.join(CACHE, "accruals_frames")
PANEL_JSON = os.path.join(CACHE, "accruals_frames_panel.json")
SIC_JSON = os.path.join(CACHE, "sec_sic_by_cik.json")
PRICES_JSON = os.path.join(CACHE, "accruals_endjune_prices.json")
TICKERS = os.path.join(CACHE, "sec_company_tickers.json")

FISCAL_YEARS = list(range(2013, 2020))  # FY2013..FY2019
CONCEPTS = {
    "ni": "NetIncomeLoss",
    "cfo": "NetCashProvidedByUsedInOperatingActivities",
    "assets": "Assets",
}


# --------------------------------------------------------------------------- #
# Fetch helpers
# --------------------------------------------------------------------------- #
def _get_json(url: str, timeout: int = 45):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_frame(concept: str, period: str) -> dict:
    """Return {cik: value} for a us-gaap concept/period frame, cached to disk."""
    os.makedirs(FRAMES_DIR, exist_ok=True)
    fn = os.path.join(FRAMES_DIR, f"{concept}_{period}.json")
    if os.path.exists(fn):
        return json.load(open(fn))
    url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"
    d = _get_json(url)
    out = {}
    for row in d.get("data", []):
        # frames can carry multiple facts per cik across submissions; keep last (latest end)
        out[str(row["cik"])] = {"val": row["val"], "end": row["end"], "name": row.get("entityName")}
    json.dump(out, open(fn, "w"))
    time.sleep(0.15)  # be polite to SEC
    return out


def build_panel() -> pd.DataFrame:
    """Build firm-year accruals panel across FISCAL_YEARS."""
    # duration concepts use CY{Y}; instantaneous (Assets) uses CY{Y}Q4I
    ni = {y: fetch_frame(CONCEPTS["ni"], f"CY{y}") for y in FISCAL_YEARS}
    cfo = {y: fetch_frame(CONCEPTS["cfo"], f"CY{y}") for y in FISCAL_YEARS}
    # need Assets_t and Assets_{t-1}
    assets = {y: fetch_frame(CONCEPTS["assets"], f"CY{y}Q4I")
              for y in range(FISCAL_YEARS[0] - 1, FISCAL_YEARS[-1] + 1)}

    rows = []
    for y in FISCAL_YEARS:
        for cik, nrec in ni[y].items():
            if cik not in cfo[y] or cik not in assets[y] or cik not in assets.get(y - 1, {}):
                continue
            a_t = assets[y][cik]["val"]
            a_tm1 = assets[y - 1][cik]["val"]
            avg_a = (a_t + a_tm1) / 2.0
            if avg_a <= 0:
                continue
            acc = (nrec["val"] - cfo[y][cik]["val"]) / avg_a
            rows.append({
                "cik": cik, "fy": y, "accruals": acc,
                "assets": a_t, "name": nrec.get("name"),
            })
    df = pd.DataFrame(rows)
    # winsorize accruals at 1/99 pct per year to tame outliers (transform keeps all cols)
    lo = df.groupby("fy")["accruals"].transform(lambda s: s.quantile(0.01))
    hi = df.groupby("fy")["accruals"].transform(lambda s: s.quantile(0.99))
    df["accruals"] = df["accruals"].clip(lower=lo, upper=hi)
    df.to_json(PANEL_JSON, orient="records")
    return df


# --------------------------------------------------------------------------- #
# Tickers, prices, SIC
# --------------------------------------------------------------------------- #
def cik_to_ticker() -> dict:
    """Map zero-padded/int cik -> ticker using the surviving cache (ticker->cik)."""
    t2c = json.load(open(TICKERS))
    c2t = {}
    for ticker, cik in t2c.items():
        c2t[str(int(cik))] = ticker  # strip leading zeros
    return c2t


def endjune_prices(tickers: list[str], years: list[int]) -> dict:
    """
    Download adjusted end-June prices for each ticker across `years`.
    Returns {ticker: {year: price}}. Cached to disk.
    """
    if os.path.exists(PRICES_JSON):
        cache = json.load(open(PRICES_JSON))
    else:
        cache = {}
    todo = [t for t in tickers if t not in cache]
    start = f"{min(years)-0}-06-01"
    end = f"{max(years)}-07-15"
    B = 120
    for i in range(0, len(todo), B):
        batch = todo[i:i + B]
        try:
            df = safe_download(batch, start=start, end=end)
        except Exception as e:
            print(f"  batch {i} failed: {str(e)[:80]}")
            for t in batch:
                cache[t] = {}
            continue
        for t in batch:
            col = f"Close_{t}" if len(batch) > 1 else "Close"
            try:
                s = df[col].dropna() if col in df.columns else None
            except Exception:
                s = None
            per_year = {}
            if s is not None and len(s):
                s.index = pd.to_datetime(s.index)
                for y in years:
                    window = s[(s.index >= f"{y}-06-15") & (s.index <= f"{y}-07-10")]
                    if len(window):
                        per_year[str(y)] = float(window.iloc[0])
            cache[t] = per_year
        json.dump(cache, open(PRICES_JSON, "w"))
        print(f"  priced {min(i+B,len(todo))}/{len(todo)} tickers")
    return cache


def fetch_sic(ciks: list[str]) -> dict:
    """SIC per cik from SEC submissions API, cached."""
    if os.path.exists(SIC_JSON):
        sic = json.load(open(SIC_JSON))
    else:
        sic = {}
    todo = [c for c in ciks if c not in sic]
    for n, cik in enumerate(todo):
        url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
        try:
            d = _get_json(url, timeout=20)
            sic[cik] = {"sic": d.get("sic"), "desc": d.get("sicDescription")}
        except Exception:
            sic[cik] = {"sic": None, "desc": None}
        if n % 100 == 0:
            json.dump(sic, open(SIC_JSON, "w"))
            print(f"  sic {n}/{len(todo)}")
        time.sleep(0.11)  # ~9 req/s < SEC 10/s limit
    json.dump(sic, open(SIC_JSON, "w"))
    return sic


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def is_financial(sic) -> bool:
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return False
    return 6000 <= s <= 6999


def analyze():
    panel = pd.read_json(PANEL_JSON)
    panel["cik"] = panel["cik"].astype(str)
    c2t = cik_to_ticker()
    panel["ticker"] = panel["cik"].map(c2t)
    panel = panel.dropna(subset=["ticker"])

    prices = json.load(open(PRICES_JSON))
    sic = json.load(open(SIC_JSON))

    fwd, formp = [], []
    for t, fy in zip(panel["ticker"].tolist(), panel["fy"].tolist()):
        p = prices.get(t, {})
        y1, y2 = str(int(fy) + 1), str(int(fy) + 2)
        p1, p2 = p.get(y1), p.get(y2)
        if p1 and p2 and p1 > 0:
            fwd.append(p2 / p1 - 1.0)
            formp.append(p1)
        else:
            fwd.append(np.nan)
            formp.append(np.nan)
    panel["fwd_ret"] = fwd
    panel["form_price"] = formp
    panel["sic"] = panel["cik"].map(lambda c: (sic.get(c) or {}).get("sic"))
    panel["is_fin"] = panel["sic"].map(is_financial)
    panel = panel.dropna(subset=["fwd_ret"])

    def spread_report(df, label):
        print(f"\n=== {label} ===")
        print(f"  firm-years with returns: {len(df)}  unique firms: {df['ticker'].nunique()}")
        # quintiles per year, then pool
        recs = []
        legweights = defaultdict(list)
        for fy, g in df.groupby("fy"):
            if len(g) < 25:
                continue
            g = g.copy()
            g["q"] = pd.qcut(g["accruals"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
            means = g.groupby("q", observed=True)["fwd_ret"].mean()
            if 1 in means.index and 5 in means.index:
                recs.append({"fy": fy, "q1": means[1], "q5": means[5],
                             "hl": means[5] - means[1], "n": len(g)})
                # financial concentration in the two legs
                if "is_fin" in g.columns:
                    legweights["q5_fin"].append(g[g["q"] == 5]["is_fin"].mean())
                    legweights["q1_fin"].append(g[g["q"] == 1]["is_fin"].mean())
        rd = pd.DataFrame(recs)
        if rd.empty:
            print("  insufficient data")
            return
        hl = rd["hl"].values
        mean_hl = hl.mean()
        # t-stat across years (Fama-MacBeth style)
        t = mean_hl / (hl.std(ddof=1) / np.sqrt(len(hl))) if len(hl) > 1 else np.nan
        print(f"  years used: {len(rd)}  ({rd['fy'].min()}-{rd['fy'].max()})")
        print(f"  mean Q1 (low acc): {rd['q1'].mean():+.3%}   mean Q5 (high acc): {rd['q5'].mean():+.3%}")
        print(f"  High-minus-Low (Q5-Q1): {mean_hl:+.3%}  t={t:.2f}  "
              f"[{'POSITIVE/inverted' if mean_hl>0 else 'NEGATIVE/Sloan'}]")
        print(f"  per-year H-L: {[f'{x:+.1%}' for x in hl]}")
        if legweights["q5_fin"]:
            print(f"  financial weight  Q5(high)={np.mean(legweights['q5_fin']):.1%}  "
                  f"Q1(low)={np.mean(legweights['q1_fin']):.1%}  "
                  f"gap={np.mean(legweights['q5_fin'])-np.mean(legweights['q1_fin']):+.1%}")

    def panel_ols_beta(df, label):
        """Fama-MacBeth: per-year OLS of fwd_ret on within-year accruals z-score,
        then average the slopes. Slope = return per +1 z of accruals.
        Sloan predicts NEGATIVE; the prior inverted result was +6.3%/z POSITIVE."""
        slopes = []
        for fy, g in df.groupby("fy"):
            if len(g) < 25:
                continue
            z = (g["accruals"] - g["accruals"].mean()) / g["accruals"].std(ddof=0)
            y = g["fwd_ret"].values
            zc = z.values - z.values.mean()
            denom = (zc ** 2).sum()
            if denom == 0:
                continue
            slopes.append(((zc * (y - y.mean())).sum()) / denom)
        slopes = np.array(slopes)
        if len(slopes) < 2:
            print(f"  [{label}] insufficient years"); return
        m = slopes.mean(); t = m / (slopes.std(ddof=1) / np.sqrt(len(slopes)))
        print(f"  [{label}] accruals-z beta: {m:+.3%}/z  t={t:.2f}  "
              f"[{'POSITIVE/inverted' if m>0 else 'NEGATIVE/Sloan'}]  (n_yrs={len(slopes)})")

    print("\n--- PANEL-OLS accruals-z betas (Fama-MacBeth across years) ---")
    panel_ols_beta(panel, "full universe")
    panel_ols_beta(panel[panel["form_price"] >= 5], "price>=$5")
    panel_ols_beta(panel[(~panel["is_fin"]) & (panel["form_price"] >= 5)], "ex-fin + price>=$5")

    spread_report(panel, "FULL UNIVERSE (replicate inverted sign)")
    spread_report(panel[panel["form_price"] >= 5], "PRICE >= $5")
    spread_report(panel[(~panel["is_fin"]) & (panel["form_price"] >= 5)],
                  "EX-FINANCIALS (SIC not 6000-6999) + PRICE >= $5  [FRONTIER PREDICTION]")
    spread_report(panel[~panel["is_fin"]], "EX-FINANCIALS only")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--prices", action="store_true")
    ap.add_argument("--sic", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args()

    if a.build:
        df = build_panel()
        print(f"panel: {len(df)} firm-years, {df['cik'].nunique()} firms, FY{FISCAL_YEARS[0]}-{FISCAL_YEARS[-1]}")
    if a.prices:
        panel = pd.read_json(PANEL_JSON)
        panel["cik"] = panel["cik"].astype(str)
        c2t = cik_to_ticker()
        tickers = sorted({c2t[c] for c in panel["cik"].unique() if c in c2t})
        print(f"pricing {len(tickers)} tickers")
        endjune_prices(tickers, list(range(FISCAL_YEARS[0] + 1, FISCAL_YEARS[-1] + 3)))
    if a.sic:
        panel = pd.read_json(PANEL_JSON)
        panel["cik"] = panel["cik"].astype(str)
        c2t = cik_to_ticker()
        ciks = sorted({c for c in panel["cik"].unique() if c in c2t})
        print(f"fetching SIC for {len(ciks)} ciks")
        fetch_sic(ciks)
    if a.analyze:
        analyze()


if __name__ == "__main__":
    main()
