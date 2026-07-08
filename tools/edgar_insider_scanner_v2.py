#!/usr/bin/env python3
"""
SEC EDGAR Insider Buying Cluster Scanner v2

Finds open-market purchase clusters directly from SEC EDGAR.
Replaces OpenInsider dependency (134 friction events from downtime).

Strategy:
1. Query EDGAR EFTS for ALL Form 4 filings in date range (metadata only, fast)
2. Group by issuer company — only companies with 3+ filings are cluster candidates
3. Download and parse XML only for cluster candidates (saves 90%+ of requests)
4. Filter for open-market purchases (transaction code P, acquired not disposed)
5. Return qualifying clusters (3+ unique insiders, each >$50K)

Usage:
    python3 tools/edgar_insider_scanner_v2.py [--days 14] [--min-insiders 3] [--min-value 50000]
    python3 tools/edgar_insider_scanner_v2.py --days 30 --min-insiders 3 --output table

Programmatic:
    from tools.edgar_insider_scanner_v2 import scan_insider_clusters
    clusters = scan_insider_clusters(days=14, min_insiders=3, min_value_per_insider=50000)
"""

import os
import re
import sys
import json
import time
import pickle
import argparse
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Financial Research Bot contact@example.com"
)
CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "edgar_form4_cache_v2"
)

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

SEC_DELAY = 0.12  # 10 req/sec max
SEC_TIMEOUT = 20
SEC_MAX_RETRIES = 3

_last_request_time = 0.0
_rate_limit_lock = threading.Lock()


def _rate_limit():
    """Thread-safe dispatch spacing so aggregate SEC request rate stays
    within the fair-access limit regardless of worker-thread count."""
    global _last_request_time
    with _rate_limit_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < SEC_DELAY:
            time.sleep(SEC_DELAY - elapsed)
        _last_request_time = time.time()


def sec_get(url: str, timeout: int = SEC_TIMEOUT) -> Optional[requests.Response]:
    """GET with rate limiting and retry."""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    for attempt in range(SEC_MAX_RETRIES):
        try:
            _rate_limit()
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < SEC_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# Step 1: Fetch ALL Form 4 metadata (fast — no XML download)
# ---------------------------------------------------------------------------

def fetch_form4_metadata(start_date: str, end_date: str, quiet: bool = False) -> list[dict]:
    """Paginate through EDGAR EFTS to get all Form 4 filing metadata.

    Returns list of dicts: {accession, filename, insider_name, insider_cik,
                            issuer_name, issuer_cik, filing_date}
    """
    all_filings = []
    from_offset = 0
    page_size = 100  # EFTS max

    while True:
        url = (
            f"{EFTS_URL}?forms=4"
            f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
            f"&from={from_offset}"
        )

        resp = sec_get(url, timeout=30)
        if resp is None or resp.status_code != 200:
            # Retry once with longer timeout
            time.sleep(2)
            resp = sec_get(url, timeout=60)
            if resp is None or resp.status_code != 200:
                if not quiet:
                    print(f"  EFTS error at offset {from_offset}: {resp.status_code if resp else 'timeout'}, continuing with {len(all_filings)} filings")
                break

        try:
            data = resp.json()
        except json.JSONDecodeError:
            break

        hits = data.get("hits", {}).get("hits", [])
        total_info = data.get("hits", {}).get("total", {})
        total_count = total_info.get("value", 0) if isinstance(total_info, dict) else (total_info or 0)

        if not hits:
            break

        for hit in hits:
            source = hit.get("_source", {})
            raw_id = hit.get("_id", "")

            # Parse _id: "accession:filename.xml"
            if ":" in raw_id:
                accession, filename = raw_id.split(":", 1)
            else:
                accession = raw_id
                filename = ""

            # display_names: [insider_name, issuer_name]
            display_names = source.get("display_names", [])
            ciks = source.get("ciks", [])

            insider_name = display_names[0] if len(display_names) > 0 else ""
            issuer_name = display_names[1] if len(display_names) > 1 else ""
            insider_cik = ciks[0] if len(ciks) > 0 else ""
            issuer_cik = ciks[1] if len(ciks) > 1 else ""

            # Clean CIK of "(CIK ...)" suffix in display names
            insider_name = re.sub(r'\s*\(CIK\s+\d+\)', '', insider_name).strip()
            issuer_name = re.sub(r'\s*\(CIK\s+\d+\)', '', issuer_name).strip()

            all_filings.append({
                "accession": accession,
                "filename": filename,
                "insider_name": insider_name,
                "insider_cik": insider_cik,
                "issuer_name": issuer_name,
                "issuer_cik": issuer_cik,
                "filing_date": source.get("file_date", ""),
            })

        if not quiet and from_offset % 500 == 0:
            print(f"  Fetched {len(all_filings)}/{total_count} filing metadata...")

        from_offset += len(hits)
        if from_offset >= total_count or from_offset >= 10000:
            break

    if not quiet:
        print(f"  Total Form 4 metadata: {len(all_filings)} (from {total_count} total)")

    return all_filings


