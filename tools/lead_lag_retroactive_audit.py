#!/usr/bin/env python3
"""
Retroactive audit of all stored lead-lag (Granger) results against the
xcorr guard added 2026-04-12.

Guard: if |best_lag_xcorr| < XCORR_LAG_THRESHOLD AND |lag0_xcorr| > XCORR_LAG0_FLOOR
then the "significant" Granger finding is a false positive — the
relationship is purely contemporaneous, not predictive.

Also flags borderline cases for manual review: when best_lag_xcorr is
small but above the guard threshold while lag-0 xcorr is large. These
do not auto-retire but suggest a regime-restricted rerun is warranted.

Usage:
    python3 tools/lead_lag_retroactive_audit.py                 # print table
    python3 tools/lead_lag_retroactive_audit.py --verbose       # include non-significant
    python3 tools/lead_lag_retroactive_audit.py --json          # machine-readable

Safe to re-run — does not mutate the database.
"""
import argparse
import json
import sys
from pathlib import Path

XCORR_LAG_THRESHOLD = 0.05   # below this at best_lag means no predictive content
XCORR_LAG0_FLOOR = 0.15      # above this at lag0 means contemporaneous correlation exists
BORDERLINE_RATIO = 0.40      # best_lag_xcorr / lag0_xcorr — below this = suspicious

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "research.db"

sys.path.insert(0, str(ROOT))
import db

# Known structural breaks. When both (factor, target) are rate-sensitive and the
# IS window starts before the break, flag as REGIME_SPANNING. See
# rate_sensitive_sectors_2022_structural_break in known_effects.
RATE_BREAK_DATE = "2022-01-01"
RATE_SENSITIVE_SYMBOLS = {"XLRE", "IYR", "KRE", "XLF", "XLU", "IYZ", "TLT", "IEF"}
RATE_FACTORS = {"FRED:DGS10", "FRED:DGS2", "FRED:FEDFUNDS"}


def audit() -> list[dict]:
    rows = db._q(
        "SELECT id, timestamp, parameters, result "
        "FROM task_results WHERE task_type='regression_lead_lag' "
        "ORDER BY timestamp"
    )

    audit = []
    for r in rows:
        id_, ts = r["id"], r["timestamp"]
        params_json, result_json = r["parameters"], r["result"]
        try:
            params = json.loads(params_json or "{}")
            result = json.loads(result_json or "{}")
        except Exception as e:
            audit.append({"id": id_, "status": "parse_error", "error": str(e)})
            continue

        details = result.get("details") or {}
        xcorr = details.get("cross_correlation") or {}
        best_lag = (details.get("in_sample") or {}).get("best_lag")
        best_xcorr = xcorr.get(str(best_lag)) if best_lag is not None else None
        lag0 = xcorr.get("0")

        is_p = result.get("p_value")
        oos = result.get("oos_result") or {}
        oos_p = oos.get("p_value")
        is_significant_original = bool(result.get("significant"))
        oos_significant = bool(oos.get("significant"))

        # Apply guard
        guard_trips = False
        borderline = False
        if best_xcorr is not None and lag0 is not None:
            if abs(best_xcorr) < XCORR_LAG_THRESHOLD and abs(lag0) > XCORR_LAG0_FLOOR:
                guard_trips = True
            elif abs(lag0) > XCORR_LAG0_FLOOR:
                ratio = abs(best_xcorr) / abs(lag0) if abs(lag0) > 0 else 0
                if ratio < BORDERLINE_RATIO:
                    borderline = True

        # Regime-spanning check: rate-sensitive pair with IS start pre-2022
        regime_spanning = False
        target = params.get("target", "") or ""
        factor = params.get("factor", "") or ""
        if (factor in RATE_FACTORS
                and target in RATE_SENSITIVE_SYMBOLS
                and (params.get("start") or "") < RATE_BREAK_DATE):
            regime_spanning = True
        if (factor in RATE_FACTORS
                and target in RATE_SENSITIVE_SYMBOLS
                and (params.get("start") or "") < RATE_BREAK_DATE
                and (params.get("oos_start") or "") and (params.get("oos_start") or "") > RATE_BREAK_DATE):
            regime_spanning = True

        if guard_trips:
            new_status = "RETIRED_XCORR_GUARD"
        elif regime_spanning and is_significant_original:
            new_status = "REGIME_SPANNING_INVALID"
        elif borderline and is_significant_original:
            new_status = "BORDERLINE_REGIME_RECHECK"
        elif is_significant_original and oos_significant:
            new_status = "PASSES_GUARD"
        elif is_significant_original:
            new_status = "IS_ONLY_NO_OOS"
        else:
            new_status = "NOT_SIGNIFICANT"

        audit.append({
            "id": id_,
            "timestamp": ts,
            "target": params.get("target"),
            "factor": params.get("factor"),
            "is_start": params.get("start"),
            "is_end": params.get("end"),
            "oos_start": params.get("oos_start"),
            "best_lag": best_lag,
            "is_p": is_p,
            "oos_p": oos_p,
            "lag0_xcorr": lag0,
            "best_lag_xcorr": best_xcorr,
            "is_significant_original": is_significant_original,
            "oos_significant": oos_significant,
            "guard_trips": guard_trips,
            "borderline": borderline,
            "regime_spanning": regime_spanning,
            "new_status": new_status,
        })
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument("--verbose", action="store_true", help="Include non-significant results")
    args = parser.parse_args()

    results = audit()

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return

    to_show = results if args.verbose else [r for r in results if r.get("is_significant_original")]
    if not to_show:
        print("No significant lead-lag results found.")
        return

    header = f"{'id':13} {'target':8} {'factor':13} {'window':23} {'best_lag':8} {'is_p':10} {'lag0':8} {'best':8} {'status'}"
    print(header)
    print("-" * len(header))
    for r in to_show:
        window = f"{(r.get('is_start') or '')[:10]}>{(r.get('oos_start') or '-')[:10]}"
        print(
            f"{r['id']:13} {(r.get('target') or '')[:7]:8} {(r.get('factor') or '')[:12]:13} "
            f"{window:23} {str(r.get('best_lag')):8} "
            f"{(r.get('is_p') or 0):.2e}  "
            f"{(r.get('lag0_xcorr') or 0):+.3f}  "
            f"{(r.get('best_lag_xcorr') or 0):+.3f}  "
            f"{r['new_status']}"
        )

    print()
    print("Summary:")
    for status in ("PASSES_GUARD", "BORDERLINE_REGIME_RECHECK", "REGIME_SPANNING_INVALID",
                   "RETIRED_XCORR_GUARD", "IS_ONLY_NO_OOS", "NOT_SIGNIFICANT"):
        n = sum(1 for r in results if r["new_status"] == status)
        if n:
            print(f"  {status}: {n}")


if __name__ == "__main__":
    main()
