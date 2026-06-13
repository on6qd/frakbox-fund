#!/usr/bin/env python3
"""Item 3.02 DISTRESSED classifier v2 canonical retest — 2026-04-21.

Applies `classify_302_distressed` over all largecap+price-filtered Item 3.02
filings (2023-01-01 .. 2026-04-18). Uses a JSON cache of classifier results
so reruns are fast.

Pipeline mirrors item_302_canonical_retest.py, but swaps the filter predicate:
    OLD:  dt == 'dilutive_pipe' and conf in ('high','medium')   (v1, inverts direction)
    NEW:  tier in ('strong_distressed', 'moderate_distressed')  (v2)

Output
------
- Classification breakdown by tier
- SPY-adjusted backtest per tier (pooled, 2024+ recent subset)
- XBI-adjusted 5d/10d for strong+moderate cohort
- Pass criterion: strong_distressed tier shows p < 0.05 AND mean_abn < -2%
  at 5d AND 10d horizons, in both pooled and 2024+ samples.

Caches
------
- Raw events cached at tools/item_302_raw_events.json (re-used)
- Distressed classifications cached at
  tools/item_302_distressed_classifications.json (new — keyed by accession)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import requests

try:
    import yfinance as yf
except ImportError:
    yf = None

from tools.item_302_distressed_classifier import classify_302_distressed

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
SEC_DELAY = 0.20  # SEC fair-use target: ~5 req/s
CACHE_CLASSIF = "tools/item_302_distressed_classifications.json"


def _fetch_8k_text(cik: str, accession: str, max_retries: int = 3) -> str | None:
    if ":" in accession:
        accession_num, file_hint = accession.split(":")[:2]
    else:
        accession_num, file_hint = accession, ""
    accession_num = accession_num.replace("-", "")
    cik_num = str(int(cik.lstrip("0"))) if cik.lstrip("0") else cik
    if not file_hint:
        return None
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{accession_num}/{file_hint}"
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                text = re.sub(r"<[^>]+>", " ", resp.text)
                text = re.sub(r"\s+", " ", text).strip()
                text = text.replace("&#8220;", '"').replace("&#8221;", '"')
                text = text.replace("&#8217;", "'").replace("&#160;", " ")
                text = text.replace("&amp;", "&")
                return text
            if resp.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            # 404/403/etc non-retryable
            return None
        except Exception:
            time.sleep(delay)
            delay *= 2
    return None


def fast_cap_price(events, min_mcap=500_000_000, min_price=5.0, workers=10):
    if yf is None:
        return [e for e in events if e.get("ticker")]
    tickers = sorted({e["ticker"] for e in events if e.get("ticker")})
    info_cache = {}

    def fetch(t):
        try:
            info = yf.Ticker(t).info
            mcap = info.get("marketCap", 0) or 0
            price = info.get("regularMarketPrice") or info.get("previousClose", 0) or 0
            return t, mcap, price
        except Exception:
            return t, 0, 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch, t): t for t in tickers}
        done = 0
        for f in as_completed(futs):
            t, mcap, price = f.result()
            info_cache[t] = (mcap, price)
            done += 1
            if done % 200 == 0:
                print(f"  cap/price {done}/{len(tickers)}", file=sys.stderr)

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


def classify_all(events, workers=6, cache_path=CACHE_CLASSIF):
    """Classify each event's Item 3.02 section. Cache keyed by accession."""
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
        print(f"Loaded {len(cache)} cached classifications", file=sys.stderr)

    todo = [e for e in events if e.get("accession") and e["accession"] not in cache]
    print(f"Classifying {len(todo)} new events ({workers} workers)...", file=sys.stderr)

    def do(e):
        # Small jitter so threads don't stampede
        time.sleep(SEC_DELAY)
        text = _fetch_8k_text(e["cik"], e["accession"])
        if not text:
            return e["accession"], None
        cls = classify_302_distressed(
            text,
            market_cap=e.get("market_cap"),
            price=e.get("price_at_scan"),
        )
        return e["accession"], {
            "tier": cls.tier,
            "distressed_score": cls.distressed_score,
            "premium_score": cls.premium_score,
            "net": cls.net_distressed_score,
            "matched": cls.matched_patterns,
            "reasons": cls.reasons,
        }

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(do, e): e for e in todo}
            done = 0
            for f in as_completed(futs):
                acc, cls = f.result()
                if cls is not None:
                    cache[acc] = cls
                done += 1
                if done % 25 == 0:
                    print(f"  classified {done}/{len(todo)}", file=sys.stderr)
                    # periodic checkpoint
                    with open(cache_path, "w") as fh:
                        json.dump(cache, fh)
        with open(cache_path, "w") as fh:
            json.dump(cache, fh)
        print(f"Saved {len(cache)} classifications to {cache_path}", file=sys.stderr)

    # Attach classification to events
    for e in events:
        c = cache.get(e.get("accession"))
        if c:
            e["v2_tier"] = c["tier"]
            e["v2_distressed"] = c["distressed_score"]
            e["v2_premium"] = c["premium_score"]
            e["v2_net"] = c["net"]
            e["v2_matched"] = c["matched"]
            e["v2_reasons"] = c["reasons"]
        else:
            e["v2_tier"] = "fetch_failed"
    return events


