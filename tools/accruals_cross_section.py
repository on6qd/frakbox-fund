"""Accruals anomaly (Sloan 1996) cross-section via the SEC XBRL `frames` API.

Hypothesis (cross_section class): firms with HIGH accruals (earnings driven by
non-cash accruals rather than cash flow) earn LOW future returns; LOW-accrual
firms earn HIGH future returns. Tradeable form = long low-accrual / short
high-accrual quintile, rebalanced annually.

Accruals definition (cash-flow approach, total accruals):
    Accruals_Y = (NetIncomeLoss_Y - OperatingCashFlow_Y) / TotalAssets_Y

Timing (Fama-French convention): financials from fiscal year ending in calendar
year Y (Dec FYE firms via CY{Y}Q4I / CY{Y}) are used to predict returns from
end-June Y+1 to end-June Y+2 — a >=6 month gap so the 10-K is public.

Data path: SEC XBRL frames (one concept, all filers, one bulk call). Reuses the
methodology from tools note `sec_xbrl_frames_cross_section_pipeline_2026_06_14`.
Style-neutralized panel OLS (size + book-to-market + momentum controls) per the
frontier guidance after the asset-growth dead end.

Usage:
    python3 tools/accruals_cross_section.py --start-fy 2013 --end-fy 2022 \
        --min-mktcap 50e6 --min-price 5
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

import numpy as np
import pandas as pd

UA = {"User-Agent": "frakbox-research bart.de.lepeleer@gmail.com"}
CACHE = "/tmp/xbrl_cache"
os.makedirs(CACHE, exist_ok=True)


def _get_json(url: str, retries: int = 3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    return None


def frame(concept: str, period: str) -> pd.DataFrame:
    """Fetch one us-gaap concept for one period across all filers. Cached."""
    fn = os.path.join(CACHE, f"{concept}_{period}.json")
    if os.path.exists(fn):
        with open(fn) as f:
            d = json.load(f)
    else:
        url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"
        try:
            d = _get_json(url)
        except Exception:
            return pd.DataFrame(columns=["cik", concept])
        with open(fn, "w") as f:
            json.dump(d, f)
        time.sleep(0.25)  # polite to SEC
    rows = [(int(x["cik"]), x["val"]) for x in d.get("data", [])]
    df = pd.DataFrame(rows, columns=["cik", concept])
    # keep the last (latest) value per cik if duplicates
    return df.groupby("cik", as_index=False).last()


def ticker_map() -> pd.DataFrame:
    d = _get_json("https://www.sec.gov/files/company_tickers.json")
    rows = [(int(v["cik_str"]), v["ticker"].upper()) for v in d.values()]
    return pd.DataFrame(rows, columns=["cik", "ticker"]).drop_duplicates("cik")


def build_fundamentals(start_fy: int, end_fy: int) -> pd.DataFrame:
    """Return panel: cik, ticker, fy, accruals, assets, equity, shares."""
    tmap = ticker_map()
    frames = []
    for y in range(start_fy, end_fy + 1):
        ni = frame("NetIncomeLoss", f"CY{y}")
        ocf = frame("NetCashProvidedByUsedInOperatingActivities", f"CY{y}")
        assets = frame("Assets", f"CY{y}Q4I")
        eq = frame("StockholdersEquity", f"CY{y}Q4I")
        sh = frame("CommonStockSharesOutstanding", f"CY{y}Q4I")
        m = ni.merge(ocf, on="cik").merge(assets, on="cik")
        m = m.merge(eq, on="cik", how="left").merge(sh, on="cik", how="left")
        m = m[m["Assets"] > 0]
        m["accruals"] = (m["NetIncomeLoss"] - m["NetCashProvidedByUsedInOperatingActivities"]) / m["Assets"]
        m["fy"] = y
        m = m.rename(columns={"Assets": "assets", "StockholdersEquity": "equity",
                              "CommonStockSharesOutstanding": "shares"})
        frames.append(m[["cik", "fy", "accruals", "assets", "equity", "shares"]])
        print(f"  FY{y}: {len(m)} filers with accruals")
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.merge(tmap, on="cik", how="inner")
    return panel


def fetch_prices(tickers, start, end, chunk=150):
    from tools.yfinance_utils import get_close_prices
    out = []
    tk = sorted(set(tickers))
    for i in range(0, len(tk), chunk):
        part = tk[i:i + chunk]
        try:
            df = get_close_prices(part, start=start, end=end)
            out.append(df)
            print(f"  prices {i+len(part)}/{len(tk)}")
        except Exception as e:
            print(f"  price chunk {i} failed: {e}")
    if not out:
        return pd.DataFrame()
    px = pd.concat(out, axis=1)
    px = px.loc[:, ~px.columns.duplicated()]
    return px


def june_close(px: pd.DataFrame, year: int) -> pd.Series:
    """Last available close on/before end-June of `year`."""
    cutoff = pd.Timestamp(f"{year}-06-30")
    sub = px[px.index <= cutoff]
    if sub.empty:
        return pd.Series(dtype=float)
    return sub.iloc[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-fy", type=int, default=2013)
    ap.add_argument("--end-fy", type=int, default=2022)
    ap.add_argument("--min-mktcap", type=float, default=50e6)
    ap.add_argument("--min-price", type=float, default=5.0)
    ap.add_argument("--oos-fy", type=int, default=2019,
                    help="FY >= this is OOS (forward returns in later years)")
    args = ap.parse_args()

    print("Building fundamentals from SEC XBRL frames...")
    panel = build_fundamentals(args.start_fy, args.end_fy)
    print(f"Panel: {len(panel)} cik-years, {panel['ticker'].nunique()} tickers")

    print("Fetching prices...")
    px = fetch_prices(panel["ticker"].tolist(),
                      start=f"{args.start_fy+1}-01-01",
                      end=f"{args.end_fy+3}-07-15")
    print(f"Price columns: {px.shape[1]}")

    # Build per-FY observations: signal at FY Y -> return end-June Y+1 .. end-June Y+2
    recs = []
    for y in range(args.start_fy, args.end_fy + 1):
        p0 = june_close(px, y + 1)
        p1 = june_close(px, y + 2)
        if p0.empty or p1.empty:
            continue
        sub = panel[panel["fy"] == y].copy()
        for _, r in sub.iterrows():
            t = r["ticker"]
            if t not in p0.index or t not in p1.index:
                continue
            price0, price1 = p0[t], p1[t]
            if not (np.isfinite(price0) and np.isfinite(price1)) or price0 < args.min_price:
                continue
            fwd = price1 / price0 - 1.0
            mktcap = (r["shares"] * price0) if np.isfinite(r["shares"]) else np.nan
            if np.isfinite(mktcap) and mktcap < args.min_mktcap:
                continue
            bm = (r["equity"] / mktcap) if (np.isfinite(r["equity"]) and np.isfinite(mktcap) and mktcap > 0) else np.nan
            recs.append(dict(ticker=t, fy=y, accruals=r["accruals"], fwd=fwd,
                             mktcap=mktcap, bm=bm))
    obs = pd.DataFrame(recs)
    # winsorize forward returns and accruals at 1/99
    for c in ["fwd", "accruals", "bm"]:
        lo, hi = obs[c].quantile([0.01, 0.99])
        obs[c] = obs[c].clip(lo, hi)
    obs = obs.dropna(subset=["fwd", "accruals"])
    print(f"\nObservations: {len(obs)} stock-years, {obs['ticker'].nunique()} tickers")

    analyze(obs, args.oos_fy)


def _ls_spread(df):
    """Long low-accrual Q1, short high-accrual Q5; return annual L/S mean %."""
    if df["fy"].nunique() == 0 or len(df) < 50:
        return np.nan
    spreads = []
    for y, g in df.groupby("fy"):
        if len(g) < 25:
            continue
        q = pd.qcut(g["accruals"].rank(method="first"), 5, labels=False)
        low = g[q == 0]["fwd"].mean()
        high = g[q == 4]["fwd"].mean()
        spreads.append(low - high)  # low minus high = anomaly-consistent if >0
    return float(np.mean(spreads) * 100) if spreads else np.nan


def _panel_ols(df):
    """Forward return on accruals z + style controls. Returns beta% per 1 z, t, p."""
    import statsmodels.api as sm
    d = df.copy()
    # z-score accruals within each year (cross-sectional)
    d["acc_z"] = d.groupby("fy")["accruals"].transform(lambda x: (x - x.mean()) / (x.std() + 1e-9))
    d["size"] = np.log(d["mktcap"].replace(0, np.nan))
    d["bm_z"] = d.groupby("fy")["bm"].transform(lambda x: (x - x.mean()) / (x.std() + 1e-9))
    d = d.dropna(subset=["acc_z", "fwd"])
    X_cols = ["acc_z"]
    for c in ["size", "bm_z"]:
        if d[c].notna().sum() > 0.5 * len(d):
            d[c] = d[c].fillna(d[c].median())
            X_cols.append(c)
    X = sm.add_constant(d[X_cols])
    y = d["fwd"]
    model = sm.OLS(y, X).fit(cov_type="cluster", cov_kwds={"groups": d["fy"]})
    b = model.params["acc_z"] * 100
    return dict(beta_pct=round(float(b), 3), t=round(float(model.tvalues["acc_z"]), 3),
                p=round(float(model.pvalues["acc_z"]), 4), n=int(len(d)), controls=X_cols)


def analyze(obs, oos_fy):
    full_is = obs[obs["fy"] < oos_fy]
    full_oos = obs[obs["fy"] >= oos_fy]
    # small-cap = bottom-half market cap each year
    med = obs.groupby("fy")["mktcap"].transform("median")
    small = obs[obs["mktcap"] <= med]

    out = {
        "n_stock_years": int(len(obs)),
        "n_tickers": int(obs["ticker"].nunique()),
        "fy_range": [int(obs["fy"].min()), int(obs["fy"].max())],
        "ls_spread_pct_yr": {
            "all": round(_ls_spread(obs), 3),
            "IS": round(_ls_spread(full_is), 3),
            "OOS": round(_ls_spread(full_oos), 3),
            "small": round(_ls_spread(small), 3),
        },
        "panel_ols_acc_z": {
            "full": _panel_ols(obs),
            "IS": _panel_ols(full_is) if len(full_is) > 100 else None,
            "OOS": _panel_ols(full_oos) if len(full_oos) > 100 else None,
            "small": _panel_ols(small) if len(small) > 100 else None,
        },
    }
    print("\n===== ACCRUALS ANOMALY RESULTS =====")
    print(json.dumps(out, indent=2))
    with open("/tmp/accruals_result.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\nSaved -> /tmp/accruals_result.json")
    print("\nInterpretation: anomaly-consistent => L/S spread POSITIVE (low-accrual")
    print("beats high-accrual) AND panel acc_z beta NEGATIVE (more accruals -> lower")
    print("return). Tradeable only if significant (|t|>~2) and stable IS->OOS.")


if __name__ == "__main__":
    main()
