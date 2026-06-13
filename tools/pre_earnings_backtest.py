"""
pre_earnings_backtest.py - Clean backtest of pre-earnings drift signal.

Hypothesis: Stocks with 3+ consecutive EPS beats show abnormal positive returns
in the 5 trading days BEFORE the next earnings date.

Method:
  1. Collect earnings dates + EPS surprise history from yfinance for large-cap universe.
  2. For each earnings date (2020-2024), classify whether the stock was a "consistent
     beater" (beat EPS in the 3 prior consecutive quarters).
  3. For beater events, the "event date" passed to measure_event_impact() is set to
     T-5 (5 trading-days before earnings). This makes the [0,5d] window in
     measure_event_impact() equal to the [T-5, T-1] pre-earnings window.
  4. Also measure a "control" group: same stocks, same dates, but classified as
     "mixed" (not consistent beaters) — to test whether the effect is specific to
     consistent beaters.
  5. Report all standard stats including passes_multiple_testing, p-values, N.

Usage:
    python tools/pre_earnings_backtest.py

Outputs to stdout.  Also returns result dict for programmatic use.
"""

import sys
import os
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_data
from tools.yfinance_utils import safe_download

import yfinance as yf  # only used for yf.Ticker().earnings_dates

# ------------------------------------------------------------------
# Universe: large-cap S&P 500 stocks, 30 tickers
# Chosen for data completeness and EPS reliability
# ------------------------------------------------------------------
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ", "V", "UNH",
    "HD", "PG", "MA", "COST", "ABBV", "MRK", "CVX", "LLY", "BAC", "NFLX",
    "ADBE", "CRM", "QCOM", "TXN", "AVGO", "GS", "MS", "CAT", "HON", "MMM",
]

START_DATE = "2019-01-01"  # need prior history for classification
ANALYSIS_START = "2020-01-01"  # events measured from here
ANALYSIS_END = "2024-12-31"


def get_earnings_history(ticker: str) -> pd.DataFrame | None:
    """
    Fetch earnings_dates from yfinance for a ticker.
    Returns DataFrame with [earnings_date, surprise_pct, beat] sorted ascending.
    Returns None if insufficient data (< 8 quarters with reported EPS).
    """
    try:
        stock = yf.Ticker(ticker)
        ed = stock.earnings_dates
        if ed is None or len(ed) < 8:
            return None

        df = ed.copy()
        # Normalize index to tz-naive datetime
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "earnings_date"
        df = df.reset_index()

        # Keep only past earnings with both Reported EPS and Surprise(%)
        df = df[df["Reported EPS"].notna()].copy()
        df = df[df["Surprise(%)"].notna()].copy()

        if len(df) < 8:
            return None

        df["surprise_pct"] = df["Surprise(%)"].astype(float)
        df["beat"] = df["surprise_pct"] > 0
        df = df.sort_values("earnings_date").reset_index(drop=True)
        df["earnings_date"] = pd.to_datetime(df["earnings_date"])

        return df[["earnings_date", "surprise_pct", "beat"]]

    except Exception as e:
        print(f"  {ticker}: earnings history failed: {e}")
        return None


def classify_prior_n(beats: list, n: int = 3) -> str:
    """
    Classify the last N quarters before an earnings event.
    'consistent_beater': all N were beats
    'consistent_misser': all N were misses
    'mixed': some of each
    'insufficient': fewer than N prior quarters
    """
    if len(beats) < n:
        return "insufficient"
    last_n = beats[-n:]
    if all(last_n):
        return "consistent_beater"
    elif not any(last_n):
        return "consistent_misser"
    else:
        return "mixed"


def offset_date_by_trading_days(reference_date: pd.Timestamp, n_days: int,
                                 trading_calendar: pd.DatetimeIndex) -> pd.Timestamp | None:
    """
    Go back n_days trading days before reference_date.
    Returns None if not enough trading days exist.
    """
    prior_days = trading_calendar[trading_calendar < reference_date]
    if len(prior_days) < n_days:
        return None
    return prior_days[-n_days]


