"""
FOMC Cycle (biweekly) anomaly test — Cieslak, Morse & Vissing-Jorgensen (2019).

DISTINCT from the pre-FOMC announcement drift (Lucca-Moench, already a dead end here).
Claim: over the ~6-7 week FOMC cycle, equity EXCESS returns concentrate in EVEN weeks
(weeks 0, 2, 4, 6 relative to the scheduled FOMC announcement) and are ~zero/negative
in ODD weeks (1, 3, 5). The original paper used 1994-2013; our window (2015-2026) is
essentially a clean post-publication out-of-sample test of persistence.

Cycle-time convention (Cieslak et al.):
  week w covers cycle days {5w-1, 5w, 5w+1, 5w+2, 5w+3}, so:
    week 0 = days {-1,0,1,2,3}   (day -1 = the trading day BEFORE the announcement)
    week 1 = days {4..8}, week 2 = {9..13}, ...
  cycle day t = trading days since the most recent SCHEDULED FOMC announcement (t>=0),
  EXCEPT the single trading day immediately before the NEXT meeting is assigned t=-1
  (it belongs to week 0 of the upcoming cycle).
  week(t) = floor((t+1)/5); even-week indicator = week % 2 == 0.

Uses only free data: SPY daily closes (yfinance) + scheduled FOMC dates.
"""
import sys, json
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from yfinance_utils import get_close_prices  # noqa

# Scheduled FOMC announcement dates. Emergency/unscheduled 2020 meetings (Mar 3, Mar 15)
# are EXCLUDED — they distort cycle timing and are not part of the regular cadence.
FOMC_DATES = [
    "2015-01-28","2015-03-18","2015-04-29","2015-06-17","2015-07-29","2015-09-17","2015-10-28","2015-12-16",
    "2016-01-27","2016-03-16","2016-04-27","2016-06-15","2016-07-27","2016-09-21","2016-11-02","2016-12-14",
    "2017-02-01","2017-03-15","2017-05-03","2017-06-14","2017-07-26","2017-09-20","2017-11-01","2017-12-13",
    "2018-01-31","2018-03-21","2018-05-02","2018-06-13","2018-08-01","2018-09-26","2018-11-08","2018-12-19",
    "2019-01-30","2019-03-20","2019-05-01","2019-06-19","2019-07-31","2019-09-18","2019-10-30","2019-12-11",
    "2020-01-29","2020-04-29","2020-06-10","2020-07-29","2020-09-16","2020-11-05","2020-12-16",
    "2021-01-27","2021-03-17","2021-04-28","2021-06-16","2021-07-28","2021-09-22","2021-11-03","2021-12-15",
    "2022-01-26","2022-03-16","2022-05-04","2022-06-15","2022-07-27","2022-09-21","2022-11-02","2022-12-14",
    "2023-02-01","2023-03-22","2023-05-03","2023-06-14","2023-07-26","2023-09-20","2023-11-01","2023-12-13",
    "2024-01-31","2024-03-20","2024-05-01","2024-06-12","2024-07-31","2024-09-18","2024-11-07","2024-12-18",
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18","2025-07-30","2025-09-17","2025-10-29","2025-12-17",
    "2026-01-28","2026-03-18","2026-04-29",
]


def assign_cycle(idx, fomc_days):
    """For each trading-day index position, return cycle day t and week."""
    fomc_pos = []
    for d in fomc_days:
        # snap each FOMC date to the trading day on/after it (announcement ~2pm = that day's close move)
        loc = idx.searchsorted(d)
        if loc < len(idx):
            fomc_pos.append(loc)
    fomc_pos = np.array(sorted(set(fomc_pos)))

    n = len(idx)
    t_arr = np.full(n, np.nan)
    for i in range(n):
        # most recent meeting at or before i
        prev = fomc_pos[fomc_pos <= i]
        nxt = fomc_pos[fomc_pos > i]
        if len(nxt) and (nxt[0] - i) == 1:
            t_arr[i] = -1  # day before next meeting -> week 0 of next cycle
        elif len(prev):
            t_arr[i] = i - prev[-1]
        else:
            t_arr[i] = np.nan  # before first meeting in window
    week = np.floor((t_arr + 1) / 5.0)
    return t_arr, week


def summarize(rets, week, t_arr, label):
    mask = ~np.isnan(week)
    r = rets[mask]; w = week[mask]; t = t_arr[mask]
    even = r[(w % 2 == 0) & (w >= 0) & (w <= 6)]
    odd = r[(w % 2 == 1) & (w >= 1) & (w <= 5)]
    tt, p = stats.ttest_ind(even, odd, equal_var=False)
    out = {
        "label": label,
        "n_days": int(len(r)),
        "even_n": int(len(even)),
        "odd_n": int(len(odd)),
        "even_mean_daily_pct": round(float(even.mean()) * 100, 4),
        "odd_mean_daily_pct": round(float(odd.mean()) * 100, 4),
        "diff_pct": round(float(even.mean() - odd.mean()) * 100, 4),
        "even_annualized_pct": round(float(even.mean()) * 252 * 100, 2),
        "odd_annualized_pct": round(float(odd.mean()) * 252 * 100, 2),
        "welch_t": round(float(tt), 3),
        "p_value": round(float(p), 4),
    }
    # per-week means for transparency
    pw = {}
    for wk in range(0, 7):
        rr = r[w == wk]
        if len(rr):
            pw[f"week{wk}_{'even' if wk%2==0 else 'odd'}"] = {
                "mean_daily_pct": round(float(rr.mean()) * 100, 4), "n": int(len(rr))}
    out["per_week"] = pw
    return out


def main():
    fomc = pd.to_datetime(FOMC_DATES)
    px = get_close_prices("SPY", start="2014-12-01", end="2026-06-13")
    if px is None or len(px) == 0:
        print(json.dumps({"error": "no SPY data"})); return
    if isinstance(px, pd.DataFrame):
        px = px.iloc[:, 0]
    px = px.dropna()
    rets = px.pct_change().dropna()
    idx = rets.index
    t_arr, week = assign_cycle(idx, fomc)
    rets_v = rets.values

    full = summarize(rets_v, week, t_arr, "FULL 2015-2026")
    # IS / OOS split (both post original-paper sample)
    is_mask = idx < pd.Timestamp("2021-01-01")
    oos_mask = ~is_mask
    is_res = summarize(rets_v[is_mask], week[is_mask], t_arr[is_mask], "IS 2015-2020")
    oos_res = summarize(rets_v[oos_mask], week[oos_mask], t_arr[oos_mask], "OOS 2021-2026")

    result = {"hypothesis": "FOMC biweekly cycle (Cieslak-Morse-Vissing-Jorgensen 2019): SPY even-week > odd-week",
              "full": full, "is": is_res, "oos": oos_res}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
