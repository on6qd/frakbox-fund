"""
FINRA Consolidated (settled, bi-monthly) Short Interest fetcher.

Distinct from FINRA REGSHO DAILY short VOLUME (see short_interest_spike_backtester.py),
which is an already-mapped dead domain. This module pulls the SETTLED bi-monthly
short-interest file (Asquith-Pathak-Ritter style days-to-cover / short-interest-ratio
anomaly), which exposes:
    currentShortPositionQuantity   - shares sold short as of settlement date
    averageDailyVolumeQuantity     - ADV used by FINRA
    daysToCoverQuantity            - short position / ADV (precomputed)
    changePercent                  - % change in short position vs prior period

API: https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest
  - settlementDate is a PARTITION KEY: to sort you must filter it with EQUAL.
  - One settlement date returns the full cross-section (~6000 symbols).
  - Dissemination lag: FINRA publishes ~8 business days AFTER the settlement date,
    so any backtest must enter on/after settlement_date + ~9 trading days (no lookahead).

Usage:
    from tools.finra_short_interest import settlement_dates, fetch_cross_section
    dates = settlement_dates("2021-01-01", "2026-06-01")
    df = fetch_cross_section("2026-05-15")   # DataFrame indexed by symbol
"""
import os
import sys
import json
import time
import requests
import pandas as pd

API = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "finra_si_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _post(body, timeout=45, retries=4):
    last = None
    for i in range(retries):
        try:
            r = requests.post(API, json=body, timeout=timeout,
                              headers={"Accept": "application/json",
                                       "Content-Type": "application/json"})
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last = str(e)
        time.sleep(2 ** i)
    raise RuntimeError(f"FINRA API failed: {last}")


def settlement_dates(start, end, probe_symbol="AAPL"):
    """Enumerate bi-monthly settlement dates in [start, end] using a continuously
    listed probe symbol (AAPL exists every period)."""
    body = {"limit": 1000,
            "compareFilters": [
                {"compareType": "equal", "fieldName": "symbolCode", "fieldValue": probe_symbol},
                {"compareType": "gte", "fieldName": "settlementDate", "fieldValue": start},
                {"compareType": "lte", "fieldName": "settlementDate", "fieldValue": end}]}
    rows = _post(body)
    return sorted(set(r["settlementDate"] for r in rows))


def fetch_cross_section(settlement_date, use_cache=True):
    """Full cross-section for one settlement date -> DataFrame indexed by symbol.
    Columns: short_position, prev_short_position, adv, days_to_cover, change_pct, market."""
    cache = os.path.join(CACHE_DIR, f"si_{settlement_date}.parquet")
    if use_cache and os.path.exists(cache):
        try:
            return pd.read_parquet(cache)
        except Exception:
            pass
    # Paginate (cross-section ~6000 rows; API default cap is small, page by offset).
    out = []
    offset = 0
    page = 5000
    while True:
        body = {"limit": page, "offset": offset,
                "compareFilters": [
                    {"compareType": "equal", "fieldName": "settlementDate",
                     "fieldValue": settlement_date}]}
        rows = _post(body)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df = df.rename(columns={
        "symbolCode": "symbol",
        "currentShortPositionQuantity": "short_position",
        "previousShortPositionQuantity": "prev_short_position",
        "averageDailyVolumeQuantity": "adv",
        "daysToCoverQuantity": "days_to_cover",
        "changePercent": "change_pct",
        "marketClassCode": "market",
    })
    keep = ["symbol", "short_position", "prev_short_position", "adv",
            "days_to_cover", "change_pct", "market"]
    df = df[[c for c in keep if c in df.columns]]
    for c in ["short_position", "prev_short_position", "adv", "days_to_cover", "change_pct"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"]).set_index("symbol")
    if use_cache:
        try:
            df.to_parquet(cache)
        except Exception:
            pass
    return df


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", action="store_true", help="list settlement dates")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-06-01")
    ap.add_argument("--fetch", help="fetch one settlement date cross-section")
    args = ap.parse_args()
    if args.dates:
        d = settlement_dates(args.start, args.end)
        print(f"{len(d)} settlement dates {d[0]}..{d[-1]}")
        print(d)
    if args.fetch:
        df = fetch_cross_section(args.fetch)
        print(df.shape)
        print(df.sort_values("days_to_cover", ascending=False).head(10))
