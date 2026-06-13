#!/usr/bin/env python3
"""8-K Item 3.02 (Unregistered Sale of Equity Securities) scanner.

Item 3.02 is the PIPE / private placement disclosure — dilutive share issuance
outside of a registered offering. Canonical retest validated 2026-04-20:
pooled h=10 SPY-adj -3.48% p=4e-5 at price>$5 (n=644); recent 2023+ -5.21%
p=1e-5 (n=430); 2024+ -6.07% p=1e-4 (n=204). Effect is monotonic in price,
recency, horizon, and chronic-filer count. Chronic repeat filers have STRONGER
signal — do NOT exclude them.

Tradeable cell (knowledge: item_302_pipe_private_placement_short_validated_canonical_2026_04_20):
  8-K Item 3.02 + price > $5 + hold 10d -> short, expected -5.21% SPY-adj,
  65% negative rate, cluster buffer 30d.

Hypothesis id: 84f218f0.

Usage:
    # Recent monitoring (daily use)
    python tools/item_302_pipe_scanner.py --days 3 --json-events

    # Historical scan
    python tools/item_302_pipe_scanner.py --start 2024-01-01 --end 2026-04-20

    # Full backtest
    python tools/item_302_pipe_scanner.py --backtest --start 2023-01-01
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
    import yfinance as yf
except ImportError:
    yf = None

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
SEC_DELAY = 0.15
MIN_MARKET_CAP = 500_000_000   # $500M default (daily scanner baseline)
MIN_PRICE = 5.0                 # Canonical liquidity floor
EFTS_PAGE_SIZE = 100


def search_item_302(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for 8-K filings containing Item 3.02."""
    q = '%22Item+3.02%22'
    base_url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )

    all_hits = []
    url = base_url + f"&from=0&size={EFTS_PAGE_SIZE}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"EFTS error: {resp.status_code}", file=sys.stderr)
        return []

    data = resp.json()
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])
    all_hits.extend(hits)
    print(f"  8-K Item 3.02: {total} total filings ({start_date} to {end_date})",
          file=sys.stderr)

    fetched = len(hits)
    max_to_fetch = min(total, 10000)
    while fetched < max_to_fetch:
        time.sleep(SEC_DELAY)
        url = base_url + f"&from={fetched}&size={EFTS_PAGE_SIZE}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"  Pagination error at offset {fetched}: {resp.status_code}",
                  file=sys.stderr)
            break
        page_hits = resp.json().get("hits", {}).get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        fetched += len(page_hits)

    results = []
    seen = set()
    for h in all_hits:
        src = h.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        file_date = src.get("file_date", "")
        items = src.get("items", [])

        # Confirm Item 3.02 in items list
        has_302 = any("3.02" in str(it) for it in items)
        if items and not has_302:
            continue

        cik = ciks[0].lstrip("0") if ciks else ""
        dedup_key = (cik, file_date)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Extract ticker from display_name
        ticker = None
        if names:
            m = re.search(r'\(([A-Z]{1,5})\)', names[0])
            if m:
                ticker = m.group(1)

        results.append({
            "cik": cik,
            "display_name": names[0] if names else "",
            "ticker": ticker,
            "file_date": file_date,
            "items": items,
            "accession": h.get("_id", ""),
        })

    return results


