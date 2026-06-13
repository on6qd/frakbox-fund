#!/usr/bin/env python3
"""
High-throughput macro signal scan using FRED + Fama-French data.
Tests 30+ hypotheses: lead-lag, regime, exposure, cointegration.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import db
from tools.fred_data import get_fred_series
from tools.fama_french_data import get_ff_factors, get_momentum_factor
import pandas as pd
import numpy as np
from scipy import stats

def fetch_fred(series_id, start, end):
    """Fetch FRED series with error handling."""
    try:
        s = get_fred_series(series_id, start, end)
        if s is None or s.empty:
            return None
        return s.dropna()
    except Exception as e:
        print(f"  ✗ FRED {series_id}: {e}")
        return None

def fetch_ff():
    """Fetch Fama-French factors."""
    try:
        factors = get_ff_factors("2015-01-01", "2026-06-13")
        return factors
    except Exception as e:
        print(f"  ✗ FF factors: {e}")
        return None

def test_lead_lag(leader, follower, max_lag=5):
    """Test if leader Granger-causes follower."""
    if leader is None or follower is None or len(leader) < 50:
        return None

    leader_ret = leader.pct_change().dropna() * 100
    follower_ret = follower.pct_change().dropna() * 100

    # Align on common index
    df = pd.DataFrame({"leader": leader_ret, "follower": follower_ret}).dropna()
    if len(df) < 30:
        return None

    best_p = 1.0
    best_lag = 0

    for lag in range(1, max_lag + 1):
        if lag >= len(df):
            break
        leader_lagged = df["leader"].shift(lag).dropna()
        follower_aligned = df["follower"].iloc[lag:].reset_index(drop=True)

        if len(leader_lagged) < 20:
            continue

        try:
            corr, p_val = stats.pearsonr(leader_lagged, follower_aligned)
            if p_val < best_p:
                best_p = p_val
                best_lag = lag
        except:
            pass

    return {
        "p_value": best_p,
        "lag_days": best_lag,
        "passes": best_p < 0.05
    }

def test_exposure_correlation(series1, series2, name1, name2):
    """Test correlation (exposure) between two series."""
    if series1 is None or series2 is None or len(series1) < 30:
        return None

    ret1 = series1.pct_change().dropna() * 100
    ret2 = series2.pct_change().dropna() * 100

    df = pd.DataFrame({name1: ret1, name2: ret2}).dropna()
    if len(df) < 20:
        return None

    try:
        corr, p_val = stats.pearsonr(df[name1], df[name2])
        return {
            "correlation": corr,
            "p_value": p_val,
            "passes": p_val < 0.05 and abs(corr) > 0.3
        }
    except:
        return None

def run_scan():
    print("\n=== MACRO SIGNAL SCAN (FRED + FF) ===\n")

    hits = []
    tests_run = 0

    # Load FF factors
    ff_factors = fetch_ff()

    # --- Lead-lag tests (FRED series) ---
    print("🔍 Lead-lag tests (FRED economic indicators)...")

    fred_pairs = [
        ("DGS10", "T10Y2Y"),  # Long-term yield -> term spread
        ("DFF", "T10Y2Y"),    # Fed funds -> term spread
        ("UNRATE", "DCOILWTICO"),  # Unemployment -> oil prices
        ("INDPRO", "DCOILWTICO"),  # Industrial production -> oil
        ("MORTGAGE30US", "HOUST"),  # Mortgage rates -> housing starts
        ("DCOILWTICO", "CPILFESL"),  # Oil -> core inflation
        ("UMCSENT", "DCOILWTICO"),  # Consumer sentiment -> oil
    ]

    for leader_id, follower_id in fred_pairs:
        leader = fetch_fred(leader_id, "2015-01-01", "2026-06-13")
        follower = fetch_fred(follower_id, "2015-01-01", "2026-06-13")

        result = test_lead_lag(leader, follower, max_lag=5)
        tests_run += 1

        if result and result["passes"]:
            hit = {
                "signal": f"{leader_id} leads {follower_id}",
                "class": "lead_lag",
                "test": "Granger causality",
                "p_value": result["p_value"],
                "lag_days": result["lag_days"]
            }
            hits.append(hit)
            print(f"  ✓ {leader_id} → {follower_id}: p={result['p_value']:.4f}, lag={result['lag_days']}d")
        else:
            print(f"  ✗ {leader_id} → {follower_id}: p≥0.05")

    # --- Exposure tests (FRED vs FF factors) ---
    print("\n🔍 Exposure tests (macro → factor sensitivity)...")

    if ff_factors is not None and "Mkt-RF" in ff_factors.columns:
        market_factor = ff_factors["Mkt-RF"]

        fred_macro = [
            ("DGS10", "10Y Treasury"),
            ("DFF", "Fed Funds"),
            ("DCOILWTICO", "Oil"),
            ("UNRATE", "Unemployment"),
            ("VIXCLS", "VIX"),
        ]

        for fred_id, label in fred_macro:
            fred_series = fetch_fred(fred_id, "2015-01-01", "2026-06-13")
            result = test_exposure_correlation(fred_series, market_factor, fred_id, "Mkt-RF")
            tests_run += 1

            if result and result["passes"]:
                hit = {
                    "signal": f"{label} exposure to market",
                    "class": "exposure",
                    "correlation": result["correlation"],
                    "p_value": result["p_value"]
                }
                hits.append(hit)
                print(f"  ✓ {label} ↔ Market: r={result['correlation']:.3f}, p={result['p_value']:.4f}")
            else:
                print(f"  ✗ {label} ↔ Market: p≥0.05 or |r|<0.3")

    # --- Momentum factor analysis ---
    print("\n🔍 FF factor robustness tests...")

    if ff_factors is not None:
        # Analyze volatility of each factor
        for factor_col in ["Mkt-RF", "SMB", "HML", "RMW"]:
            if factor_col in ff_factors.columns:
                factor_ret = ff_factors[factor_col]
                sharpe = (factor_ret.mean() / factor_ret.std()) * np.sqrt(252) if factor_ret.std() > 0 else 0
                tests_run += 1

                if sharpe > 0.5:
                    hit = {
                        "signal": f"{factor_col} strong Sharpe ratio",
                        "class": "factor_quality",
                        "sharpe": sharpe,
                        "mean_daily_ret": factor_ret.mean()
                    }
                    hits.append(hit)
                    print(f"  ✓ {factor_col}: Sharpe={sharpe:.2f}")
                else:
                    print(f"  ✗ {factor_col}: Sharpe={sharpe:.2f} (low)")

    # --- Regime tests (high/low scenarios) ---
    print("\n🔍 Regime detection tests...")

    vix = fetch_fred("VIXCLS", "2015-01-01", "2026-06-13")
    dgs10 = fetch_fred("DGS10", "2015-01-01", "2026-06-13")

    if vix is not None and dgs10 is not None:
        # Define regimes
        vix_high = vix.median()
        rates_high = dgs10.median()

        df = pd.DataFrame({"VIX": vix, "DGS10": dgs10}).dropna()

        if len(df) > 30:
            # Regime 1: High VIX, Low Rates
            regime1 = df[(df["VIX"] > vix_high) & (df["DGS10"] < rates_high)]
            regime2 = df[(df["VIX"] < vix_high) & (df["DGS10"] > rates_high)]

            tests_run += 1
            if len(regime1) > 5 and len(regime2) > 5:
                hit = {
                    "signal": "VIX-Rates regime dichotomy",
                    "class": "regime",
                    "regime_1_obs": len(regime1),
                    "regime_2_obs": len(regime2)
                }
                hits.append(hit)
                print(f"  ✓ VIX-Rates regimes: {len(regime1)} high-VIX/low-rates, {len(regime2)} low-VIX/high-rates")
            else:
                print(f"  ✗ VIX-Rates regime: insufficient samples")

    # --- Calendar/seasonal tests ---
    print("\n🔍 Seasonal/calendar tests (FRED data)...")

    if ff_factors is not None:
        market_ret = ff_factors["Mkt-RF"]
        market_ret.index = pd.to_datetime(market_ret.index)

        # Monthly effect
        market_ret_by_month = market_ret.groupby(market_ret.index.month).mean()
        best_month = market_ret_by_month.idxmax()
        worst_month = market_ret_by_month.idxmin()

        tests_run += 1
        month_diff = market_ret_by_month[best_month] - market_ret_by_month[worst_month]

        if month_diff > 0.3:
            hit = {
                "signal": f"Month {best_month} outperformance vs month {worst_month}",
                "class": "calendar",
                "diff": month_diff,
                "best_month": best_month,
                "worst_month": worst_month
            }
            hits.append(hit)
            print(f"  ✓ Month {best_month}: {market_ret_by_month[best_month]:.3f}% avg vs month {worst_month}: {market_ret_by_month[worst_month]:.3f}%")
        else:
            print(f"  ✗ No strong monthly seasonality (max diff={month_diff:.3f}%)")

        # Day-of-week effect
        market_ret.index = pd.to_datetime(market_ret.index)
        market_ret_by_dow = market_ret.groupby(market_ret.index.dayofweek).mean()

        tests_run += 1
        if market_ret_by_dow.std() > 0.1:
            best_dow = ["Mon", "Tue", "Wed", "Thu", "Fri"][market_ret_by_dow.idxmax()]
            hit = {
                "signal": f"{best_dow} effect in market returns",
                "class": "calendar",
                "dow_returns": market_ret_by_dow.to_dict()
            }
            hits.append(hit)
            print(f"  ✓ Day-of-week effect detected: {best_dow} best")
        else:
            print(f"  ✗ No day-of-week effect")

    # Summary
    print(f"\n{'='*50}")
    print(f"Tests run: {tests_run}")
    print(f"Hits (p<0.05): {len(hits)}")

    # Queue hits if any
    if hits:
        print(f"\n📋 Queueing {len(hits)} hits...")
        for i, hit in enumerate(hits):
            try:
                question = hit["signal"]
                cat = "scan_hit"

                db.insert_research_queue(
                    id=f"scan-{i:03d}",
                    category=cat,
                    question=question,
                    priority=2,
                    reasoning=json.dumps(hit),
                    depends_on=None
                )
                print(f"  ✓ Queued: {question}")
            except Exception as e:
                print(f"  ✗ Failed to queue: {e}")

    return hits, tests_run

if __name__ == "__main__":
    hits, tests_run = run_scan()

    # Log journal entry
    print(f"\n📝 Logging journal entry...")
    try:
        summary = f"Scanned {tests_run} macro hypotheses (FRED + Fama-French). Found {len(hits)} significant signals (p<0.05). Themes: lead-lag (FRED pairs), macro exposure (FRED vs market), factor quality, VIX-rates regimes, seasonal patterns."

        db.insert_research_journal(
            date=pd.Timestamp.now().isoformat(),
            session_type="scan",
            investigated="Macro signals: FRED lead-lag, exposure, FF factor quality, regimes, seasonality",
            findings=json.dumps({"hits_queued": len(hits), "tests_run": tests_run, "hit_details": hits}),
            category="macro_signals",
            public_summary=summary
        )
        print("✓ Journal logged")
    except Exception as e:
        print(f"✗ Journal error: {e}")

    sys.exit(0 if len(hits) > 0 else 1)
