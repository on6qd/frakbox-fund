"""
pre_earnings_runup.py - Research tool for pre-earnings drift signal.

Hypothesis: Stocks with a history of consistent positive EPS surprises show
above-average abnormal returns in the 1-5 days BEFORE the next earnings date,
as institutional traders front-run the expected positive outcome.

Literature: Frazzini & Lamont (2007) "Dumb Money" documents earnings drift.
Barber et al (2013) "The behavior of individual investors" pre-earnings patterns.
"Pre-earnings announcement drift" (PEAD in reverse) discussed in Ball (1978).

Methodology:
1. Get earnings dates + EPS surprise data from yfinance
2. For each upcoming earnings date, look back at prior N quarters
3. Classify as "consistent beater" if beat EPS estimates 3+ consecutive times
4. Measure abnormal return (vs SPY) over [t-5, t-1], [t-3, t-1], [t-2, t-1]
   where t=0 is earnings date
5. Compare consistent beaters vs non-beaters vs missed (prior miss)

Returns structure designed for use with measure_event_impact().
"""

import sys
import os
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# S&P 500 large-cap universe (50 stocks for sufficient power, >$50B market cap typical)
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
    "UNH", "HD", "PG", "MA", "COST", "ABBV", "MRK", "CVX", "LLY", "BAC",
    "NFLX", "ADBE", "CRM", "AMD", "QCOM", "TXN", "AVGO", "ORCL", "IBM", "INTC",
    "GS", "MS", "C", "WFC", "AXP", "BLK", "USB", "TFC", "COF", "MMC",
    "CAT", "DE", "GE", "HON", "MMM", "LMT", "RTX", "NOC", "BA", "EMR",
]


def get_earnings_history(ticker: str, min_quarters: int = 8) -> pd.DataFrame | None:
    """
    Get earnings dates and EPS surprise history for a ticker.

    Returns DataFrame with columns: [earnings_date, surprise_pct, beat]
    sorted ascending by date. Returns None if insufficient data.
    """
    try:
        stock = yf.Ticker(ticker)
        ed = stock.earnings_dates
        if ed is None or len(ed) < min_quarters:
            return None

        df = ed.copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "earnings_date"
        df = df.reset_index()

        # Keep only past earnings with actual surprise data
        df = df[df["Reported EPS"].notna()].copy()
        df = df[df["Surprise(%)"].notna()].copy()

        if len(df) < min_quarters:
            return None

        df["surprise_pct"] = df["Surprise(%)"].astype(float)
        df["beat"] = df["surprise_pct"] > 0
        df = df.sort_values("earnings_date").reset_index(drop=True)
        df["earnings_date"] = pd.to_datetime(df["earnings_date"])

        return df[["earnings_date", "surprise_pct", "beat"]]

    except Exception as e:
        print(f"  {ticker}: earnings history failed: {e}")
        return None