def classify_302_dilution_type(cik: str, accession: str) -> dict:
    """Classify an 8-K Item 3.02 filing by dilution type.

    Returns dict with keys:
        dilution_type: 'dilutive_pipe' | 'share_conversion' | 'm_and_a_related' |
                       'de_spac_closing' | 'unknown'
        confidence: 'high' | 'medium' | 'low'
        matched_patterns: list of patterns that fired
        excerpt: first 400 chars of Item 3.02 section

    Added 2026-04-21 after three false positives (DELL/AVEX/USAR) slipped through:
      - DELL: Class B→C common conversion under Section 3(a)(9) — non-dilutive
      - AVEX: De-SPAC closing, sponsor shares + board reconstitution
      - USAR: Merger Agreement consideration — M&A related
    See knowledge: item_302_pipe_scanner_false_positives_2026_04_21.
    """
    if "-" in accession:
        acc_parts = accession.split(":")
        accession_num = acc_parts[0].replace("-", "")
        file_hint = acc_parts[1] if len(acc_parts) > 1 else ""
    else:
        accession_num = accession.replace("-", "")
        file_hint = ""

    cik_num = str(int(cik.lstrip("0"))) if cik.lstrip("0") else cik

    # Fetch filing index to find the main 8-K document if we don't have the filename
    if not file_hint:
        idx_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_num}&type=8-K&dateb=&owner=include&count=10"
        return {"dilution_type": "unknown", "confidence": "low",
                "matched_patterns": [], "excerpt": "(no file hint)"}

    url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{accession_num}/{file_hint}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {"dilution_type": "unknown", "confidence": "low",
                    "matched_patterns": [f"http_{resp.status_code}"], "excerpt": ""}

        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        # Decode common entities
        text = text.replace("&#8220;", '"').replace("&#8221;", '"')
        text = text.replace("&#8217;", "'").replace("&#160;", " ")
        text = text.replace("&amp;", "&")

        # Find Item 3.02 section — take ~2500 chars of context
        idx = text.lower().find("item 3.02")
        if idx < 0:
            idx = text.lower().find("3.02")
        section = text[idx:idx + 2500] if idx >= 0 else text[:2500]
        section_lower = section.lower()
    except Exception as e:
        return {"dilution_type": "unknown", "confidence": "low",
                "matched_patterns": [f"fetch_error:{type(e).__name__}"], "excerpt": ""}

    matched = []

    # ----- Pattern 1: Share CONVERSION (non-dilutive reclassification) -----
    # Key marker: Section 3(a)(9) of the Securities Act (intra-shareholder exchange)
    #             OR explicit "conversion of ... common stock ... into ... common stock"
    if re.search(r"section\s*3\s*\(\s*a\s*\)\s*\(\s*9\s*\)", section_lower):
        matched.append("section_3a9_conversion_exemption")
    if re.search(r"conversion of .{0,40}class\s+[a-z]\s+common stock", section_lower):
        matched.append("class_share_conversion")
    if re.search(r"one-to-one basis", section_lower) and "convert" in section_lower:
        matched.append("one_for_one_conversion")

    # ----- Pattern 2: M&A / Merger-related issuance -----
    ma_patterns = [
        (r"merger agreement", "merger_agreement_reference"),
        (r"business combination", "business_combination_reference"),
        (r"combination agreement", "combination_agreement_reference"),
        (r"acquisition consideration", "acquisition_consideration"),
        (r"merger consideration", "merger_consideration"),
        (r"in connection with the (merger|acquisition|combination|transactions?)\b",
         "in_connection_with_ma"),
        (r"potential future issuance .{0,60}(merger|acquisition|combination)",
         "future_issuance_ma"),
    ]
    for pat, tag in ma_patterns:
        if re.search(pat, section_lower):
            matched.append(tag)

    # ----- Pattern 3: De-SPAC closing -----
    # Markers: "Prospectus" + sponsor entity + often concurrent 5.02 board changes
    #          + large sponsor/founder share issuance
    despac_hits = 0
    if "prospectus" in section_lower:
        despac_hits += 1
        matched.append("prospectus_reference")
    if re.search(r"\bsponsor\b", section_lower):
        despac_hits += 1
        matched.append("sponsor_reference")
    if re.search(r"item\s*5\.02", section_lower) and re.search(
            r"(appointed|appointment) .{0,40}(director|board)", section_lower):
        despac_hits += 1
        matched.append("concurrent_502_board_appointments")
    # Large single-holder issuance (>50M shares to one entity) suggests de-SPAC sponsor
    m_big = re.search(r"([\d,]{8,})\s+shares of class\s+[a-z]\s+common stock", section_lower)
    if m_big:
        try:
            n = int(m_big.group(1).replace(",", ""))
            if n > 20_000_000:
                matched.append(f"large_single_issuance_{n}")
                despac_hits += 1
        except Exception:
            pass

    # ----- Pattern 4: True dilutive PIPE indicators -----
    pipe_patterns = [
        (r"private placement", "private_placement"),
        (r"securities purchase agreement", "securities_purchase_agreement"),
        (r"registered direct offering", "registered_direct"),
        (r"gross proceeds", "gross_proceeds"),
        (r"aggregate purchase price of\s*\$", "aggregate_purchase_price"),
        (r"institutional investors?", "institutional_investors"),
        (r"accredited investors?", "accredited_investors"),
        (r"pre-funded warrant", "prefunded_warrants"),
        (r"registration rights agreement", "registration_rights"),
        (r"placement agent", "placement_agent"),
    ]
    pipe_hits = 0
    for pat, tag in pipe_patterns:
        if re.search(pat, section_lower):
            matched.append(tag)
            pipe_hits += 1

    # ----- Pattern 4b: STRATEGIC PIPE red-flags (opposite direction to distressed) -----
    # Added 2026-04-21 after INTC Sep 2025 finding: the NVDA $5B strategic
    # investment was tagged "dilutive_pipe" but is actually a POSITIVE catalyst.
    # Strategic PIPEs: named corporate investor + collaboration/partnership language.
    # Distressed PIPEs: discounts, warrants, placement agents, registration rights.
    strategic_patterns = [
        (r"strategic (investor|investment|partner|alliance)", "strategic_keyword"),
        (r"(collaboration|partnership) (agreement|arrangement)", "collab_agreement"),
        (r"to develop .{0,60}(ai|product|technology|infrastructure)", "codevelopment"),
        # Named mega-cap corporate investor counts strongly as strategic
        (r"\b(nvidia|microsoft|alphabet|google|apple|meta|amazon|oracle|intel|amd|tesla)\b",
         "megacap_corporate_investor"),
        # "not involving a public offering" + large single buyer = strategic not distressed
        (r"section\s*4\s*\(\s*a\s*\)\s*\(\s*2\s*\)", "section_4a2_private_exempt"),
    ]
    strategic_hits = 0
    for pat, tag in strategic_patterns:
        if re.search(pat, section_lower):
            matched.append(tag)
            strategic_hits += 1

    # ----- Decision logic -----
    # Priority: share_conversion > m_and_a > de_spac > strategic_pipe > dilutive_pipe
    # Conversion patterns are most definitive (Section 3(a)(9) is unambiguous)
    if any(p in matched for p in ["section_3a9_conversion_exemption",
                                   "class_share_conversion",
                                   "one_for_one_conversion"]):
        dilution_type = "share_conversion"
        confidence = "high"
    elif any(p in matched for p in ["merger_agreement_reference",
                                     "business_combination_reference",
                                     "combination_agreement_reference",
                                     "acquisition_consideration",
                                     "merger_consideration",
                                     "in_connection_with_ma",
                                     "future_issuance_ma"]):
        dilution_type = "m_and_a_related"
        confidence = "high" if pipe_hits <= 1 else "medium"
    elif despac_hits >= 2:
        dilution_type = "de_spac_closing"
        confidence = "high" if despac_hits >= 3 else "medium"
    elif strategic_hits >= 2 and pipe_hits >= 1:
        # Strategic PIPE: positive catalyst, opposite direction from short signal
        dilution_type = "strategic_pipe"
        confidence = "high" if strategic_hits >= 3 else "medium"
    elif pipe_hits >= 2:
        # If strategic_hits == 1 and pipe_hits >= 2, still likely distressed
        # but downgrade confidence if any strategic markers present
        dilution_type = "dilutive_pipe"
        if strategic_hits >= 1:
            confidence = "low"  # ambiguous
        else:
            confidence = "high" if pipe_hits >= 3 else "medium"
    elif pipe_hits == 1:
        dilution_type = "dilutive_pipe"
        confidence = "low"
    else:
        dilution_type = "unknown"
        confidence = "low"

    return {
        "dilution_type": dilution_type,
        "confidence": confidence,
        "matched_patterns": matched,
        "excerpt": section[:400],
    }


