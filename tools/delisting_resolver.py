"""
Delisting resolver — survivorship-free terminal returns for delisted US equities.

THE PROBLEM (see tiingo_survivorship_free_requires_delisting_ticker_map_2026_06_15):
Cross-sectional anomaly tests on the yfinance/raw-ticker path are survivorship-biased
because delisted/bankrupt names vanish from the current ticker map AND their original
ticker is frequently REUSED by a new company, masking the blowup. Examples confirmed
via Tiingo metadata:
  - BBBY  -> "Beyond Inc" (NYSE, active) reuses the symbol; the real Bed Bath & Beyond
            terminal lives at BBBYQ (PINK, ended 2023-09-29 at ~$0.08).
  - RAD   -> "RADIOPHARM" (ASX) reuses the symbol; real Rite Aid is RADCQ (PINK).
A price feed alone cannot detect the delisting because reuse contaminates both the
price series AND the metadata endDate of the original symbol.

THE FIX (this module): use SEC EDGAR as the reuse-proof delisting detector. A company's
Form 25 / 25-NSE (notification of removal from listing) and Form 15 (deregistration)
are keyed by CIK and immune to later ticker reuse. We validated this on 9/9 known
recent bankruptcies (BBBY, RAD, PRTY, SI, YELL, SDC, RIDE, WE, TUP): every one has a
Form 25 at a sensible date even where the symbol was later reused.

Two-layer design:
  1. get_delisting_info(cik): reuse-proof flag + date from SEC submissions (Form 25/15).
  2. survivorship_free_return(...): realized return over a window. If the name delisted
     inside the window, assign a survivorship-free terminal return — preferring an OTC
     terminal price (resolved + name-validated against SEC) and falling back to a
     Shumway-style delisting return when no clean OTC series exists.

All SEC calls obey the 10 req/s fair-access limit via a simple throttle.
"""

import sys
import time
import re
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from config import TIINGO_API_KEY
except Exception:
    TIINGO_API_KEY = None
from tools.tiingo_cache import get_tiingo_cached

SEC_HEADERS = {"User-Agent": "frakbox_fund research bart.de.lepeleer@gmail.com"}
DELIST_FORMS_25 = {"25", "25-NSE"}
DELIST_FORMS_15 = {"15-12B", "15-12G", "15-15D"}
# EXPM/EXPD = Tiingo pseudo-codes for expunged/expired (delisted) symbols.
OTC_EXCHANGES = {"PINK", "OTC", "OTCMKTS", "GREY", "OTCBB", "OTCQB", "OTCQX", "EXPM", "EXPD"}
# Max gap (days) to accept a null-name OTC candidate purely on endDate proximity.
OTC_ENDDATE_PROXIMITY_DAYS = 150

# Shumway (1997) / Beaver-McNichols-Price style delisting returns, used when no clean
# OTC terminal price is available. Performance delistings (bankruptcy/insolvency) are
# the relevant case for distress cross-sections.
DELIST_RETURN_DEFAULT = -0.55   # conservative blend; NASDAQ perf-delist ~ -0.55
DELIST_RETURN_NYSE_AMEX = -0.30

_last_sec_call = [0.0]


def _sec_throttle():
    dt = time.time() - _last_sec_call[0]
    if dt < 0.12:
        time.sleep(0.12 - dt)
    _last_sec_call[0] = time.time()


def _cik10(cik):
    return str(cik).lstrip("0").zfill(10)