def classify_prior_run(beats: list[bool], n: int = 3) -> str:
    """
    Classify the last N quarters before an earnings date.

    Returns: 'consistent_beater' (all N beat), 'consistent_misser' (all N miss),
             'mixed' (some of each)
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


def measure_pre_earnings_drift(
    ticker: str,
    earnings_df: pd.DataFrame,
    price_data: pd.DataFrame,
    spy_data: pd.DataFrame,
    days_before: int = 5,
    min_prior_quarters: int = 3,
) -> list[dict]:
    """
    For each earnings date, measure the abnormal return in the N days BEFORE earnings.

    Uses abnormal return = stock return - SPY return over [t - days_before, t - 1].

    Returns list of event dicts for each measurable earnings date.
    """
    events = []

    for idx in range(min_prior_quarters, len(earnings_df)):
        row = earnings_df.iloc[idx]
        ed = row["earnings_date"]

        # Prior earnings history for classification
        prior = earnings_df.iloc[:idx]
        classification = classify_prior_run(prior["beat"].tolist(), n=min_prior_quarters)
        if classification == "insufficient":
            continue

        # Find price window: t-days_before to t-1 (exclude earnings day itself)
        # Entry: close at t - days_before - 1 (we enter evening before window)
        # Exit: close at t - 1 (day before earnings)
        try:
            # Get prices in the window [entry_date, t-1]
            entry_date = ed - timedelta(days=days_before + 5)  # buffer for weekends
            exit_date = ed - timedelta(days=1)

            stock_slice = price_data[
                (price_data.index >= entry_date) & (price_data.index <= exit_date)
            ]["Close"]
            spy_slice = spy_data[
                (spy_data.index >= entry_date) & (spy_data.index <= exit_date)
            ]["Close"]

            if len(stock_slice) < 3 or len(spy_slice) < 3:
                continue

            # Take last days_before trading days before earnings
            stock_window = stock_slice.tail(days_before)
            spy_window = spy_slice.tail(len(stock_window))
            # Align on same dates
            common = stock_window.index.intersection(spy_window.index)
            if len(common) < 2:
                continue

            stock_ret = (stock_slice[common[-1]] / stock_slice[common[0]] - 1) * 100
            spy_ret = (spy_slice[common[-1]] / spy_slice[common[0]] - 1) * 100
            abnormal = stock_ret - spy_ret

            actual_days = len(common) - 1

            # Also compute 3d and 2d windows if we have enough data
            windows = {}
            for w in [2, 3, 5]:
                w_dates = common[-min(w + 1, len(common)):]
                if len(w_dates) >= 2:
                    sr = (stock_slice[w_dates[-1]] / stock_slice[w_dates[0]] - 1) * 100
                    br = (spy_slice[w_dates[-1]] / spy_slice[w_dates[0]] - 1) * 100
                    windows[f"abnormal_{w}d"] = round(sr - br, 4)
                    windows[f"raw_{w}d"] = round(sr, 4)

            events.append({
                "ticker": ticker,
                "earnings_date": ed.strftime("%Y-%m-%d"),
                "classification": classification,
                "prior_beat_rate": prior["beat"].mean(),
                "n_prior_quarters": len(prior),
                "actual_trading_days": actual_days,
                "abnormal_return_pct": round(abnormal, 4),
                "raw_return_pct": round(stock_ret, 4),
                "spy_return_pct": round(spy_ret, 4),
                **windows,
            })

        except Exception:
            continue

    return events


def run_pre_earnings_analysis(
    tickers: list[str] = None,
    start_date: str = "2021-01-01",
    end_date: str = "2026-01-01",
    days_before: int = 5,
    min_prior_quarters: int = 3,
    verbose: bool = True,
) -> dict:
    """
    Main analysis function. Returns dict with results and stats.
    """
    if tickers is None:
        tickers = UNIVERSE

    if verbose:
        print(f"Pre-Earnings Run-Up Analysis")
        print(f"Universe: {len(tickers)} tickers")
        print(f"Period: {start_date} to {end_date}")
        print(f"Measuring {days_before}d window before earnings")
        print(f"Classification: consistent_beater = beat last {min_prior_quarters} quarters")
        print()

    # Download SPY once
    spy_data = yf.download("SPY", start=start_date, end=end_date, auto_adjust=True, progress=False)
    if spy_data.empty:
        print("ERROR: Could not download SPY data")
        return {}

    all_events = []
    succeeded = 0
    failed = 0

    for ticker in tickers:
        if verbose:
            print(f"Processing {ticker}...", end=" ", flush=True)

        earnings_df = get_earnings_history(ticker, min_quarters=min_prior_quarters + 2)
        if earnings_df is None:
            if verbose:
                print("no earnings data")
            failed += 1
            continue

        # Filter earnings dates to our analysis window
        earnings_df = earnings_df[
            (earnings_df["earnings_date"] >= pd.Timestamp(start_date)) &
            (earnings_df["earnings_date"] <= pd.Timestamp(end_date))
        ]

        if len(earnings_df) < min_prior_quarters + 1:
            if verbose:
                print(f"only {len(earnings_df)} dates in window")
            failed += 1
            continue

        # Download price data
        price_data = yf.download(
            ticker, start=start_date, end=end_date, auto_adjust=True, progress=False
        )
        if price_data.empty or len(price_data) < 100:
            if verbose:
                print("no price data")
            failed += 1
            continue

        # Handle MultiIndex columns from yfinance
        if isinstance(price_data.columns, pd.MultiIndex):
            price_data.columns = price_data.columns.get_level_values(0)
        if isinstance(spy_data.columns, pd.MultiIndex):
            spy_data.columns = spy_data.columns.get_level_values(0)

        events = measure_pre_earnings_drift(
            ticker, earnings_df, price_data, spy_data,
            days_before=days_before, min_prior_quarters=min_prior_quarters
        )

        all_events.extend(events)
        if verbose:
            n_beaters = sum(1 for e in events if e["classification"] == "consistent_beater")
            print(f"{len(events)} events ({n_beaters} beater instances)")
        succeeded += 1

    if verbose:
        print(f"\nData collection: {succeeded} tickers succeeded, {failed} failed")
        print(f"Total events: {len(all_events)}")

    if not all_events:
        return {"error": "No events collected"}

    df = pd.DataFrame(all_events)

    # Separate by classification
    beaters = df[df["classification"] == "consistent_beater"]
    missers = df[df["classification"] == "consistent_misser"]
    mixed = df[df["classification"] == "mixed"]

    results = {}

    for label, subset in [("consistent_beater", beaters), ("consistent_misser", missers), ("mixed", mixed)]:
        if len(subset) < 5:
            results[label] = {"n": len(subset), "note": "insufficient data"}
            continue

        # Use primary window (5d abnormal)
        returns = subset["abnormal_5d"].dropna() if "abnormal_5d" in subset.columns else subset["abnormal_return_pct"].dropna()

        from scipy import stats
        t_stat, p_val = stats.ttest_1samp(returns, 0)
        pos_rate = (returns > 0).mean()
        _, wilcoxon_p = stats.wilcoxon(returns) if len(returns) >= 10 else (None, None)

        results[label] = {
            "n": len(returns),
            "avg_abnormal_pct": round(returns.mean(), 4),
            "median_abnormal_pct": round(returns.median(), 4),
            "stdev": round(returns.std(), 4),
            "positive_rate": round(pos_rate, 4),
            "t_stat": round(t_stat, 3),
            "p_value": round(p_val, 4),
            "wilcoxon_p": round(wilcoxon_p, 4) if wilcoxon_p is not None else None,
        }

        # Also compute 2d and 3d windows
        for w in [2, 3]:
            col = f"abnormal_{w}d"
            if col in subset.columns:
                r = subset[col].dropna()
                if len(r) >= 10:
                    _, pp = stats.ttest_1samp(r, 0)
                    results[label][f"avg_abnormal_{w}d"] = round(r.mean(), 4)
                    results[label][f"p_value_{w}d"] = round(pp, 4)

    if verbose:
        print("\n" + "="*60)
        print("PRE-EARNINGS DRIFT RESULTS (abnormal return vs SPY)")
        print("="*60)
        for label, res in results.items():
            if "note" in res:
                print(f"\n{label}: n={res['n']} ({res['note']})")
            else:
                print(f"\n{label} (n={res['n']}):")
                print(f"  5d avg abnormal: {res['avg_abnormal_pct']:+.2f}%  "
                      f"(p={res['p_value']:.4f}, pos_rate={res['positive_rate']:.1%})")
                if "avg_abnormal_3d" in res:
                    print(f"  3d avg abnormal: {res['avg_abnormal_3d']:+.2f}%  "
                          f"(p={res['p_value_3d']:.4f})")
                if "avg_abnormal_2d" in res:
                    print(f"  2d avg abnormal: {res['avg_abnormal_2d']:+.2f}%  "
                          f"(p={res['p_value_2d']:.4f})")

    return {
        "results": results,
        "all_events": df.to_dict("records"),
        "n_total": len(df),
        "tickers_succeeded": succeeded,
        "tickers_failed": failed,
    }


if __name__ == "__main__":
    output = run_pre_earnings_analysis(verbose=True)
