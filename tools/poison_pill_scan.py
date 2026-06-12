"""Scan EDGAR EFTS for 8-K shareholder-rights-plan ("poison pill") adoptions.

Outputs a JSON list of {symbol, date} events (first filing per ticker in window),
for backtesting via data_tasks.py. Novel event-class probe 2026-06-12.
"""
from __future__ import annotations

import json
import re
import sys
import time

from edgar_efts import efts_get_json, EFTSFetchError, DEFAULT_HEADERS

PAGE = 100
DELAY = 0.3


def search(phrase: str, start: str, end: str) -> list[dict]:
    q = '%22' + phrase.replace(" ", "+") + '%22'
    base = (
        f"https://efts.sec.gov/LATEST/search-index?q={q}&forms=8-K"
        f"&dateRange=custom&startdt={start}&enddt={end}"
    )
    data = efts_get_json(base + f"&from=0&size={PAGE}", label="pill")
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = list(data.get("hits", {}).get("hits", []))
    print(f"  '{phrase}': {total} filings {start}..{end}", file=sys.stderr)
    fetched = len(hits)
    while fetched < min(total, 3000):
        time.sleep(DELAY)
        try:
            d = efts_get_json(base + f"&from={fetched}&size={PAGE}", label="pill-pg")
        except EFTSFetchError as e:
            print(f"  pagination stop @ {fetched}: {e}", file=sys.stderr)
            break
        ph = d.get("hits", {}).get("hits", [])
        if not ph:
            break
        hits.extend(ph)
        fetched += len(ph)
    return hits


def parse(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        src = h.get("_source", {})
        names = src.get("display_names", [])
        fd = src.get("file_date", "")
        items = src.get("items", [])
        # Rights-plan adoption is disclosed under Item 3.03 (material modification
        # to rights of security holders) and/or 1.01 (material agreement). Require
        # one of these to drop unrelated mentions.
        if items and not any(str(it).startswith(("3.03", "1.01", "8.01")) for it in items):
            continue
        ticker = None
        if names:
            m = re.search(r"\(([A-Z]{1,5})\)", names[0])
            if m:
                ticker = m.group(1)
        if ticker and fd:
            out.append({"ticker": ticker, "date": fd,
                        "name": names[0] if names else "",
                        "cik": (src.get("ciks") or [""])[0].lstrip("0")})
    return out


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2015-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2024-12-31"
    all_hits = []
    for phrase in ["shareholder rights plan", "stockholder rights plan",
                   "tax benefit preservation plan"]:
        all_hits.extend(search(phrase, start, end))
    events = parse(all_hits)
    # dedup: first filing per ticker
    first = {}
    for e in sorted(events, key=lambda x: x["date"]):
        if e["ticker"] not in first:
            first[e["ticker"]] = e
    uniq = sorted(first.values(), key=lambda x: x["date"])
    print(f"  unique tickers (first filing): {len(uniq)}", file=sys.stderr)
    print(json.dumps([{"symbol": e["ticker"], "date": e["date"]} for e in uniq]))


if __name__ == "__main__":
    main()