def get_delisting_info(cik):
    """
    Reuse-proof delisting detector from SEC EDGAR submissions.

    Returns dict:
      company_name, current_tickers, current_exchanges,
      delist_date  (first Form 25 / 25-NSE date, ISO str or None),
      dereg_date   (first Form 15-* date, ISO str or None),
      is_delisted  (bool: any Form 25/15 present OR current tickers empty),
      detector     ('form25' | 'form15' | 'empty_tickers' | None)
    """
    url = f"https://data.sec.gov/submissions/CIK{_cik10(cik)}.json"
    _sec_throttle()
    r = requests.get(url, headers=SEC_HEADERS, timeout=20)
    r.raise_for_status()
    j = r.json()
    rec = j.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    dates = rec.get("filingDate", [])
    d25 = sorted(dates[i] for i in range(len(forms)) if forms[i] in DELIST_FORMS_25)
    d15 = sorted(dates[i] for i in range(len(forms)) if forms[i] in DELIST_FORMS_15)
    tickers = j.get("tickers", []) or []
    former_names = [f.get("name") for f in (j.get("formerNames") or []) if f.get("name")]

    # Use the LATEST Form 25 as the terminal delisting (a name may have an old, partial
    # Form 25 in the recent window — e.g. RAD's 2018 vs its 2023 bankruptcy delisting).
    delist_date = d25[-1] if d25 else None
    dereg_date = d15[-1] if d15 else None
    if delist_date:
        detector = "form25"
    elif dereg_date:
        detector = "form15"
    elif not tickers:
        detector = "empty_tickers"
    else:
        detector = None

    return {
        "cik": _cik10(cik),
        "company_name": j.get("name"),
        "former_names": former_names,
        "current_tickers": tickers,
        "current_exchanges": j.get("exchanges", []) or [],
        "delist_date": delist_date,
        "dereg_date": dereg_date,
        "delist_dates": d25,
        "dereg_dates": d15,
        "is_delisted": detector is not None,
        "detector": detector,
    }


def _name_tokens(name):
    if not name:
        return set()
    name = name.upper()
    for junk in ["INC", "CORP", "CORPORATION", "LLC", "LTD", "CO", "COMPANY",
                 "HOLDCO", "HOLDINGS", "GROUP", "PLC", "THE", "NEW", "OLD", ",", "."]:
        name = name.replace(junk, " ")
    return {t for t in re.split(r"\s+", name) if len(t) >= 3}


_META_CACHE_DIR = Path.home() / ".tiingo_cache" / "meta"
_META_MEM = {}


def _tiingo_meta(ticker):
    """Fetch Tiingo ticker metadata, cached to disk. Caches both hits and confirmed
    misses (404) but NOT 429 rate-limit responses, so a throttled lookup can be retried
    once the free-tier hourly allocation resets."""
    if ticker in _META_MEM:
        return _META_MEM[ticker]
    if not TIINGO_API_KEY:
        return None
    import json as _json
    _META_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = _META_CACHE_DIR / f"{ticker}.json"
    if cpath.exists():
        try:
            payload = _json.loads(cpath.read_text())
            meta = payload.get("meta")
            _META_MEM[ticker] = meta
            return meta
        except Exception:
            pass
    meta = None
    try:
        _r = requests.get(f"https://api.tiingo.com/tiingo/daily/{ticker}",
                          params={"token": TIINGO_API_KEY}, timeout=15)
        if _r.status_code == 200:
            meta = _r.json()
        elif _r.status_code == 429:
            return None  # rate limited — do not cache, allow retry later
    except Exception:
        return None
    try:
        cpath.write_text(_json.dumps({"meta": meta}))
    except Exception:
        pass
    _META_MEM[ticker] = meta
    return meta


def _days_between(a, b):
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).days)
    except Exception:
        return 10 ** 9


def resolve_otc_ticker(orig_ticker, names, delist_date=None):
    """
    Resolve the post-delisting OTC terminal ticker for a delisted name.

    The naive orig+'Q' heuristic is brittle (RAD->RADCQ, SI->SICP). We generate several
    candidates and VALIDATE each against Tiingo metadata. A candidate on an OTC venue is
    accepted when EITHER:
      (a) its name shares a meaningful token with the SEC company name OR any SEC former
          name (former names are essential — post-bankruptcy shells get renamed, e.g.
          BBBY's CIK is now "DK-Butterfly" but former "BED BATH & BEYOND" matches BBBYQ); OR
      (b) the candidate has no usable name but its endDate sits within
          OTC_ENDDATE_PROXIMITY_DAYS of the SEC delisting date (e.g. RIDEQ, null name,
          PINK, ended 2023-07-14 vs Form 25 2023-07-27).
    The name-match guard rejects reused tickers (BBBY->"Beyond Inc" fails the match).

    `names` may be a single string or a list of strings (current + former SEC names).
    Returns (otc_ticker, meta) or (None, None).
    """
    if not orig_ticker:
        return None, None
    if isinstance(names, str):
        names = [names]
    o = orig_ticker.upper()
    candidates = [o + "Q", o + "CQ", o + "QQ", o + "Q1", o + "BQ",
                  o[:-1] + "Q" if len(o) > 1 else o + "Q", o + "CP", o]
    seen = set()
    want = set()
    for nm in (names or []):
        want |= _name_tokens(nm)
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        meta = _tiingo_meta(c)
        if not meta:
            continue
        exch = (meta.get("exchangeCode") or "").upper()
        if exch not in OTC_EXCHANGES:
            continue
        cand_tokens = _name_tokens(meta.get("name"))
        name_ok = bool(want & cand_tokens)
        proximity_ok = (not cand_tokens and delist_date and meta.get("endDate")
                        and _days_between(meta["endDate"], delist_date) <= OTC_ENDDATE_PROXIMITY_DAYS)
        if name_ok or proximity_ok:
            return c, meta
    return None, None


