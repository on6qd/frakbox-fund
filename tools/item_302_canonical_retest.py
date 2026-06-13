#!/usr/bin/env python3
"""Canonical retest of 8-K Item 3.02 PIPE short signal, classifier-filtered.

P0 task (handoff 2026-04-21): Rerun canonical retest with classifier=dilutive_pipe
high/med confidence only. Apply cluster buffer 30d + SPY-adj + price>$5.

Expected: magnitude materially LARGER than pooled -5.21% (n=430) since
strategic PIPEs and share-conversions removed. If PASSES -> re-enable 84f218f0
triggers. If FAILS -> retire signal.

EFTS paginates out at offset 100, so we chunk by 30-day windows.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.item_302_pipe_scanner import (
    search_item_302,
    classify_302_dilution_type,
    SEC_DELAY,
)

# Fast threaded filter using yfinance
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import yfinance as yf
except ImportError:
    yf = None


def fast_filter_largecap_and_price(events, min_mcap=500_000_000, min_price=5.0, workers=10):
    """Threaded cap/price filter using yfinance. ~10x faster than serial."""
    if yf is None:
        return [e for e in events if e.get("ticker")]

    tickers = sorted({e["ticker"] for e in events if e.get("ticker")})
    print(f"  Filtering {len(tickers)} unique tickers with {workers} workers...", file=sys.stderr)

    info_cache = {}

    def fetch(tick):
        try:
            info = yf.Ticker(tick).info
            mcap = info.get("marketCap", 0) or 0
            price = info.get("regularMarketPrice") or info.get("previousClose", 0) or 0
            return tick, mcap, price
        except Exception:
            return tick, 0, 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, t): t for t in tickers}
        done = 0
        for f in as_completed(futs):
            tick, mcap, price = f.result()
            info_cache[tick] = (mcap, price)
            done += 1
            if done % 100 == 0:
                print(f"  Cap/price check: {done}/{len(tickers)}", file=sys.stderr)

    kept = []
    for e in events:
        t = e.get("ticker")
        if not t or t not in info_cache:
            continue
        mcap, price = info_cache[t]
        if mcap >= min_mcap and price >= min_price:
            e["market_cap"] = mcap
            e["price_at_scan"] = price
            kept.append(e)
    return kept


def chunked_date_range(start: str, end: str, days: int = 30):
    cur = datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while cur < stop:
        nxt = min(cur + timedelta(days=days), stop)
        yield cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")
        cur = nxt + timedelta(days=1)


def apply_30d_cluster_buffer(events: list[dict]) -> list[dict]:
    """Keep first filing per ticker in rolling 30-day window.

    If a ticker has two filings within 30 days, keep only the FIRST one.
    Then if there's another filing >30 days after the first, keep that too.
    """
    by_ticker = {}
    for e in events:
        t = e["ticker"]
        by_ticker.setdefault(t, []).append(e)

    kept = []
    for t, evs in by_ticker.items():
        evs.sort(key=lambda e: e["file_date"])
        last_dt = None
        for e in evs:
            d = datetime.strptime(e["file_date"], "%Y-%m-%d")
            if last_dt is None or (d - last_dt).days > 30:
                kept.append(e)
                last_dt = d
    kept.sort(key=lambda e: e["file_date"])
    return kept


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-04-18")
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--min-mcap-m", type=float, default=500)
    p.add_argument("--chunk-days", type=int, default=30)
    p.add_argument("--skip-classify", action="store_true",
                   help="Diagnostic: skip classifier filter to compare pre/post classifier")
    p.add_argument("--output-json", default="tools/item_302_canonical_retest_events.json",
                   help="Write final event list to this JSON")
    p.add_argument("--raw-events-cache", default="tools/item_302_raw_events.json",
                   help="Cache raw-event list here to skip re-fetch on reruns")
    p.add_argument("--classify-workers", type=int, default=6,
                   help="Parallel SEC-fetch workers for classifier phase")
    args = p.parse_args()

    # Phase 1: collect raw events across chunked date windows (cache-friendly)
    import os
    cache_path = args.raw_events_cache
    if cache_path and os.path.exists(cache_path):
        with open(cache_path) as f:
            all_events = json.load(f)
        print(f"Loaded {len(all_events)} raw events from cache: {cache_path}", file=sys.stderr)
    else:
        all_events = []
        chunks = list(chunked_date_range(args.start, args.end, args.chunk_days))
        print(f"Scanning {len(chunks)} chunks of {args.chunk_days} days each...", file=sys.stderr)

        for i, (s, e) in enumerate(chunks):
            evs = search_item_302(s, e)
            evs = [x for x in evs if x.get("ticker")]
            all_events.extend(evs)
            print(f"  [{i+1}/{len(chunks)}] {s} to {e}: {len(evs)} events (running total: {len(all_events)})",
                  file=sys.stderr)
            time.sleep(SEC_DELAY)

        # Dedup by (cik, file_date) in case chunk overlap
        seen = set()
        deduped = []
        for e in all_events:
            k = (e["cik"], e["file_date"])
            if k not in seen:
                seen.add(k)
                deduped.append(e)
        all_events = deduped
        with open(cache_path, "w") as f:
            json.dump(all_events, f, default=str)
        print(f"Cached {len(all_events)} raw events to {cache_path}", file=sys.stderr)
    print(f"\nTotal raw events with tickers: {len(all_events)}", file=sys.stderr)

    # Phase 2: largecap + price filter
    print("\nApplying cap/price filter (threaded)...", file=sys.stderr)
    filtered = fast_filter_largecap_and_price(
        all_events, min_mcap=args.min_mcap_m * 1e6, min_price=args.min_price)
    print(f"After cap/price filter: {len(filtered)}", file=sys.stderr)

    # Phase 3: classify each filing (threaded to SEC; SEC allows ~10 req/s from one IP)
    if not args.skip_classify:
        print(f"\nClassifying {len(filtered)} filings with {args.classify_workers} workers...", file=sys.stderr)

        def do_classify(idx_e):
            i, e = idx_e
            acc = e.get("accession", "")
            cls = classify_302_dilution_type(e["cik"], acc)
            return i, cls

        with ThreadPoolExecutor(max_workers=args.classify_workers) as ex:
            futs = {ex.submit(do_classify, (i, e)): i for i, e in enumerate(filtered)}
            done = 0
            for f in as_completed(futs):
                i, cls = f.result()
                e = filtered[i]
                e["dilution_type"] = cls["dilution_type"]
                e["classification_confidence"] = cls["confidence"]
                e["classification_patterns"] = cls["matched_patterns"]
                done += 1
                if done % 25 == 0:
                    print(f"  Classified {done}/{len(filtered)}", file=sys.stderr)

        # Class counts
        by_class = {}
        for e in filtered:
            k = (e.get("dilution_type"), e.get("classification_confidence"))
            by_class[k] = by_class.get(k, 0) + 1
        print("\nClassification breakdown:", file=sys.stderr)
        for (dt, conf), n in sorted(by_class.items(), key=lambda x: -x[1]):
            print(f"  {dt:<20} {conf:<10} {n}", file=sys.stderr)

        # Filter: keep only dilutive_pipe high/medium confidence
        true_pipe = [e for e in filtered
                     if e.get("dilution_type") == "dilutive_pipe"
                     and e.get("classification_confidence") in ("high", "medium")]
        print(f"\nTrue dilutive_pipe (high/med confidence): {len(true_pipe)}", file=sys.stderr)
    else:
        true_pipe = filtered
        print(f"\nSkipping classifier — using all {len(true_pipe)} filtered events", file=sys.stderr)

    # Phase 4: apply 30d cluster buffer
    buffered = apply_30d_cluster_buffer(true_pipe)
    print(f"After 30d cluster buffer (per ticker): {len(buffered)}", file=sys.stderr)

    # Dump JSON event list
    out = []
    for e in buffered:
        out.append({
            "symbol": e["ticker"],
            "date": e["file_date"],
            "dilution_type": e.get("dilution_type"),
            "classification_confidence": e.get("classification_confidence"),
            "market_cap": e.get("market_cap"),
            "price_at_scan": e.get("price_at_scan"),
            "cik": e.get("cik"),
            "accession": e.get("accession"),
        })
    with open(args.output_json, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"\nWrote {len(out)} events to {args.output_json}", file=sys.stderr)

    # Phase 5: backtest
    print("\nRunning SPY-adjusted backtest...", file=sys.stderr)
    import market_data
    import db
    db.init_db()

    result = market_data.measure_event_impact(
        event_dates=[{"symbol": e["ticker"], "date": e["file_date"]} for e in buffered],
        entry_price="open",
        benchmark="SPY",
    )

    print(f"\nEvents measured: {result.get('n_events', 0)}")
    print("\n--- CLASSIFIER-FILTERED CANONICAL RETEST ---")
    print(f"{'Horizon':<8} {'Avg Abn':>10} {'Neg%':>8} {'p-value':>10}")
    print("-" * 40)
    for h in ['1d', '3d', '5d', '10d']:
        d = result.get(h, {})
        print(f"{h:<8} {d.get('abnormal_mean', 0):>+9.3f}% {d.get('negative_rate', 0):>7.1f}% {d.get('p_value', 1):>10.4f}")

    # Stratify by recency
    print("\n--- Stratify by recency (2024+) ---")
    recent = [e for e in buffered if e["file_date"] >= "2024-01-01"]
    if recent:
        result_r = market_data.measure_event_impact(
            event_dates=[{"symbol": e["ticker"], "date": e["file_date"]} for e in recent],
            entry_price="open",
            benchmark="SPY",
        )
        print(f"Events: {result_r.get('n_events', 0)}")
        for h in ['5d', '10d']:
            d = result_r.get(h, {})
            print(f"  {h}: {d.get('abnormal_mean', 0):+.3f}%  neg {d.get('negative_rate', 0):.1f}%  p={d.get('p_value', 1):.4f}")

    # Also store task result
    summary = {
        "status": "ok",
        "test_type": "item_302_classifier_filtered_canonical_retest",
        "n_raw": len(all_events),
        "n_largecap_price5": len(filtered),
        "n_true_pipe": len(true_pipe),
        "n_cluster_buffered": len(buffered),
        "pooled": {h: result.get(h, {}) for h in ['1d', '3d', '5d', '10d']},
        "recent_2024plus": {h: result_r.get(h, {}) if recent else None for h in ['5d', '10d']} if recent else None,
    }
    tid = db.store_task_result(
        task_type="item_302_canonical_retest",
        parameters={"start": args.start, "end": args.end, "min_price": args.min_price,
                    "min_mcap_m": args.min_mcap_m},
        result=summary,
        summary=json.dumps(summary),
    )
    print(f"\nStored task result: {tid}")


if __name__ == "__main__":
    main()
