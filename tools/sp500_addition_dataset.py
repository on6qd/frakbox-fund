#!/usr/bin/env python3
"""S&P 500 index-addition dataset + effective-anchored capture backtest.

Rebuilds the reproducible quarterly-addition event set from the live Wikipedia
"List of S&P 500 companies" table (current constituents, survivor basis — the
same basis prior audits used) and measures the effective-date-anchored capture
premium with a regime split.

WHY EFFECTIVE-ANCHORED (lookahead-free): S&P announces quarterly additions
after close ~10 trading days before the effective rebalance date. Anchoring the
entry to the effective date (N trading days before it, N<=8 stays safely after
the announcement) avoids the one-day announcement-open lookahead that inflated
the original headline (+5.2%/+8.9%) to roughly double the realizable edge.

Key 2026-09-06 finding (n=87, 2016-2026): the capture premium is CONFINED to
the 2023-2026 regime. Pre-2023 (n=50) shows zero premium. See knowledge entry
sp500_index_addition_capture_regime_confined_2023_2026_2026_09_06.

Usage:
    python tools/sp500_addition_dataset.py                 # full backtest + regime split
    python tools/sp500_addition_dataset.py --start-year 2016 --end-year 2026
    python tools/sp500_addition_dataset.py --dump events.json   # just dump the event set

Note: importing anything under tools/ installs the curl_cffi chrome->safari
shim (tools/__init__.py) so the Wikipedia fetch and yfinance both work behind
the egress proxy.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: F401  installs curl_cffi shim
import numpy as np
import pandas as pd
from curl_cffi import requests as cr

from tools.yfinance_utils import safe_download

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
REBALANCE_MONTHS = (3, 6, 9, 12)


def fetch_addition_events(start_year: int = 2016, end_year: int = 2026) -> pd.DataFrame:
    """Return current-constituent quarterly-month additions in [start_year, end_year].

    Columns: sym, eff (effective/Date-added, datetime), year.
    """
    r = cr.get(
        WIKI_URL,
        impersonate="safari15_5",
        headers={"User-Agent": "Mozilla/5.0 research"},
        timeout=30,
    )
    r.raise_for_status()
    df = pd.read_html(io.StringIO(r.text))[0]
    df["Date added"] = pd.to_datetime(df["Date added"], errors="coerce")
    df = df.dropna(subset=["Date added"])
    df = df[df["Date added"].dt.month.isin(REBALANCE_MONTHS)]
    df = df[(df["Date added"].dt.year >= start_year) & (df["Date added"].dt.year <= end_year)]
    out = df[["Symbol", "Date added"]].rename(columns={"Symbol": "sym", "Date added": "eff"})
    out["sym"] = out["sym"].str.replace(".", "-", regex=False)
    out["year"] = out["eff"].dt.year
    return out.reset_index(drop=True)


def run_backtest(events: pd.DataFrame) -> pd.DataFrame:
    """For each event compute SPY-adjusted capture returns anchored to the effective date."""
    spy = safe_download("SPY", start="2015-11-01", end="2026-12-31")
    spy = spy[["Open", "Close"]].rename(columns={"Open": "spy_open", "Close": "spy_close"})

    rows = []
    for sym, eff, year in zip(events["sym"], events["eff"], events["year"]):
        try:
            px = safe_download(
                sym,
                start=(eff - pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
                end=(eff + pd.Timedelta(days=20)).strftime("%Y-%m-%d"),
            )
            if px is None or len(px) < 15:
                continue
            px = px[["Open", "Close"]]
            idx = px.index
            pos = idx.searchsorted(eff, side="right") - 1
            if pos < 12 or pos >= len(idx):
                continue
            eff_row = idx[pos]

            def capture(nback):
                e_pos = pos - nback
                if e_pos < 0:
                    return None
                entry, exit_ = px["Open"].iloc[e_pos], px["Close"].iloc[pos]
                s_e, s_x = spy["spy_open"].asof(idx[e_pos]), spy["spy_close"].asof(eff_row)
                if any(pd.isna([entry, exit_, s_e, s_x])) or entry <= 0 or s_e <= 0:
                    return None
                return (exit_ / entry - 1) * 100 - (s_x / s_e - 1) * 100

            def post(nfwd):
                x_pos = pos + nfwd
                if x_pos >= len(idx):
                    return None
                entry, exit_ = px["Close"].iloc[pos], px["Close"].iloc[x_pos]
                s_e, s_x = spy["spy_close"].asof(eff_row), spy["spy_close"].asof(idx[x_pos])
                if any(pd.isna([entry, exit_, s_e, s_x])) or entry <= 0 or s_e <= 0:
                    return None
                return (exit_ / entry - 1) * 100 - (s_x / s_e - 1) * 100

            rows.append(
                dict(sym=sym, eff=eff, year=int(year),
                     cap5=capture(5), cap8=capture(8), cap10=capture(10), post5=post(5))
            )
        except Exception:
            continue
    return pd.DataFrame(rows)


def summarize(res: pd.DataFrame) -> None:
    from scipy import stats

    def line(s, label):
        s = s.dropna()
        if len(s) < 5:
            print(f"{label:28s} n={len(s):3d} (too few)")
            return
        _, p = stats.ttest_1samp(s, 0)
        print(f"{label:28s} n={len(s):3d} mean={s.mean():+6.2f}% pos={100*(s>0).mean():4.1f}% p={p:.3f}")

    for col in ["cap5", "cap8", "cap10", "post5"]:
        print(f"--- {col} ---")
        line(res[col], "  ALL")
        for lo, hi, lab in [(2016, 2019, "2016-2019"), (2020, 2022, "2020-2022"), (2023, 2026, "2023-2026")]:
            line(res[(res["year"] >= lo) & (res["year"] <= hi)][col], f"  {lab}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2016)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--dump", type=str, default=None, help="write event set as JSON and exit")
    args = ap.parse_args()

    events = fetch_addition_events(args.start_year, args.end_year)
    print(f"quarterly-month additions {args.start_year}-{args.end_year}: n={len(events)}")

    if args.dump:
        events.assign(eff=events["eff"].dt.strftime("%Y-%m-%d")).to_json(args.dump, orient="records", indent=1)
        print("wrote", args.dump)
        return

    res = run_backtest(events)
    print(f"backtested n={len(res)}")
    summarize(res)


if __name__ == "__main__":
    main()
