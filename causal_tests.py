"""
Statistical tests for non-event hypothesis classes.

All functions return a standardized result dict with:
    test_name: str          — identifies the test type
    hypothesis_class: str   — which of the 10 classes this serves
    statistic: float        — primary test statistic
    p_value: float          — p-value of primary test
    significant: bool       — at alpha=0.05
    effect_size: float      — beta, correlation, spread return, etc.
    n_observations: int     — sample size
    confidence_interval: [float, float]  — 95% CI on effect_size (where applicable)
    r_squared: float        — where applicable
    oos_result: dict|None   — out-of-sample validation (same shape, or None)
    details: dict           — class-specific full output
    summary: str            — human-readable one-liner

Usage:
    from causal_tests import test_exposure, test_cointegration
    result = test_exposure(target_returns, factor_returns, oos_start="2024-01-01")
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

# Optional statsmodels (needed for cointegration/Granger)
try:
    import statsmodels.api as sm
    from statsmodels.tsa.stattools import coint, grangercausalitytests, adfuller
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

# Ensure project root is importable
_project_dir = Path(__file__).parent
if str(_project_dir) not in sys.path:
    sys.path.insert(0, str(_project_dir))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ols_numpy(y: np.ndarray, X: np.ndarray):
    """OLS via numpy.linalg.lstsq. Returns (betas, residuals, r_squared, se_betas, t_stats, p_values)."""
    n, k = X.shape
    result = np.linalg.lstsq(X, y, rcond=None)
    betas = result[0]
    y_hat = X @ betas
    residuals = y - y_hat
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # HAC-like robust standard errors (Newey-West with bandwidth = int(n^(1/3)))
    dof = n - k
    if dof <= 0:
        se_betas = np.full(k, np.nan)
        t_stats = np.full(k, np.nan)
        p_values = np.full(k, np.nan)
    else:
        sigma2 = ss_res / dof
        try:
            XtX_inv = np.linalg.inv(X.T @ X)
        except np.linalg.LinAlgError:
            XtX_inv = np.linalg.pinv(X.T @ X)
        se_betas = np.sqrt(np.diag(sigma2 * XtX_inv))
        t_stats = betas / se_betas
        p_values = np.array([2 * scipy_stats.t.sf(abs(t), dof) for t in t_stats])

    return betas, residuals, r_squared, se_betas, t_stats, p_values


def _temporal_split(series_or_df, oos_start: str):
    """Split time series at oos_start date. Returns (in_sample, out_of_sample)."""
    idx = pd.Timestamp(oos_start)
    if isinstance(series_or_df, pd.DataFrame):
        return series_or_df[series_or_df.index < idx], series_or_df[series_or_df.index >= idx]
    return series_or_df[series_or_df.index < idx], series_or_df[series_or_df.index >= idx]


def _auto_oos_start(index: pd.DatetimeIndex, split_pct: float = 0.7) -> str:
    """Pick the OOS start date at the split_pct mark."""
    n = len(index)
    split_idx = int(n * split_pct)
    return str(index[split_idx].date())


# ---------------------------------------------------------------------------
# 1. Exposure (OLS regression)
# ---------------------------------------------------------------------------

def test_exposure(
    target_returns: pd.Series,
    factor_returns: pd.Series,
    control_returns: pd.DataFrame | None = None,
    oos_start: str | None = None,
) -> dict:
    """
    OLS regression of target on factor (+ optional controls).

    Tests: target_ret = alpha + beta * factor_ret [+ gamma * controls] + epsilon

    Returns standardized result dict with beta, t-stat, p-value, R-squared.
    """
    # Align all series
    df = pd.DataFrame({"target": target_returns, "factor": factor_returns})
    if control_returns is not None:
        if isinstance(control_returns, pd.Series):
            control_returns = control_returns.to_frame()
        df = df.join(control_returns, how="inner")
    df = df.dropna()

    if len(df) < 30:
        return {"test_name": "exposure_regression", "hypothesis_class": "exposure",
                "error": f"Insufficient data: {len(df)} observations (need >= 30)"}

    if oos_start is None:
        oos_start = _auto_oos_start(df.index)

    is_df, oos_df = _temporal_split(df, oos_start)

    def _run_regression(data):
        y = data["target"].values
        factor_col = data["factor"].values
        other_cols = [c for c in data.columns if c not in ("target", "factor")]
        X_cols = [factor_col]
        for c in other_cols:
            X_cols.append(data[c].values)
        X_cols.append(np.ones(len(y)))  # intercept
        X = np.column_stack(X_cols)
        betas, residuals, r_sq, se, t_stats, p_vals = _ols_numpy(y, X)
        beta_factor = float(betas[0])
        return {
            "beta": beta_factor,
            "t_stat": float(t_stats[0]),
            "p_value": float(p_vals[0]),
            "r_squared": float(r_sq),
            "alpha": float(betas[-1]),
            "n": len(y),
            "se": float(se[0]),
            "ci_lower": beta_factor - 1.96 * float(se[0]),
            "ci_upper": beta_factor + 1.96 * float(se[0]),
        }

    is_result = _run_regression(is_df)

    oos_result = None
    if len(oos_df) >= 10:
        oos_result = _run_regression(oos_df)

    target_name = target_returns.name or "target"
    factor_name = factor_returns.name or "factor"

    sign_word = "negative" if is_result["beta"] < 0 else "positive"
    oos_note = ""
    if oos_result:
        oos_sig = "significant" if oos_result["p_value"] < 0.05 else f"p={oos_result['p_value']:.3f}"
        oos_note = f" OOS: beta={oos_result['beta']:.3f}, {oos_sig}."

    return {
        "test_name": "exposure_regression",
        "hypothesis_class": "exposure",
        "statistic": is_result["t_stat"],
        "p_value": is_result["p_value"],
        "significant": is_result["p_value"] < 0.05,
        "effect_size": is_result["beta"],
        "confidence_interval": [is_result["ci_lower"], is_result["ci_upper"]],
        "r_squared": is_result["r_squared"],
        "n_observations": is_result["n"],
        "alpha_daily": is_result["alpha"],
        "oos_result": {
            "beta": oos_result["beta"],
            "t_stat": oos_result["t_stat"],
            "p_value": oos_result["p_value"],
            "significant": oos_result["p_value"] < 0.05,
            "r_squared": oos_result["r_squared"],
            "n": oos_result["n"],
            "effect_size": oos_result["beta"],
        } if oos_result else None,
        "details": {
            "in_sample": is_result,
            "oos_start": oos_start,
            "target": target_name,
            "factor": factor_name,
            "controls": [c for c in df.columns if c not in ("target", "factor")],
        },
        "summary": (
            f"{target_name} has {sign_word} exposure to {factor_name} "
            f"(beta={is_result['beta']:.3f}, t={is_result['t_stat']:.2f}, "
            f"p={is_result['p_value']:.4f}, R2={is_result['r_squared']:.3f}, "
            f"n={is_result['n']}).{oos_note}"
        ),
    }


# ---------------------------------------------------------------------------
# 2. Lead-lag (Granger causality + cross-correlation)
# ---------------------------------------------------------------------------

def test_lead_lag(
    leader_returns: pd.Series,
    follower_returns: pd.Series,
    max_lags: int = 10,
    oos_start: str | None = None,
) -> dict:
    """
    Granger causality test + cross-correlation at each lag.

    Tests whether leader_returns Granger-causes follower_returns.
    """
    if not HAS_STATSMODELS:
        return {"test_name": "granger_causality", "hypothesis_class": "lead_lag",
                "error": "statsmodels required for Granger causality test. pip install statsmodels"}

    df = pd.DataFrame({"leader": leader_returns, "follower": follower_returns}).dropna()

    if len(df) < max_lags + 30:
        return {"test_name": "granger_causality", "hypothesis_class": "lead_lag",
                "error": f"Insufficient data: {len(df)} observations (need >= {max_lags + 30})"}

    if oos_start is None:
        oos_start = _auto_oos_start(df.index)

    is_df, oos_df = _temporal_split(df, oos_start)

    def _run_granger(data):
        if len(data) < max_lags + 15:
            return None
        # grangercausalitytests expects [y, x] — tests if x Granger-causes y
        test_data = data[["follower", "leader"]].values
        try:
            results = grangercausalitytests(test_data, maxlag=max_lags, verbose=False)
        except Exception as e:
            return {"error": str(e)}

        # Find best lag (lowest p-value on F-test)
        best_lag = 1
        best_p = 1.0
        lag_results = {}
        for lag in range(1, max_lags + 1):
            f_test = results[lag][0]["ssr_ftest"]
            f_stat, p_val = float(f_test[0]), float(f_test[1])
            lag_results[lag] = {"f_stat": f_stat, "p_value": p_val}
            if p_val < best_p:
                best_p = p_val
                best_lag = lag

        return {
            "best_lag": best_lag,
            "best_p_value": best_p,
            "best_f_stat": lag_results[best_lag]["f_stat"],
            "lag_results": lag_results,
            "n": len(data),
        }

    # Cross-correlation
    xcorr = {}
    for lag in range(-max_lags, max_lags + 1):
        if lag < 0:
            corr = is_df["follower"].iloc[-lag:].reset_index(drop=True).corr(
                is_df["leader"].iloc[:lag].reset_index(drop=True)
            )
        elif lag == 0:
            corr = is_df["follower"].corr(is_df["leader"])
        else:
            corr = is_df["follower"].iloc[:-lag].reset_index(drop=True).corr(
                is_df["leader"].iloc[lag:].reset_index(drop=True)
            ) if lag < len(is_df) else None
        if corr is not None and not np.isnan(corr):
            xcorr[lag] = float(corr)

    is_granger = _run_granger(is_df)
    if is_granger is None or "error" in is_granger:
        return {"test_name": "granger_causality", "hypothesis_class": "lead_lag",
                "error": is_granger.get("error", "Insufficient in-sample data") if is_granger else "Insufficient data"}

    oos_granger = None
    if len(oos_df) >= max_lags + 15:
        oos_granger = _run_granger(oos_df)

    leader_name = leader_returns.name or "leader"
    follower_name = follower_returns.name or "follower"

    oos_note = ""
    if oos_granger and "error" not in oos_granger:
        oos_sig = "confirmed" if oos_granger["best_p_value"] < 0.05 else f"p={oos_granger['best_p_value']:.3f}"
        oos_note = f" OOS: {oos_sig} at lag {oos_granger['best_lag']}."

    # --- Cross-correlation sanity check (methodology rule 2026-04-12) ---
    # Granger causality can report spurious "lead-lag" when the true relationship is
    # CONTEMPORANEOUS. If cross-correlation at the best positive lag is tiny
    # (|xcorr| < 0.05) but lag-0 xcorr is large, flag the test as a likely false
    # positive — the F-test is fitting noise.
    best_lag = is_granger["best_lag"]
    lag0_xcorr = xcorr.get(0)
    best_lag_xcorr = xcorr.get(best_lag)
    spurious_granger = False
    spurious_reason = None
    if lag0_xcorr is not None and best_lag_xcorr is not None:
        if abs(best_lag_xcorr) < 0.05 and abs(lag0_xcorr) > 0.15:
            spurious_granger = True
            spurious_reason = (
                f"Granger false positive: xcorr at best lag {best_lag}="
                f"{best_lag_xcorr:+.3f} (|<0.05|), but lag-0 xcorr={lag0_xcorr:+.3f} "
                f"(|>0.15|). Relationship is contemporaneous, not predictive."
            )
    xcorr_note = ""
    if spurious_granger:
        xcorr_note = f" | XCORR WARNING: {spurious_reason}"

    return {
        "test_name": "granger_causality",
        "hypothesis_class": "lead_lag",
        "statistic": is_granger["best_f_stat"],
        "p_value": is_granger["best_p_value"],
        "significant": is_granger["best_p_value"] < 0.05 and not spurious_granger,
        "effect_size": is_granger["best_lag"],
        "n_observations": is_granger["n"],
        "confidence_interval": None,
        "r_squared": None,
        "oos_result": {
            "best_lag": oos_granger["best_lag"],
            "p_value": oos_granger["best_p_value"],
            "significant": oos_granger["best_p_value"] < 0.05,
            "n": oos_granger["n"],
            "effect_size": oos_granger["best_lag"],
        } if (oos_granger and "error" not in oos_granger) else None,
        "details": {
            "in_sample": is_granger,
            "cross_correlation": xcorr,
            "oos_start": oos_start,
            "leader": leader_name,
            "follower": follower_name,
            "lag0_xcorr": lag0_xcorr,
            "best_lag_xcorr": best_lag_xcorr,
            "spurious_granger": spurious_granger,
            "spurious_reason": spurious_reason,
        },
        "summary": (
            f"{leader_name} Granger-causes {follower_name} at lag {is_granger['best_lag']} "
            f"(F={is_granger['best_f_stat']:.2f}, p={is_granger['best_p_value']:.4f}, "
            f"n={is_granger['n']}).{oos_note}{xcorr_note}"
        ),
    }


# ---------------------------------------------------------------------------
# 3. Cointegration (Engle-Granger)
# ---------------------------------------------------------------------------

def test_cointegration(
    series_a: pd.Series,
    series_b: pd.Series,
    oos_start: str | None = None,
) -> dict:
    """
    Engle-Granger cointegration test. Returns test stat, p-value, half-life, hedge ratio.
    """
    if not HAS_STATSMODELS:
        return {"test_name": "engle_granger_cointegration", "hypothesis_class": "cointegration",
                "error": "statsmodels required. pip install statsmodels"}

    df = pd.DataFrame({"a": series_a, "b": series_b}).dropna()

    if len(df) < 60:
        return {"test_name": "engle_granger_cointegration", "hypothesis_class": "cointegration",
                "error": f"Insufficient data: {len(df)} observations (need >= 60)"}

    if oos_start is None:
        oos_start = _auto_oos_start(df.index)

    is_df, oos_df = _temporal_split(df, oos_start)

    def _run_coint(data):
        if len(data) < 30:
            return None
        t_stat, p_value, crit_values = coint(data["a"].values, data["b"].values)

        # Hedge ratio via OLS: a = alpha + beta * b
        X = sm.add_constant(data["b"].values)
        model = sm.OLS(data["a"].values, X).fit()
        hedge_ratio = float(model.params[1])

        # Spread and half-life
        spread = data["a"] - hedge_ratio * data["b"]
        spread_lag = spread.shift(1).dropna()
        spread_diff = spread.diff().dropna()
        common_idx = spread_lag.index.intersection(spread_diff.index)
        if len(common_idx) < 10:
            half_life = None
        else:
            X_hl = sm.add_constant(spread_lag.loc[common_idx].values)
            y_hl = spread_diff.loc[common_idx].values
            hl_model = sm.OLS(y_hl, X_hl).fit()
            lam = float(hl_model.params[1])
            half_life = -math.log(2) / math.log(1 + lam) if -1 < lam < 0 else None

        return {
            "t_stat": float(t_stat),
            "p_value": float(p_value),
            "crit_values": {f"{int(k)}%": float(v) for k, v in zip([1, 5, 10], crit_values)},
            "hedge_ratio": hedge_ratio,
            "half_life_days": round(half_life, 1) if half_life else None,
            "spread_mean": float(spread.mean()),
            "spread_std": float(spread.std()),
            "n": len(data),
        }

    is_result = _run_coint(is_df)
    if is_result is None:
        return {"test_name": "engle_granger_cointegration", "hypothesis_class": "cointegration",
                "error": "Insufficient in-sample data"}

    # OOS: apply IS hedge ratio, check if spread still mean-reverts
    oos_result = None
    if len(oos_df) >= 30:
        oos_spread = oos_df["a"] - is_result["hedge_ratio"] * oos_df["b"]
        oos_spread_zscore = (oos_spread - is_result["spread_mean"]) / is_result["spread_std"]
        # Check if OOS spread reverts: does its ADF reject unit root?
        try:
            adf_stat, adf_p, _, _, _, _ = adfuller(oos_spread.values, maxlag=int(len(oos_spread) ** (1/3)))
            oos_result = {
                "adf_stat": float(adf_stat),
                "p_value": float(adf_p),
                "significant": float(adf_p) < 0.05,
                "spread_mean": float(oos_spread.mean()),
                "spread_std": float(oos_spread.std()),
                "n": len(oos_df),
                "effect_size": is_result["hedge_ratio"],
            }
        except Exception:
            pass

    a_name = series_a.name or "series_a"
    b_name = series_b.name or "series_b"

    oos_note = ""
    if oos_result:
        oos_sig = "confirmed" if oos_result["significant"] else f"ADF p={oos_result['p_value']:.3f}"
        oos_note = f" OOS: spread stationarity {oos_sig}."

    hl_note = f", half-life={is_result['half_life_days']}d" if is_result["half_life_days"] else ""

    return {
        "test_name": "engle_granger_cointegration",
        "hypothesis_class": "cointegration",
        "statistic": is_result["t_stat"],
        "p_value": is_result["p_value"],
        "significant": is_result["p_value"] < 0.05,
        "effect_size": is_result["hedge_ratio"],
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": is_result["n"],
        "oos_result": oos_result,
        "details": {
            "in_sample": is_result,
            "oos_start": oos_start,
            "series_a": a_name,
            "series_b": b_name,
        },
        "summary": (
            f"{a_name} and {b_name} cointegration: t={is_result['t_stat']:.2f}, "
            f"p={is_result['p_value']:.4f}, hedge_ratio={is_result['hedge_ratio']:.3f}"
            f"{hl_note} (n={is_result['n']}).{oos_note}"
        ),
    }


# ---------------------------------------------------------------------------
# 4. Regime comparison
# ---------------------------------------------------------------------------

def test_regime(
    returns: pd.Series,
    regime_labels: pd.Series,
) -> dict:
    """
    Compare mean returns across regime buckets using Kruskal-Wallis test.

    Args:
        returns: pd.Series of returns.
        regime_labels: pd.Series of regime labels (same index as returns).
    """
    df = pd.DataFrame({"returns": returns, "regime": regime_labels}).dropna()

    regimes = df["regime"].unique()
    if len(regimes) < 2:
        return {"test_name": "regime_comparison", "hypothesis_class": "regime",
                "error": f"Need >= 2 regimes, got {len(regimes)}"}

    groups = [df[df["regime"] == r]["returns"].values for r in regimes]
    groups = [g for g in groups if len(g) >= 5]
    if len(groups) < 2:
        return {"test_name": "regime_comparison", "hypothesis_class": "regime",
                "error": "Need >= 5 observations per regime for at least 2 regimes"}

    h_stat, p_value = scipy_stats.kruskal(*groups)

    regime_stats = {}
    for r in regimes:
        group = df[df["regime"] == r]["returns"]
        if len(group) >= 2:
            regime_stats[str(r)] = {
                "mean": float(group.mean()),
                "median": float(group.median()),
                "std": float(group.std()),
                "n": len(group),
                "sharpe_annual": float(group.mean() / group.std() * (252 ** 0.5)) if group.std() > 0 else 0,
            }

    # Best and worst regimes
    sorted_regimes = sorted(regime_stats.items(), key=lambda x: x[1]["mean"], reverse=True)
    best = sorted_regimes[0]
    worst = sorted_regimes[-1]
    spread = best[1]["mean"] - worst[1]["mean"]

    return {
        "test_name": "regime_comparison",
        "hypothesis_class": "regime",
        "statistic": float(h_stat),
        "p_value": float(p_value),
        "significant": float(p_value) < 0.05,
        "effect_size": spread,
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": len(df),
        "oos_result": None,
        "details": {
            "regime_stats": regime_stats,
            "best_regime": best[0],
            "worst_regime": worst[0],
            "target": returns.name or "returns",
        },
        "summary": (
            f"Regime comparison (H={h_stat:.2f}, p={p_value:.4f}, n={len(df)}): "
            f"best={best[0]} ({best[1]['mean']:.3f}%/day), "
            f"worst={worst[0]} ({worst[1]['mean']:.3f}%/day), "
            f"spread={spread:.3f}%/day."
        ),
    }


# ---------------------------------------------------------------------------
# 5. Structural break (Chow test)
# ---------------------------------------------------------------------------

def test_structural_break(
    target_returns: pd.Series,
    factor_returns: pd.Series,
    break_date: str,
) -> dict:
    """
    Chow test: compare OLS regression before vs after break_date.
    """
    df = pd.DataFrame({"target": target_returns, "factor": factor_returns}).dropna()
    break_ts = pd.Timestamp(break_date)

    pre = df[df.index < break_ts]
    post = df[df.index >= break_ts]

    if len(pre) < 30 or len(post) < 30:
        return {"test_name": "chow_structural_break", "hypothesis_class": "structural_break",
                "error": f"Need >= 30 obs on each side. Pre={len(pre)}, Post={len(post)}"}

    def _fit(data):
        y = data["target"].values
        X = np.column_stack([data["factor"].values, np.ones(len(y))])
        betas, residuals, r_sq, se, t_stats, p_vals = _ols_numpy(y, X)
        ss_res = np.sum(residuals ** 2)
        return {"beta": float(betas[0]), "alpha": float(betas[1]),
                "r_squared": float(r_sq), "ss_res": float(ss_res), "n": len(y),
                "t_stat": float(t_stats[0]), "p_value": float(p_vals[0])}

    pooled = _fit(df)
    pre_result = _fit(pre)
    post_result = _fit(post)

    k = 2  # number of parameters
    n1, n2 = pre_result["n"], post_result["n"]
    f_stat = ((pooled["ss_res"] - pre_result["ss_res"] - post_result["ss_res"]) / k) / \
             ((pre_result["ss_res"] + post_result["ss_res"]) / (n1 + n2 - 2 * k))
    p_value = float(scipy_stats.f.sf(f_stat, k, n1 + n2 - 2 * k))

    target_name = target_returns.name or "target"
    factor_name = factor_returns.name or "factor"

    return {
        "test_name": "chow_structural_break",
        "hypothesis_class": "structural_break",
        "statistic": float(f_stat),
        "p_value": p_value,
        "significant": p_value < 0.05,
        "effect_size": post_result["beta"] - pre_result["beta"],
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": len(df),
        "oos_result": None,
        "details": {
            "break_date": break_date,
            "pre": pre_result,
            "post": post_result,
            "pooled": pooled,
            "target": target_name,
            "factor": factor_name,
        },
        "summary": (
            f"Structural break in {target_name}~{factor_name} at {break_date}: "
            f"F={f_stat:.2f}, p={p_value:.4f}. "
            f"Pre-beta={pre_result['beta']:.3f} (n={n1}), "
            f"Post-beta={post_result['beta']:.3f} (n={n2})."
        ),
    }


# ---------------------------------------------------------------------------
# 6. Threshold (threshold-triggered event study)
# ---------------------------------------------------------------------------

def test_threshold(
    trigger_series: pd.Series,
    target_returns: pd.Series,
    threshold: float,
    direction: str = "above",
    horizons: list[int] | None = None,
) -> dict:
    """
    When trigger crosses threshold, measure target returns at horizons.

    Args:
        trigger_series: The indicator series (e.g., VIX levels).
        target_returns: The asset returns to measure.
        threshold: The trigger level.
        direction: "above" or "below" — which crossing triggers.
        horizons: Days to measure forward (default [5, 10, 20]).
    """
    if horizons is None:
        horizons = [5, 10, 20]

    df = pd.DataFrame({"trigger": trigger_series, "target": target_returns}).dropna()

    if len(df) < 50:
        return {"test_name": "threshold_event_study", "hypothesis_class": "threshold",
                "error": f"Insufficient data: {len(df)} observations"}

    # Find threshold crossings
    if direction == "above":
        crossed = (df["trigger"] >= threshold) & (df["trigger"].shift(1) < threshold)
    else:
        crossed = (df["trigger"] <= threshold) & (df["trigger"].shift(1) > threshold)

    crossing_dates = df.index[crossed]
    if len(crossing_dates) < 3:
        return {"test_name": "threshold_event_study", "hypothesis_class": "threshold",
                "error": f"Only {len(crossing_dates)} threshold crossings (need >= 3)"}

    # Measure forward returns from each crossing
    horizon_results = {}
    for h in horizons:
        returns_at_h = []
        for date in crossing_dates:
            loc = df.index.get_loc(date)
            end_loc = min(loc + h, len(df) - 1)
            if end_loc > loc:
                cumulative = df["target"].iloc[loc + 1:end_loc + 1].sum()
                returns_at_h.append(cumulative)
        if len(returns_at_h) >= 3:
            arr = np.array(returns_at_h)
            t_stat, p_val = scipy_stats.ttest_1samp(arr, 0)
            horizon_results[f"{h}d"] = {
                "mean": float(arr.mean()),
                "median": float(np.median(arr)),
                "std": float(arr.std()),
                "t_stat": float(t_stat),
                "p_value": float(p_val),
                "significant": float(p_val) < 0.05,
                "positive_rate": float(np.mean(arr > 0)),
                "n": len(arr),
            }

    if not horizon_results:
        return {"test_name": "threshold_event_study", "hypothesis_class": "threshold",
                "error": "No horizon had enough crossing events to measure"}

    # Pick best horizon
    best_h = min(horizon_results.items(), key=lambda x: x[1]["p_value"])
    trigger_name = trigger_series.name or "trigger"
    target_name = target_returns.name or "target"

    return {
        "test_name": "threshold_event_study",
        "hypothesis_class": "threshold",
        "statistic": best_h[1]["t_stat"],
        "p_value": best_h[1]["p_value"],
        "significant": best_h[1]["p_value"] < 0.05,
        "effect_size": best_h[1]["mean"],
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": len(crossing_dates),
        "oos_result": None,
        "details": {
            "threshold": threshold,
            "direction": direction,
            "crossing_count": len(crossing_dates),
            "horizon_results": horizon_results,
            "best_horizon": best_h[0],
            "trigger": trigger_name,
            "target": target_name,
        },
        "summary": (
            f"When {trigger_name} crosses {direction} {threshold}: "
            f"{target_name} {best_h[0]} avg={best_h[1]['mean']:.2f}% "
            f"(t={best_h[1]['t_stat']:.2f}, p={best_h[1]['p_value']:.4f}, "
            f"n={len(crossing_dates)} crossings)."
        ),
    }


def identify_first_close_events(
    trigger_series: pd.Series,
    threshold: float,
    direction: str = "above",
    cluster_days: int = 30,
) -> list:
    """
    Identify first-close threshold events with cluster buffering.

    Returns date strings (YYYY-MM-DD) for each cluster-buffered event — only the
    first close past the threshold is kept; subsequent in-cluster crossings
    within `cluster_days` are suppressed.

    This is the canonical methodology for threshold-triggered event studies.
    Raw threshold crossings (including in-cluster duplicates) systematically
    overstate effect size by 40-60% vs first-close cluster-buffered counting.

    See: threshold_scan_hit_canonical_retest_rule_2026_04_18.
    """
    events = []
    last_event = None
    for dt, v in trigger_series.dropna().items():
        trips = (direction == "above" and v > threshold) or (
            direction == "below" and v < threshold
        )
        if not trips:
            continue
        if last_event is None or (dt - last_event).days > cluster_days:
            events.append(dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt))
            last_event = dt
        else:
            last_event = dt
    return events


def _measure_horizon_stats(impact: dict, horizons: list[int]) -> dict:
    """Extract per-horizon abnormal-return stats from a measure_event_impact result."""
    horizon_stats = {}
    for h in horizons:
        h_label = f"{h}d"
        avg = impact.get(f"avg_abnormal_{h_label}")
        if avg is None:
            continue
        horizon_stats[h_label] = {
            "abnormal_mean": avg,
            "median": impact.get(f"median_abnormal_{h_label}"),
            "positive_rate": impact.get(f"positive_rate_abnormal_{h_label}"),
            "p_value": impact.get(f"p_value_abnormal_{h_label}"),
            "n": impact.get("events_measured"),
        }
    return horizon_stats


def _pick_best_horizon(horizon_stats: dict, require_mean_pct: float = 1.0, p_threshold: float = 0.05):
    """Pick the horizon with lowest p_value that meets p<thresh and |mean|>=require_mean_pct."""
    best_horizon = None
    best_p = 1.0
    for h_label, hs in horizon_stats.items():
        p = hs.get("p_value")
        mean = hs.get("abnormal_mean")
        if p is None or mean is None:
            continue
        if p < p_threshold and abs(mean) >= require_mean_pct and p < best_p:
            best_p = p
            best_horizon = h_label
    return best_horizon, best_p if best_horizon else None


def canonical_retest_threshold(
    trigger_identifier: str,
    target_symbol: str,
    threshold: float,
    direction: str = "above",
    cluster_days: int = 30,
    horizons: list[int] | None = None,
    start: str = "2010-01-01",
    end: str | None = None,
    benchmark: str | None = "auto",
    recency_split: str = "2020-01-01",
) -> dict:
    """
    Canonical re-test of a threshold-triggered hypothesis.

    Methodology (per threshold_scan_hit_canonical_retest_rule_2026_04_18):
    1. Identify first-close cluster-buffered events (not raw crossings)
    2. Measure SPY-benchmarked abnormal returns with entry_price="open"
       (VIX closes after market → entry at next-day open is realistic)
    3. Evaluate on TWO samples: pooled (full range) + recency subset (post-split)
    4. Require both samples to pass p<0.05 AND |mean|>=1% AND sign consistency
       (same direction). This guards against regime-specific signals like XLK/XLF.

    Used to validate threshold-mode scan hits before queueing them.
    """
    from tools.timeseries import get_series
    from tools.asset_class import classify_asset, resolve_event_benchmark
    import market_data

    if horizons is None:
        horizons = [1, 3, 5, 10, 20]

    # Benchmark-class guard: for non-equity targets (Treasuries/commodities/FX/
    # crypto) SPY-adjustment injects an equity-market signal into the "abnormal"
    # return and produces false PASSes. Auto-resolve to raw returns (benchmark
    # None) unless the caller explicitly forced a benchmark.
    # See threshold_canonical_retest_nonequity_benchmark_invalid_rule_2026_06_08.
    target_class = classify_asset(target_symbol)
    if benchmark == "auto":
        benchmark = resolve_event_benchmark(target_symbol)
    benchmark_mode = "spy_adjusted" if benchmark else "raw_returns_nonequity"

    # Fetch trigger series
    try:
        trigger = get_series(trigger_identifier, start, end)
    except Exception as e:
        return {"error": f"Could not fetch trigger {trigger_identifier}: {e}"}

    # Identify cluster-buffered events (full sample)
    events = identify_first_close_events(trigger, threshold, direction, cluster_days)

    if len(events) < 3:
        return {
            "error": f"Only {len(events)} first-close events (need >= 3)",
            "n_events": len(events),
            "method": f"first_close_cluster_buffered_{cluster_days}d",
            "event_dates": events,
        }

    # Split into pooled (all events) and recency (post-split events)
    recent_events = [d for d in events if d >= recency_split]

    def _measure(event_list, label):
        if len(event_list) < 3:
            return {
                "label": label,
                "n_events": len(event_list),
                "error": f"Only {len(event_list)} events (need >=3)",
                "horizons": {},
                "passes": False,
            }
        event_entries = [{"symbol": target_symbol, "date": d} for d in event_list]
        try:
            # event_timing="after_hours" aligns target entry at T+1 open with
            # benchmark reference at T close, preventing look-ahead bias. Threshold
            # signals fire at close of T (e.g. VIX closes >30) so first tradable
            # entry is T+1 open. Prior bug: default event_timing entered target at
            # open of T (before signal) while bench referenced close of T-1,
            # inflating abnormal by ~2-3% per VIX30-style event.
            # See: canonical_retest_lookahead_bias_bug_2026_04_19.
            impact = market_data.measure_event_impact(
                event_dates=event_entries,
                benchmark=benchmark,
                entry_price="open",
                event_timing="after_hours",
                check_factors=False,
                check_seasonal=False,
            )
        except Exception as e:
            return {"label": label, "error": f"measure_event_impact failed: {e}", "horizons": {}, "passes": False}
        if "error" in impact:
            return {"label": label, "error": impact["error"], "horizons": {}, "passes": False}

        h_stats = _measure_horizon_stats(impact, horizons)
        best_h, best_p = _pick_best_horizon(h_stats)
        return {
            "label": label,
            "n_events": len(event_list),
            "events_measured": impact.get("events_measured"),
            "horizons": h_stats,
            "best_horizon": best_h,
            "best_p_value": best_p,
            "passes": best_h is not None,
        }

    pooled = _measure(events, f"pooled_{start[:4]}_to_{(end or 'now')[:4]}")
    recent = _measure(recent_events, f"recency_{recency_split[:4]}_plus")

    # Overall pass requires BOTH periods to pass AND signs to agree on best horizon
    both_pass = pooled.get("passes") and recent.get("passes")
    sign_agrees = False
    if both_pass:
        p_bh = pooled.get("best_horizon")
        r_bh = recent.get("best_horizon")
        if p_bh and r_bh:
            p_mean = pooled["horizons"][p_bh]["abnormal_mean"]
            r_mean = recent["horizons"][r_bh]["abnormal_mean"]
            sign_agrees = (p_mean >= 0) == (r_mean >= 0)

    passes = both_pass and sign_agrees

    # Failure reason
    if not pooled.get("passes"):
        fail_reason = "pooled_sample_fails"
    elif not recent.get("passes"):
        fail_reason = "recency_subset_fails_regime_dependent"
    elif not sign_agrees:
        fail_reason = "sign_flip_between_samples"
    else:
        fail_reason = None

    return {
        "method": f"first_close_cluster_buffered_{cluster_days}d_{benchmark_mode}_dual_sample",
        "trigger": trigger_identifier,
        "target": target_symbol,
        "target_class": target_class,
        "threshold": threshold,
        "direction": direction,
        "cluster_days": cluster_days,
        "benchmark": benchmark,
        "benchmark_mode": benchmark_mode,
        "n_events_pooled": pooled.get("n_events"),
        "n_events_recent": recent.get("n_events"),
        "pooled": pooled,
        "recent": recent,
        "passes": passes,
        "fail_reason": fail_reason,
        "event_dates": events,
        "summary": (
            f"Canonical retest {trigger_identifier}{'>' if direction == 'above' else '<'}{threshold} "
            f"-> {target_symbol}: pooled n={pooled.get('n_events')}, recent n={recent.get('n_events')}, "
            f"{'PASS (both samples)' if passes else 'FAIL (' + str(fail_reason) + ')'}"
        ),
    }


# ---------------------------------------------------------------------------
# 7. Network propagation
# ---------------------------------------------------------------------------

def test_network(
    hub_returns: pd.Series,
    spoke_returns: pd.DataFrame,
    max_lag: int = 5,
) -> dict:
    """
    Test whether hub leads spokes. Cross-correlation + optional Granger per spoke.
    """
    df = pd.DataFrame({"hub": hub_returns}).join(spoke_returns, how="inner").dropna()

    if len(df) < 60:
        return {"test_name": "network_propagation", "hypothesis_class": "network",
                "error": f"Insufficient data: {len(df)} observations"}

    spoke_cols = [c for c in df.columns if c != "hub"]
    spoke_results = {}

    for spoke in spoke_cols:
        # Cross-correlation at positive lags (hub leads spoke)
        best_corr = 0
        best_lag = 0
        for lag in range(1, max_lag + 1):
            if lag < len(df):
                corr = df["hub"].iloc[:-lag].reset_index(drop=True).corr(
                    df[spoke].iloc[lag:].reset_index(drop=True)
                )
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag

        contemporaneous_corr = df["hub"].corr(df[spoke])

        spoke_results[spoke] = {
            "contemporaneous_corr": float(contemporaneous_corr),
            "best_lag": best_lag,
            "best_lag_corr": float(best_corr),
            "n": len(df),
        }

        # Granger test if available
        if HAS_STATSMODELS and len(df) >= max_lag + 30:
            try:
                test_data = df[[spoke, "hub"]].values
                results = grangercausalitytests(test_data, maxlag=max_lag, verbose=False)
                best_p = min(results[lag][0]["ssr_ftest"][1] for lag in range(1, max_lag + 1))
                spoke_results[spoke]["granger_p"] = float(best_p)
                spoke_results[spoke]["granger_significant"] = float(best_p) < 0.05
            except Exception:
                pass

    hub_name = hub_returns.name or "hub"
    significant_spokes = [s for s, r in spoke_results.items() if r.get("granger_significant", False)]

    return {
        "test_name": "network_propagation",
        "hypothesis_class": "network",
        "statistic": len(significant_spokes),
        "p_value": None,
        "significant": len(significant_spokes) >= 2,
        "effect_size": len(significant_spokes) / len(spoke_cols) if spoke_cols else 0,
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": len(df),
        "oos_result": None,
        "details": {
            "hub": hub_name,
            "spoke_results": spoke_results,
            "significant_spokes": significant_spokes,
        },
        "summary": (
            f"{hub_name} leads {len(significant_spokes)}/{len(spoke_cols)} spokes "
            f"(Granger p<0.05). "
            f"Tested: {', '.join(spoke_cols[:5])}{'...' if len(spoke_cols) > 5 else ''}. "
            f"n={len(df)}."
        ),
    }


# ---------------------------------------------------------------------------
# 8. Calendar anomaly
# ---------------------------------------------------------------------------

def test_calendar(
    returns: pd.Series,
    pattern_type: str,
    pattern_spec: dict | None = None,
    oos_start_year: int | None = None,
) -> dict:
    """
    Test calendar anomaly (monthly, day-of-week, turn-of-month).

    Args:
        returns: Daily returns series.
        pattern_type: "monthly", "dow" (day of week), "tom" (turn of month).
        pattern_spec: Optional filter, e.g. {"month": 1} for January effect.
        oos_start_year: Year to start OOS validation.
    """
    r = returns.dropna()
    if len(r) < 252:
        return {"test_name": "calendar_anomaly", "hypothesis_class": "calendar",
                "error": f"Need >= 1 year of data, got {len(r)} days"}

    if pattern_type == "monthly":
        r_df = pd.DataFrame({"returns": r, "group": r.index.month})
        group_labels = {i: pd.Timestamp(2000, i, 1).strftime("%b") for i in range(1, 13)}
    elif pattern_type == "dow":
        r_df = pd.DataFrame({"returns": r, "group": r.index.dayofweek})
        group_labels = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    elif pattern_type == "tom":
        # Turn of month: last 2 + first 2 trading days vs rest
        day_of_month = r.index.day
        max_day = r.groupby(r.index.to_period("M")).transform("count")
        is_tom = (day_of_month <= 2) | (day_of_month >= 27)
        r_df = pd.DataFrame({"returns": r, "group": is_tom.astype(int)})
        group_labels = {0: "mid_month", 1: "turn_of_month"}
    else:
        return {"test_name": "calendar_anomaly", "hypothesis_class": "calendar",
                "error": f"Unknown pattern_type: {pattern_type}. Use monthly/dow/tom"}

    groups = r_df.groupby("group")["returns"]
    group_stats = {}
    for gid, group in groups:
        label = group_labels.get(gid, str(gid))
        if len(group) >= 10:
            t, p = scipy_stats.ttest_1samp(group.values, 0)
            group_stats[label] = {
                "mean": float(group.mean()),
                "std": float(group.std()),
                "t_stat": float(t),
                "p_value": float(p),
                "n": len(group),
            }

    # Kruskal-Wallis across groups
    group_arrays = [g.values for _, g in groups if len(g) >= 10]
    if len(group_arrays) >= 2:
        h_stat, kw_p = scipy_stats.kruskal(*group_arrays)
    else:
        h_stat, kw_p = 0, 1.0

    # If pattern_spec filters to a specific group, highlight that
    target_group = None
    if pattern_spec and "month" in pattern_spec:
        target_label = group_labels.get(pattern_spec["month"], str(pattern_spec["month"]))
        target_group = group_stats.get(target_label)

    # OOS if requested
    oos_result = None
    if oos_start_year:
        oos_r = r[r.index.year >= oos_start_year]
        is_r = r[r.index.year < oos_start_year]
        if len(oos_r) >= 60 and target_group:
            oos_df = pd.DataFrame({"returns": oos_r, "group": oos_r.index.month if pattern_type == "monthly" else oos_r.index.dayofweek})
            target_key = pattern_spec.get("month") or pattern_spec.get("dow")
            if target_key is not None:
                oos_subset = oos_df[oos_df["group"] == target_key]["returns"]
                if len(oos_subset) >= 5:
                    t, p = scipy_stats.ttest_1samp(oos_subset.values, 0)
                    oos_result = {
                        "mean": float(oos_subset.mean()),
                        "t_stat": float(t),
                        "p_value": float(p),
                        "significant": float(p) < 0.05,
                        "n": len(oos_subset),
                        "effect_size": float(oos_subset.mean()),
                    }

    target_name = returns.name or "returns"

    return {
        "test_name": "calendar_anomaly",
        "hypothesis_class": "calendar",
        "statistic": float(h_stat),
        "p_value": float(kw_p),
        "significant": float(kw_p) < 0.05,
        "effect_size": target_group["mean"] if target_group else max((s["mean"] for s in group_stats.values()), default=0),
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": len(r),
        "oos_result": oos_result,
        "details": {
            "pattern_type": pattern_type,
            "pattern_spec": pattern_spec,
            "group_stats": group_stats,
            "target": target_name,
        },
        "summary": (
            f"Calendar anomaly ({pattern_type}) for {target_name}: "
            f"Kruskal-Wallis H={h_stat:.2f}, p={kw_p:.4f}, n={len(r)}. "
            + (f"Target group: mean={target_group['mean']:.3f}%/day." if target_group else
               "Groups: " + ", ".join(f"{k}={v['mean']:.3f}%" for k, v in list(group_stats.items())[:4]))
        ),
    }


# ---------------------------------------------------------------------------
# 9. Cross-section (quintile sorts)
# ---------------------------------------------------------------------------

def test_cross_section(
    universe_returns: pd.DataFrame,
    sort_factor: pd.Series,
    n_quantiles: int = 5,
    holding_period_days: int = 21,
    oos_start: str | None = None,
) -> dict:
    """
    Sort stocks by factor into quantiles, form portfolios, test long-short spread.

    Args:
        universe_returns: DataFrame of daily returns (columns = tickers).
        sort_factor: Series mapping tickers to factor values (index = tickers).
        n_quantiles: Number of quantile buckets (default 5 = quintiles).
        holding_period_days: Rebalance frequency in trading days.
        oos_start: OOS split date.
    """
    common_tickers = list(set(universe_returns.columns) & set(sort_factor.index))
    if len(common_tickers) < n_quantiles * 3:
        return {"test_name": "cross_section_sort", "hypothesis_class": "cross_section",
                "error": f"Need >= {n_quantiles * 3} stocks, got {len(common_tickers)}"}

    returns = universe_returns[common_tickers]
    factor = sort_factor.loc[common_tickers].sort_values()

    # Assign quantiles
    quantile_labels = pd.qcut(factor, n_quantiles, labels=False, duplicates="drop")
    quantile_labels = quantile_labels.dropna()

    # Compute equal-weight portfolio returns per quantile
    quantile_returns = {}
    for q in sorted(quantile_labels.unique()):
        tickers_in_q = quantile_labels[quantile_labels == q].index.tolist()
        q_rets = returns[tickers_in_q].mean(axis=1)
        quantile_returns[int(q)] = q_rets

    q_df = pd.DataFrame(quantile_returns).dropna()
    if q_df.empty:
        return {"test_name": "cross_section_sort", "hypothesis_class": "cross_section",
                "error": "No overlapping return data across quantiles"}

    # Long-short spread: Q0 (lowest factor) - Q(n-1) (highest), or vice versa
    long_q = 0
    short_q = max(q_df.columns)
    spread = q_df[long_q] - q_df[short_q]

    t_stat, p_value = scipy_stats.ttest_1samp(spread.values, 0)
    mean_spread = float(spread.mean())
    annual_spread = mean_spread * 252

    quantile_stats = {}
    for q in sorted(q_df.columns):
        qr = q_df[q]
        quantile_stats[f"Q{q}"] = {
            "mean_daily": float(qr.mean()),
            "annual_return": float(qr.mean() * 252),
            "sharpe": float(qr.mean() / qr.std() * (252 ** 0.5)) if qr.std() > 0 else 0,
            "n_stocks": int((quantile_labels == q).sum()),
        }

    factor_name = sort_factor.name or "factor"

    return {
        "test_name": "cross_section_sort",
        "hypothesis_class": "cross_section",
        "statistic": float(t_stat),
        "p_value": float(p_value),
        "significant": float(p_value) < 0.05,
        "effect_size": mean_spread,
        "confidence_interval": None,
        "r_squared": None,
        "n_observations": len(q_df),
        "oos_result": None,
        "details": {
            "n_quantiles": n_quantiles,
            "quantile_stats": quantile_stats,
            "spread_annual_pct": annual_spread,
            "n_stocks": len(common_tickers),
            "factor": factor_name,
            "long_quantile": long_q,
            "short_quantile": short_q,
        },
        "summary": (
            f"Cross-section sort by {factor_name}: "
            f"Q{long_q}-Q{short_q} spread={mean_spread:.3f}%/day "
            f"({annual_spread:.1f}%/yr), t={t_stat:.2f}, p={p_value:.4f}. "
            f"{len(common_tickers)} stocks, {len(q_df)} days."
        ),
    }