# ---------------------------------------------------------------------------
# Step 2: Pre-filter — only download XML for issuers with 3+ filings
# ---------------------------------------------------------------------------

def group_by_issuer(filings: list[dict]) -> dict[str, list[dict]]:
    """Group filings by issuer CIK. Returns {issuer_cik: [filings]}."""
    groups = defaultdict(list)
    for f in filings:
        key = f["issuer_cik"]
        if key:
            groups[key].append(f)
    return dict(groups)


# ---------------------------------------------------------------------------
# Step 3: Download and parse Form 4 XML
# ---------------------------------------------------------------------------

def fetch_form4_xml(filing: dict) -> Optional[str]:
    """Fetch the Form 4 XML text for a filing. Uses cache."""
    accession = filing["accession"]
    filename = filing["filename"]
    issuer_cik = filing["issuer_cik"].lstrip("0")

    if not issuer_cik or not accession:
        return None

    # Cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    acc_clean = accession.replace("-", "")
    cache_path = os.path.join(CACHE_DIR, f"{acc_clean}_{filename}.xml")

    if os.path.exists(cache_path):
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < 72:  # 3-day cache
            with open(cache_path, "r") as f:
                return f.read()

    # Construct URL: https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}
    url = f"{EDGAR_ARCHIVES}/{issuer_cik}/{acc_clean}/{filename}"
    resp = sec_get(url)

    if resp and resp.status_code == 200 and "<" in resp.text[:100]:
        with open(cache_path, "w") as f:
            f.write(resp.text)
        return resp.text

    # Fallback: try filing index to find XML
    acc_dashed = accession
    index_url = f"{EDGAR_ARCHIVES}/{issuer_cik}/{acc_clean}/{acc_dashed}-index.htm"
    resp = sec_get(index_url)
    if resp and resp.status_code == 200:
        xml_links = re.findall(r'href="([^"]*\.xml)"', resp.text, re.IGNORECASE)
        for link in xml_links:
            if link.startswith("R") or "FilingSummary" in link:
                continue
            if link.startswith("/"):
                xml_url = f"https://www.sec.gov{link}"
            elif link.startswith("http"):
                xml_url = link
            else:
                xml_url = f"{EDGAR_ARCHIVES}/{issuer_cik}/{acc_clean}/{link}"
            xml_resp = sec_get(xml_url)
            if xml_resp and xml_resp.status_code == 200 and "<ownershipDocument" in xml_resp.text:
                with open(cache_path, "w") as f:
                    f.write(xml_resp.text)
                return xml_resp.text
                break

    return None