def collect_events(
    tickers: list[str],
    n_consecutive: int = 3,
    days_before: int = 5,
    verbose: bool = True,
) -> tuple[list[dict], list[dict]]:
    """
    For each ticker, scan all earnings dates in [ANALYSIS_START, ANALYSIS_END].
    Classify prior quarters. Return two lists:
      - beater_events: {symbol, date} dicts for consistent_beater instances
      - control_events: {symbol, date} dicts for mixed instances (control group)

    The "date" in each event is shifted back `days_before` trading days from the
    actual earnings date. This lets measure_event_impact() measure the [0, N] window
    which corresponds to [T-5, T-1] relative to earnings.
    """
    # Download SPY once to get trading calendar
    try:
        spy_prices = safe_download("SPY", start=START_DATE, end=ANALYSIS_END)
        trading_calendar = spy_prices.index
    except Exception as e:
        print(f"ERROR: Could not load SPY trading calendar: {e}")
        return [], []

    beater_events = []
    control_events = []
    failed = 0
    succeeded = 0

    for ticker in tickers:
        if verbose:
            print(f"  {ticker}...", end=" ", flush=True)

        earnings_df = get_earnings_history(ticker)
        if earnings_df is None:
            if verbose:
                print("no earnings data")
            failed += 1
            continue

        # Filter to analysis window (keep prior history for classification)
        full_df = earnings_df.copy()
        window_df = earnings_df[
            (earnings_df["earnings_date"] >= pd.Timestamp(ANALYSIS_START)) &
            (earnings_df["earnings_date"] <= pd.Timestamp(ANALYSIS_END))
        ].copy()

        if len(window_df) < 4:
            if verbose:
                print(f"only {len(window_df)} dates in window")
            failed += 1
            continue

        n_beater = 0
        n_control = 0

        for _, row in window_df.iterrows():
            ed = row["earnings_date"]

            # All prior quarters from the full history before this date
            prior = full_df[full_df["earnings_date"] < ed]
            classification = classify_prior_n(prior["beat"].tolist(), n=n_consecutive)

            if classification == "insufficient":
                continue

            # Shift event date back by days_before trading days
            shifted_date = offset_date_by_trading_days(ed, days_before, trading_calendar)
            if shifted_date is None:
                continue

            event = {
                "symbol": ticker,
                "date": shifted_date.strftime("%Y-%m-%d"),
                "earnings_date": ed.strftime("%Y-%m-%d"),
                "classification": classification,
            }

            if classification == "consistent_beater":
                beater_events.append(event)
                n_beater += 1
            elif classification == "mixed":
                control_events.append(event)
                n_control += 1
            # Skip consistent_missers from control (keep control clean)

        if verbose:
            print(f"{n_beater} beater | {n_control} control")
        succeeded += 1

    if verbose:
        print(f"\nData collection: {succeeded} succeeded, {failed} failed")
        print(f"Beater events: {len(beater_events)}  |  Control events: {len(control_events)}")

    return beater_events, control_events


def run_analysis(verbose: bool = True) -> dict:
    """
    Main entry point. Collect events, measure impact, report results.
    """
    if verbose:
        print("=" * 65)
        print("PRE-EARNINGS DRIFT BACKTEST")
        print("=" * 65)
        print(f"Universe: {len(UNIVERSE)} large-cap tickers")
        print(f"Analysis period: {ANALYSIS_START} to {ANALYSIS_END}")
        print(f"Signal: 3+ consecutive EPS beats before next earnings")
        print(f"Window: 5 trading days before earnings date")
        print(f"Benchmark: SPY (abnormal return = stock - SPY)")
        print()

    beater_events, control_events = collect_events(
        UNIVERSE, n_consecutive=3, days_before=5, verbose=verbose
    )

    if len(beater_events) < 10:
        print(f"ERROR: Only {len(beater_events)} beater events — insufficient for analysis")
        return {"error": "insufficient_beater_events", "n": len(beater_events)}

    if verbose:
        print(f"\nMeasuring abnormal returns for {len(beater_events)} beater events...")

    # --- Measure beater group ---
    beater_result = market_data.measure_event_impact(
        event_dates=beater_events,
        benchmark="SPY",
        estimate_costs=False,
    )

    # --- Measure control group (if large enough) ---
    control_result = None
    if len(control_events) >= 10:
        if verbose:
            print(f"Measuring abnormal returns for {len(control_events)} control events...")
        control_result = market_data.measure_event_impact(
            event_dates=control_events,
            benchmark="SPY",
            estimate_costs=False,
        )

    # --- Collect results ---
    if verbose:
        _print_results(beater_result, control_result, len(beater_events), len(control_events))

    return {
        "beater_events": beater_events,
        "control_events": control_events,
        "beater_result": beater_result,
        "control_result": control_result,
    }


