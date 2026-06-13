#!/usr/bin/env python3
"""8-K Item 4.01 (Changes in Registrant's Certifying Accountant) scanner and backtester.

Companies must file an 8-K within 4 business days of a change in auditor.
Item 4.01 covers both dismissals and resignations.  Auditor resignations in
particular are associated with heightened governance risk and negative returns
because auditors resign when they discover issues they are unwilling to sign
off on.

Hypothesis:
  Large-cap companies filing an 8-K with Item 4.01 — especially resignations —
  produce negative abnormal returns of -1% to -5% over 5-20 days after filing.
  Resignations with reported disagreements or reportable events are the
  highest-conviction subgroup.

Causal mechanism:
  1. Actors: Companies changing auditors (routine rotation vs. conflict-driven)
  2. Transmission: Resignations signal unresolved accounting disputes,
     disagreements signal potential restatements, reportable events (material
     weaknesses) foreshadow SEC enforcement or earnings surprises.
  3. Market inefficiency: Item 4.01 filings are dense legalese; headline scanners
     miss the resignation vs. dismissal distinction; disagreement disclosures
     are often buried in Exhibit 16 letters.

Usage:
    # Historical scan
    python tools/auditor_change_8k_scanner.py --start 2024-01-01 --end 2025-12-31

    # Recent monitoring (last 30 days)
    python tools/auditor_change_8k_scanner.py --days 30

    # Full backtest with abnormal return measurement (all events)
    python tools/auditor_change_8k_scanner.py --backtest --start 2024-01-01 --end 2025-12-31

    # Backtest resignations only (highest conviction)
    python tools/auditor_change_8k_scanner.py --backtest --resignations-only --start 2022-01-01 --end 2025-12-31

    # JSON events for data_tasks.py
    python tools/auditor_change_8k_scanner.py --start 2024-01-01 --end 2025-12-31 --json-events
"""
import argparse
import json
import re
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import yfinance as yf
except ImportError:
    yf = None

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
SEC_DELAY = 0.15          # seconds between EDGAR requests (stay well under 10 req/s)
MIN_MARKET_CAP = 500_000_000  # $500M
EFTS_PAGE_SIZE = 100

# ---------------------------------------------------------------------------
# Resignation / dismissal text patterns
# ---------------------------------------------------------------------------

# Strong resignation signals — auditor chose to leave
RESIGNATION_PATTERNS = [
    r"resigned",
    r"decline[sd]? to stand for re.?election",
    r"decline[sd]? to stand for re.?appointment",
    r"declined to continue",
    r"withdrew from",
    r"not stand for re.?election",
    r"not standing for re.?election",
]

# Dismissal / routine replacement — company chose to end the relationship
DISMISSAL_PATTERNS = [
    r"dismissed",
    r"was terminated",
    r"termination of",
    r"terminated the engagement",
    r"engagement of .{0,60}was terminated",
    r"engaged .{0,80}as .{0,30}new",
    r"appointed .{0,80}as .{0,30}new",
    r"selected .{0,80}as .{0,30}new",
    r"retained .{0,80}as .{0,30}new",
    r"replaced .{0,80}with",
    r"relat?ionship with .{0,60}was concluded",
    r"audit committee .{0,60}appointed",
    r"board of directors .{0,60}approved .{0,60}engagement",
    r"engagement of .{0,80}as .{0,30}independent",
]

# Disagreement / reportable event signals (red flags)
DISAGREEMENT_PATTERNS = [
    r"disagreement",
    r"disagreed",
]

REPORTABLE_EVENT_PATTERNS = [
    r"reportable event",
    r"material weakness",
    r"significant deficiency",
    r"going concern",
    r"scope limitation",
    r"adverse opinion",
    r"disclaimer of opinion",
]


# ---------------------------------------------------------------------------
# EDGAR helpers
# ---------------------------------------------------------------------------