def filter_largecap_and_price(events: list[dict], min_mcap: float = MIN_MARKET_CAP,
                              min_price: float = MIN_PRICE) -> list[dict]:
    """Filter to large-cap (>$500M) and price > $5 (canonical liquidity floor)."""
    if yf is None:
        print("yfinance not available, skipping cap/price filter", file=sys.stderr)
        return [e for e in events if e.get("ticker")]

    filtered = []
    tickers = list(set(e["ticker"] for e in events if e.get("ticker")))

    for i, tick in enumerate(tickers):
        try:
            info = yf.Ticker(tick).info
            mcap = info.get("marketCap", 0) or 0
            price = info.get("regularMarketPrice") or info.get("previousClose", 0) or 0

            if mcap < min_mcap:
                print(f"  Filter out {tick}: cap ${mcap/1e6:.0f}M < ${min_mcap/1e6:.0f}M",
                      file=sys.stderr)
            elif price < min_price:
                print(f"  Filter out {tick}: price ${price:.2f} < ${min_price:.2f} (liquidity floor)",
                      file=sys.stderr)
            else:
                for e in events:
                    if e.get("ticker") == tick:
                        e["market_cap"] = mcap
                        e["price_at_scan"] = price
                        filtered.append(e)
        except Exception as ex:
            print(f"  Error checking {tick}: {ex}", file=sys.stderr)

        if (i + 1) % 10 == 0:
            print(f"  Cap/price check: {i+1}/{len(tickers)}", file=sys.stderr)
        time.sleep(0.2)

    return filtered


