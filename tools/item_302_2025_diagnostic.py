#!/usr/bin/env python3
"""Diagnostic: classify all 2025-2026 large-cap Item 3.02 filings.

Uses existing market_cap_cache.json (rate-limit safe) to filter.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

sys.path.insert(0, '/Users/frakbox/Bots/financial_researcher')
from tools.item_302_pipe_scanner import classify_302_dilution_type

RAW_EVENTS = 'tools/item_302_raw_events.json'
CAP_CACHE = 'data/ticker_cache/market_cap_cache.json'
MIN_MCAP_M = 500   # $500M
MIN_PRICE = 5.0


def main():
    cap_cache = json.load(open(CAP_CACHE))
    all_events = json.load(open(RAW_EVENTS))

    events_25 = [e for e in all_events
                 if e['file_date'] >= '2025-01-01' and e.get('ticker')]
    events_2324 = [e for e in all_events
                   if e['file_date'] < '2025-01-01' and e.get('ticker')]
    print(f"2025+ events with tickers: {len(events_25)}", file=sys.stderr)
    print(f"2023-2024 events with tickers: {len(events_2324)}", file=sys.stderr)

    def filter_cap(events):
        kept = []
        missing_cap = 0
        for e in events:
            t = e['ticker']
            cap_m = cap_cache.get(t)
            if cap_m is None:
                missing_cap += 1
                continue
            if cap_m >= MIN_MCAP_M:
                e['market_cap'] = cap_m * 1e6
                kept.append(e)
        return kept, missing_cap

    largecap_25, miss_25 = filter_cap(events_25)
    largecap_2324, miss_23 = filter_cap(events_2324)
    print(f"\n2025+ largecap: {len(largecap_25)} (missing cap: {miss_25})", file=sys.stderr)
    print(f"2023-2024 largecap: {len(largecap_2324)} (missing cap: {miss_23})", file=sys.stderr)

    # NOTE: no price filter — relies on cached price data absent. Classifier runs regardless.

    def classify_set(events, label):
        print(f"\nClassifying {len(events)} {label} events with 6 workers...",
              file=sys.stderr)

        def do_cls(idx_e):
            i, e = idx_e
            cls = classify_302_dilution_type(e['cik'], e.get('accession', ''))
            return i, cls

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(do_cls, (i, e)): i
                    for i, e in enumerate(events)}
            done = 0
            for f in as_completed(futs):
                i, cls = f.result()
                e = events[i]
                e['dilution_type'] = cls['dilution_type']
                e['classification_confidence'] = cls['confidence']
                e['classification_patterns'] = cls['matched_patterns']
                e['excerpt'] = cls.get('excerpt', '')[:300]
                done += 1
                if done % 25 == 0:
                    print(f"  classified: {done}/{len(events)}", file=sys.stderr)
        return events

    # Only classify 2025+ (2023-2024 already classified in cached results)
    # Load classification cache if exists
    CLS_CACHE_FILE = 'tools/item_302_2025_cls_cache.json'
    import os
    if os.path.exists(CLS_CACHE_FILE):
        cls_cache = json.load(open(CLS_CACHE_FILE))
        print(f"Loaded {len(cls_cache)} cached classifications", file=sys.stderr)
    else:
        cls_cache = {}

    # Apply cache
    to_classify = []
    for e in largecap_25:
        key = e.get('accession', '') + '::' + e['cik']
        if key in cls_cache:
            c = cls_cache[key]
            e['dilution_type'] = c['dilution_type']
            e['classification_confidence'] = c['confidence']
            e['classification_patterns'] = c['matched_patterns']
            e['excerpt'] = c.get('excerpt', '')[:300]
        else:
            to_classify.append(e)
    print(f"To classify (not in cache): {len(to_classify)}", file=sys.stderr)

    if to_classify:
        classified_25 = classify_set(to_classify, '2025+ largecap')
        # update cache — only cache successful fetches (no http_/fetch_error markers)
        for e in classified_25:
            key = e.get('accession', '') + '::' + e['cik']
            patterns = e.get('classification_patterns', [])
            is_failure = any(p.startswith('http_') or 'fetch_error' in p
                             for p in patterns)
            if not is_failure:
                cls_cache[key] = {
                    'dilution_type': e['dilution_type'],
                    'confidence': e['classification_confidence'],
                    'matched_patterns': patterns,
                    'excerpt': e.get('excerpt', '')[:300],
                }
        with open(CLS_CACHE_FILE, 'w') as f:
            json.dump(cls_cache, f, default=str)
        print(f"Saved {len(cls_cache)} successful classifications to cache",
              file=sys.stderr)

    classified_25 = largecap_25  # all now have dilution_type attached

    # How many were HTTP errors (failed fetches)?
    http_errs = [e for e in classified_25
                 if any('http_' in p or 'fetch_error' in p
                        for p in e.get('classification_patterns', []))]
    print(f"\nFetch errors (HTTP 4xx/5xx, rate-limit etc): {len(http_errs)}",
          file=sys.stderr)

    # Tier counts
    c = Counter((e['dilution_type'], e['classification_confidence'])
                for e in classified_25)
    print(f"\n=== 2025-2026 LARGE-CAP CLASSIFICATION BREAKDOWN (n={len(classified_25)}) ===")
    for (dt, conf), n in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {dt:<24} {conf:<10} {n}")

    # Dilutive_pipe high/med
    dp_hm = [e for e in classified_25
             if e['dilution_type'] == 'dilutive_pipe'
             and e['classification_confidence'] in ('high', 'medium')]
    print(f"\nDilutive_pipe high/med: {len(dp_hm)} (expected 0)")
    for e in dp_hm[:30]:
        print(f"  {e['ticker']} {e['file_date']} mcap=${e['market_cap']/1e9:.1f}B "
              f"patterns={e['classification_patterns'][:6]}")

    # Strategic pipe
    sp = [e for e in classified_25 if e['dilution_type'] == 'strategic_pipe']
    print(f"\nStrategic_pipe: {len(sp)}")
    for e in sp[:30]:
        print(f"  {e['ticker']} {e['file_date']} mcap=${e['market_cap']/1e9:.1f}B "
              f"conf={e['classification_confidence']} "
              f"patterns={e['classification_patterns'][:6]}")

    # Dilutive_pipe low (downgraded)
    dp_low = [e for e in classified_25
              if e['dilution_type'] == 'dilutive_pipe'
              and e['classification_confidence'] == 'low']
    print(f"\nDilutive_pipe low (pipe>=2 + 1 strategic marker): {len(dp_low)}")
    for e in dp_low[:10]:
        print(f"  {e['ticker']} {e['file_date']} mcap=${e['market_cap']/1e9:.1f}B "
              f"patterns={e['classification_patterns'][:6]}")

    # Save
    out = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M'),
        'n_events_25plus_largecap': len(classified_25),
        'tier_counts_25plus': {f"{dt}/{conf}": n for (dt, conf), n in c.items()},
        'dilutive_pipe_highmed': [
            {'ticker': e['ticker'], 'date': e['file_date'],
             'patterns': e['classification_patterns'],
             'excerpt': e['excerpt']} for e in dp_hm],
        'strategic_pipe': [
            {'ticker': e['ticker'], 'date': e['file_date'],
             'conf': e['classification_confidence'],
             'patterns': e['classification_patterns'],
             'excerpt': e['excerpt']} for e in sp],
        'dilutive_pipe_low': [
            {'ticker': e['ticker'], 'date': e['file_date'],
             'patterns': e['classification_patterns'],
             'excerpt': e['excerpt']} for e in dp_low],
    }
    with open('tools/item_302_2025_diagnostic_output.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote tools/item_302_2025_diagnostic_output.json", file=sys.stderr)


if __name__ == '__main__':
    main()
