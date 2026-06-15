"""Asset-growth cross-section on a BROAD universe with style/size neutralization.

Frontier follow-up to asset_growth_cross_section.py. The large-cap (S&P 500) test
found NO edge (asset growth there is a glamour/style bet). The meta-guidance was:
revisit fundamental cross-sections on a BROAD/small-cap universe (where limits-to-
arbitrage let anomalies survive) and NEUTRALIZE the value/growth style factor.

Hypothesis (Cooper, Gulen & Schill 2008): high total-asset-growth firms subsequently
earn LOW returns; low-asset-growth firms earn HIGH returns. Long Q1 (low growth),
short Q5 (high growth).

Design (look-ahead-safe):
  - Asset growth for fiscal year Y = Assets(FYE Dec Y) / Assets(FYE Dec Y-1) - 1.
  - Portfolio FORMED end-June Y+1 (Dec-FYE 10-Ks public by ~March). Held 12 months to
    end-June Y+2. Total-return prices (yfinance auto_adjust).
  - Universe: ALL SEC XBRL filers that report us-gaap:Assets and map to a ticker with
    price data. Tradeability screen: Assets(Y) > $500M AND formation price > $5.
  - Style neutralization (no market cap needed -> avoids sparse shares frame):
      * size      = log Assets(Y)            (z-scored within year)
      * value     = StockholdersEquity(Y)/Assets(Y)  (book-equity ratio, z within year)
      * momentum  = prior 12m return June(Y)->June(Y+1) (z within year)
    Pooled panel OLS: fwd_ret ~ ag_z + value_z + size_z + mom_z + year-FE,
    SE clustered by year. The ag_z coefficient is the STYLE-NEUTRALIZED asset-growth
    effect (negative => anomaly holds).
  - Economic magnitude: quintile L/S spread overall and within each size tercile.
  - IS = holding-start years 2015-2019; OOS = 2020-2024.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.yfinance_utils import get_close_prices  # noqa: E402

H = {
    "User-Agent": "frakbox-research bart.de.lepeleer@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

FORM_YEARS = list(range(2014, 2024))   # growth measured FYE Y; hold July Y+1 -> June Y+2
IS_HOLD = set(range(2015, 2020))       # holding-start years for IS
N_QUINTILES = 5
ASSETS_FLOOR = 500e6                    # tradeability: book size > $500M
PRICE_FLOOR = 5.0                       # tradeability: formation price > $5
PRICE_CHUNK = 300


def fetch_frame_by_year(concept, unit, years):
    """{cik:int -> {year -> val}} from SEC XBRL frames, instant Q4 (FYE Dec)."""
    out = {}
    for y in years:
        url = f"https://data.sec.gov/api/xbrl/frames/{concept}/{unit}/CY{y}Q4I.json"
        r = requests.get(url, headers=H, timeout=60)
        r.raise_for_status()
        data = r.json()["data"]
        for row in data:
            out.setdefault(int(row["cik"]), {})[y] = float(row["val"])
        print(f"  {concept} CY{y}Q4I: {len(data)} rows", file=sys.stderr)
    return out


def build_cik_ticker_map():
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=H, timeout=30)
    r.raise_for_status()
    ct = r.json()
    cik2tick = {}
    for v in ct.values():
        c, t = int(v["cik_str"]), v["ticker"].upper()
        # prefer the shortest ticker per CIK (primary common share class)
        if c not in cik2tick or len(t) < len(cik2tick[c]):
            cik2tick[c] = t
    return cik2tick


def june_close(prices: pd.DataFrame, year: int) -> pd.Series:
    cutoff = pd.Timestamp(f"{year}-06-30")
    sub = prices.loc[:cutoff]
    if sub.empty:
        return pd.Series(dtype=float)
    return sub.ffill().iloc[-1]


def zscore(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return s * 0.0
    return (s - s.mean()) / sd


def download_prices(tickers):
    frames = []
    for i in range(0, len(tickers), PRICE_CHUNK):
        chunk = tickers[i:i + PRICE_CHUNK]
        try:
            df = get_close_prices(chunk, start="2013-01-01", end="2025-07-15",
                                  interval="1mo", progress=False, auto_adjust=True)
            frames.append(df)
            print(f"  prices {i+len(chunk)}/{len(tickers)} ok ({df.shape[1]} cols)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  prices chunk {i} FAILED: {e}", file=sys.stderr)
    if not frames:
        raise RuntimeError("no price data downloaded")
    prices = pd.concat(frames, axis=1)
    prices = prices.loc[:, ~prices.columns.duplicated()]
    return prices.dropna(how="all")


def main():
    cik2tick = build_cik_ticker_map()

    asset_years = sorted(set([y for y in FORM_YEARS] + [y - 1 for y in FORM_YEARS]))
    assets = fetch_frame_by_year("us-gaap/Assets", "USD", asset_years)
    equity = fetch_frame_by_year("us-gaap/StockholdersEquity", "USD", FORM_YEARS)

    # candidate tickers = any CIK that ever clears the assets floor in a form year
    cand = set()
    for cik, yrs in assets.items():
        t = cik2tick.get(cik)
        if not t:
            continue
        if any(yrs.get(Y, 0) > ASSETS_FLOOR for Y in FORM_YEARS):
            cand.add(t)
    cand = sorted(cand)
    print(f"candidate tickers (assets>${ASSETS_FLOOR/1e6:.0f}M ever): {len(cand)}", file=sys.stderr)

    prices = download_prices(cand)
    avail = set(prices.columns)
    print(f"price data available for {len(avail)} tickers", file=sys.stderr)

    spy = get_close_prices("SPY", start="2013-01-01", end="2025-07-15",
                           interval="1mo", progress=False, auto_adjust=True)["SPY"]

    panel = []     # per stock-year rows for the pooled regression
    yearly = []    # per (year,size_bucket) L/S spreads
    for Y in FORM_YEARS:
        hold_start = Y + 1
        recs = {}
        for cik, yrs in assets.items():
            t = cik2tick.get(cik)
            if not t or t not in avail:
                continue
            a_y, a_p = yrs.get(Y), yrs.get(Y - 1)
            if a_y is None or a_p is None or a_p <= 0 or a_y <= ASSETS_FLOOR:
                continue
            se = equity.get(cik, {}).get(Y)
            recs[t] = {
                "ag": a_y / a_p - 1.0,
                "log_assets": np.log(a_y),
                "be_ratio": (se / a_y) if (se is not None and a_y > 0) else np.nan,
            }
        if len(recs) < 50:
            continue
        d = pd.DataFrame(recs).T

        p_form = june_close(prices, hold_start)             # end-June Y+1
        p_exit = june_close(prices, hold_start + 1)         # end-June Y+2
        p_prev = june_close(prices, hold_start - 1)         # end-June Y (for momentum)
        d["p_form"] = p_form.reindex(d.index)
        d = d[d["p_form"] > PRICE_FLOOR]
        d["fwd"] = (p_exit.reindex(d.index) / d["p_form"] - 1.0)
        d["mom"] = (d["p_form"] / p_prev.reindex(d.index) - 1.0)
        d = d.replace([np.inf, -np.inf], np.nan)
        d = d.dropna(subset=["ag", "fwd", "log_assets"])
        if len(d) < 50:
            continue

        # within-year standardized features
        d["ag_z"] = zscore(d["ag"].clip(d["ag"].quantile(.01), d["ag"].quantile(.99)))
        d["size_z"] = zscore(d["log_assets"])
        d["value_z"] = zscore(d["be_ratio"].fillna(d["be_ratio"].median()))
        d["mom_z"] = zscore(d["mom"].fillna(0.0).clip(-0.9, 3.0))
        d["year"] = hold_start
        d["sample"] = "IS" if hold_start in IS_HOLD else "OOS"

        # size terciles by book assets
        d["size_bucket"] = pd.qcut(d["log_assets"], 3, labels=["Small", "Mid", "Large"])

        # asset-growth quintiles overall and within size bucket
        sp_form = june_close(spy.to_frame("SPY"), hold_start)["SPY"]
        sp_exit = june_close(spy.to_frame("SPY"), hold_start + 1)["SPY"]
        spy_ret = sp_exit / sp_form - 1.0

        def ls_spread(sub):
            if len(sub) < 25:
                return np.nan, np.nan, len(sub)
            try:
                q = pd.qcut(sub["ag"], N_QUINTILES, labels=range(1, N_QUINTILES + 1))
            except ValueError:
                q = pd.qcut(sub["ag"].rank(method="first"), N_QUINTILES,
                            labels=range(1, N_QUINTILES + 1))
            lo = sub["fwd"][q == 1].mean()
            hi = sub["fwd"][q == N_QUINTILES].mean()
            return lo - hi, lo - spy_ret, len(sub)

        for bucket, sub in [("ALL", d)] + list(d.groupby("size_bucket", observed=True)):
            ls, long_minus_spy, n = ls_spread(sub)
            yearly.append({"year": hold_start, "bucket": str(bucket), "n": n,
                           "long_short": ls, "long_minus_spy": long_minus_spy,
                           "spy": spy_ret, "sample": d["sample"].iloc[0]})

        panel.append(d[["fwd", "ag_z", "size_z", "value_z", "mom_z", "year",
                        "sample", "size_bucket"]])

    if not panel:
        print("NO RESULTS")
        return
    P = pd.concat(panel)
    YL = pd.DataFrame(yearly)

    # ---- economic magnitude: L/S quintile spread ----
    def agg(s):
        s = s.dropna()
        n = len(s)
        if n < 2:
            return n, np.nan, np.nan, np.nan
        m = s.mean()
        t = m / (s.std(ddof=1) / np.sqrt(n))
        return n, m * 100, t, (s > 0).mean() * 100

    print("=== ASSET-GROWTH L/S SPREAD (Q1 low-growth minus Q5 high-growth), %/yr ===")
    print("(per-year spreads averaged; t-stat over years)")
    for bucket in ["ALL", "Small", "Mid", "Large"]:
        sub = YL[YL["bucket"] == bucket]
        for label in ["ALL", "IS", "OOS"]:
            ss = sub if label == "ALL" else sub[sub["sample"] == label]
            n, m, t, w = agg(ss["long_short"])
            print(f"  {bucket:5s} {label:3s}: years={n} mean={m:6.2f}%/yr  t={t:5.2f}  winrate={w:3.0f}%")
        print()

    # ---- well-powered pooled panel regression, style-neutralized ----
    import statsmodels.formula.api as smf
    print("=== POOLED PANEL OLS  fwd ~ ag_z + value_z + size_z + mom_z + C(year) ===")
    print("(ag_z<0 => high asset-growth predicts LOW forward return = anomaly holds)")
    print(f"total stock-years: {len(P)}")
    for label, sub in [("FULL UNIVERSE", P),
                       ("SMALL tercile", P[P["size_bucket"] == "Small"]),
                       ("OOS only", P[P["sample"] == "OOS"]),
                       ("SMALL & OOS", P[(P["size_bucket"] == "Small") & (P["sample"] == "OOS")])]:
        if len(sub) < 100:
            print(f"  {label}: n={len(sub)} too small"); continue
        m = smf.ols("fwd ~ ag_z + value_z + size_z + mom_z + C(year)", data=sub).fit(
            cov_type="cluster", cov_kwds={"groups": sub["year"]})
        b, se_, p = m.params["ag_z"], m.bse["ag_z"], m.pvalues["ag_z"]
        print(f"  {label:14s}: n={len(sub):5d}  ag_z beta={b*100:+.3f}%  t={b/se_:+.2f}  p={p:.3f}")

    YL.to_csv(Path(__file__).parent / "asset_growth_broad_results.csv", index=False)
    print("\nsaved -> tools/asset_growth_broad_results.csv")


if __name__ == "__main__":
    main()
