"""
Bank-basket -> SPY lead-lag investigation.

Scan hit c9c47f63: JPM/BAC/GS/WFC each Granger-cause SPY at lag 2.
Meta-rule concern: Lead-lag Granger rarely converts to tradeable edge (commodity meta-rule,
XLF->SPY known-null). Test: does this pattern produce tradeable abnormal returns, or is it
also an exposure artifact?

Design:
  IS: 2010-01-01 to 2022-12-31
  OOS: 2023-01-01 to 2026-04-20
  Target: SPY close-to-close log return at t
  Factor: BANK_BASKET log return at t-1 and t-2 (JPM/BAC/GS/WFC equal-weight)
  Controls: SPY[t-1], BANK[t-1] interaction with regime (optional)

Tests:
  1. OLS with Newey-West SE: SPY[t] = a + b1*BANK[t-1] + b2*BANK[t-2] + b3*SPY[t-1] + e
     - Report beta, t-stat, p-value in IS and OOS separately
     - Criterion: p<0.05 same sign both samples, |b|>=0.05
  2. Threshold backtest: when BANK[t-1] > +1%, long SPY open at t, hold 1 day (close-to-close)
     (and symmetric < -1% short)
     - Report mean abnormal return vs unconditional SPY drift, hit rate, N
     - Criterion: abnormal return >= 0.5% magnitude in both IS and OOS
  3. Transaction-cost adjustment: 10bps round-trip on SPY; net abnormal must remain >0.2%

Outputs: JSON summary to stdout.
"""
import json
import numpy as np
import pandas as pd
import statsmodels.api as sm
from tools.yfinance_utils import get_close_prices


def get_prices(symbols, start, end):
    px = get_close_prices(symbols, start=start, end=end)
    return px.dropna()


def compute_log_returns(px):
    return np.log(px / px.shift(1))


def bank_basket(rets, banks=("JPM", "BAC", "GS", "WFC")):
    return rets[list(banks)].mean(axis=1)


def regression_test(df, is_end="2022-12-31"):
    """SPY[t] = a + b1*BANK[t-1] + b2*BANK[t-2] + b3*SPY[t-1] + e"""
    df = df.copy()
    df["SPY_lag1"] = df["SPY"].shift(1)
    df["BANK_lag1"] = df["BANK"].shift(1)
    df["BANK_lag2"] = df["BANK"].shift(2)
    df = df.dropna()
    is_df = df.loc[:is_end]
    oos_df = df.loc[is_end:].iloc[1:]

    results = {}
    for name, sub in [("IS", is_df), ("OOS", oos_df)]:
        X = sub[["BANK_lag1", "BANK_lag2", "SPY_lag1"]]
        X = sm.add_constant(X)
        y = sub["SPY"]
        model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
        results[name] = {
            "n": int(len(sub)),
            "r2": float(model.rsquared),
            "beta_BANK_lag1": float(model.params["BANK_lag1"]),
            "p_BANK_lag1": float(model.pvalues["BANK_lag1"]),
            "t_BANK_lag1": float(model.tvalues["BANK_lag1"]),
            "beta_BANK_lag2": float(model.params["BANK_lag2"]),
            "p_BANK_lag2": float(model.pvalues["BANK_lag2"]),
            "beta_SPY_lag1": float(model.params["SPY_lag1"]),
            "p_SPY_lag1": float(model.pvalues["SPY_lag1"]),
        }
    return results


