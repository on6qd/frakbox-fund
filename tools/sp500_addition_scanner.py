#!/usr/bin/env python3
"""
S&P 500 Addition Scanner — Daily operational detector.

Detects new S&P 500 addition announcements from multiple sources, then
automatically updates hypothesis 061ae3a8 and sets a trade trigger for
the next market open.

Sources (tried in order):
  1. press.spglobal.com archive — primary, HTML scrape of S&P Dow Jones releases
  2. Wikipedia constituent list — "Date added" column diff against saved state
  3. EDGAR 8-K full-text search — company-filed notices of S&P 500 addition

Usage:
    python tools/sp500_addition_scanner.py
    python tools/sp500_addition_scanner.py --dry-run
    python tools/sp500_addition_scanner.py --force-symbol NVDA 2026-03-23
    python tools/sp500_addition_scanner.py --days 48        # look back N days (default 2)

Schedule: Daily at ~9:15 PM ET via launchd alongside cluster_auto_scanner.

State: SQLite kv_state table (key='sp500_scanner')
Detection log: SQLite scanner_signals table
"""

import sys
import os
import re
import json
import argparse
from datetime import datetime, timedelta

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db as _db

os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HYPOTHESIS_ID = "061ae3a8"

PRESS_ARCHIVE_URL = (
    "https://press.spglobal.com/index.php?keywords=s%26p+500+index&l=30&s=2429"
)

