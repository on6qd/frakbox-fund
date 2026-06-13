#!/usr/bin/env python3
"""
oil_airline_threshold.py - Test: when oil has a large daily move,
what happens to airlines on the same day and subsequent days?

This is an event study where "events" are days CL=F returns exceed a threshold.
"""
import sys, json, warnings
import os
import numpy as np
import pandas as pd
from datetime import datetime

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import get_close_prices


def run_oil_airline_threshold(
    oil_threshold_pct: float = 3.0,
    airlines: list = None,
    benchmark: str = "SPY",
    start: str = "2020-01-01",
    oos_start: str = "2024-01-01",
    horizons: list = None,
):
    """
    Identify days where |CL=F daily return| > threshold, then measure
    airline abnormal returns on same day and subsequent days.
    """
    if airlines is None:
        airlines = ["AAL", "DAL", "UAL", "LUV"]
    if horizons is None:
        horizons = [0, 1, 2, 3, 5]  # 0=same day, 1=next day, etc.

    tickers = airlines + [benchmark, "CL=F"]
    end = datetime.now().strftime("%Y-%m-%d")
    closes = get_close_prices(tickers, start=start, end=end)
    if closes is None or closes.empty:
        return {"error": "Failed to fetch price data"}

    # Daily returns
    rets = closes.pct_change().dropna()

    # Identify large oil move days
    oil_up = rets[rets["CL=F"] > oil_threshold_pct / 100].index
    oil_down = rets[rets["CL=F"] < -oil_threshold_pct / 100].index

    results = {}
    for direction, event_dates in [("oil_spike_up", oil_up), ("oil_crash_down", oil_down)]:
        # Split into IS/OOS
        oos_dt = pd.Timestamp(oos_start)
        is_dates = [d for d in event_dates if d < oos_dt]
        oos_dates = [d for d in event_dates if d >= oos_dt]

        dir_results = {
            "n_events_is": len(is_dates),
            "n_events_oos": len(oos_dates),
            "n_events_total": len(event_dates),
            "airlines": {},
        }

        for airline in airlines:
            airline_data = {"horizons": {}}
            for h in horizons:
                # For each event date, get the return from event date to event+h days
                same_day_rets = []
                oos_rets = []
                for d in event_dates:
                    idx = rets.index.get_loc(d)
                    if idx + h >= len(rets):
                        continue
                    if h == 0:
                        # Same-day: airline return on the oil event day
                        ar = rets[airline].iloc[idx] - rets[benchmark].iloc[idx]
                    else:
                        # Cumulative return from day after event to day+h
                        cum_airline = (1 + rets[airline].iloc[idx + 1 : idx + h + 1]).prod() - 1
                        cum_bench = (1 + rets[benchmark].iloc[idx + 1 : idx + h + 1]).prod() - 1
                        ar = cum_airline - cum_bench

                    if d < oos_dt:
                        same_day_rets.append(ar)
                    else:
                        oos_rets.append(ar)

                all_rets = same_day_rets + oos_rets
                horizon_data = {
                    "is_mean_abnormal_pct": round(np.mean(same_day_rets) * 100, 2) if same_day_rets else None,
                    "is_n": len(same_day_rets),
                    "oos_mean_abnormal_pct": round(np.mean(oos_rets) * 100, 2) if oos_rets else None,
                    "oos_n": len(oos_rets),
                    "total_mean_abnormal_pct": round(np.mean(all_rets) * 100, 2) if all_rets else None,
                    "total_n": len(all_rets),
                }

                # Direction consistency
                if all_rets:
                    if direction == "oil_spike_up":
                        # Expect airlines to go DOWN (negative abnormal)
                        horizon_data["direction_correct_pct"] = round(
                            sum(1 for r in all_rets if r < 0) / len(all_rets) * 100, 1
                        )
                    else:
                        # Oil crash -> airlines should go UP (positive abnormal)
                        horizon_data["direction_correct_pct"] = round(
                            sum(1 for r in all_rets if r > 0) / len(all_rets) * 100, 1
                        )

                # t-test if enough data
                if len(all_rets) >= 5:
                    from scipy import stats
                    t_stat, p_val = stats.ttest_1samp(all_rets, 0)
                    horizon_data["t_stat"] = round(t_stat, 2)
                    horizon_data["p_value"] = round(p_val, 4)

                airline_data["horizons"][f"day_{h}"] = horizon_data

            dir_results["airlines"][airline] = airline_data

        # Average across all airlines (basket)
        basket_horizons = {}
        for h in horizons:
            hkey = f"day_{h}"
            basket_rets_is = []
            basket_rets_oos = []
            for airline in airlines:
                ah = dir_results["airlines"][airline]["horizons"][hkey]
                # Reconstruct individual event returns isn't easy from means,
                # so compute basket from first principles
            # Instead, compute basket return directly
            for d in event_dates:
                idx = rets.index.get_loc(d)
                if idx + h >= len(rets):
                    continue
                if h == 0:
                    basket_r = np.mean([rets[a].iloc[idx] for a in airlines]) - rets[benchmark].iloc[idx]
                else:
                    cum_airlines = np.mean([(1 + rets[a].iloc[idx+1:idx+h+1]).prod() - 1 for a in airlines])
                    cum_bench = (1 + rets[benchmark].iloc[idx+1:idx+h+1]).prod() - 1
                    basket_r = cum_airlines - cum_bench

                if d < oos_dt:
                    basket_rets_is.append(basket_r)
                else:
                    basket_rets_oos.append(basket_r)

            all_basket = basket_rets_is + basket_rets_oos
            basket_horizons[hkey] = {
                "is_mean_pct": round(np.mean(basket_rets_is) * 100, 2) if basket_rets_is else None,
                "oos_mean_pct": round(np.mean(basket_rets_oos) * 100, 2) if basket_rets_oos else None,
                "total_mean_pct": round(np.mean(all_basket) * 100, 2) if all_basket else None,
                "n_total": len(all_basket),
            }
            if len(all_basket) >= 5:
                from scipy import stats
                t_stat, p_val = stats.ttest_1samp(all_basket, 0)
                basket_horizons[hkey]["t_stat"] = round(t_stat, 2)
                basket_horizons[hkey]["p_value"] = round(p_val, 4)

        dir_results["basket"] = basket_horizons

        # Oil event details (dates and magnitudes)
        oil_events = []
        for d in event_dates:
            oil_events.append({
                "date": d.strftime("%Y-%m-%d"),
                "oil_return_pct": round(rets["CL=F"].loc[d] * 100, 2),
                "period": "IS" if d < oos_dt else "OOS",
            })
        dir_results["events"] = oil_events

        results[direction] = dir_results

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=3.0)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--oos-start", default="2024-01-01")
    args = parser.parse_args()

    r = run_oil_airline_threshold(
        oil_threshold_pct=args.threshold,
        start=args.start,
        oos_start=args.oos_start,
    )
    print(json.dumps(r, indent=2))
