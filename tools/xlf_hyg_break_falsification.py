"""
Canonical retest for XLF-HYG exposure with 2022-03-16 structural break claim.

Scan hit: XLF-HYG pre-break beta=2.078, post-break beta=1.506 (p=2.6e-9, F=20.03).
Meta-rule: dgs10_structural_break_scan_artifact_rule_2026_04_19 — many 2022 breaks are
secular drift artifacts, not genuine regime shifts.

Alt-date falsification: if F-stat at target break date is NOT >=3x max F at alt dates
(2020-06-15, 2024-01-15), the break is a secular-drift artifact.
"""
import sys
import os
import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_aligned_returns


def chow_test(df, break_date):
    """
    Run Chow structural break test for regression: XLF ~ HYG + SPY
    Returns F-stat for null of no break.
    """
    before = df.loc[:break_date]
    after = df.loc[break_date:]
    if len(before) < 50 or len(after) < 50:
        return None

    # Pooled regression
    def run_reg(data):
        y = data["xlf"].values
        X = np.column_stack([np.ones(len(data)), data["hyg"].values, data["spy"].values])
        try:
            beta, residuals, rank, sv = np.linalg.lstsq(X, y, rcond=None)
            y_pred = X @ beta
            rss = ((y - y_pred) ** 2).sum()
            return beta, rss
        except Exception:
            return None, None

    beta_full, rss_full = run_reg(df)
    beta_b, rss_b = run_reg(before)
    beta_a, rss_a = run_reg(after)
    if rss_full is None or rss_b is None or rss_a is None:
        return None

    k = 3  # num params
    n = len(df)
    rss_unrestricted = rss_b + rss_a
    f_stat = ((rss_full - rss_unrestricted) / k) / (rss_unrestricted / (n - 2 * k))
    return {
        "F": float(f_stat),
        "beta_before": beta_b.tolist(),
        "beta_after": beta_a.tolist(),
        "n_before": len(before),
        "n_after": len(after),
    }


def main():
    ret = get_aligned_returns(["XLF", "HYG", "SPY"], start="2019-01-01", end="2026-04-18")
    ret.columns = ["xlf", "hyg", "spy"]
    ret = ret.dropna()
    print(f"Aligned rows: {len(ret)}  [{ret.index[0]} to {ret.index[-1]}]")

    # Test target break date + alt dates
    dates = {
        "target_2022_03_16": "2022-03-16",
        "alt_2020_06_15": "2020-06-15",
        "alt_2024_01_15": "2024-01-15",
        "alt_2021_06_15": "2021-06-15",
        "alt_2023_06_15": "2023-06-15",
    }

    results = {}
    for name, d in dates.items():
        r = chow_test(ret, d)
        if r is None:
            print(f"{name:30} SKIPPED (insufficient data)")
            continue
        results[name] = r
        print(f"{name:30} F={r['F']:6.2f}  n_before={r['n_before']} n_after={r['n_after']}")
        print(f"                                     beta_before: const={r['beta_before'][0]:+.4f} hyg={r['beta_before'][1]:+.4f} spy={r['beta_before'][2]:+.4f}")
        print(f"                                     beta_after:  const={r['beta_after'][0]:+.4f} hyg={r['beta_after'][1]:+.4f} spy={r['beta_after'][2]:+.4f}")

    target_f = results.get("target_2022_03_16", {}).get("F", 0)
    alt_fs = [v["F"] for k, v in results.items() if k != "target_2022_03_16"]
    if not alt_fs:
        return
    max_alt_f = max(alt_fs)
    ratio = target_f / max_alt_f if max_alt_f > 0 else float("inf")
    print(f"\nTarget F = {target_f:.2f}, Max alt F = {max_alt_f:.2f}, Ratio = {ratio:.2f}")
    print(f"Rule: ratio >= 3.0 means real break. Ratio < 3.0 means secular-drift artifact.")
    if ratio >= 3.0:
        print("VERDICT: Real regime shift. XLF-HYG break is genuine.")
    else:
        print("VERDICT: Secular drift artifact. NOT a real regime shift. DEAD END per meta-rule.")


if __name__ == "__main__":
    main()