def apply_30d_cluster_buffer(events):
    by_ticker = {}
    for e in events:
        by_ticker.setdefault(e["ticker"], []).append(e)
    kept = []
    for t, evs in by_ticker.items():
        evs.sort(key=lambda e: e["file_date"])
        last = None
        for e in evs:
            d = datetime.strptime(e["file_date"], "%Y-%m-%d")
            if last is None or (d - last).days > 30:
                kept.append(e)
                last = d
    kept.sort(key=lambda e: e["file_date"])
    return kept


def backtest_cohort(events, label, benchmark="SPY"):
    import market_data
    import db
    db.init_db()
    if not events:
        return None
    result = market_data.measure_event_impact(
        event_dates=[{"symbol": e["ticker"], "date": e["file_date"]} for e in events],
        entry_price="open",
        benchmark=benchmark,
    )
    print(f"\n--- {label} [{benchmark}-adj] n={len(events)} (measured={result.get('n_events', 0)}) ---",
          file=sys.stderr)
    print(f"  {'H':<4} {'Avg':>8} {'Neg%':>6} {'p':>8}", file=sys.stderr)
    for h in ['1d', '3d', '5d', '10d', '20d']:
        d = result.get(h) or {}
        print(f"  {h:<4} {d.get('abnormal_mean', 0):>+7.2f}%  "
              f"{d.get('negative_rate', 0):>5.1f}%  "
              f"p={d.get('p_value', 1):>6.4f}", file=sys.stderr)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-cache", default="tools/item_302_raw_events.json")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-04-18")
    ap.add_argument("--min-mcap-m", type=float, default=500)
    ap.add_argument("--min-price", type=float, default=5.0)
    ap.add_argument("--classify-workers", type=int, default=6)
    ap.add_argument("--output-json", default="tools/item_302_distressed_retest_events.json")
    args = ap.parse_args()

    # Load raw events
    with open(args.raw_cache) as f:
        raw = json.load(f)
    print(f"Loaded {len(raw)} raw events from {args.raw_cache}", file=sys.stderr)
    raw = [e for e in raw
           if e.get("file_date", "") >= args.start and e.get("file_date", "") <= args.end
           and e.get("ticker")]
    print(f"After date filter {args.start}..{args.end}: {len(raw)}", file=sys.stderr)

    # Cap/price filter
    print("\nApplying cap/price filter...", file=sys.stderr)
    filtered = fast_cap_price(raw, args.min_mcap_m * 1e6, args.min_price)
    print(f"After cap+price filter: {len(filtered)}", file=sys.stderr)

    # v2 distressed classification
    print("\nApplying v2 distressed classifier...", file=sys.stderr)
    classified = classify_all(filtered, workers=args.classify_workers)

    # Tier breakdown
    from collections import Counter
    tier_counts = Counter(e.get("v2_tier") for e in classified)
    print("\nv2 tier breakdown:", file=sys.stderr)
    for tier, n in tier_counts.most_common():
        print(f"  {tier:<25} {n}", file=sys.stderr)

    # Dump full classified events
    dump = []
    for e in classified:
        dump.append({
            "symbol": e["ticker"],
            "date": e["file_date"],
            "cik": e["cik"],
            "accession": e.get("accession"),
            "market_cap": e.get("market_cap"),
            "price_at_scan": e.get("price_at_scan"),
            "v2_tier": e.get("v2_tier"),
            "v2_distressed": e.get("v2_distressed"),
            "v2_premium": e.get("v2_premium"),
            "v2_net": e.get("v2_net"),
            "v2_reasons": e.get("v2_reasons"),
        })
    with open(args.output_json, "w") as f:
        json.dump(dump, f, indent=1, default=str)
    print(f"\nWrote {len(dump)} classified events to {args.output_json}", file=sys.stderr)

    # --- backtest per tier ---
    strong = [e for e in classified if e.get("v2_tier") == "strong_distressed"]
    moderate = [e for e in classified if e.get("v2_tier") == "moderate_distressed"]
    strong_mod = strong + moderate
    ambiguous = [e for e in classified if e.get("v2_tier") == "ambiguous_distressed"]
    not_distressed = [e for e in classified if e.get("v2_tier") == "not_distressed"]
    premium = [e for e in classified if e.get("v2_tier") == "premium"]

    # Cluster-buffer each cohort independently
    strong_buf = apply_30d_cluster_buffer(strong)
    mod_buf = apply_30d_cluster_buffer(moderate)
    strong_mod_buf = apply_30d_cluster_buffer(strong_mod)
    ambiguous_buf = apply_30d_cluster_buffer(ambiguous)
    premium_buf = apply_30d_cluster_buffer(premium)

    print("\n\n===== V2 CANONICAL RETEST BACKTESTS =====", file=sys.stderr)

    results = {}
    results["strong_distressed"] = backtest_cohort(strong_buf, "strong_distressed pooled")
    results["moderate_distressed"] = backtest_cohort(mod_buf, "moderate_distressed pooled")
    results["strong_plus_moderate"] = backtest_cohort(strong_mod_buf, "strong+moderate pooled")
    results["ambiguous"] = backtest_cohort(ambiguous_buf, "ambiguous pooled")
    results["premium"] = backtest_cohort(premium_buf, "premium pooled")

    # Recent 2024+ stratification
    print("\n--- 2024+ recent subset ---", file=sys.stderr)
    strong_mod_2024 = [e for e in strong_mod_buf if e["file_date"] >= "2024-01-01"]
    results["strong_plus_moderate_2024plus"] = backtest_cohort(
        strong_mod_2024, "strong+moderate 2024+")
    strong_2024 = [e for e in strong_buf if e["file_date"] >= "2024-01-01"]
    results["strong_distressed_2024plus"] = backtest_cohort(
        strong_2024, "strong_distressed 2024+")

    # XBI-benchmark sanity for strong+moderate
    print("\n--- XBI benchmark sanity ---", file=sys.stderr)
    results["strong_plus_moderate_xbi"] = backtest_cohort(
        strong_mod_buf, "strong+moderate XBI-adj", benchmark="XBI")

    # Pass criterion
    def extract(res, h):
        if not res or not res.get(h):
            return None
        d = res[h]
        return {"mean": d.get("abnormal_mean"), "p": d.get("p_value"),
                "neg": d.get("negative_rate"), "n": res.get("n_events")}

    def pass_short(res):
        if not res:
            return False
        for h in ("5d", "10d"):
            d = res.get(h) or {}
            if (d.get("p_value", 1) >= 0.05) or (d.get("abnormal_mean", 0) >= -2.0):
                return False
        return True

    pass_strong = pass_short(results.get("strong_distressed"))
    pass_strong_recent = pass_short(results.get("strong_distressed_2024plus"))
    pass_combined = pass_short(results.get("strong_plus_moderate"))
    pass_combined_recent = pass_short(results.get("strong_plus_moderate_2024plus"))

    # Summary
    print("\n\n===== PASS CRITERIA =====", file=sys.stderr)
    print(f"strong_distressed pooled       pass={pass_strong}", file=sys.stderr)
    print(f"strong_distressed 2024+        pass={pass_strong_recent}", file=sys.stderr)
    print(f"strong+moderate pooled         pass={pass_combined}", file=sys.stderr)
    print(f"strong+moderate 2024+          pass={pass_combined_recent}", file=sys.stderr)

    summary = {
        "test_type": "item_302_distressed_v2_retest",
        "date": "2026-04-21",
        "n_raw": len(raw),
        "n_largecap_price5": len(filtered),
        "tier_counts": dict(tier_counts),
        "strong_distressed": {
            "n_post_cluster": len(strong_buf),
            "n_2024plus": len(strong_2024),
            "horizons": {h: extract(results.get("strong_distressed"), h) for h in ("1d","3d","5d","10d","20d")},
            "horizons_2024plus": {h: extract(results.get("strong_distressed_2024plus"), h) for h in ("5d","10d")},
            "pass": pass_strong,
            "pass_2024plus": pass_strong_recent,
        },
        "strong_plus_moderate": {
            "n_post_cluster": len(strong_mod_buf),
            "n_2024plus": len(strong_mod_2024),
            "horizons": {h: extract(results.get("strong_plus_moderate"), h) for h in ("1d","3d","5d","10d","20d")},
            "horizons_2024plus": {h: extract(results.get("strong_plus_moderate_2024plus"), h) for h in ("5d","10d")},
            "horizons_xbi": {h: extract(results.get("strong_plus_moderate_xbi"), h) for h in ("5d","10d")},
            "pass": pass_combined,
            "pass_2024plus": pass_combined_recent,
        },
        "ambiguous": {
            "n_post_cluster": len(ambiguous_buf),
            "horizons": {h: extract(results.get("ambiguous"), h) for h in ("5d","10d")},
        },
        "premium": {
            "n_post_cluster": len(premium_buf),
            "horizons": {h: extract(results.get("premium"), h) for h in ("5d","10d")},
        },
    }
    print("\n\n===== SUMMARY JSON =====")
    print(json.dumps(summary, indent=2, default=str))

    import db, uuid
    db.init_db()
    tid = f"T-{uuid.uuid4().hex[:8]}"
    db.store_task_result(
        result_id=tid,
        task_type="item_302_distressed_v2_retest",
        parameters={"start": args.start, "end": args.end, "min_mcap_m": args.min_mcap_m},
        result=summary,
        summary=json.dumps(summary)[:2000],
    )
    print(f"\nStored task result: {tid}", file=sys.stderr)


if __name__ == "__main__":
    main()
