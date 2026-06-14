"""Asset-growth cross-section anomaly test, restricted to the large-cap S&P 500.

Hypothesis (Cooper, Gulen & Schill 2008): firms with high total-asset growth
subsequently EARN LOW returns; low-asset-growth firms earn high returns. Trade =
long the lowest-asset-growth quintile, short the highest, equal-weighted, annual
rebalance.

This is a FUNDAMENTAL cross-section (not a price-based anomaly). It directly probes
the system's meta-rule that cross-sectional anomalies invert/vanish in large caps,
using a fresh factor sourced from SEC XBRL `frames` (one bulk call per fiscal year)
rather than the flaky EFTS full-text endpoint.

Design (look-ahead-safe):
  - Asset growth for fiscal year Y = Assets(FYE Dec Y) / Assets(FYE Dec Y-1) - 1.
  - Portfolio FORMED at the last trading day of June Y+1 (10-Ks for Dec FYE are
    filed by ~March, so the signal is public). Held 12 months to end-June Y+2.
  - Universe: current S&P 500 (large-cap, liquid). Survivorship caveat noted.
  - Quintiles within the universe each year; long Q1 (low growth), short Q5 (high).
  - Total-return prices via yfinance auto_adjust (includes dividends).
  - IS = holding years 2015-2019 (form Y 2014-2018); OOS = 2020-2024 (form Y 2019-2023).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.build_sp500_universe import load_sp500_universe  # noqa: E402
from tools.yfinance_utils import get_close_prices  # noqa: E402

H = {
    "User-Agent": "frakbox-research bart.de.lepeleer@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

FORM_YEARS = list(range(2014, 2024))  # Y: growth measured FYE Y; hold July Y+1 -> June Y+2
IS_HOLD = set(range(2015, 2020))      # holding-start years for IS
N_QUINTILES = 5


def fetch_assets_by_year(years):
    """Return {cik:int -> {year -> assets}} from SEC XBRL frames (us-gaap:Assets)."""
    out = {}
    for y in years:
        url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY{y}Q4I.json"
        r = requests.get(url, headers=H, timeout=60)
        r.raise_for_status()
        data = r.json()["data"]
        # A CIK can appear multiple times (amended filings); keep the last (latest) value.
        for row in data:
            out.setdefault(int(row["cik"]), {})[y] = float(row["val"])
        print(f"  frames CY{y}Q4I: {len(data)} filer-rows", file=sys.stderr)
    return out


def build_cik_ticker_map(universe):
    """Map S&P 500 tickers -> CIK using SEC company_tickers.json."""
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=H, timeout=30)
    r.raise_for_status()
    ct = r.json()
    tick2cik = {v["ticker"].upper(): int(v["cik_str"]) for v in ct.values()}
    uni = set(t.upper() for t in universe)
    m = {t: tick2cik[t] for t in uni if t in tick2cik}
    print(f"mapped {len(m)}/{len(uni)} universe tickers to CIK", file=sys.stderr)
    return m


def june_close(prices: pd.DataFrame, year: int) -> pd.Series:
    """Last available close on/before June 30 of `year`, per ticker."""
    cutoff = pd.Timestamp(f"{year}-06-30")
    sub = prices.loc[:cutoff]
    if sub.empty:
        return pd.Series(dtype=float)
    return sub.ffill().iloc[-1]


def main():
    universe = load_sp500_universe()
    tick2cik = build_cik_ticker_map(universe)
    cik2tick = {c: t for t, c in tick2cik.items()}

    years_needed = sorted(set([y for y in FORM_YEARS] + [y - 1 for y in FORM_YEARS]))
    assets = fetch_assets_by_year(years_needed)

    # Monthly total-return prices for the whole universe.
    tickers = sorted(tick2cik.keys())
    print(f"downloading prices for {len(tickers)} tickers...", file=sys.stderr)
    prices = get_close_prices(tickers, start="2014-01-01", end="2025-07-15",
                              interval="1mo", progress=False, auto_adjust=True)
    prices = prices.dropna(how="all")
    avail = set(prices.columns)

    # SPY benchmark for the same June-to-June windows.
    spy = get_close_prices("SPY", start="2014-01-01", end="2025-07-15",
                           interval="1mo", progress=False, auto_adjust=True)["SPY"]

    rows = []
    for Y in FORM_YEARS:
        hold_start = Y + 1  # July Y+1
        # asset growth for firms in universe with both years present
        growth = {}
        for cik, yrs in assets.items():
            t = cik2tick.get(cik)
            if t is None or t not in avail:
                continue
            if Y in yrs and (Y - 1) in yrs and yrs[Y - 1] > 0:
                growth[t] = yrs[Y] / yrs[Y - 1] - 1.0
        g = pd.Series(growth).dropna()
        if len(g) < 25:
            continue

        # forward total return: end-June(Y+1) -> end-June(Y+2)
        p0 = june_close(prices, hold_start)
        p1 = june_close(prices, hold_start + 1)
        fwd = (p1 / p0 - 1.0)
        fwd = fwd.replace([np.inf, -np.inf], np.nan).dropna()

        common = g.index.intersection(fwd.index)
        g2 = g.loc[common]
        fwd2 = fwd.loc[common]
        if len(common) < 25:
            continue

        # quintiles by asset growth (labels 1..5; 1 = lowest growth)
        try:
            q = pd.qcut(g2, N_QUINTILES, labels=range(1, N_QUINTILES + 1))
        except ValueError:
            q = pd.qcut(g2.rank(method="first"), N_QUINTILES, labels=range(1, N_QUINTILES + 1))
        low = fwd2[q == 1]
        high = fwd2[q == N_QUINTILES]
        ls = low.mean() - high.mean()  # long low-growth, short high-growth

        sp0 = june_close(spy.to_frame("SPY"), hold_start)["SPY"]
        sp1 = june_close(spy.to_frame("SPY"), hold_start + 1)["SPY"]
        spy_ret = sp1 / sp0 - 1.0

        rows.append({
            "form_year": Y,
            "hold": f"{hold_start}-07..{hold_start + 1}-06",
            "n": len(common),
            "low_growth_ret": low.mean(),
            "high_growth_ret": high.mean(),
            "long_short": ls,
            "univ_mean": fwd2.mean(),
            "spy": spy_ret,
            "low_minus_spy": low.mean() - spy_ret,
            "sample": "IS" if hold_start in IS_HOLD else "OOS",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        print("NO RESULTS — insufficient data")
        return

    pd.set_option("display.width", 200, "display.max_columns", 30)
    show = df.copy()
    for c in ["low_growth_ret", "high_growth_ret", "long_short", "univ_mean",
              "spy", "low_minus_spy"]:
        show[c] = (show[c] * 100).round(2)
    print(show.to_string(index=False))

    def stats(s):
        s = s.dropna()
        n = len(s)
        if n < 2:
            return n, np.nan, np.nan, np.nan
        m = s.mean()
        t = m / (s.std(ddof=1) / np.sqrt(n))
        winrate = (s > 0).mean()
        return n, m * 100, t, winrate * 100

    print("\n=== ASSET-GROWTH LONG/SHORT (Q1 low-growth minus Q5 high-growth) ===")
    for label, sub in [("ALL", df), ("IS", df[df["sample"] == "IS"]),
                       ("OOS", df[df["sample"] == "OOS"])]:
        n, m, t, w = stats(sub["long_short"])
        print(f"  {label:4s}: n={n} mean={m:.2f}%/yr t={t:.2f} winrate={w:.0f}%")

    print("\n=== LONG LEG ONLY (low-growth) minus SPY ===")
    for label, sub in [("ALL", df), ("IS", df[df["sample"] == "IS"]),
                       ("OOS", df[df["sample"] == "OOS"])]:
        n, m, t, w = stats(sub["low_minus_spy"])
        print(f"  {label:4s}: n={n} mean={m:.2f}%/yr t={t:.2f} winrate={w:.0f}%")

    df.to_csv(Path(__file__).parent / "asset_growth_cross_section_results.csv", index=False)
    print("\nsaved -> tools/asset_growth_cross_section_results.csv")


if __name__ == "__main__":
    main()