WIKIPEDIA_URL = (
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

EDGAR_HEADERS = {
    "User-Agent": "financial-research-agent admin@research.local",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json",
}

# Title phrases that reliably indicate S&P 500 additions (checked lowercase)
ADDITION_TITLE_KEYWORDS = [
    "set to join s&p 500",
    "to join the s&p 500",
    "added to the s&p 500",
    "will join the s&p 500",
    "s&p 500 changes",
    "s&p 500 index changes",
    "s&p 500 rebalancing",
    "s&p 500 rebalanc",
    "joins the s&p 500",
    "join s&p 500",
]

# All-caps tokens that appear in press releases but are NOT tickers
FALSE_POS = {
    "SP", "ETF", "CEO", "CFO", "COO", "CTO", "EVP", "SVP", "NYSE", "NASDAQ",
    "USD", "USA", "THE", "FOR", "AND", "INC", "LLC", "LTD", "PLC", "CORP",
    "SPX", "SPY", "QQQ", "INDEX", "SPDJI", "DJIA", "ESG", "NA", "TBD",
    "PR", "US", "IT", "AI", "ML",
}

# Regex: NYSE: TICK  |  (NYSE: TICK)  |  Nasdaq: TICK
TICKER_RE = re.compile(
    r"""
    (?:
        \b(?:NYSE|Nasdaq|NASDAQ|NYSE\s*American|AMEX)\s*:\s*([A-Z]{1,5})\b
        |
        \((?:NYSE|Nasdaq|NASDAQ|NYSE\s*American|AMEX)\s*:\s*([A-Z]{1,5})\)
        |
        \b(?:NYSE|Nasdaq|NASDAQ|NYSE\s*American|AMEX)\s*\u2013\s*([A-Z]{1,5})\b
    )
    """,
    re.VERBOSE,
)

EFFECTIVE_DATE_RE = re.compile(
    r"""
    (?:
        effective\s+(?:before|prior\s+to)\s+the\s+open(?:\s+of\s+trading)?\s+on
        | effective\s+after\s+the\s+close\s+(?:of\s+trading\s+)?on
        | effective\s+on
        | prior\s+to\s+the\s+open\s+of\s+trading\s+on
        | before\s+the\s+open\s+of\s+trading\s+on
        | changes\s+(?:will\s+)?(?:be\s+)?effective\s+(?:before\s+the\s+open\s+of\s+trading\s+)?on
        | will\s+be\s+effective\s+before\s+the\s+open\s+of\s+trading\s+on
    )
    \s+
    (?:
        (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+
    )?
    (?P<date>
        (?:January|February|March|April|May|June|July|August|September|October|November|December)
        \s+\d{1,2},?\s+\d{4}
        |
        \d{1,2}/\d{1,2}/\d{4}
        |
        \d{4}-\d{2}-\d{2}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    _db.init_db()
    return _db.get_state('sp500_scanner') or {
        "last_check": None,
        "seen_press_links": [],
        "wikipedia_tickers": [],
        "wikipedia_dates": {},
        "triggered_tickers": [],
        "last_triggered_hypothesis": None,
    }


def save_state(state: dict, dry_run: bool = False) -> None:
    if dry_run:
        return
    _db.init_db()
    _db.set_state('sp500_scanner', state)


# ---------------------------------------------------------------------------
# Detection log
# ---------------------------------------------------------------------------

def log_detection(record: dict, dry_run: bool = False) -> None:
    """Append one detection event to the SQLite scanner_signals table."""
    if dry_run:
        print(f"  [DRY-RUN] Would log: {json.dumps(record)}")
        return
    _db.init_db()
    _db.append_scanner_signal('sp500_additions', record)


# ---------------------------------------------------------------------------
# Ticker helpers
# ---------------------------------------------------------------------------

def extract_tickers(text: str) -> list:
    tickers = set()
    for m in TICKER_RE.finditer(text):
        for grp in m.groups():
            if grp:
                tickers.add(grp.upper())
    return sorted(tickers - FALSE_POS)


def parse_effective_date(text: str):
    m = EFFECTIVE_DATE_RE.search(text)
    if not m:
        return None
    raw = m.group("date").strip().rstrip(",")
    for fmt in ("%B %d, %Y", "%B %d %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def date_from_url(url: str) -> str:
    m = re.search(r"/(\d{4}-\d{2}-\d{2})-", url)
    return m.group(1) if m else ""


def is_addition_title(title: str) -> bool:
    t = title.lower().replace("&amp;", "&")
    for kw in ADDITION_TITLE_KEYWORDS:
        if kw in t:
            return True
    if "s&p 500" in t and any(
        k in t for k in ("change", "recompos", "rebalanc", "addition", "join", "replac", "member")
    ):
        return True
    return False


def is_real_ticker(ticker: str) -> bool:
    """
    Verify ticker is real and tradeable on US markets using yfinance.
    Returns True if we can fetch recent price data.
    """
    try:
        from tools.yfinance_utils import get_current_price
        price = get_current_price(ticker)
        if price and price > 0:
            return True
    except Exception:
        pass
    # Fallback: try raw yfinance with a short period
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        mktcap = getattr(info, "market_cap", None)
        if mktcap and mktcap > 0:
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# HTML table parser (for S&P Global structured press releases)
# ---------------------------------------------------------------------------

def _cell_text(cell_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", cell_html)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&#\d+;", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_press_release_table(html: str) -> list:
    """
    Parse the structured table in S&P Global press releases.
    Columns: Effective Date | Index Name | Action | Company Name | Ticker | GICS Sector
    Returns rows where Action=='Addition' and Index contains 'S&P 500'.
    """
    rows = []
    tr_re = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    td_re = re.compile(r"<td\b[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)

    for tr_match in tr_re.finditer(html):
        row_html = tr_match.group(1)
        cells = [_cell_text(td.group(1)) for td in td_re.finditer(row_html)]
        if len(cells) < 5:
            continue

        date_raw, index_name, action, company, ticker = (
            cells[0], cells[1], cells[2], cells[3], cells[4]
        )

        if "addition" not in action.lower():
            continue
        if "s&p 500" not in index_name.lower():
            continue

        effective_date = None
        date_clean = date_raw.rstrip(",").strip()
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
            try:
                effective_date = datetime.strptime(date_clean, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass

        ticker_clean = re.sub(r"[^A-Z]", "", ticker.upper())
        if not ticker_clean or ticker_clean in FALSE_POS or len(ticker_clean) > 5:
            continue

        rows.append({
            "effective_date": effective_date,
            "effective_date_raw": date_raw,
            "index": index_name,
            "action": action,
            "company": company,
            "ticker": ticker_clean,
        })

    return rows


def fetch_press_release(url: str) -> dict:
    """
    Fetch a single press release. Uses structured table parser first,
    falls back to regex.
    Returns {"additions": [...], "tickers": [...], "effective_date": str|None}
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {"additions": [], "tickers": [], "effective_date": None}

        html = resp.text

        # Primary: structured table
        additions = parse_press_release_table(html)
        if additions:
            tickers = [a["ticker"] for a in additions]
            dates = [a["effective_date"] for a in additions if a["effective_date"]]
            effective_date = min(dates) if dates else None
            return {"additions": additions, "tickers": tickers, "effective_date": effective_date}

        # Fallback: regex
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\s+", " ", text)

        idx = text.lower().find("s&p 500")
        snippet = text[max(0, idx - 300): idx + 3000] if idx >= 0 else text[:4000]

        tickers = extract_tickers(snippet)
        effective_date = parse_effective_date(snippet)
        return {"additions": [], "tickers": tickers, "effective_date": effective_date}

    except Exception as e:
        return {"additions": [], "tickers": [], "effective_date": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Source 1: press.spglobal.com archive
# ---------------------------------------------------------------------------

def check_press_archive(days_back: int = 2, seen_links: set = None) -> tuple:
    """
    Scrape press.spglobal.com for recent S&P 500 addition announcements.
    Returns (new_items, error_str_or_None).
    Each item has: title, link, pub_date, tickers, effective_date, additions, source.
    """
    if seen_links is None:
        seen_links = set()

    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(PRESS_ARCHIVE_URL, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code} from press.spglobal.com"
        html = resp.text
    except requests.RequestException as e:
        return None, f"Network error: {e}"

    link_re = re.compile(
        r'<a\s+[^>]*href="((?:https://press\.spglobal\.com)?/\d{4}-\d{2}-\d{2}-[^"]+)"[^>]*>'
        r'\s*(.*?)\s*</a>',
        re.DOTALL | re.IGNORECASE,
    )

    new_items = []
    seen_local = set()

    for m in link_re.finditer(html):
        href = m.group(1).strip()
        raw_title = re.sub(r"<[^>]+>", "", m.group(2))
        raw_title = re.sub(r"&amp;", "&", raw_title)
        raw_title = re.sub(r"&#\d+;", "", raw_title)
        raw_title = re.sub(r"\s+", " ", raw_title).strip()

        if not raw_title or not is_addition_title(raw_title):
            continue

        if not href.startswith("http"):
            href = "https://press.spglobal.com" + href

        pub_date = date_from_url(href)
        if pub_date and pub_date < cutoff:
            continue  # Too old

        if href in seen_links or href in seen_local:
            continue
        seen_local.add(href)

        # Enrich by fetching the full press release
        parsed = fetch_press_release(href)

        new_items.append({
            "title": raw_title,
            "link": href,
            "pub_date": pub_date,
            "source": "press.spglobal.com",
            "tickers": parsed["tickers"],
            "effective_date": parsed["effective_date"],
            "additions": parsed.get("additions", []),
        })

    return new_items, None


# ---------------------------------------------------------------------------
# Source 2: Wikipedia S&P 500 constituent list
# ---------------------------------------------------------------------------

def check_wikipedia(prev_tickers: dict, is_first_run: bool = False) -> tuple:
    """
    Fetch Wikipedia's S&P 500 list and compare against the previously saved
    ticker->date_added mapping. New tickers are potential recent additions.

    On first run (is_first_run=True), only save the snapshot — do not flag any
    tickers as "new" because the entire 503-company list would look like additions.
    On subsequent runs, flag tickers that are both new AND have a date_added within
    the past 7 days (matching the S&P announcement-to-effective timeline).

    Returns (current_snapshot_dict, new_additions_list, error_str_or_None).
    Each new_addition: {"ticker": str, "company": str, "date_added": str, "source": str}
    """
    try:
        resp = requests.get(WIKIPEDIA_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None, f"Wikipedia HTTP {resp.status_code}"
        html = resp.text
    except requests.RequestException as e:
        return None, f"Wikipedia network error: {e}"

    # Find the first <table class="wikitable sortable"> in the page
    # Columns: Symbol | Security | GICS Sector | GICS Sub-Industry | HQ Location | CIK | Founded | Date added
    table_re = re.compile(
        r'<table[^>]+wikitable[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE,
    )

    table_match = table_re.search(html)
    if not table_match:
        return None, "Wikipedia: could not find wikitable"

    table_html = table_match.group(1)

    tr_re = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    td_re = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)

    # Find the header row to determine column positions
    header_cols = {}
    rows = list(tr_re.finditer(table_html))
    if not rows:
        return None, "Wikipedia: no rows in table"

    for row_m in rows[:3]:  # header is in first few rows
        cells = [_cell_text(td.group(1)) for td in td_re.finditer(row_m.group(1))]
        for i, c in enumerate(cells):
            c_lower = c.lower()
            if "symbol" in c_lower or "ticker" in c_lower:
                header_cols["symbol"] = i
            elif "security" in c_lower or "company" in c_lower or "name" in c_lower:
                header_cols.setdefault("company", i)
            elif "date added" in c_lower or "date_added" in c_lower:
                header_cols["date_added"] = i
        if "symbol" in header_cols and "date_added" in header_cols:
            break

    if "symbol" not in header_cols or "date_added" not in header_cols:
        return None, f"Wikipedia: couldn't find Symbol/Date added columns. Found: {header_cols}"

    sym_col = header_cols["symbol"]
    date_col = header_cols["date_added"]
    name_col = header_cols.get("company", 1)

    current = {}  # ticker -> {"date_added": str, "company": str}

    for row_m in rows[1:]:  # skip header
        cells = [_cell_text(td.group(1)) for td in td_re.finditer(row_m.group(1))]
        if len(cells) <= max(sym_col, date_col):
            continue

        raw_ticker = cells[sym_col].strip()
        # Wikipedia sometimes has preferred share suffixes like "BRK.B" -> "BRK-B"
        # Normalize to standard market ticker format
        ticker = re.sub(r"[^A-Z0-9]", "", raw_ticker.upper().replace(".", "-").replace("-", ""))
        # Re-add the hyphen for preferred shares: KIMPRL -> KIM-PL etc.
        # Most S&P 500 tickers are 1-5 uppercase letters only; skip if looks wrong
        if not ticker or len(ticker) > 6 or not re.match(r"^[A-Z]{1,5}$", ticker):
            # Try stripping suffix after dot
            plain = raw_ticker.split(".")[0].upper()
            if re.match(r"^[A-Z]{1,5}$", plain):
                ticker = plain
            else:
                continue

        company = cells[name_col] if len(cells) > name_col else ""
        date_added = cells[date_col] if len(cells) > date_col else ""

        current[ticker] = {"date_added": date_added, "company": company}

    if not current:
        return None, [], "Wikipedia: parsed 0 tickers — table format may have changed"

    # On first run, just save the snapshot and report nothing new.
    # This prevents flagging all 503 current S&P 500 members as "additions".
    if is_first_run:
        return current, [], None

    # Detect new tickers (present now but not in previous snapshot)
    new_additions = []
    today = datetime.now().date()
    # Only flag additions where Wikipedia's date_added is within the past 7 days.
    # S&P 500 announcements are effective within 1-3 weeks; Wikipedia is updated
    # quickly after announcement. A 7-day window catches genuine recent additions
    # without triggering on stale additions that weren't in our snapshot yet.
    recency_cutoff = today - timedelta(days=7)

    for ticker, info in current.items():
        if ticker in (prev_tickers or {}):
            continue  # Already in our snapshot — not a new addition

        # Parse the date_added to see if it's recent
        date_added_str = info["date_added"]
        added_date = None
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                added_date = datetime.strptime(date_added_str.strip(), fmt).date()
                break
            except (ValueError, AttributeError):
                pass

        if added_date is None:
            # Can't parse date — skip rather than false-trigger
            continue

        if added_date < recency_cutoff:
            # Ticker is new to our snapshot but was added more than 7 days ago —
            # it was probably in the index before our first run, just not in our state.
            continue

        new_additions.append({
            "ticker": ticker,
            "company": info["company"],
            "date_added": date_added_str,
            "source": "Wikipedia",
        })

    return current, new_additions, None


# ---------------------------------------------------------------------------
# Source 3: EDGAR 8-K full-text search
# ---------------------------------------------------------------------------

def check_edgar_8k(days_back: int = 3) -> tuple:
    """
    Search EDGAR full-text for 8-K filings where companies announce S&P 500 addition.
    Returns (items, error_str_or_None).
    Each item: {"ticker": str, "company": str, "date": str, "source": str}
    """
    today = datetime.now()
    start_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    phrases = [
        '"added to the S%26P 500"',
        '"will join the S%26P 500"',
        '"set to join the S%26P 500"',
        '"joins the S%26P 500"',
        '"joining the S%26P 500"',
        '"S%26P 500 Index effective"',
    ]
    query = "+OR+".join(phrases)
    url = (
        f"https://efts.sec.gov/LATEST/search-index?q={query}"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}&forms=8-K"
    )

    try:
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None, f"EDGAR HTTP {resp.status_code}"

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        items = []

        for hit in hits[:20]:
            src = hit.get("_source", {})
            file_date = src.get("file_date", "")[:10]
            display_names = src.get("display_names", [])

            ticker = None
            company = None
            for dn in display_names:
                matches = re.findall(r'\(([A-Z]{1,5})\)', dn)
                if matches:
                    ticker = matches[0]
                    # Extract company name before the first parenthetical
                    company_match = re.match(r'^([^(]+)', dn)
                    company = company_match.group(1).strip() if company_match else dn
                    break

            if not ticker:
                entity = src.get("entity_name", "")
                ticker = None
                company = entity or "Unknown"

            if ticker and ticker not in FALSE_POS:
                items.append({
                    "ticker": ticker,
                    "company": company or "Unknown",
                    "date": file_date,
                    "source": "EDGAR 8-K",
                })

        return items, None

    except requests.RequestException as e:
        return None, f"EDGAR network error: {e}"
    except Exception as e:
        return None, f"EDGAR unexpected error: {e}"


# ---------------------------------------------------------------------------
# Pre-announce run-up: INFORMATIONAL telemetry only (NOT a trade gate)
# ---------------------------------------------------------------------------

# History of this metric: a 2026-06-11 guardrail assumed that a name which had
# already rallied hard INTO the announcement had front-run the inclusion premium
# and would fade, and SKIPPED any addition with >20% pre-announce 5d abnormal
# run-up. MRVL (+31% pre-announce, Q2 2026) was tagged the canonical casualty.
#
# The Q2 2026 OOS REJECTED that premise (resolved 2026-06-22): MRVL did NOT fade
# — it returned +16.6% abnormal over the 9d hold (the single biggest OOS winner),
# while FLEX (only +3% pre-run-up) was the -4.1% miss. The pre5->post5 relation
# is mildly POSITIVE in every sample we have (historical n=29 pearson +0.23;
# recent n=6 pearson +0.44), the opposite sign to the fade theory. The guardrail
# would have skipped exactly the winner, so it is removed.
#
# The base signal is validated as "trade ALL quarterly-rebalance additions long,
# no cherry-picking". We still COMPUTE the run-up and surface it as a note for
# manual review of out-of-distribution cases, but it never blocks a trigger.
# See knowledge: sp500_index_addition_runup_guardrail_falsified_2026_06_22.
RUNUP_NOTE_THRESHOLD_PCT = 20.0


def pre_announce_abnormal_runup(symbol: str, announcement_date: str, lookback: int = 5):
    """5d abnormal run-up (symbol minus SPY) from close[D-lookback] to close[D],
    where D is the after-hours announcement date. Returns (pct, status)."""
    try:
        import pandas as pd
        from tools.yfinance_utils import get_close_prices
        d = pd.Timestamp(announcement_date)
        start = (d - pd.Timedelta(days=lookback + 25)).strftime("%Y-%m-%d")
        end = (d + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        sym = get_close_prices(symbol, start, end)
        spy = get_close_prices("SPY", start, end)
        if sym is None or spy is None:
            return None, "fetch_none"
        if hasattr(sym, "columns"):
            sym = sym.iloc[:, 0]
        if hasattr(spy, "columns"):
            spy = spy.iloc[:, 0]
        sym = sym[sym.index <= d]
        spy = spy[spy.index <= d]
        if len(sym) < lookback + 1 or len(spy) < lookback + 1:
            return None, "insufficient"
        sym_ret = (float(sym.iloc[-1]) / float(sym.iloc[-(lookback + 1)]) - 1) * 100
        spy_ret = (float(spy.iloc[-1]) / float(spy.iloc[-(lookback + 1)]) - 1) * 100
        return round(sym_ret - spy_ret, 2), "ok"
    except Exception as e:
        return None, f"err:{e}"


# ---------------------------------------------------------------------------
# Hypothesis updater
# ---------------------------------------------------------------------------

def update_hypothesis(ticker: str, announcement_date: str, effective_date: str,
                      source: str, dry_run: bool = False) -> bool:
    """
    Update hypothesis 061ae3a8: set expected_symbol, trigger, position size, stop-loss.
    Idempotent: if symbol already set to this ticker, skips.
    Returns True if updated (or would update in dry-run), False if already set.
    """
    import research

    hypotheses = research.load_hypotheses()
    target = None
    for h in hypotheses:
        if h.get("id", "").startswith(HYPOTHESIS_ID):
            target = h
            break

    if target is None:
        print(f"  ERROR: hypothesis {HYPOTHESIS_ID} not found.")
        return False

    current_status = target.get("status", "")
    if current_status not in ("pending",):
        print(f"  SKIP: hypothesis status is '{current_status}' (not 'pending'). "
              f"Cannot set trigger on a non-pending hypothesis.")
        return False

    current_symbol = target.get("expected_symbol", "TBD")
    if current_symbol == ticker:
        print(f"  SKIP (idempotent): expected_symbol already set to {ticker}.")
        return False

    if current_symbol != "TBD":
        print(f"  WARNING: expected_symbol is currently '{current_symbol}' (not TBD). "
              f"Will overwrite with '{ticker}'.")

    # Pre-announce run-up: informational only. The 2026-06-11 fade guardrail was
    # falsified by the Q2 2026 OOS (MRVL +31% pre-run-up -> +16.6% post, the
    # biggest winner). We log the run-up but NEVER skip — trade ALL quarterly
    # additions long.
    runup, runup_status = pre_announce_abnormal_runup(ticker, announcement_date)
    if runup is not None:
        note = ""
        if runup > RUNUP_NOTE_THRESHOLD_PCT:
            note = (f" [out-of-distribution >{RUNUP_NOTE_THRESHOLD_PCT:.0f}%; "
                    f"flag for manual review — NOT a skip]")
        print(f"  Pre-announce 5d abnormal run-up: {runup:+.1f}%{note}")
    else:
        print(f"  Note: could not compute pre-announce run-up ({runup_status}); "
              f"proceeding (informational metric only).")

    print(f"  Updating hypothesis {target['id']}:")
    print(f"    expected_symbol: {current_symbol} -> {ticker}")
    print(f"    trigger: next_market_open")
    print(f"    trigger_position_size: 5000")
    print(f"    trigger_stop_loss_pct: 15")

    if not dry_run:
        target["expected_symbol"] = ticker
        target["trigger"] = "next_market_open"
        target["trigger_position_size"] = 5000
        # CORRECTED 2026-06-12 (Q2 MRVL/FLEX post-mortem): the validated edge is a
        # stopless 14d CLOSE-to-close hold. check_stop_losses() runs every 2 min on
        # Alpaca's intraday current_price, so a tight 10% stop fires on intraday
        # drawdown even when the close-to-close signal is intact — that whipsaw booked
        # ~-9.5% on both MRVL and FLEX. Use a WIDE 15% catastrophe-only stop (cannot be
        # None: trade_loop enforces MIN_STOP_LOSS_PCT). See knowledge entry
        # sp500_index_addition_stop_whipsaw_fix_2026_06_12.
        target["trigger_stop_loss_pct"] = 15
        target["_sp500_addition_meta"] = {
            "announced": announcement_date,
            "effective": effective_date,
            "source": source,
            "scanner_set_at": datetime.now().isoformat(),
        }
        import db as _db
        _db.save_hypothesis(target)
        print(f"  Hypothesis saved. trade_loop.py will execute at next market open.")
    else:
        print(f"  [DRY-RUN] No changes written.")

    return True


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_scan(days_back: int = 2, dry_run: bool = False, verbose: bool = True) -> list:
    """
    Full scan. Returns list of detected addition dicts (may be empty).
    """

    def log(msg=""):
        if verbose:
            print(msg)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"[{now_str}] === S&P 500 Addition Scanner ===")
    log(f"Hypothesis: {HYPOTHESIS_ID}  |  Expected: +5% at 5d  |  Confidence: 9")
    if dry_run:
        log("[DRY-RUN MODE — no changes will be written]")
    log()

    state = load_state()
    seen_links = set(state.get("seen_press_links", []))
    prev_wiki_tickers = state.get("wikipedia_dates", {})
    triggered_tickers = set(state.get("triggered_tickers", []))
    # On first run (no prior Wikipedia snapshot), don't flag anything from Wikipedia.
    # Just save the snapshot so future runs can do a proper diff.
    is_first_wiki_run = not bool(prev_wiki_tickers)

    detected = []  # all confirmed additions found this run

    # ------------------------------------------------------------------
    # Source 1: press.spglobal.com
    # ------------------------------------------------------------------
    log(f"[1/3] Checking press.spglobal.com (last {days_back} days)...")
    press_items, press_err = check_press_archive(days_back=days_back, seen_links=seen_links)

    if press_err:
        log(f"  FAILED: {press_err}")
    elif press_items is None:
        log("  No items returned.")
    elif not press_items:
        log("  No new S&P 500 addition press releases found.")
    else:
        log(f"  Found {len(press_items)} new press release(s):")
        for item in press_items:
            log(f"  [{item['pub_date']}] {item['title'][:80]}")
            additions = item.get("additions", [])
            if additions:
                for a in additions:
                    log(f"    + Addition: {a['ticker']} ({a['company']}) eff. {a.get('effective_date', '?')}")
                    detected.append({
                        "ticker": a["ticker"],
                        "company": a.get("company", ""),
                        "announced": item["pub_date"],
                        "effective": a.get("effective_date"),
                        "source": "press.spglobal.com",
                        "press_release": item["link"],
                    })
            else:
                tickers = item.get("tickers", [])
                log(f"    Tickers (regex): {tickers or '(none)'}")
                log(f"    Effective: {item.get('effective_date', '(unknown)')}")
                for t in tickers:
                    detected.append({
                        "ticker": t,
                        "company": "",
                        "announced": item["pub_date"],
                        "effective": item.get("effective_date"),
                        "source": "press.spglobal.com (regex)",
                        "press_release": item["link"],
                    })

            # Mark link as seen
            seen_links.add(item["link"])

    # ------------------------------------------------------------------
    # Source 2: Wikipedia diff
    # ------------------------------------------------------------------
    log("[2/3] Checking Wikipedia S&P 500 constituent list...")
    if is_first_wiki_run:
        log("  First run — saving baseline snapshot (no diff possible yet).")
    current_wiki, wiki_new, wiki_err = check_wikipedia(
        prev_wiki_tickers, is_first_run=is_first_wiki_run
    )

    if wiki_err:
        log(f"  FAILED: {wiki_err}")
    elif not wiki_new:
        if current_wiki:
            log(f"  No new tickers detected vs. snapshot ({len(current_wiki)} tickers tracked).")
        else:
            log("  No data returned.")
    else:
        log(f"  {len(wiki_new)} new ticker(s) vs. previous snapshot:")
        for item in wiki_new:
            log(f"    + {item['ticker']} ({item['company']}) — added: {item['date_added']}")
            detected.append({
                "ticker": item["ticker"],
                "company": item["company"],
                "announced": item["date_added"],
                "effective": None,
                "source": "Wikipedia",
                "press_release": WIKIPEDIA_URL,
            })

    # ------------------------------------------------------------------
    # Source 3: EDGAR 8-K
    # ------------------------------------------------------------------
    log(f"[3/3] Checking EDGAR 8-K filings (last {days_back} days)...")
    edgar_items, edgar_err = check_edgar_8k(days_back=days_back)

    if edgar_err:
        log(f"  FAILED: {edgar_err}")
    elif not edgar_items:
        log("  No S&P 500 addition 8-K filings found.")
    else:
        log(f"  Found {len(edgar_items)} 8-K(s):")
        for item in edgar_items:
            log(f"    {item['date']} | {item['ticker']} | {item['company']}")
            detected.append({
                "ticker": item["ticker"],
                "company": item["company"],
                "announced": item["date"],
                "effective": None,
                "source": "EDGAR 8-K",
                "press_release": "",
            })

    log()

    # ------------------------------------------------------------------
    # Deduplicate detected additions
    # ------------------------------------------------------------------
    seen_tickers_this_run = {}
    deduped = []
    for d in detected:
        t = d["ticker"]
        if t not in seen_tickers_this_run:
            seen_tickers_this_run[t] = d
            deduped.append(d)
        else:
            # Prefer press.spglobal.com over others; update effective date if found
            existing = seen_tickers_this_run[t]
            if "press.spglobal.com" in d["source"] and "press.spglobal.com" not in existing["source"]:
                seen_tickers_this_run[t] = d
                deduped = [seen_tickers_this_run[tt] for tt in seen_tickers_this_run]
            elif not existing.get("effective") and d.get("effective"):
                existing["effective"] = d["effective"]

    detected = deduped

    if not detected:
        log("No new S&P 500 additions detected.")
        state["last_check"] = datetime.now().isoformat()
        state["seen_press_links"] = sorted(seen_links)
        if current_wiki:
            state["wikipedia_tickers"] = sorted(current_wiki.keys())
            state["wikipedia_dates"] = {t: v["date_added"] for t, v in current_wiki.items()}
        save_state(state, dry_run=dry_run)
        return []

    # ------------------------------------------------------------------
    # Filter already-triggered tickers
    # ------------------------------------------------------------------
    new_detections = [d for d in detected if d["ticker"] not in triggered_tickers]
    already_triggered = [d for d in detected if d["ticker"] in triggered_tickers]

    if already_triggered:
        log(f"Already triggered (skipping): {[d['ticker'] for d in already_triggered]}")

    if not new_detections:
        log("All detected additions already processed. Nothing to do.")
        state["last_check"] = datetime.now().isoformat()
        state["seen_press_links"] = sorted(seen_links)
        if current_wiki:
            state["wikipedia_tickers"] = sorted(current_wiki.keys())
            state["wikipedia_dates"] = {t: v["date_added"] for t, v in current_wiki.items()}
        save_state(state, dry_run=dry_run)
        return detected

    # ------------------------------------------------------------------
    # Verify each ticker is real, then update hypothesis for the first one
    # ------------------------------------------------------------------
    log("=== DETECTION RESULTS ===")

    for d in new_detections:
        ticker = d["ticker"]
        log()
        log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] S&P 500 ADDITION DETECTED: {ticker}")
        log(f"  Announcement date: {d['announced']}")
        log(f"  Effective date:    {d['effective'] or 'unknown'}")
        log(f"  Source:            {d['source']}")
        if d.get("press_release"):
            log(f"  Press release:     {d['press_release']}")
        if d.get("company"):
            log(f"  Company:           {d['company']}")

        # Verify ticker is real
        log(f"  Verifying ticker {ticker}...")
        valid = is_real_ticker(ticker)
        if not valid:
            log(f"  WARNING: Could not verify {ticker} as tradeable. Skipping hypothesis update.")
            log(f"           Review manually and use --force-symbol {ticker} {d['announced']} if confirmed.")
            d["verified"] = False
            continue

        d["verified"] = True
        log(f"  Ticker {ticker} verified as tradeable.")

        # Update hypothesis
        log(f"  Action: Hypothesis {HYPOTHESIS_ID} updated, trigger set for next_market_open")
        updated = update_hypothesis(
            ticker=ticker,
            announcement_date=d["announced"],
            effective_date=d["effective"] or "",
            source=d["source"],
            dry_run=dry_run,
        )

        if updated or dry_run:
            if not dry_run:
                # Add to triggered set BEFORE logging so state is consistent even if log write fails
                triggered_tickers.add(ticker)

            # Log the detection
            log_record = {
                "timestamp": datetime.now().isoformat(),
                "ticker": ticker,
                "company": d.get("company", ""),
                "announced": d["announced"],
                "effective": d["effective"],
                "source": d["source"],
                "press_release": d.get("press_release", ""),
                "hypothesis_id": HYPOTHESIS_ID,
                "trigger": "next_market_open",
                "dry_run": dry_run,
            }
            log_detection(log_record, dry_run=dry_run)

            # For this hypothesis, only set one trigger at a time.
            # If multiple additions are announced simultaneously (quarterly rebalance),
            # the user can run --force-symbol for subsequent ones after the first trade closes.
            if len(new_detections) > 1:
                log()
                log(f"  NOTE: {len(new_detections) - 1} additional addition(s) detected "
                    f"({[x['ticker'] for x in new_detections if x['ticker'] != ticker]}). "
                    f"Only one trade trigger can be active at a time. After the first "
                    f"trade completes, run --force-symbol for the next one.")
            break

    # ------------------------------------------------------------------
    # Save state
    # ------------------------------------------------------------------
    state["last_check"] = datetime.now().isoformat()
    state["seen_press_links"] = sorted(seen_links)
    state["triggered_tickers"] = sorted(triggered_tickers)
    if current_wiki:
        state["wikipedia_tickers"] = sorted(current_wiki.keys())
        state["wikipedia_dates"] = {t: v["date_added"] for t, v in current_wiki.items()}
    save_state(state, dry_run=dry_run)

    return detected


# ---------------------------------------------------------------------------
# --force-symbol handler
# ---------------------------------------------------------------------------

def force_symbol(ticker: str, announcement_date: str, dry_run: bool = False) -> None:
    """
    Manually trigger a known addition without going through source scraping.
    Used when announcement is confirmed but scraping missed it.
    """
    ticker = ticker.upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] FORCE-SYMBOL: {ticker} (announced {announcement_date})")

    # Verify ticker
    print(f"  Verifying {ticker}...")
    valid = is_real_ticker(ticker)
    if not valid:
        print(f"  WARNING: {ticker} not verified as tradeable via yfinance.")
        resp = input("  Continue anyway? [y/N]: ").strip().lower()
        if resp != "y":
            print("  Aborted.")
            return

    # Update hypothesis
    updated = update_hypothesis(
        ticker=ticker,
        announcement_date=announcement_date,
        effective_date="",
        source="manual --force-symbol",
        dry_run=dry_run,
    )

    if updated or dry_run:
        state = load_state()
        triggered = set(state.get("triggered_tickers", []))
        if not dry_run:
            triggered.add(ticker)
        state["triggered_tickers"] = sorted(triggered)
        save_state(state, dry_run=dry_run)

        log_detection({
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "announced": announcement_date,
            "effective": None,
            "source": "manual --force-symbol",
            "hypothesis_id": HYPOTHESIS_ID,
            "trigger": "next_market_open",
            "dry_run": dry_run,
        }, dry_run=dry_run)

        print(f"  Done. trade_loop.py will execute at next market open.")