def _print_results(beater: dict, control: dict | None, n_beater: int, n_control: int):
    """Print formatted results summary."""
    print("\n" + "=" * 65)
    print("RESULTS: CONSISTENT BEATERS (3+ consecutive EPS beats)")
    print("=" * 65)
    print(f"N events: {beater.get('events_measured', n_beater)}")

    if beater.get("data_quality_warning"):
        print(f"DATA WARNING: {beater['data_quality_warning']}")

    for horizon in ["1d", "3d", "5d"]:
        avg_key = f"avg_abnormal_{horizon}"
        pos_key = f"positive_rate_abnormal_{horizon}"
        p_key = f"wilcoxon_p_abnormal_{horizon}"
        if avg_key in beater:
            avg = beater[avg_key]
            pos = beater.get(pos_key, float("nan"))
            p = beater.get(p_key, float("nan"))
            print(f"  {horizon} avg abnormal: {avg:+.3f}%  "
                  f"positive_rate={pos:.1%}  wilcoxon_p={p:.4f}")

    print(f"\n  passes_multiple_testing: {beater.get('passes_multiple_testing', 'N/A')}")

    # Bootstrap CI at 5d
    bci = beater.get("bootstrap_ci_abnormal_5d")
    if bci:
        print(f"  5d bootstrap 95% CI: [{bci['ci_lower']:+.3f}%, {bci['ci_upper']:+.3f}%]  "
              f"excludes_zero={bci['ci_excludes_zero']}")

    if control:
        print(f"\n{'=' * 65}")
        print(f"RESULTS: CONTROL GROUP (mixed EPS history)")
        print(f"{'=' * 65}")
        print(f"N events: {control.get('events_measured', n_control)}")
        for horizon in ["1d", "3d", "5d"]:
            avg_key = f"avg_abnormal_{horizon}"
            pos_key = f"positive_rate_abnormal_{horizon}"
            p_key = f"wilcoxon_p_abnormal_{horizon}"
            if avg_key in control:
                avg = control[avg_key]
                pos = control.get(pos_key, float("nan"))
                p = control.get(p_key, float("nan"))
                print(f"  {horizon} avg abnormal: {avg:+.3f}%  "
                      f"positive_rate={pos:.1%}  wilcoxon_p={p:.4f}")
        print(f"\n  passes_multiple_testing: {control.get('passes_multiple_testing', 'N/A')}")

    # Differential signal: beater minus control at 5d
    if control and "avg_abnormal_5d" in beater and "avg_abnormal_5d" in control:
        diff = beater["avg_abnormal_5d"] - control["avg_abnormal_5d"]
        print(f"\n{'=' * 65}")
        print(f"DIFFERENTIAL (beater minus control, 5d): {diff:+.3f}%")
        print(f"{'=' * 65}")

    # Individual event breakdown
    if "individual_impacts" in beater:
        indiv = beater["individual_impacts"]
        abnormals_5d = [e.get("abnormal_5d") for e in indiv if e.get("abnormal_5d") is not None]
        if abnormals_5d:
            print(f"\nPer-event 5d abnormal returns (beaters): N={len(abnormals_5d)}")
            print(f"  min={min(abnormals_5d):+.2f}%  max={max(abnormals_5d):+.2f}%  "
                  f"stdev={float(pd.Series(abnormals_5d).std()):.2f}%")


if __name__ == "__main__":
    result = run_analysis(verbose=True)
