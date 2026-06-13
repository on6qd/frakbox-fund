"""
midcap_pre_earnings_analysis.py - Pre-earnings drift for $1B-$5B mid-cap stocks.

Research question: Does the pre-earnings drift signal that failed for large-caps
($50B+) exist for mid-cap stocks where institutional coverage is lower and
price discovery is less efficient?

Hypothesis: Lower analyst coverage in mid-caps means less information leakage
before earnings, OR conversely, there's more room for consistent beaters to
show pre-earnings drift because fewer institutions are front-running.

Mid-cap universe: S&P MidCap 400 representative stocks, $1B-$5B market cap typical.
"""

import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.pre_earnings_runup import run_pre_earnings_analysis

# S&P MidCap 400 representative stocks, ~$1B-$8B market cap range
# Selected for: earnings consistency, liquidity, diverse sectors
MIDCAP_UNIVERSE = [
    # Technology
    "EXLS", "NSIT", "EPAM", "MANH", "PRFT", "SAIC", "LDOS", "CACI",
    # Healthcare
    "ENSG", "AMED", "LHCG", "ACAD", "IART", "INSP", "OMCL", "PINC",
    # Industrials
    "AAON", "TREX", "UFPI", "SAIA", "WERN", "MRTN", "ODFL", "ARCB",
    # Consumer Discretionary
    "FIVE", "BOOT", "DXPE", "SFM", "CATO", "PRGO", "ELY", "PLAY",
    # Financials
    "HOMB", "CVBF", "SFNC", "CADE", "IBCP", "COLB", "FHB", "PACW",
    # Real Estate / Other
    "IIPR", "GMRE", "NXRT", "ROCC",
    # Additional mid-cap growth
    "MEDP", "QDEL", "HALO", "MMSI", "LNTH", "PDCO", "PDFS",
]

def main():
    print("=" * 70)
    print("MID-CAP PRE-EARNINGS DRIFT ANALYSIS")
    print(f"Universe: {len(MIDCAP_UNIVERSE)} mid-cap tickers ($1B-$8B)")
    print("Period: 2021-01-01 to 2026-01-01")
    print("=" * 70)
    print()

    result = run_pre_earnings_analysis(
        tickers=MIDCAP_UNIVERSE,
        start_date="2021-01-01",
        end_date="2026-01-01",
        days_before=5,
        min_prior_quarters=3,
        verbose=True,
    )

    if "error" in result:
        print(f"\nERROR: {result['error']}")
        return

    print("\n" + "=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)

    results = result.get("results", {})
    for label, res in results.items():
        if "note" in res:
            print(f"{label}: n={res['n']} ({res['note']})")
        else:
            print(f"\n{label} (n={res['n']}):")
            print(f"  5d avg abnormal: {res['avg_abnormal_pct']:+.4f}%  "
                  f"median={res['median_abnormal_pct']:+.4f}%  "
                  f"stdev={res['stdev']:.4f}%")
            print(f"  positive_rate: {res['positive_rate']:.1%}  "
                  f"t-stat={res['t_stat']:.3f}  p={res['p_value']:.4f}")
            if res.get('wilcoxon_p'):
                print(f"  wilcoxon_p: {res['wilcoxon_p']:.4f}")
            if "avg_abnormal_3d" in res:
                print(f"  3d avg abnormal: {res['avg_abnormal_3d']:+.4f}%  "
                      f"p={res['p_value_3d']:.4f}")
            if "avg_abnormal_2d" in res:
                print(f"  2d avg abnormal: {res['avg_abnormal_2d']:+.4f}%  "
                      f"p={res['p_value_2d']:.4f}")

    # Multiple testing check
    beater_res = results.get("consistent_beater", {})
    if "note" not in beater_res:
        n_sig_horizons = sum(
            1 for key in ["p_value", "p_value_3d", "p_value_2d"]
            if key in beater_res and beater_res[key] < 0.05
        )
        passes_mt = (n_sig_horizons >= 2) or (
            beater_res.get("p_value", 1.0) < 0.01
        )
        print(f"\n{'='*70}")
        print(f"MULTIPLE TESTING CHECK:")
        print(f"  Significant horizons (p<0.05): {n_sig_horizons}/3")
        print(f"  Passes multiple testing: {passes_mt}")
        print(f"  Consistent beater avg 5d abnormal: "
              f"{beater_res.get('avg_abnormal_pct', 'N/A')}")
        print(f"  Direction threshold (>0.5%): "
              f"{abs(beater_res.get('avg_abnormal_pct', 0)) > 0.5}")

    # Save results
    output = {
        "date_run": datetime.now().isoformat(),
        "universe_size": len(MIDCAP_UNIVERSE),
        "n_total_events": result.get("n_total", 0),
        "tickers_succeeded": result.get("tickers_succeeded", 0),
        "tickers_failed": result.get("tickers_failed", 0),
        "results": results,
    }
    with open("data/midcap_pre_earnings_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to data/midcap_pre_earnings_results.json")


if __name__ == "__main__":
    main()