def threshold_backtest(df, threshold=0.01, is_end="2022-12-31"):
    """
    When BANK[t-1] return > +threshold, long SPY at t close-to-close.
    When BANK[t-1] return < -threshold, short SPY at t close-to-close.
    Return stats in IS and OOS.
    """
    df = df.copy()
    df["BANK_lag1"] = df["BANK"].shift(1)
    df = df.dropna()
    df["signal_long"] = df["BANK_lag1"] > threshold
    df["signal_short"] = df["BANK_lag1"] < -threshold
    unconditional_mean = df["SPY"].mean()
    df["abn_long"] = df["SPY"] - unconditional_mean
    df["abn_short"] = -(df["SPY"] - unconditional_mean)

    is_df = df.loc[:is_end]
    oos_df = df.loc[is_end:].iloc[1:]

    out = {}
    for name, sub in [("IS", is_df), ("OOS", oos_df)]:
        long_trades = sub.loc[sub["signal_long"], "abn_long"]
        short_trades = sub.loc[sub["signal_short"], "abn_short"]
        out[name] = {
            "n_total": int(len(sub)),
            "unconditional_mean_spy_ret_pct": float(sub["SPY"].mean() * 100),
            "n_long_trigger": int(len(long_trades)),
            "n_short_trigger": int(len(short_trades)),
            "long_mean_abn_ret_pct": float(long_trades.mean() * 100) if len(long_trades) else None,
            "short_mean_abn_ret_pct": float(short_trades.mean() * 100) if len(short_trades) else None,
            "long_hit_rate": float((long_trades > 0).mean()) if len(long_trades) else None,
            "short_hit_rate": float((short_trades > 0).mean()) if len(short_trades) else None,
            "combined_mean_abn_ret_pct": float(pd.concat([long_trades, short_trades]).mean() * 100)
                if (len(long_trades) + len(short_trades)) else None,
            "combined_n": int(len(long_trades) + len(short_trades)),
        }
    return out


def main():
    print("Fetching 2009-2026 prices for SPY + 4 banks...", flush=True)
    px = get_prices(["SPY", "JPM", "BAC", "GS", "WFC"],
                     start="2009-01-01", end="2026-04-21")
    rets = compute_log_returns(px).dropna()
    bank = bank_basket(rets)
    df = pd.DataFrame({"SPY": rets["SPY"], "BANK": bank}).dropna()
    print(f"Loaded {len(df)} daily obs from {df.index.min().date()} to {df.index.max().date()}", flush=True)

    print("\n=== REGRESSION TEST ===", flush=True)
    reg = regression_test(df)
    print(json.dumps(reg, indent=2), flush=True)

    print("\n=== THRESHOLD BACKTEST (|BANK_lag1| > 1.0%) ===", flush=True)
    th1 = threshold_backtest(df, threshold=0.01)
    print(json.dumps(th1, indent=2), flush=True)

    print("\n=== THRESHOLD BACKTEST (|BANK_lag1| > 2.0%) ===", flush=True)
    th2 = threshold_backtest(df, threshold=0.02)
    print(json.dumps(th2, indent=2), flush=True)

    # Decision summary
    print("\n=== DECISION SUMMARY ===", flush=True)
    reg_is = reg["IS"]
    reg_oos = reg["OOS"]
    same_sign = (reg_is["beta_BANK_lag1"] * reg_oos["beta_BANK_lag1"]) > 0
    both_sig = (reg_is["p_BANK_lag1"] < 0.05) and (reg_oos["p_BANK_lag1"] < 0.05)
    mag_ok = abs(reg_is["beta_BANK_lag1"]) >= 0.05 and abs(reg_oos["beta_BANK_lag1"]) >= 0.05
    decision = {
        "regression_passes": bool(same_sign and both_sig and mag_ok),
        "same_sign_is_oos": bool(same_sign),
        "both_significant": bool(both_sig),
        "magnitude_pass_0.05": bool(mag_ok),
        "threshold_1pct_combined_IS_abn_pct": th1["IS"]["combined_mean_abn_ret_pct"],
        "threshold_1pct_combined_OOS_abn_pct": th1["OOS"]["combined_mean_abn_ret_pct"],
        "threshold_1pct_passes_0.5pct_both": bool(
            th1["IS"]["combined_mean_abn_ret_pct"] is not None
            and th1["OOS"]["combined_mean_abn_ret_pct"] is not None
            and abs(th1["IS"]["combined_mean_abn_ret_pct"]) >= 0.5
            and abs(th1["OOS"]["combined_mean_abn_ret_pct"]) >= 0.5
            and (th1["IS"]["combined_mean_abn_ret_pct"] * th1["OOS"]["combined_mean_abn_ret_pct"]) > 0
        ),
    }
    print(json.dumps(decision, indent=2), flush=True)


if __name__ == "__main__":
    main()