def search_item_401(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings containing Item 4.01."""
    q = '%22Item+4.01%22'
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits: list[dict] = []

    # First page
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"EFTS error on first page: {resp.status_code}", file=sys.stderr)
        return []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(
        f"  8-K Item 4.01: {total} total filings found ({start_date} to {end_date})",
        file=sys.stderr,
    )

    # Paginate — EDGAR EFTS caps addressable window at 10 000
    fetched = len(hits)
    max_to_fetch = min(total, 10_000)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(
                f"  Pagination error at offset {fetched}: {resp.status_code}",
                file=sys.stderr,
            )
            break
        page_hits = resp.json().get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)
        if fetched % 500 == 0:
            print(f"  ... fetched {fetched}/{max_to_fetch}", file=sys.stderr)

    # Parse into uniform dicts
    results: list[dict] = []
    seen: set[tuple] = set()

    for h in all_hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])

        # Confirm Item 4.01 is present in the items metadata (when populated)
        has_401 = any("4.01" in str(it) for it in items)
        if items and not has_401:
            continue

        cik = ciks[0].lstrip("0") if ciks else ""
        dedup_key = (cik, file_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Extract ticker from display_name, e.g. "ACME Corp (ACME)"
        ticker = None
        if names:
            m = re.search(r'\(([A-Z]{1,5})\)', names[0])
            if m:
                ticker = m.group(1)

        results.append({
            "cik": cik,
            "company_name": names[0] if names else "",
            # keep display_name for compatibility with filter_largecap helpers
            "display_name": names[0] if names else "",
            "ticker": ticker,
            "filing_date": file_date,
            "file_date": file_date,  # alias used by backtest helpers
            "items": items,
            "accession": h.get("_id", ""),
        })

    return results


def _is_exhibit_file(file_name: str) -> bool:
    """Return True if the file name looks like an exhibit rather than the 8-K body."""
    name_lower = file_name.lower()
    return (
        "ex" in name_lower.split(".")[0]  # e.g. dex161.htm, ex-16.htm
        or "exhibit" in name_lower
        or re.search(r"ex\d", name_lower) is not None
    )


def _fetch_url(url: str) -> str | None:
    """GET a URL with rate-limit delay; return text on 200, else None."""
    try:
        time.sleep(SEC_DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def fetch_filing_text(accession_id: str, cik: str) -> str | None:
    """Fetch the primary 8-K document for classification.

    The EFTS _id field has the format:
        0000950170-24-123456:acme-8k-20240115.htm

    Sometimes EFTS returns an exhibit (e.g. EX-16.1) instead of the 8-K body.
    Strategy:
      1. Try the filing index JSON (fast, clean document list).
      2. Directly fetch the named file if it's not an exhibit.
      3. Scan the filing directory HTML for an 8-K htm file.
      4. Try common guesses (form8k.htm, derived 8-K stem).
    """
    if not accession_id or not cik:
        return None

    parts = accession_id.split(":")
    raw_acc = parts[0].replace("-", "")
    base_archive = f"https://www.sec.gov/Archives/edgar/data/{cik}/{raw_acc}"

    # --- Attempt 1: filing index JSON ---
    filing_index_url = f"{base_archive}/{raw_acc}-index.json"
    idx_text = _fetch_url(filing_index_url)
    if idx_text:
        try:
            idx_data = json.loads(idx_text)
            for doc in idx_data.get("documents", []):
                doc_type = doc.get("type", "")
                doc_url_path = doc.get("documentUrl", "")
                if doc_type in ("8-K", "8-K/A") and doc_url_path:
                    full_url = f"https://www.sec.gov{doc_url_path}"
                    text = _fetch_url(full_url)
                    if text:
                        return text
        except Exception:
            pass

    # --- Attempt 2: direct file URL when it is not an exhibit ---
    if len(parts) >= 2:
        file_name = parts[1]
        if not _is_exhibit_file(file_name):
            text = _fetch_url(f"{base_archive}/{file_name}")
            if text:
                return text

    # --- Attempt 3: scan the filing directory for an 8-K HTML file ---
    dir_text = _fetch_url(base_archive + "/")
    if dir_text:
        # Look for .htm links that look like the 8-K body (not exhibits)
        links = re.findall(r'href="(/Archives/edgar/data/[^"]+\.htm)"', dir_text, re.IGNORECASE)
        # Prefer links that contain "8k" or "8-k" in the filename
        primary_links = [l for l in links if re.search(r'8.?k', l.split("/")[-1], re.IGNORECASE)]
        non_exhibit_links = [l for l in links if not _is_exhibit_file(l.split("/")[-1])]
        for link in (primary_links or non_exhibit_links)[:3]:
            text = _fetch_url(f"https://www.sec.gov{link}")
            if text:
                return text

    # --- Attempt 4: common fallback filenames ---
    for fallback in ["form8k.htm", "form8-k.htm"]:
        text = _fetch_url(f"{base_archive}/{fallback}")
        if text:
            return text

    # --- Attempt 5: exhibit stem → 8-K stem guess ---
    if len(parts) >= 2:
        file_name = parts[1]
        stem = re.sub(r"dex\d+\.htm$", "d8k.htm", file_name, flags=re.IGNORECASE)
        if stem != file_name:
            text = _fetch_url(f"{base_archive}/{stem}")
            if text:
                return text
        # e.g. _ex161.htm → _8k.htm
        stem2 = re.sub(r"_ex\d+\.htm$", "_8k.htm", file_name, flags=re.IGNORECASE)
        if stem2 != file_name:
            text = _fetch_url(f"{base_archive}/{stem2}")
            if text:
                return text

    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _search_patterns(text: str, patterns: list[str]) -> bool:
    """Return True if any compiled pattern matches the text."""
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _check_disagreements(snippet: str) -> bool:
    """Return True only if the filing POSITIVELY discloses disagreements.

    Item 4.01 filings always include boilerplate language like:
      "there were no disagreements ... which disagreements, if not resolved ..."
      "any matter that was the subject of a disagreement (as defined in ...)"
    These must NOT trigger the flag.

    A true positive looks like:
      "there were disagreements between the Company and [Auditor] regarding ..."
      "disagreed with management's position on ..."

    Strategy: look for 'disagreement(s)' followed by affirmative context
    (e.g. describing a specific topic) in a sentence that does NOT contain
    a negation immediately before it. Skip definitional/boilerplate references.
    """
    # Boilerplate phrases that surround the match — these are definitional /
    # negative-disclosure contexts that should NOT trigger the flag.
    BOILERPLATE = [
        r"which disagreements.{0,40}if not resolved",
        r"disagreements?.{0,80}as defined in\s+Item",
        r"disagreement.{0,20}\(as defined",          # "a disagreement (as defined in ...)"
        r"there were no disagreements",
        r"no disagreements",
        r"were not.{0,20}disagreements",
        r"subject of.{0,30}(a disagreement|either a disagreement)",  # "subject of a/either a disagreement (as defined)"
    ]

    for m in re.finditer(r'disagreement', snippet, re.IGNORECASE):
        # Extract broader context: 80 chars before + 160 chars after
        ctx_start = max(0, m.start() - 80)
        ctx_end = min(len(snippet), m.end() + 160)
        context = snippet[ctx_start:ctx_end]

        # Skip boilerplate
        is_boilerplate = any(re.search(bp, context, re.IGNORECASE) for bp in BOILERPLATE)
        if is_boilerplate:
            continue

        # Skip if clear negation immediately precedes the word
        pre_context = snippet[ctx_start: m.start()].lower()
        if re.search(r'\b(no|not|none|without|absence|weren.t|were\s+not|had\s+no)\b', pre_context[-40:]):
            continue

        # If we get here, it looks like a real disclosure
        return True
    return False


def _check_reportable_events(snippet: str) -> bool:
    """Return True only if the filing POSITIVELY discloses reportable events.

    Similar to disagreements — 'there were no reportable events' is a negative
    disclosure.  We also check for material weakness, going concern, etc.
    which are almost always positive disclosures when mentioned.
    """
    # These terms almost always indicate a positive disclosure
    POSITIVE_ONLY_PATTERNS = [
        r"material\s+weakness",
        r"significant\s+deficiency",
        r"going\s+concern",
        r"scope\s+limitation",
        r"adverse\s+opinion",
        r"disclaimer\s+of\s+opinion",
    ]
    for pat in POSITIVE_ONLY_PATTERNS:
        if re.search(pat, snippet, re.IGNORECASE):
            # These are so specific that any mention is likely a positive disclosure
            # except when explicitly negated
            for m in re.finditer(pat, snippet, re.IGNORECASE):
                ctx_start = max(0, m.start() - 60)
                ctx = snippet[ctx_start: m.start()].lower()
                if not re.search(r'\b(no|not|none|without|absence|did\s+not|were\s+not)\b', ctx):
                    return True

    # Boilerplate for reportable events
    RE_BOILERPLATE = [
        r"there were no reportable events",
        r"no reportable events",
        r"reportable event.{0,20}\(as described",    # "a reportable event (as described in ...)"
        r"reportable event.{0,20}\(as defined",
        r"a reportable event.{0,80}Item 304",
    ]

    for m in re.finditer(r'reportable\s+event', snippet, re.IGNORECASE):
        ctx_start = max(0, m.start() - 80)
        ctx_end = min(len(snippet), m.end() + 160)
        context = snippet[ctx_start:ctx_end]

        is_boilerplate = any(re.search(bp, context, re.IGNORECASE) for bp in RE_BOILERPLATE)
        if is_boilerplate:
            continue

        pre_ctx = snippet[ctx_start: m.start()].lower()
        if re.search(r'\b(no|not|none|without|absence|were\s+not|had\s+no)\b', pre_ctx[-40:]):
            continue

        return True

    return False


def classify_auditor_change(text: str) -> dict:
    """Classify an Item 4.01 filing text.

    Looks for the Item 4.01 section header and examines ~3000 characters
    after it (covers the typical disclosure length without bleeding into
    unrelated items).

    Returns a dict with:
        change_type: 'RESIGNATION' | 'DISMISSAL' | 'UNKNOWN'
        had_disagreements: bool
        had_reportable_events: bool
        snippet: str  (first 500 chars of the relevant section)
    """
    # Strip HTML tags and entities for cleaner matching
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"&#?\w+;", " ", clean)   # HTML entities (named and numeric)
    clean = re.sub(r"\s+", " ", clean)        # collapse whitespace

    # Find the Item 4.01 section — try several header formats.
    # After whitespace collapse, the header may look like "Item 4.01" or "4.01"
    # with surrounding whitespace or section separators.
    idx = -1
    for header_pat in [
        r"[Ii]tem\s*4\s*[.\s]\s*01",   # "Item 4.01" or "Item 4 01" (after collapse)
        r"ITEM\s*4\s*[.\s]\s*01",
        r"4\s*\.\s*01\s+Changes\s+in\s+Registrant",
    ]:
        m = re.search(header_pat, clean)
        if m:
            idx = m.start()
            break

    if idx < 0:
        # Broaden: just look for "4.01" anywhere — the section must mention it
        m = re.search(r"4\.01", clean)
        if m:
            idx = m.start()

    if idx < 0:
        # Fall back to searching the full text
        snippet = clean[:4000]
    else:
        snippet = clean[idx: idx + 4000]

    # Classify change type
    is_resignation = _search_patterns(snippet, RESIGNATION_PATTERNS)
    is_dismissal = _search_patterns(snippet, DISMISSAL_PATTERNS)

    if is_resignation:
        change_type = "RESIGNATION"
    elif is_dismissal:
        change_type = "DISMISSAL"
    else:
        change_type = "UNKNOWN"

    had_disagreements = _check_disagreements(snippet)
    had_reportable_events = _check_reportable_events(snippet)

    return {
        "change_type": change_type,
        "had_disagreements": had_disagreements,
        "had_reportable_events": had_reportable_events,
        "snippet": snippet[:500],
    }


def classify_events(events: list[dict], verbose: bool = True) -> list[dict]:
    """Fetch and classify each filing.  Adds classification fields in-place."""
    for i, e in enumerate(events):
        if e.get("change_type"):
            continue  # already classified

        acc = e.get("accession", "")
        cik = e.get("cik", "")
        text = fetch_filing_text(acc, cik)

        if text:
            result = classify_auditor_change(text)
            e.update(result)
        else:
            e["change_type"] = "UNKNOWN"
            e["had_disagreements"] = False
            e["had_reportable_events"] = False
            e["snippet"] = ""

        if verbose:
            flags = []
            if e.get("had_disagreements"):
                flags.append("DISAGREEMENTS")
            if e.get("had_reportable_events"):
                flags.append("REPORTABLE_EVENTS")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            print(
                f"  [{i+1}/{len(events)}] {e.get('ticker','?')} {e.get('filing_date','?')}: "
                f"{e['change_type']}{flag_str}",
                file=sys.stderr,
            )

    return events


# ---------------------------------------------------------------------------
# Large-cap filter
# ---------------------------------------------------------------------------

def filter_largecap(events: list[dict]) -> list[dict]:
    """Filter to large-cap stocks (>$500M market cap).

    Prefers the cached `largecap_filter` module; falls back to inline yfinance.
    """
    if not events:
        return []

    if pd is not None:
        try:
            from tools.largecap_filter import filter_to_largecap as _filter_lc

            df = pd.DataFrame(events)
            df_with_tickers = df[df["ticker"].notna()].copy()
            if df_with_tickers.empty:
                return []

            df_filtered = _filter_lc(
                df_with_tickers,
                min_market_cap_m=500,
                ticker_col="ticker",
            )
            return df_filtered.to_dict("records")

        except Exception as ex:
            print(
                f"  largecap_filter failed ({ex}), falling back to inline yfinance check",
                file=sys.stderr,
            )

    # Fallback: inline yfinance
    if yf is None:
        print(
            "yfinance not available — skipping market cap filter (all tickers kept)",
            file=sys.stderr,
        )
        return [e for e in events if e.get("ticker")]

    filtered: list[dict] = []
    tickers = list({e["ticker"] for e in events if e.get("ticker")})

    for i, tick in enumerate(tickers):
        try:
            info = yf.Ticker(tick).info
            mcap = info.get("marketCap", 0) or 0
            if mcap >= MIN_MARKET_CAP:
                for e in events:
                    if e.get("ticker") == tick:
                        e["market_cap"] = mcap
                        filtered.append(e)
            else:
                print(
                    f"  Filtered out {tick}: market cap ${mcap/1e6:.0f}M < $500M",
                    file=sys.stderr,
                )
        except Exception as ex:
            print(f"  Error checking {tick}: {ex}", file=sys.stderr)

        if (i + 1) % 10 == 0:
            print(f"  Market cap check: {i+1}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.2)

    return filtered


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def _run_single_backtest(
    event_dates: list[dict],
    label: str,
) -> dict:
    """Run measure_event_impact for a list of {symbol, date} dicts.

    Returns the raw result dict from market_data.
    """
    import market_data

    if not event_dates:
        return {}

    print(
        f"\nMeasuring abnormal returns for {len(event_dates)} events [{label}]...",
        file=sys.stderr,
    )
    return market_data.measure_event_impact(
        event_dates=event_dates,
        entry_price="open",   # 8-Ks often filed after hours — enter at next open
        benchmark="SPY",
    )


def _print_result_table(result: dict, label: str) -> None:
    """Print a formatted table for a backtest result."""
    n = result.get("n_events", 0)
    print(f"\n--- {label} (n={n}) ---")
    print(f"{'Horizon':<12} {'Avg Abn Return':>16} {'Dir% (short)':>14} {'p-value':>10}")
    print("-" * 56)
    for h_key in ["1d", "3d", "5d", "10d", "20d"]:
        h_data = result.get(h_key, {})
        if not h_data:
            continue
        avg = h_data.get("abnormal_mean", 0)
        neg_rate = h_data.get("negative_rate", 0)
        p = h_data.get("p_value", 1.0)
        print(f"{h_key:<12} {avg:>+14.3f}% {neg_rate:>13.1f}% {p:>10.4f}")


def run_backtest(events: list[dict]) -> dict:
    """Run full abnormal return backtest, split by change_type.

    Groups: ALL events, RESIGNATION only, DISMISSAL only.
    For each group: full-sample + 60/40 OOS split.
    Records result in knowledge base.
    """
    import db
    from research import record_known_effect, record_dead_end

    db.init_db()

    # Build event lists by group
    all_events = [
        {"symbol": e["ticker"], "date": e["filing_date"]}
        for e in events
        if e.get("ticker") and e.get("filing_date")
    ]
    resignation_events = [
        {"symbol": e["ticker"], "date": e["filing_date"]}
        for e in events
        if e.get("ticker") and e.get("filing_date") and e.get("change_type") == "RESIGNATION"
    ]
    dismissal_events = [
        {"symbol": e["ticker"], "date": e["filing_date"]}
        for e in events
        if e.get("ticker") and e.get("filing_date") and e.get("change_type") == "DISMISSAL"
    ]
    disagree_events = [
        {"symbol": e["ticker"], "date": e["filing_date"]}
        for e in events
        if e.get("ticker") and e.get("filing_date")
        and (e.get("had_disagreements") or e.get("had_reportable_events"))
    ]

    if not all_events:
        print("No events to backtest", file=sys.stderr)
        return {}

    print(f"\n{'='*70}")
    print(f"BACKTEST — ALL Item 4.01 filings (n={len(all_events)})")
    print(f"  Resignations : {len(resignation_events)}")
    print(f"  Dismissals   : {len(dismissal_events)}")
    print(f"  w/ Disagreements or Reportable Events: {len(disagree_events)}")
    print(f"{'='*70}")

    assessments = {}

    for group_label, group_events, db_key in [
        ("ALL EVENTS", all_events, "auditor_change_8k_item_401_all_short"),
        ("RESIGNATIONS ONLY", resignation_events, "auditor_change_8k_item_401_resignation_short"),
        ("DISMISSALS ONLY", dismissal_events, "auditor_change_8k_item_401_dismissal_short"),
        ("DISAGREEMENTS / REPORTABLE EVENTS", disagree_events, "auditor_change_8k_item_401_flags_short"),
    ]:
        if len(group_events) < 3:
            print(f"\n[{group_label}] Skipped — fewer than 3 events", file=sys.stderr)
            continue

        result = _run_single_backtest(group_events, group_label)
        if not result:
            continue

        _print_result_table(result, group_label)

        # OOS split: first 60% = discovery, last 40% = validation
        sorted_events = sorted(group_events, key=lambda e: e["date"])
        split_idx = int(len(sorted_events) * 0.6)
        discovery = sorted_events[:split_idx]
        validation = sorted_events[split_idx:]

        print(f"\n  --- OOS SPLIT [{group_label}] ---")
        if len(discovery) >= 5:
            disc_result = _run_single_backtest(discovery, f"DISCOVERY n={len(discovery)}")
            _print_result_table(disc_result, f"DISCOVERY [{group_label}]")

        if len(validation) >= 3:
            val_result = _run_single_backtest(validation, f"VALIDATION n={len(validation)}")
            _print_result_table(val_result, f"VALIDATION [{group_label}]")

        passes_mt = result.get("passes_multiple_testing", False)
        print(f"\n  Passes multiple testing correction: {passes_mt}")

        # Best horizon
        best_horizon = None
        best_abs = 0
        for h_key in ["1d", "3d", "5d", "10d", "20d"]:
            h_data = result.get(h_key, {})
            avg = abs(h_data.get("abnormal_mean", 0))
            if avg > best_abs:
                best_abs = avg
                best_horizon = h_key

        h_data = result.get(best_horizon, {}) if best_horizon else {}
        avg_abn = h_data.get("abnormal_mean", 0)
        neg_rate = h_data.get("negative_rate", 0)
        p = h_data.get("p_value", 1.0)
        n = result.get("n_events", 0)

        checks = {
            "n_sufficient": n >= 10,
            "passes_mt": passes_mt,
            "direction_correct": neg_rate > 50,
            "abnormal_above_threshold": abs(avg_abn) > 0.5,
            "return_after_costs": abs(avg_abn) > 0.416,
        }
        failed = [k for k, v in checks.items() if not v]

        if not failed:
            status = "VALIDATED"
        elif len(failed) <= 2 and checks.get("direction_correct") and checks.get("n_sufficient"):
            status = "PRELIMINARY_NEEDS_MORE_DATA"
        else:
            status = "DEAD_END"

        assessment = {
            "status": status,
            "group": group_label,
            "hypothesis_class": "event",
            "expected_direction": "short",
            "universe": f"EDGAR 8-K Item 4.01, large-cap >$500M, {group_label}",
            "n_events": n,
            "best_horizon": best_horizon,
            "avg_abnormal": avg_abn,
            "p_value": p,
            "neg_rate": neg_rate,
            "passes_mt": passes_mt,
            "discovery_n": len(discovery),
            "validation_n": len(validation),
            "sample_events": [f"{e['symbol']} {e['date']}" for e in group_events[:10]],
            "full_result": result,
        }
        assessments[group_label] = assessment

        if "DEAD_END" in status:
            record_dead_end(
                db_key,
                f"Signal failed check(s): {', '.join(failed)}. n={n}, "
                f"best_horizon={best_horizon}, avg_abnormal={avg_abn:.3f}%, "
                f"p={p:.4f}, neg_rate={neg_rate:.1f}%, passes_mt={passes_mt}.",
            )

        record_known_effect(db_key, assessment)
        print(f"\n  Status [{group_label}]: {status}")

    return assessments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scan EDGAR for 8-K Item 4.01 auditor change filings"
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Look back N days from today")
    parser.add_argument("--json-events", action="store_true",
                        help="Output as JSON events for data_tasks.py")
    parser.add_argument("--backtest", action="store_true",
                        help="Run full backtest with abnormal returns (requires --classify)")
    parser.add_argument("--no-filter", action="store_true",
                        help="Skip market cap filter")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Keep all filings per ticker (not just first per company)")
    parser.add_argument("--classify", action="store_true",
                        help="Fetch each filing to classify as RESIGNATION vs DISMISSAL")
    parser.add_argument("--resignations-only", action="store_true",
                        help="After classification, keep only resignations (implies --classify)")
    args = parser.parse_args()

    # --backtest and --resignations-only both need classification
    if args.backtest or args.resignations_only:
        args.classify = True

    today = datetime.now().strftime("%Y-%m-%d")

    if args.days:
        start = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        end = today
    elif args.start:
        start = args.start
        end = args.end or today
    else:
        # Default: last 30 days
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        end = today

    print(f"Scanning EDGAR for 8-K Item 4.01 filings from {start} to {end}...", file=sys.stderr)

    # 1. Search EDGAR EFTS
    events = search_item_401(start, end)
    print(f"Raw events found: {len(events)}", file=sys.stderr)

    # 2. Keep only events where a ticker could be extracted
    events = [e for e in events if e.get("ticker")]
    print(f"Events with tickers: {len(events)}", file=sys.stderr)

    # 3. Large-cap filter
    if not args.no_filter and events:
        events = filter_largecap(events)
        print(f"Large-cap events (>$500M): {len(events)}", file=sys.stderr)

    # 4. Dedup: keep only the FIRST filing per company (initial change is the signal)
    if not args.no_dedup and events:
        events.sort(key=lambda e: e["filing_date"])
        seen_tickers: set[str] = set()
        deduped: list[dict] = []
        followups = 0
        for e in events:
            if e["ticker"] not in seen_tickers:
                seen_tickers.add(e["ticker"])
                deduped.append(e)
            else:
                followups += 1
        if followups:
            print(
                f"Deduplication: removed {followups} follow-up filings "
                f"(keeping first per ticker)",
                file=sys.stderr,
            )
        events = deduped

    # 5. Classification (fetch filing text)
    if args.classify and events:
        print(
            f"\nClassifying {len(events)} filings (fetching text from EDGAR)...",
            file=sys.stderr,
        )
        events = classify_events(events)

        resignations = [e for e in events if e.get("change_type") == "RESIGNATION"]
        dismissals = [e for e in events if e.get("change_type") == "DISMISSAL"]
        unknowns = [e for e in events if e.get("change_type") == "UNKNOWN"]
        with_flags = [
            e for e in events
            if e.get("had_disagreements") or e.get("had_reportable_events")
        ]
        print(
            f"Classification: {len(resignations)} resignations, "
            f"{len(dismissals)} dismissals, {len(unknowns)} unknown",
            file=sys.stderr,
        )
        print(f"  With disagreements or reportable events: {len(with_flags)}", file=sys.stderr)

        if args.resignations_only:
            events = resignations
            print(f"Keeping resignations only: {len(events)} events", file=sys.stderr)

    # --- Output ---

    if args.json_events:
        json_events = [
            {
                "symbol": e["ticker"],
                "date": e["filing_date"],
                **({"change_type": e["change_type"]} if e.get("change_type") else {}),
            }
            for e in events
        ]
        print(json.dumps(json_events))
        return events

    # Human-readable event list
    print(f"\nFinal events: {len(events)}")
    for e in events:
        mcap = e.get("market_cap")
        if mcap:
            mcap_str = f" (${mcap/1e9:.1f}B)" if mcap >= 1e9 else f" (${mcap/1e6:.0f}M)"
        else:
            mcap_str = ""

        ct = e.get("change_type", "")
        ct_str = f" [{ct}]" if ct else ""

        flags = []
        if e.get("had_disagreements"):
            flags.append("DISAGREE")
        if e.get("had_reportable_events"):
            flags.append("REPORTABLE")
        flag_str = f" ({', '.join(flags)})" if flags else ""

        print(
            f"  {e['ticker']} {e['filing_date']}{mcap_str}{ct_str}{flag_str}: "
            f"{e['company_name'][:55]}"
        )

    # Run backtest
    if args.backtest and events:
        if len(events) < 5:
            print(
                f"\nWARNING: Only {len(events)} events — below 5 minimum for meaningful backtest. "
                f"Proceeding anyway.",
                file=sys.stderr,
            )
        assessments = run_backtest(events)
        print(f"\n{'='*70}")
        print("BACKTEST SUMMARY")
        print(f"{'='*70}")
        for group, a in assessments.items():
            print(
                f"  {group}: status={a['status']}, n={a['n_events']}, "
                f"best_horizon={a['best_horizon']}, "
                f"avg_abnormal={a['avg_abnormal']:+.3f}%, "
                f"p={a['p_value']:.4f}"
            )

    return events


if __name__ == "__main__":
    main()