def parse_form4_purchases(xml_text: str) -> Optional[dict]:
    """Parse Form 4 XML for open-market purchases. Returns purchase info or None."""
    try:
        xml_clean = re.sub(r'<\?xml[^>]*\?>', '', xml_text)
        xml_clean = re.sub(r'xmlns="[^"]*"', '', xml_clean)
        xml_clean = re.sub(r'xmlns:[a-z]+="[^"]*"', '', xml_clean)
        root = ET.fromstring(xml_clean)
    except ET.ParseError:
        return _parse_form4_regex(xml_text)

    result = {
        "issuer_name": "",
        "issuer_ticker": "",
        "owner_name": "",
        "owner_cik": "",
        "is_officer": False,
        "is_director": False,
        "officer_title": "",
        "transactions": [],
    }

    issuer = root.find(".//issuer")
    if issuer is not None:
        result["issuer_name"] = _xml_text(issuer, "issuerName")
        result["issuer_ticker"] = _xml_text(issuer, "issuerTradingSymbol").upper().strip()

    owner = root.find(".//reportingOwner")
    if owner is not None:
        owner_id = owner.find("reportingOwnerId")
        if owner_id is not None:
            result["owner_name"] = _xml_text(owner_id, "rptOwnerName")
            result["owner_cik"] = _xml_text(owner_id, "rptOwnerCik")
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            result["is_officer"] = _xml_text(rel, "isOfficer") in ("1", "true")
            result["is_director"] = _xml_text(rel, "isDirector") in ("1", "true")
            result["officer_title"] = _xml_text(rel, "officerTitle")

    for txn in root.findall(".//nonDerivativeTransaction"):
        code_elem = txn.find(".//transactionCoding")
        if code_elem is None:
            continue
        trans_code = _xml_text(code_elem, "transactionCode")
        if trans_code != "P":
            continue

        # Check acquired (A), not disposed (D)
        acq_disp = ""
        ad_elem = txn.find(".//transactionAmounts/transactionAcquiredDisposedCode")
        if ad_elem is not None:
            acq_disp = _xml_text_direct(ad_elem) or _xml_text(ad_elem, "value")
        if acq_disp == "D":
            continue

        shares = _xml_float(txn, ".//transactionAmounts/transactionShares/value")
        price = _xml_float(txn, ".//transactionAmounts/transactionPricePerShare/value")
        trans_date = _xml_text(txn, ".//transactionDate/value")

        if shares and price and shares > 0 and price > 0:
            result["transactions"].append({
                "date": trans_date,
                "shares": shares,
                "price": price,
                "value": round(shares * price, 2),
            })

    return result if result["transactions"] else None


def _parse_form4_regex(xml_text: str) -> Optional[dict]:
    """Fallback regex parser for malformed XML."""
    if "<transactionCode>P</transactionCode>" not in xml_text:
        return None

    result = {
        "issuer_name": "",
        "issuer_ticker": "",
        "owner_name": "",
        "owner_cik": "",
        "is_officer": False,
        "is_director": False,
        "officer_title": "",
        "transactions": [],
    }

    for tag, key in [
        ("issuerName", "issuer_name"),
        ("issuerTradingSymbol", "issuer_ticker"),
        ("rptOwnerName", "owner_name"),
        ("rptOwnerCik", "owner_cik"),
        ("officerTitle", "officer_title"),
    ]:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", xml_text)
        if m:
            result[key] = m.group(1).strip()

    result["issuer_ticker"] = result["issuer_ticker"].upper()
    result["is_officer"] = "<isOfficer>1</isOfficer>" in xml_text or "<isOfficer>true</isOfficer>" in xml_text
    result["is_director"] = "<isDirector>1</isDirector>" in xml_text or "<isDirector>true</isDirector>" in xml_text

    blocks = re.findall(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
        xml_text, re.DOTALL
    )
    for block in blocks:
        if "<transactionCode>P</transactionCode>" not in block:
            continue
        shares_m = re.search(r"<transactionShares>.*?<value>([\d.]+)</value>", block, re.DOTALL)
        price_m = re.search(r"<transactionPricePerShare>.*?<value>([\d.]+)</value>", block, re.DOTALL)
        date_m = re.search(r"<transactionDate>.*?<value>(\d{4}-\d{2}-\d{2})</value>", block, re.DOTALL)
        if shares_m and price_m:
            shares = float(shares_m.group(1))
            price = float(price_m.group(1))
            if shares > 0 and price > 0:
                result["transactions"].append({
                    "date": date_m.group(1) if date_m else "",
                    "shares": shares,
                    "price": price,
                    "value": round(shares * price, 2),
                })

    return result if result["transactions"] else None


def _xml_text(parent, tag: str) -> str:
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    elem = parent.find(f"{tag}/value")
    if elem is not None and elem.text:
        return elem.text.strip()
    return ""


def _xml_text_direct(elem) -> str:
    if elem is not None and elem.text:
        return elem.text.strip()
    return ""


def _xml_float(root, xpath: str) -> Optional[float]:
    elem = root.find(xpath)
    if elem is not None and elem.text:
        try:
            return float(elem.text.strip())
        except ValueError:
            return None
    return None