def survivorship_free_return(orig_ticker, cik, entry_date, exit_date,
                             entry_price=None, prefer_otc=True):
    """
    Survivorship-free realized return over [entry_date, exit_date] for a name that may
    have delisted inside the window.

    Logic:
      - Look up SEC delisting info by CIK (reuse-proof).
      - If the name did NOT delist in the window: return None (caller uses normal prices).
      - If it delisted in the window:
          * try an OTC terminal price (last close on the resolved + name-validated OTC
            ticker on/after the delist date); realized return = otc_close/entry - 1.
          * else assign a Shumway-style delisting return.
      - entry_price: pre-delisting reference price (close at entry_date). If None and an
        OTC series exists pre-delisting, we cannot reliably anchor, so fall back to the
        assigned delisting return.

    Returns dict: delisted_in_window, delist_date, method, otc_ticker, terminal_price,
                  realized_return, info.
    """
    info = get_delisting_info(cik)
    dd = info["delist_date"] or info["dereg_date"]
    out = {"delisted_in_window": False, "delist_date": dd, "method": None,
           "otc_ticker": None, "terminal_price": None, "realized_return": None,
           "info": info}
    if not dd:
        return out
    if not (entry_date <= dd <= exit_date):
        return out

    out["delisted_in_window"] = True
    names = [info["company_name"]] + info.get("former_names", [])
    otc, meta = (resolve_otc_ticker(orig_ticker, names, dd)
                 if prefer_otc else (None, None))
    if otc and entry_price:
        # last available close on the OTC ticker through exit_date
        df = get_tiingo_cached(otc, dd, exit_date)
        if df is not None and not df.empty:
            term = float(df["Close"].iloc[-1])
            out.update({"method": "otc_terminal", "otc_ticker": otc,
                        "terminal_price": term,
                        "realized_return": term / entry_price - 1.0})
            return out

    # Fallback: assigned delisting return
    exch = (info["current_exchanges"][:1] or [""])[0].upper()
    r = DELIST_RETURN_NYSE_AMEX if "NYSE" in exch or "AMEX" in exch else DELIST_RETURN_DEFAULT
    out.update({"method": "assigned_delisting_return", "realized_return": r})
    return out


if __name__ == "__main__":
    import json
    # Self-validation on the 9-name benchmark set.
    cases = [
        ("PRTY", "0001592058"), ("RAD", "0000084129"), ("SI", "0001312109"),
        ("YELL", "0000716006"), ("SDC", "0001775625"), ("RIDE", "0001759546"),
        ("WE", "0001813756"), ("TUP", "0001008654"), ("BBBY", "0000886158"),
    ]
    n_delist = n_otc = 0
    for tk, cik in cases:
        info = get_delisting_info(cik)
        names = [info["company_name"]] + info.get("former_names", [])
        otc, meta = resolve_otc_ticker(tk, names, info["delist_date"])
        n_delist += int(info["is_delisted"])
        n_otc += int(otc is not None)
        print(f"{tk:5s} CIK={cik} delist={info['delist_date']} detector={info['detector']:14s} "
              f"OTC={str(otc):7s} otc_exch={(meta or {}).get('exchangeCode')}")
    print(f"\nDetector: {n_delist}/{len(cases)} flagged delisted | "
          f"OTC terminal resolved: {n_otc}/{len(cases)}")
