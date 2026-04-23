"""Canonical retest of scan hit: Oil (CL=F) Granger-causes EEM at lag-4.

Strategy-level translation of the Granger hit:
    On day t: CL=F daily return shock > |threshold|
    Enter EEM long (positive shock) / short (negative shock) on day t+1 open
    Exit day t+4 close (the "lag 4" horizon implied by scan)
Measure cumulative EEM return and test direction/magnitude in both
in-sample (2020-01-01 .. 2024-06-30) and OOS (2024-07-01 .. today) windows.

Decision rule (per commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20
and scan_hit_canonical_all_horizons_rule_2026_04_19):
    PASS = p<0.05 AND |mean| >= 1% AND direction >= 60% in BOTH IS and OOS,
            same sign in both.
Otherwise record as DEAD_END extending the commodity->broad-ETF rule.
"""
import numpy as np
import pandas as pd
from scipy import stats
from tools.yfinance_utils import get_close_prices


def run_test(trigger_threshold_pct=2.0, lag_days=4, start="2015-01-01",
             oos_start="2024-07-01", end=None):
    cl = get_close_prices("CL=F", start=start, end=end).dropna()
    eem = get_close_prices("EEM", start=start, end=end).dropna()
    # Unwrap single-column DataFrames to Series
    if hasattr(cl, "columns"):
        cl = cl.iloc[:, 0]
    if hasattr(eem, "columns"):
        eem = eem.iloc[:, 0]

    # Align
    df = pd.concat([cl.rename("CL"), eem.rename("EEM")], axis=1).dropna()
    df["cl_ret"] = df["CL"].pct_change()
    df["eem_ret"] = df["EEM"].pct_change()

    # Future cumulative EEM return from t+1 close to t+lag close
    # (enter at t+1 open ~ t close, exit at t+lag close)
    df["eem_fwd"] = df["EEM"].shift(-lag_days) / df["EEM"] - 1.0
    df = df.dropna()

    print(f"Total days after cleaning: {len(df)}")
    print(f"CL returns mean: {df['cl_ret'].mean()*100:.3f}% std: {df['cl_ret'].std()*100:.3f}%")

    def analyze(subset, label):
        if len(subset) < 20:
            print(f"  {label}: too few rows ({len(subset)})")
            return None
        long_trigger = subset[subset["cl_ret"] > trigger_threshold_pct/100.0]
        short_trigger = subset[subset["cl_ret"] < -trigger_threshold_pct/100.0]
        print(f"  {label} window: n={len(subset)} | long_trig n={len(long_trigger)} | short_trig n={len(short_trigger)}")
        results = {}
        for name, sample, expected_sign in [("long_after_spike", long_trigger, +1),
                                            ("short_after_drop", short_trigger, -1)]:
            if len(sample) < 10:
                print(f"    {name}: n={len(sample)} too small")
                continue
            # For 'long' trade, P&L is eem_fwd; for 'short' trade, P&L is -eem_fwd
            trade_ret = sample["eem_fwd"] * expected_sign
            mean = trade_ret.mean()
            median = trade_ret.median()
            std = trade_ret.std()
            pos_rate = (trade_ret > 0).mean()
            tstat, pval = stats.ttest_1samp(trade_ret, 0.0)
            print(f"    {name}: n={len(sample)} mean={mean*100:.3f}% median={median*100:.3f}% std={std*100:.3f}% direction={pos_rate*100:.1f}% t={tstat:.2f} p={pval:.4f}")
            results[name] = {"n": len(sample), "mean_pct": mean*100, "direction_pct": pos_rate*100, "p": pval, "t": tstat}
        return results

    is_df = df[df.index < oos_start]
    oos_df = df[df.index >= oos_start]

    print(f"\n--- IS ({start} .. {oos_start}) ---")
    is_res = analyze(is_df, "IS")
    print(f"\n--- OOS ({oos_start} .. {end or 'today'}) ---")
    oos_res = analyze(oos_df, "OOS")

    # Decision
    print("\n--- DECISION ---")
    passed = False
    if is_res and oos_res:
        for leg in ("long_after_spike", "short_after_drop"):
            if leg not in is_res or leg not in oos_res:
                continue
            is_l = is_res[leg]; oos_l = oos_res[leg]
            same_sign = (is_l["mean_pct"] * oos_l["mean_pct"] > 0)
            is_pass = (is_l["p"] < 0.05) and (abs(is_l["mean_pct"]) >= 1.0) and (is_l["direction_pct"] >= 60)
            oos_pass = (oos_l["p"] < 0.05) and (abs(oos_l["mean_pct"]) >= 1.0) and (oos_l["direction_pct"] >= 60)
            print(f"  {leg}: same_sign={same_sign} IS_pass={is_pass} OOS_pass={oos_pass}")
            if same_sign and is_pass and oos_pass:
                passed = True
                print(f"  *** {leg} PASSES canonical gate ***")
    print(f"\nCANONICAL_PASSES: {passed}")
    return {"passed": passed, "is": is_res, "oos": oos_res}


if __name__ == "__main__":
    # Run both canonical thresholds
    print("===== TRIGGER: |CL=F daily return| > 2.0% =====")
    r1 = run_test(trigger_threshold_pct=2.0)
    print("\n\n===== TRIGGER: |CL=F daily return| > 3.0% =====")
    r2 = run_test(trigger_threshold_pct=3.0)
