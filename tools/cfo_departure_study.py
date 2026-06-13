"""One-off discovery study: CFO sudden-departure 8-K (Item 5.02) -> price drift.

The system's existing dead-end `ceo_cfo_sudden_departure` tested only N=10 at a
3-5 day hold and explicitly flagged the open thread: "Academic literature shows
this as a 1-3 month signal, not 3-5 day. Would need 30+ day hold." This script
follows up at a 20-day horizon with a larger, liquidity-filtered sample.

Design choices for completability (avoids fragile per-CIK / market-cap calls):
  - Tickers parsed directly from EFTS display_names (e.g. "ACME CORP (ACME) (CIK ...)").
  - "Large/mid-cap" proxied by average dollar volume from the price data itself.
  - Entry = first trading day's OPEN after the filing date (8-Ks often after hours).
  - Abnormal return = stock return minus SPY return over the same window.
  - IS vs OOS = two windows in different regimes (2022 bear / 2024 bull).

Discovery screen only: EFTS phrase match + Item 5.02, no full-text classification,
so some appointment/other-officer noise is tolerated (dilutes toward null).
"""
from __future__ import annotations

import re
import sys
import urllib.parse
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "tools")
from edgar_efts import efts_get_json
from yfinance_utils import safe_download

CFO_QUERIES = [
    '"Chief Financial Officer" "resigned"',
    '"Chief Financial Officer" "stepped down"',
    '"Chief Financial Officer" "will depart"',
    '"Chief Financial Officer" "terminated"',
]

TICKER_RE = re.compile(r"\(([A-Z][A-Z.\-]{0,6})\)\s*\(CIK")


def gather_events(start: str, end: str, max_pages: int = 3) -> dict:
    """Return {ticker: earliest_filing_date} for CFO-departure 8-Ks in window."""
    events: dict[str, str] = {}
    for q in CFO_QUERIES:
        for page in range(max_pages):
            frm = page * 100
            url = (
                f"https://efts.sec.gov/LATEST/search-index?q={urllib.parse.quote(q)}"
                f"&dateRange=custom&startdt={start}&enddt={end}&forms=8-K&from={frm}"
            )
            try:
                d = efts_get_json(url, label=f"{q[:20]}p{page}")
            except Exception as e:
                print(f"  EFTS fail {q[:25]} page{page}: {e}", file=sys.stderr)
                break
            hits = d.get("hits", {}).get("hits", [])
            if not hits:
                break
            for h in hits:
                src = h.get("_source", {})
                if "5.02" not in (src.get("items") or []):
                    continue
                dn = (src.get("display_names") or [""])[0]
                m = TICKER_RE.search(dn)
                if not m:
                    continue
                tk = m.group(1)
                fd = src.get("file_date", "")
                if not fd:
                    continue
                if tk not in events or fd < events[tk]:
                    events[tk] = fd
            if len(hits) < 100:
                break
    return events


def study(events: dict, label: str, min_dollar_vol: float = 20e6) -> None:
    tickers = sorted(events)
    print(f"\n=== {label}: {len(tickers)} unique CFO-departure tickers ===")
    # batch download price history covering all events + 30 trading-day tail
    dates = [events[t] for t in tickers]
    start = (pd.Timestamp(min(dates)) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(max(dates)) + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    spy = safe_download("SPY", start=start, end=end)
    spy_close = spy["Close"].dropna()

    rows = []
    for tk in tickers:
        try:
            df = safe_download(tk, start=start, end=end)
        except Exception:
            continue
        if df is None or df.empty or "Close" not in df or "Open" not in df:
            continue
        df = df.dropna(subset=["Close"])
        if len(df) < 30:
            continue
        # liquidity filter via avg dollar volume
        if "Volume" in df:
            adv = float((df["Close"] * df["Volume"]).tail(120).mean())
            if adv < min_dollar_vol:
                continue
        fd = pd.Timestamp(events[tk])
        idx = df.index[df.index > fd]
        if len(idx) < 25:
            continue
        entry_day = idx[0]
        entry_open = float(df.loc[entry_day, "Open"])
        if not np.isfinite(entry_open) or entry_open <= 0:
            continue
        pos = df.index.get_loc(entry_day)
        rec = {"ticker": tk, "date": events[tk]}
        for hor in (5, 10, 20):
            if pos + hor >= len(df):
                rec[f"ab{hor}"] = np.nan
                continue
            exit_px = float(df["Close"].iloc[pos + hor])
            stock_ret = (exit_px - entry_open) / entry_open * 100
            # SPY market return over same calendar span
            try:
                s0 = float(spy_close.asof(entry_day))
                s1 = float(spy_close.asof(df.index[pos + hor]))
                mkt_ret = (s1 - s0) / s0 * 100
            except Exception:
                mkt_ret = 0.0
            rec[f"ab{hor}"] = stock_ret - mkt_ret
        rows.append(rec)

    res = pd.DataFrame(rows)
    if res.empty:
        print("  no qualifying liquid events")
        return
    print(f"  qualifying liquid events: {len(res)}")
    from scipy import stats
    for hor in (5, 10, 20):
        col = res[f"ab{hor}"].dropna()
        if len(col) < 5:
            continue
        t, p = stats.ttest_1samp(col, 0)
        print(
            f"  {hor:>2}d  n={len(col):3d}  mean={col.mean():+6.2f}%  "
            f"median={col.median():+6.2f}%  neg_rate={ (col<0).mean()*100:4.1f}%  "
            f"t={t:+5.2f}  p={p:.3f}"
        )
    return res


if __name__ == "__main__":
    is_ev = gather_events("2022-01-01", "2022-12-31", max_pages=2)
    oos_ev = gather_events("2024-01-01", "2024-12-31", max_pages=2)
    study(is_ev, "IS 2022 (bear)")
    study(oos_ev, "OOS 2024 (bull)")
