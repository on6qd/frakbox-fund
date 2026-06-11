"""
strategic_alt_acquisition_scraper.py

Isolate a pure ACQUISITION-TARGET subset from the noisy "strategic alternatives"
8-K population using EDGAR EFTS exact-phrase AND queries.

Idea: EFTS supports exact-phrase AND queries (space-separated quoted phrases).
A filing matching BOTH "strategic alternatives" AND a sale-of-company keyword is
far more likely to be a real takeover candidate than the boilerplate population
(which buries "strategic alternatives" in risk factors / refinancing language).

This does the keyword classification AT THE EFTS LAYER — no full-text fetch needed,
so it sidesteps the SEC rate-limiting that blocked prior sessions.

Treatment keyword phrases (acquisition-target signal):
    "sale of the Company"
    "unsolicited proposal"
    "go private"          (take-private)

Output: deduped (symbol, date) events, large-cap filtered, split by period.

CLI:
    python tools/strategic_alt_acquisition_scraper.py --start 2021-01-01 --end 2025-12-31
"""
import argparse
import json
import time

import tools.merger_catalyst_scraper as m

BASE = '"strategic alternatives"'
TREATMENT_PHRASES = [
    '"sale of the Company"',
    '"unsolicited proposal"',
    '"go private"',
]


def _fetch_retry(query, start, end, offset, tries=4):
    last = None
    for t in range(tries):
        try:
            return m._fetch_page(query, start, end, offset)
        except Exception as e:
            last = e
            time.sleep(1.5 * (t + 1))
    raise last


def _scrape_window(query, start, end, max_pages=10):
    """Paginate one EFTS query over one window, return parsed event dicts."""
    out = []
    offset = 0
    for _ in range(max_pages):
        try:
            j = _fetch_retry(query, start, end, offset)
        except Exception as e:
            print(f"  [warn] fetch error {start}..{end} offset {offset}: {e}")
            break
        hits = j.get("hits", {}).get("hits", [])
        if not hits:
            break
        out.extend(m._parse_hits(hits, query))
        total = j.get("hits", {}).get("total", {}).get("value", 0)
        offset += len(hits)
        if offset >= total:
            break
        time.sleep(0.3)
    return out


def _scrape_query(query, start, end, max_pages=10):
    """Chunk by calendar year (EFTS 500s on large ranges), union results."""
    out = []
    sy, ey = int(start[:4]), int(end[:4])
    for yr in range(sy, ey + 1):
        ws = f"{yr}-01-01" if yr > sy else start
        we = f"{yr}-12-31" if yr < ey else end
        out.extend(_scrape_window(query, ws, we, max_pages))
        time.sleep(0.4)
    return out


def gather(start, end):
    """Gather treatment (sale-keyword) events. Returns deduped list."""
    seen = {}
    for phrase in TREATMENT_PHRASES:
        q = f"{BASE} {phrase}"
        rows = _scrape_query(q, start, end)
        print(f"  query {q!r}: {len(rows)} raw hits")
        for r in rows:
            key = (r["symbol"], r["date"])
            if key not in seen:
                r["matched_phrase"] = phrase
                seen[key] = r
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="tools/_strategic_alt_events.json")
    args = ap.parse_args()

    print(f"Gathering treatment events {args.start}..{args.end}")
    events = gather(args.start, args.end)
    print(f"Deduped treatment events: {len(events)}")

    # Large-cap filter (filter_to_largecap takes a DataFrame with a 'ticker' col)
    import pandas as pd
    from tools.largecap_filter import filter_to_largecap
    symbols = sorted({e["symbol"] for e in events})
    print(f"Unique symbols: {len(symbols)} — filtering to large-cap...")
    df = pd.DataFrame({"ticker": symbols})
    kept_df = filter_to_largecap(df, ticker_col="ticker", verbose=False)
    keep = set(kept_df["ticker"].tolist())
    lc = [e for e in events if e["symbol"] in keep]
    print(f"Large-cap events: {len(lc)}")

    # Tag with timing for next-open entry (avoid announcement-day pop)
    for e in lc:
        e["timing"] = "after_hours"

    payload = {
        "start": args.start, "end": args.end,
        "total_treatment": len(events),
        "largecap_treatment": len(lc),
        "events": sorted(lc, key=lambda x: x["date"]),
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {args.out}")

    # Period split summary
    disc = [e for e in lc if e["date"] < "2024-01-01"]
    oos = [e for e in lc if e["date"] >= "2024-01-01"]
    print(f"Discovery (pre-2024): {len(disc)} | OOS (2024+): {len(oos)}")


if __name__ == "__main__":
    main()