def run_backtest(events: list[dict]) -> dict:
    """Run abnormal return backtest on Item 3.02 events."""
    import market_data
    import db

    db.init_db()

    event_dates = []
    for e in events:
        if e.get("ticker") and e.get("file_date"):
            event_dates.append({"symbol": e["ticker"], "date": e["file_date"]})

    if not event_dates:
        print("No events to backtest", file=sys.stderr)
        return {}

    print(f"\nMeasuring abnormal returns for {len(event_dates)} events...",
          file=sys.stderr)

    result = market_data.measure_event_impact(
        event_dates=event_dates,
        entry_price="open",
        benchmark="SPY",
    )

    print(f"\nEvents measured: {result.get('n_events', 0)}")
    print(f"\n--- ABNORMAL RETURN RESULTS ---")
    print(f"{'Horizon':<12} {'Avg Abn Return':>16} {'Neg% (short)':>14} {'p-value':>10}")
    print("-" * 52)
    for h_key in ['1d', '3d', '5d', '10d']:
        h_data = result.get(h_key, {})
        avg = h_data.get('abnormal_mean', 0)
        neg_rate = h_data.get('negative_rate', 0)
        p = h_data.get('p_value', 1.0)
        print(f"{h_key:<12} {avg:>+14.3f}% {neg_rate:>13.1f}% {p:>10.4f}")

    return {
        "n_events": result.get('n_events', 0),
        "h10_mean": result.get('10d', {}).get('abnormal_mean', 0),
        "h10_p": result.get('10d', {}).get('p_value', 1),
        "h10_neg_rate": result.get('10d', {}).get('negative_rate', 0),
        "full_result": result,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scan EDGAR for 8-K Item 3.02 PIPE / private placement filings")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Look back N days from today")
    parser.add_argument("--json-events", action="store_true",
                        help="Emit final list as JSON events at end of stdout")
    parser.add_argument("--backtest", action="store_true",
                        help="Run full backtest with abnormal returns")
    parser.add_argument("--no-filter", action="store_true",
                        help="Skip market cap / price filter")
    parser.add_argument("--include-nondilutive", action="store_true",
                        help="Include filings classified as non-dilutive (conversion / M&A / de-SPAC). "
                             "Default: exclude. Use for research/diagnostics only.")
    parser.add_argument("--no-classify", action="store_true",
                        help="Skip dilution-type classification (fast path for backtests)")
    parser.add_argument("--min-price", type=float, default=MIN_PRICE,
                        help=f"Min price filter (canonical floor ${MIN_PRICE:.2f})")
    parser.add_argument("--min-mcap-m", type=float, default=MIN_MARKET_CAP / 1e6,
                        help=f"Min market cap in millions (default {MIN_MARKET_CAP/1e6:.0f})")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    if args.days:
        start = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        end = today
    elif args.start:
        start = args.start
        end = args.end or today
    else:
        start = "2024-01-01"
        end = today

    events = search_item_302(start, end)
    print(f"\nRaw events found: {len(events)}", file=sys.stderr)

    events = [e for e in events if e.get("ticker")]
    print(f"Events with tickers: {len(events)}", file=sys.stderr)

    if not args.no_filter and events:
        events = filter_largecap_and_price(
            events, min_mcap=args.min_mcap_m * 1e6, min_price=args.min_price)
        print(f"Passed cap>=${args.min_mcap_m:.0f}M & price>=${args.min_price:.2f}: {len(events)}",
              file=sys.stderr)

    # Dedup: keep only FIRST 3.02 filing per ticker within the scan window.
    # Chronic filer amplification is separate — handled at backtest/analysis time,
    # not at live-trigger time (we only want one position per ticker per window).
    events.sort(key=lambda e: e["file_date"])
    seen_tickers = set()
    deduped = []
    for e in events:
        if e["ticker"] not in seen_tickers:
            seen_tickers.add(e["ticker"])
            deduped.append(e)
    events = deduped

    # Dilution-type classification (added 2026-04-21 after DELL/AVEX/USAR false positives)
    if events and not args.no_classify:
        print(f"\nClassifying {len(events)} filings by dilution type...", file=sys.stderr)
        for e in events:
            acc_with_file = e.get("accession", "")
            classification = classify_302_dilution_type(e["cik"], acc_with_file)
            e["dilution_type"] = classification["dilution_type"]
            e["classification_confidence"] = classification["confidence"]
            e["classification_patterns"] = classification["matched_patterns"]
            time.sleep(SEC_DELAY)

        # Filter out non-dilutive unless override
        # Also filter out strategic PIPEs (opposite direction from short signal)
        # and low-confidence dilutive_pipe (ambiguous w/ strategic markers)
        if not args.include_nondilutive:
            kept = []
            for e in events:
                dt = e.get("dilution_type", "unknown")
                conf = e.get("classification_confidence", "low")
                # Accept: high/medium-confidence dilutive_pipe ONLY.
                # "unknown" and "low"-confidence dilutive_pipe go to human review
                # (queued but flagged as needs_manual_classification).
                if dt == "dilutive_pipe" and conf in ("high", "medium"):
                    kept.append(e)
                elif dt in ("dilutive_pipe", "unknown"):
                    # Flag for manual review but still include
                    e["needs_manual_review"] = True
                    kept.append(e)
                else:
                    print(f"  EXCLUDE {e['ticker']} {e['file_date']}: classified as "
                          f"{dt} ({e.get('classification_confidence')}) — "
                          f"patterns: {e.get('classification_patterns', [])[:4]}",
                          file=sys.stderr)
            events = kept

    print(f"\nFinal events (first per ticker): {len(events)}")
    for e in events:
        mcap_str = f" (${e.get('market_cap',0)/1e9:.1f}B)" if e.get('market_cap') else ""
        price_str = f" @ ${e.get('price_at_scan',0):.2f}" if e.get('price_at_scan') else ""
        dt_str = f" [{e.get('dilution_type','?')}/{e.get('classification_confidence','?')}]" if e.get('dilution_type') else ""
        print(f"  {e['ticker']} {e['file_date']}{mcap_str}{price_str}{dt_str}: "
              f"{e['display_name'][:60]}")

    if args.backtest and events:
        print("\n" + "=" * 70)
        print("RUNNING BACKTEST")
        print("=" * 70)
        run_backtest(events)

    if args.json_events:
        json_events = [
            {
                "symbol": e["ticker"],
                "date": e["file_date"],
                "market_cap": e.get("market_cap", 0),
                "price_at_scan": e.get("price_at_scan", 0),
                "accession": e.get("accession", ""),
                "dilution_type": e.get("dilution_type", "unclassified"),
                "classification_confidence": e.get("classification_confidence", "n/a"),
            }
            for e in events
        ]
        print(json.dumps(json_events))

    return events


if __name__ == "__main__":
    main()