# ---------------------------------------------------------------------------
# Historical test
# ---------------------------------------------------------------------------

def test_historical(year: int = 2025, dry_run: bool = True) -> None:
    """
    Test scanner against a known historical case by checking Wikipedia for a
    company added in the given year.
    """
    print(f"\n=== Historical test: looking for S&P 500 additions in {year} ===")
    print("Fetching Wikipedia constituent list...")

    # We'll check the current Wikipedia list for companies with date_added in the target year
    try:
        resp = requests.get(WIKIPEDIA_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  FAILED: HTTP {resp.status_code}")
            return
        html = resp.text
    except Exception as e:
        print(f"  FAILED: {e}")
        return

    table_re = re.compile(
        r'<table[^>]+wikitable[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE,
    )
    table_match = table_re.search(html)
    if not table_match:
        print("  Could not find wikitable on Wikipedia.")
        return

    table_html = table_match.group(1)
    tr_re = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    td_re = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)

    # Find header
    header_cols = {}
    rows = list(tr_re.finditer(table_html))
    for row_m in rows[:3]:
        cells = [_cell_text(td.group(1)) for td in td_re.finditer(row_m.group(1))]
        for i, c in enumerate(cells):
            c_lower = c.lower()
            if "symbol" in c_lower or "ticker" in c_lower:
                header_cols["symbol"] = i
            elif "security" in c_lower or "company" in c_lower or "name" in c_lower:
                header_cols.setdefault("company", i)
            elif "date added" in c_lower:
                header_cols["date_added"] = i
        if "symbol" in header_cols and "date_added" in header_cols:
            break

    if "symbol" not in header_cols or "date_added" not in header_cols:
        print(f"  Could not find required columns. Found: {header_cols}")
        return

    sym_col = header_cols["symbol"]
    date_col = header_cols["date_added"]
    name_col = header_cols.get("company", 1)

    year_additions = []
    for row_m in rows[1:]:
        cells = [_cell_text(td.group(1)) for td in td_re.finditer(row_m.group(1))]
        if len(cells) <= max(sym_col, date_col):
            continue
        ticker = re.sub(r"[^A-Z]", "", cells[sym_col].upper())
        if not ticker or len(ticker) > 5:
            plain = cells[sym_col].split(".")[0].upper()
            ticker = plain if re.match(r"^[A-Z]{1,5}$", plain) else None
        if not ticker:
            continue
        date_str = cells[date_col].strip()
        if str(year) in date_str:
            company = cells[name_col] if len(cells) > name_col else ""
            year_additions.append({"ticker": ticker, "company": company, "date_added": date_str})

    if not year_additions:
        print(f"  No additions found with '{year}' in date_added column.")
        print("  This may mean Wikipedia's table doesn't have date_added for all entries,")
        print("  or the column format changed.")
        return

    print(f"  Found {len(year_additions)} addition(s) from {year}:")
    for a in year_additions[:10]:
        print(f"    {a['ticker']:6s} | {a['date_added']:20s} | {a['company']}")
    if len(year_additions) > 10:
        print(f"    ... and {len(year_additions) - 10} more")

    # Test: use the first one as a "detected" addition
    first = year_additions[0]
    print(f"\n  Simulating detection of: {first['ticker']} ({first['company']})")
    print(f"  (This is a historical case — not setting a real trigger)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="S&P 500 Addition Scanner — detects announcements and sets trade triggers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/sp500_addition_scanner.py                  # normal daily scan (last 2 days)
  python tools/sp500_addition_scanner.py --dry-run        # see what would happen, no writes
  python tools/sp500_addition_scanner.py --days 7         # look back 7 days
  python tools/sp500_addition_scanner.py --force-symbol NVDA 2026-03-23
  python tools/sp500_addition_scanner.py --test-historical 2025
        """,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without writing any changes.",
    )
    parser.add_argument(
        "--days", type=int, default=2,
        help="How many days back to check press releases (default: 2).",
    )
    parser.add_argument(
        "--force-symbol", nargs=2, metavar=("TICKER", "DATE"),
        help="Manually set a known addition. Example: --force-symbol NVDA 2026-03-23",
    )
    parser.add_argument(
        "--test-historical", type=int, metavar="YEAR", default=None,
        help="List Wikipedia additions from a given year (e.g., 2025) to verify parsing.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress informational output (errors still print).",
    )

    args = parser.parse_args()

    if args.force_symbol:
        ticker, date_str = args.force_symbol
        force_symbol(ticker=ticker, announcement_date=date_str, dry_run=args.dry_run)
        return

    if args.test_historical is not None:
        test_historical(year=args.test_historical, dry_run=True)
        return

    results = run_scan(
        days_back=args.days,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )

    if not results:
        if not args.quiet:
            print("No new additions detected.")
        sys.exit(0)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
