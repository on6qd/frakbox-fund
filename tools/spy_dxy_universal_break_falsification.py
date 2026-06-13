"""
Canonical altdate falsification for UNIVERSAL equity-dollar decoupling scan hit
(c58fca16): SPY pre-beta=-0.739 -> post-beta=+0.027 at 2024-01-01, p=3.9e-6.
Claim: 9/11 sectors decoupled simultaneously at same date.

Per dgs10_structural_break_scan_artifact_rule_2026_04_19 + bank_dxy_structural_break_secular_drift_2026_04_20:
Chow-test structural breaks against macro factors (DXY, DGS10) are usually secular-drift
artifacts. Must pass 3x F-ratio rule to be considered a real regime break.

Test: regress SPY ~ DXY at candidate break dates. Compute F-stat at target (2024-01-01)
vs alt dates (2021-01, 2022-01, 2023-01, 2025-01). If target F < 3x max alt F => secular drift.

Secondary: repeat for XLK (tech/cyclical, biggest claimed sign flip per scan) and XLP
(defensive, claimed to retain negative exposure — should show low F at 2024).
"""
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_aligned_returns


def chow_test(df, break_date, y_col, x_col):
    before = df.loc[:break_date]
    after = df.loc[break_date:]
    if len(before) < 100 or len(after) < 100:
        return None

    def run_reg(data):
        y = data[y_col].values
        X = np.column_stack([np.ones(len(data)), data[x_col].values])
        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
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

    k = 2
    n = len(df)
    rss_u = rss_b + rss_a
    f_stat = ((rss_full - rss_u) / k) / (rss_u / (n - 2 * k))
    return {
        "F": float(f_stat),
        "beta_before": float(beta_b[1]),
        "beta_after": float(beta_a[1]),
        "n_before": len(before),
        "n_after": len(after),
    }


def run_target(target_symbol):
    # DX-Y.NYB is the DXY index. Need daily returns.
    try:
        ret = get_aligned_returns(
            [target_symbol, "DX-Y.NYB"], start="2019-01-01", end="2026-04-18"
        )
    except Exception as e:
        print(f"  ERROR fetching {target_symbol}: {e}")
        return None
    ret.columns = ["y", "x"]
    ret = ret.dropna()
    if len(ret) < 500:
        print(f"  insufficient data for {target_symbol}: {len(ret)} rows")
        return None

    dates = {
        "TARGET_2024_01_02": "2024-01-02",
        "alt_2021_01_04": "2021-01-04",
        "alt_2022_01_03": "2022-01-03",
        "alt_2023_01_03": "2023-01-03",
        "alt_2025_01_02": "2025-01-02",
    }

    results = {}
    print(f"\n===== {target_symbol} vs DXY =====")
    print(f"Aligned rows: {len(ret)}  [{ret.index[0].date()} to {ret.index[-1].date()}]")
    for name, d in dates.items():
        r = chow_test(ret, d, "y", "x")
        if r is None:
            print(f"  {name:22} SKIPPED")
            continue
        results[name] = r
        tag = "<-- TARGET" if name.startswith("TARGET") else ""
        print(
            f"  {name:22} F={r['F']:6.2f}  beta_pre={r['beta_before']:+.4f}  beta_post={r['beta_after']:+.4f}  (n={r['n_before']}/{r['n_after']}) {tag}"
        )

    target_f = results.get("TARGET_2024_01_02", {}).get("F", 0)
    alt_fs = [v["F"] for k, v in results.items() if not k.startswith("TARGET")]
    if not alt_fs:
        return None
    max_alt_f = max(alt_fs)
    ratio = target_f / max_alt_f if max_alt_f > 0 else float("inf")
    print(
        f"  => Target F={target_f:.2f}  Max alt F={max_alt_f:.2f}  Ratio={ratio:.2f}"
    )
    print(
        f"  VERDICT: {'REAL BREAK' if ratio >= 3.0 else 'SECULAR DRIFT (FAILS 3x RULE)'}"
    )
    return {"symbol": target_symbol, "target_F": target_f, "max_alt_F": max_alt_f, "ratio": ratio}


def main():
    summary = []
    # Primary: SPY-DXY universal equity-dollar claim
    for sym in ["SPY", "XLK", "XLY", "XLF", "XLP", "XLV", "GLD"]:
        s = run_target(sym)
        if s:
            summary.append(s)
    print("\n\n===== SUMMARY =====")
    print(f"{'symbol':<8} {'target_F':>10} {'max_alt_F':>10} {'ratio':>8} {'verdict':<30}")
    for s in summary:
        v = "REAL" if s["ratio"] >= 3.0 else "SECULAR DRIFT"
        print(f"{s['symbol']:<8} {s['target_F']:>10.2f} {s['max_alt_F']:>10.2f} {s['ratio']:>8.2f} {v:<30}")


if __name__ == "__main__":
    main()
