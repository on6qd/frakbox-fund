#!/usr/bin/env python3
"""Activist 13D filing scanner.

Monitors EDGAR for new SC 13D filings by known top-tier activist investors.
When a new initial filing is detected, outputs a GO/NO-GO recommendation.

Signal: Starboard Value 13D filing -> ~+4.0% avg abnormal at 3d
        VALIDATED MULTI-PERIOD (N=32 total):
          Pre-discovery 2020-2021 (N=8): +3.88% avg, 87.5% pos, p=0.037 (Wilcoxon 0.016)
          In-sample 2022-2023 (N=21): +4.1% avg, 71.4% pos, p=0.048
          OOS 2024 (N=3): +5.14% avg, 100% positive
        All activists: +3.36% avg at 3d (IS N=36, p=0.034) — driven by Starboard

DEFINITIVE FINDING (activist_13d_starboard_only_finding, 2026-04): individual
backtests of ALL 7 monitored activists (2020-2024) showed ONLY Starboard Value
produces tradeable 13D alpha. Elliott (N=25, p=0.85), Icahn (N=79, p=0.51),
Third Point, Trian, JANA, Pershing — all DEAD_END. Scanner continues to
monitor all 7 for situational awareness, but issues GO only for Starboard.
Other activist filings are tagged MONITOR (non-tradeable signal).

Usage:
    python tools/activist_13d_scanner.py                # scan last 7 days
    python tools/activist_13d_scanner.py --days 14      # scan last 14 days
    python tools/activist_13d_scanner.py --evaluate     # also run GO/NO-GO filter
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, '/Users/frakbox/Bots/financial_researcher')

import requests

from tools.edgar_efts import efts_get_json, EFTSFetchError

try:
    import yfinance as yf
except ImportError:
    yf = None

HEADERS = {"User-Agent": "financial-researcher research@example.com"}
MIN_MARKET_CAP = 500_000_000  # $500M

# Top-tier activist investors and their CIKs.
# tradeable=True ONLY for Starboard per activist_13d_starboard_only_finding
# (2026-04) DEFINITIVE_FINDING. All other activists are MONITOR-only.
ACTIVIST_CIKS = {
    "1517137": {"name": "Starboard Value", "tier": 1, "tradeable": True},
    "1791786": {"name": "Elliott Investment Management", "tier": 1, "tradeable": False},
    "921669": {"name": "Carl Icahn", "tier": 1, "tradeable": False},
    "1040273": {"name": "Third Point", "tier": 2, "tradeable": False},
    "1998597": {"name": "JANA Partners", "tier": 2, "tradeable": False},
    "1345471": {"name": "Trian Fund Management", "tier": 2, "tradeable": False},
    "1336528": {"name": "Pershing Square", "tier": 2, "tradeable": False},
}


def search_13d_filings(start_date: str, end_date: str) -> list[dict]:
    """Search EDGAR EFTS for SCHEDULE 13D filings (initial only) in date range.

    Fixed 2026-04-12: EDGAR form code is 'SCHEDULE 13D', not 'SC 13D'. The prior
    'SC%2013D' form-param returned zero hits for ~60 days, hiding the validated
    Starboard signal. We now also filter out amendments (SCHEDULE 13D/A) since
    only INITIAL filings carry the activist-engagement signal.
    """
    all_filings = []

    for cik, info in ACTIVIST_CIKS.items():
        # Search for initial 13D filings mentioning this activist
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q=%22{info['name'].replace(' ', '+')}%22"
            f"&forms=SCHEDULE+13D"
            f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
        )
        try:
            data = efts_get_json(url, headers=HEADERS, label=f"13d-{info['name'][:12]}")
        except EFTSFetchError as e:
            # For tradeable activists (Starboard) a silent skip risks a missed
            # live trigger — surface it loudly so the scan is flagged unreliable.
            print(f"  EFTS error for {info['name']} (data unavailable): {e}", file=sys.stderr)
            if info.get("tradeable"):
                raise
            continue

        hits = data.get("hits", {}).get("hits", [])

        for h in hits:
            src = h.get("_source", {})
            form = (src.get("form") or src.get("form_type") or "").upper().strip()
            # Filter out amendments — only INITIAL SCHEDULE 13D carries the signal
            if form != "SCHEDULE 13D":
                continue
            names = src.get("display_names", [])
            file_date = src.get("file_date", "")

            # Extract target company ticker (not the activist)
            for name in names:
                # Skip if this IS the activist
                if any(kw.lower() in name.lower() for kw in info["name"].split()[:2]):
                    continue
                m = re.search(r"\(([A-Z]{1,5})\)", name)
                if m:
                    ticker = m.group(1)
                    if ticker not in ("CIK",):
                        all_filings.append({
                            "activist": info["name"],
                            "activist_cik": cik,
                            "tier": info["tier"],
                            "tradeable": info.get("tradeable", False),
                            "target": ticker,
                            "target_name": name[:60],
                            "file_date": file_date,
                            "form": form,
                        })
                        break

        time.sleep(0.3)

    return all_filings


def deduplicate_filings(filings: list[dict]) -> list[dict]:
    """Keep only the earliest filing per activist+target (initial position reveal)."""
    seen = {}
    for f in filings:
        key = f"{f['activist']}_{f['target']}"
        if key not in seen or f["file_date"] < seen[key]["file_date"]:
            seen[key] = f
    return sorted(seen.values(), key=lambda x: x["file_date"])


def evaluate_candidate(filing: dict) -> dict:
    """Run GO/NO-GO evaluation on a candidate."""
    ticker = filing["target"]
    result = dict(filing)

    # Check market cap
    if yf is None:
        result["decision"] = "SKIP_NO_YF"
        result["reason"] = "yfinance not available"
        return result

    try:
        from tools.yfinance_utils import safe_download
        info = yf.Ticker(ticker).info
        mcap = info.get("marketCap", 0) or 0
        price = info.get("currentPrice") or info.get("previousClose") or 0
        avg_vol = info.get("averageVolume", 0) or 0
    except Exception:
        mcap = 0
        price = 0
        avg_vol = 0

    result["market_cap"] = mcap
    result["price"] = round(price, 2) if price else 0
    result["avg_volume"] = avg_vol

    # GO/NO-GO criteria
    if mcap < MIN_MARKET_CAP:
        result["decision"] = "NO_GO"
        result["reason"] = f"Market cap ${mcap / 1e6:.0f}M < ${MIN_MARKET_CAP / 1e6:.0f}M minimum"
    elif avg_vol < 100_000:
        result["decision"] = "NO_GO"
        result["reason"] = f"Average volume {avg_vol:,} < 100K minimum"
    elif price < 3:
        result["decision"] = "NO_GO"
        result["reason"] = f"Price ${price:.2f} < $3 minimum (penny stock risk)"
    elif not filing.get("tradeable", False):
        # Activist monitored but not validated individually. Emit MONITOR (not GO)
        # per activist_13d_starboard_only_finding (2026-04) DEFINITIVE_FINDING.
        result["decision"] = "MONITOR"
        result["reason"] = (
            f"{filing['activist']} filings individually backtested as DEAD_END "
            f"(see activist_13d_starboard_only_finding). Only Starboard validates — "
            f"observing this filing for situational awareness only. Do NOT trade."
        )
    else:
        result["decision"] = "GO"
        position_size = 5000
        shares = int(position_size / price) if price > 0 else 0
        result["shares_to_buy"] = shares
        result["position_size"] = position_size
        result["reason"] = (
            f"Tier {filing['tier']} activist ({filing['activist']}), "
            f"mktcap ${mcap / 1e6:.0f}M, vol {avg_vol:,}"
        )

    return result


def main():
    parser = argparse.ArgumentParser(description="Activist 13D filing scanner")
    parser.add_argument("--days", type=int, default=7, help="Days to scan back")
    parser.add_argument("--evaluate", action="store_true", help="Run GO/NO-GO filter")
    args = parser.parse_args()

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"Scanning EDGAR for activist SC 13D filings: {start_date} to {end_date}")
    print(f"  Monitoring {len(ACTIVIST_CIKS)} activists")

    filings = search_13d_filings(start_date, end_date)
    unique = deduplicate_filings(filings)

    print(f"  Raw filings found: {len(filings)}")
    print(f"  Unique initial filings: {len(unique)}")

    if not unique:
        print("\nNo new activist 13D filings detected.")
        result = {
            "scan_time": datetime.now().isoformat(),
            "days_scanned": args.days,
            "total_found": 0,
            "candidates": [],
        }
        print(json.dumps(result, indent=2))
        return

    if args.evaluate:
        candidates = [evaluate_candidate(f) for f in unique]
    else:
        candidates = unique

    go_count = sum(1 for c in candidates if c.get("decision") == "GO")
    nogo_count = sum(1 for c in candidates if c.get("decision") == "NO_GO")
    monitor_count = sum(1 for c in candidates if c.get("decision") == "MONITOR")

    print(f"\n{'=' * 60}")
    print(f"ACTIVIST 13D CANDIDATES ({len(candidates)} found)")
    print(f"{'=' * 60}")
    for c in candidates:
        dec = c.get("decision", "UNEVAL")
        tier_str = f"T{c.get('tier', '?')}"
        mcap_str = f"${c.get('market_cap', 0) / 1e6:.0f}M" if c.get("market_cap") else ""
        print(f"  {dec:6s} {tier_str} | {c['target']:6s} | {mcap_str:>8s} | {c['activist']:25s} | {c['file_date']}")
        if c.get("reason"):
            print(f"    -> {c['reason']}")

    result = {
        "scan_time": datetime.now().isoformat(),
        "days_scanned": args.days,
        "total_found": len(candidates),
        "go_count": go_count,
        "nogo_count": nogo_count,
        "monitor_count": monitor_count,
        "candidates": candidates,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