def _business_days_between(start_date_str: str, end_date_str: str) -> int:
    """Number of business days from start to end (inclusive of neither endpoint).

    Treats Mon-Fri as business days; ignores holidays. Always returns >=0.
    Returns 0 if dates are the same or end is before start.
    """
    from datetime import datetime as _dt
    try:
        start = _dt.strptime(start_date_str, "%Y-%m-%d").date()
        end = _dt.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception:
        return 0
    if end <= start:
        return 0
    days = 0
    cur = start
    from datetime import timedelta as _td
    while cur < end:
        cur = cur + _td(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_insider_clusters(
    days: int = 14,
    min_insiders: int = 3,
    min_value_per_insider: int = 50000,
    quiet: bool = False,
) -> list[dict]:
    """Scan EDGAR for insider buying clusters.

    Returns list of cluster dicts:
        {ticker, issuer_name, n_insiders, total_value, insiders: [{name, title, value, date}]}
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    if not quiet:
        print(f"Scanning EDGAR Form 4 filings: {start_date} to {end_date}")
        print(f"Cluster criteria: {min_insiders}+ insiders, >${min_value_per_insider:,} each")
        print()

    # Step 1: Fetch all metadata
    if not quiet:
        print("Step 1: Fetching filing metadata...")
    metadata = fetch_form4_metadata(start_date, end_date, quiet=quiet)
    if not metadata:
        if not quiet:
            print("No Form 4 filings found.")
        return []

    # Step 2: Group by issuer, filter to potential clusters
    if not quiet:
        print(f"\nStep 2: Grouping {len(metadata)} filings by issuer...")
    groups = group_by_issuer(metadata)
    candidates = {k: v for k, v in groups.items() if len(v) >= min_insiders}
    if not quiet:
        print(f"  {len(groups)} unique issuers, {len(candidates)} with {min_insiders}+ filings")

    if not candidates:
        if not quiet:
            print("No cluster candidates found.")
        return []

    # Step 3: Download and parse XMLs for candidates only
    total_to_fetch = sum(len(v) for v in candidates.values())
    if not quiet:
        print(f"\nStep 3: Downloading {total_to_fetch} Form 4 XMLs for {len(candidates)} candidate issuers...")

    # issuer_cik -> [{parsed purchase info}]
    purchases_by_issuer = defaultdict(list)

    # Fetch + parse all candidate XMLs CONCURRENTLY. The bottleneck is ~0.4s
    # network latency per Form 4 XML; a serial loop over ~730 filings blows the
    # timeout. ThreadPoolExecutor overlaps the waits while the thread-safe
    # _rate_limit() keeps the aggregate SEC request rate within fair-access.
    def _fetch_and_parse(filing):
        xml = fetch_form4_xml(filing)
        if xml is None:
            return (filing, "error", None)
        parsed = parse_form4_purchases(xml)
        if parsed is None:
            return (filing, "not_purchase", None)
        return (filing, "ok", parsed)

    # Flatten to (issuer_cik, filing) so results can be regrouped afterwards.
    work = [(cik, f) for cik, filings in candidates.items() for f in filings]
    # Per-issuer parsed results, kept with the source filing for deterministic dedup.
    parsed_by_issuer = defaultdict(list)
    fetched = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_and_parse, f): cik for cik, f in work}
        for fut, cik in futures.items():
            filing, status, parsed = fut.result()
            fetched += 1
            if not quiet and fetched % 50 == 0:
                print(f"  Fetched {fetched}/{total_to_fetch}...")
            if status == "error":
                errors += 1
            elif status == "ok":
                parsed_by_issuer[cik].append((filing, parsed))

    # Per-insider dedup + value filter runs serially after collection.
    # Sort each issuer's filings by filing_date so the retained record for a
    # duplicated insider is deterministic regardless of thread completion order.
    for issuer_cik, items in parsed_by_issuer.items():
        items.sort(key=lambda fp: fp[0].get("filing_date", ""))
        seen_insiders = set()
        for filing, parsed in items:
            insider_key = parsed["owner_cik"] or parsed["owner_name"]
            if insider_key in seen_insiders:
                continue
            seen_insiders.add(insider_key)

            total_value = sum(t["value"] for t in parsed["transactions"])
            if total_value >= min_value_per_insider:
                purchases_by_issuer[issuer_cik].append({
                    "name": parsed["owner_name"],
                    "cik": parsed["owner_cik"],
                    "ticker": parsed["issuer_ticker"],
                    "issuer_name": parsed["issuer_name"],
                    "is_officer": parsed["is_officer"],
                    "is_director": parsed["is_director"],
                    "title": parsed["officer_title"],
                    "value": total_value,
                    "n_transactions": len(parsed["transactions"]),
                    "dates": [t["date"] for t in parsed["transactions"]],
                    "filing_date": filing.get("filing_date", ""),
                })

    if not quiet:
        print(f"  Done: {fetched} fetched, {errors} errors")

    # Step 4: Filter to qualifying clusters
    today_str = datetime.now().strftime("%Y-%m-%d")
    clusters = []
    for issuer_cik, purchases in purchases_by_issuer.items():
        if len(purchases) >= min_insiders:
            ticker = purchases[0]["ticker"]
            issuer_name = purchases[0]["issuer_name"]
            total_value = sum(p["value"] for p in purchases)

            # Filing freshness: how stale is this cluster as of today?
            filing_dates = [p.get("filing_date", "") for p in purchases if p.get("filing_date")]
            latest_filing_date = max(filing_dates) if filing_dates else ""
            days_since_latest_filing = None
            max_trans_to_filing_lag = None
            if latest_filing_date:
                try:
                    days_since_latest_filing = _business_days_between(latest_filing_date, today_str)
                except Exception:
                    days_since_latest_filing = None
            try:
                lags = []
                for p in purchases:
                    fd = p.get("filing_date", "")
                    if not fd or not p.get("dates"):
                        continue
                    earliest_trans = min(d for d in p["dates"] if d)
                    if earliest_trans:
                        lags.append(_business_days_between(earliest_trans, fd))
                if lags:
                    max_trans_to_filing_lag = max(lags)
            except Exception:
                max_trans_to_filing_lag = None

            clusters.append({
                "ticker": ticker,
                "issuer_name": issuer_name,
                "issuer_cik": issuer_cik,
                "n_insiders": len(purchases),
                "total_value": total_value,
                "latest_filing_date": latest_filing_date,
                "days_since_latest_filing": days_since_latest_filing,
                "max_trans_to_filing_lag": max_trans_to_filing_lag,
                "insiders": [
                    {
                        "name": p["name"],
                        "title": p["title"] or ("Director" if p["is_director"] else ""),
                        "value": p["value"],
                        "dates": p["dates"],
                        "filing_date": p.get("filing_date", ""),
                    }
                    for p in sorted(purchases, key=lambda x: -x["value"])
                ],
            })

    clusters.sort(key=lambda c: -c["total_value"])

    if not quiet:
        print(f"\nFound {len(clusters)} qualifying clusters")

    return clusters


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def print_table(clusters: list[dict]):
    if not clusters:
        print("No clusters found.")
        return

    print(f"\n{'='*80}")
    print(f"INSIDER BUYING CLUSTERS ({len(clusters)} found)")
    print(f"{'='*80}")

    for c in clusters:
        print(f"\n{c['ticker']} — {c['issuer_name']}")
        print(f"  {c['n_insiders']} insiders, total ${c['total_value']:,.0f}")
        for ins in c["insiders"]:
            title = f" ({ins['title']})" if ins["title"] else ""
            dates = ", ".join(ins["dates"][:3])
            print(f"    {ins['name']}{title}: ${ins['value']:,.0f} on {dates}")


def main():
    parser = argparse.ArgumentParser(description="EDGAR insider buying cluster scanner")
    parser.add_argument("--days", type=int, default=14, help="Days to look back (default: 14)")
    parser.add_argument("--min-insiders", type=int, default=3, help="Min insiders per cluster (default: 3)")
    parser.add_argument("--min-value", type=int, default=50000, help="Min purchase value per insider (default: 50000)")
    parser.add_argument("--output", choices=["json", "table"], default="table")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    clusters = scan_insider_clusters(
        days=args.days,
        min_insiders=args.min_insiders,
        min_value_per_insider=args.min_value,
        quiet=args.quiet,
    )

    if args.output == "json":
        print(json.dumps(clusters, indent=2, default=str))
    else:
        print_table(clusters)


if __name__ == "__main__":
    main()
