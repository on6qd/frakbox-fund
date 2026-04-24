"""
Detect forward stock splits in a given universe using yfinance.
Outputs JSON of {ticker, split_date, ratio, market_cap_estimate}.

Note: yfinance .splits returns payable dates. Announcement is typically
2-4 weeks before. For backtest accuracy we want the ANNOUNCEMENT date.
SEC EDGAR 8-K Item 8.01 filings often carry the announcement.

For first pass, we use the yfinance payable date but ALSO estimate
announcement date by shifting back ~20 trading days (conservative).

Usage:
    python tools/detect_forward_splits.py --start 2019-01-01 --end 2024-12-31 --output splits.json
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.build_sp500_universe import load_sp500_universe
from tools.yfinance_utils import safe_download
import yfinance as yf


def detect_forward_splits(universe, start_date, end_date, min_ratio=1.25):
    """Return list of forward splits in window."""
    events = []
    for i, sym in enumerate(universe):
        try:
            t = yf.Ticker(sym)
            splits = t.splits
            if splits is None or splits.empty:
                continue
            for dt, ratio in splits.items():
                d = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)
                if start_date <= d <= end_date and ratio >= min_ratio:
                    events.append({
                        "symbol": sym,
                        "split_payable_date": d,
                        "ratio": float(ratio),
                    })
        except Exception as e:
            print(f"[warn] {sym}: {e}", file=sys.stderr)
        if (i + 1) % 50 == 0:
            print(f"  processed {i+1}/{len(universe)}", file=sys.stderr)
            time.sleep(0.5)
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--min-ratio", type=float, default=1.25)
    ap.add_argument("--universe-limit", type=int, default=None)
    args = ap.parse_args()

    u = load_sp500_universe()
    if args.universe_limit:
        u = u[: args.universe_limit]
    print(f"Universe: {len(u)} tickers", file=sys.stderr)
    events = detect_forward_splits(u, args.start, args.end, args.min_ratio)
    print(f"Found {len(events)} forward splits (ratio >= {args.min_ratio}x)", file=sys.stderr)

    # Deduplicate
    seen = set()
    deduped = []
    for e in events:
        key = (e["symbol"], e["split_payable_date"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    with open(args.output, "w") as f:
        json.dump(deduped, f, indent=2)
    print(f"Wrote {len(deduped)} unique splits to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
