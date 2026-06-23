"""Build the canonical, reproducible S&P 500 quarterly-addition event dataset.

Re-derives the pre5/post5 abnormal-return dataset that was lost to /tmp
(/tmp/sp500_events.json) and used in the 2026-06-11 / 2026-06-22 analyses of
whether pre-announce run-up predicts a post-announce fade (it does NOT; the
fade guardrail was falsified and removed).

Source of truth: Wikipedia "List of S&P 500 companies" -> "Selected changes"
table. We keep only QUARTERLY-rebalance additions (reason == "Market
capitalization change", effective in Mar/Jun/Sep/Dec) since these are the only
additions the fund trades (off-cycle / M&A-driven adds show no premium).

Announcement date is not in Wikipedia, so we detect it as the single largest
positive abnormal-return day vs SPY in the [effective-15, effective-3] trading
window (the S&P inclusion announcement reliably causes a one-day pop). pre5 is
the abnormal run-up over the 5 trading days ending ON the announcement day;
post5 / post1 are the abnormal returns over the 5 / 1 trading days AFTER it.

Writes data/sp500_addition_events.json (committed) and prints the correlation
test. Re-run with --refresh to re-pull Wikipedia; otherwise it reuses the
cached effective-date list embedded in the committed JSON.

Usage:
    python3 tools/sp500_addition_dataset.py            # rebuild from cache+prices
    python3 tools/sp500_addition_dataset.py --refresh  # re-pull Wikipedia first
"""
import argparse
import io
import json
import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import get_close_prices

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "sp500_addition_events.json")
QUARTER_MONTHS = (3, 6, 9, 12)


def pull_quarterly_additions():
    """Return list of {symbol, effective_date} quarterly-rebalance additions since 2020."""
    html = requests.get(WIKI_URL, headers={"User-Agent": "frakbox-research/1.0"},
                        timeout=30).text
    tables = pd.read_html(io.StringIO(html), header=0)
    ch = tables[1]
    ch.columns = ["eff_date", "add_tkr", "add_name", "rem_tkr", "rem_name", "reason"]
    ch["dt"] = pd.to_datetime(ch["eff_date"], format="%B %d, %Y", errors="coerce")
    ch = ch.dropna(subset=["dt", "add_tkr"])
    ch = ch[ch["dt"] >= "2020-01-01"]
    # Quarterly rebalance = market-cap-driven add effective in Mar/Jun/Sep/Dec.
    ch = ch[ch["dt"].dt.month.isin(QUARTER_MONTHS)]
    ch = ch[ch["reason"].str.contains("Market capitalization change", na=False)]
    out = [{"symbol": str(r["add_tkr"]).strip(), "effective_date": r["dt"].strftime("%Y-%m-%d")}
           for _, r in ch.sort_values("dt").iterrows()]
    return out


def _series(px):
    if px is None:
        return None
    if hasattr(px, "columns"):
        px = px.iloc[:, 0]
    return px


def measure(symbol, effective_date):
    """Effective-date-anchored abnormal returns vs SPY (compounded, in %).

    The effective date E (authoritative, from Wikipedia) is mechanical, never a
    news day, so it is a clean anchor. Windows:
      capture10  = open[E-10] -> close[E]    front-running run-up you'd capture
                                             entering near the announcement and
                                             exiting at the effective close
      runup5     = close[E-6] -> close[E-1]  the 5d pre-effective run-up
      post1      = close[E]   -> close[E+1]  first day of reversal
      post5      = close[E]   -> close[E+5]  post-effective reversal window
    All are abnormal (asset minus SPY) compounded returns in %.
    """
    eff = pd.Timestamp(effective_date)
    start = (eff - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    end = (eff + pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    try:
        sym = _series(get_close_prices(symbol, start, end))
        spy = _series(get_close_prices("SPY", start, end))
    except Exception as e:
        return {"status": f"fetch_err:{e}"}
    if sym is None or spy is None:
        return {"status": "no_prices"}
    df = pd.DataFrame({"sym": sym, "spy": spy}).dropna()
    if len(df) < 25:
        return {"status": "insufficient"}
    idx = df.index
    on_or_before = idx[idx <= eff]
    if len(on_or_before) == 0:
        return {"status": "no_eff_bar"}
    e_loc = idx.get_loc(on_or_before[-1])
    if e_loc < 16 or e_loc + 6 >= len(df):
        return {"status": "window_oob"}

    def abn(a, b):
        """compounded abnormal return close[a]->close[b] minus SPY, in %."""
        s = df["sym"].iloc[b] / df["sym"].iloc[a] - 1
        m = df["spy"].iloc[b] / df["spy"].iloc[a] - 1
        return round(float((s - m) * 100), 2)

    return {
        "status": "ok",
        "capture15": abn(e_loc - 15, e_loc),   # brackets the announcement-day pop
        "capture10": abn(e_loc - 10, e_loc),
        "runup5": abn(e_loc - 6, e_loc - 1),
        "post1": abn(e_loc, e_loc + 1),
        "post5": abn(e_loc, e_loc + 5),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-pull Wikipedia effective-date list")
    args = ap.parse_args()

    if args.refresh or not os.path.exists(OUT_PATH):
        base = pull_quarterly_additions()
        print(f"Pulled {len(base)} quarterly additions from Wikipedia")
    else:
        prev = json.load(open(OUT_PATH))
        base = [{"symbol": e["symbol"], "effective_date": e["effective_date"]}
                for e in prev["events"]]
        print(f"Reusing {len(base)} cached events (use --refresh to re-pull Wikipedia)")

    events = []
    for e in base:
        m = measure(e["symbol"], e["effective_date"])
        row = {**e, **m}
        events.append(row)
        print(f"{e['symbol']:6} eff={e['effective_date']} "
              f"capture10={m.get('capture10')} runup5={m.get('runup5')} "
              f"post1={m.get('post1')} post5={m.get('post5')} [{m['status']}]")

    df = pd.DataFrame([e for e in events if e.get("status") == "ok"])
    valid = df.dropna(subset=["capture10", "post5"])
    n = len(valid)
    stats = {}
    if n >= 5:
        for col in ["capture15", "capture10", "runup5", "post1", "post5"]:
            stats[col + "_mean"] = round(float(valid[col].mean()), 2)
            stats[col + "_pos_rate"] = round(float((valid[col] > 0).mean()), 3)
        pearson = round(float(valid["capture10"].corr(valid["post5"])), 3)
        spear = round(float(valid["capture10"].corr(valid["post5"], method="spearman")), 3)
        print(f"\n=== N valid = {n} ===")
        print(f"capture15 [eff-15->eff]  mean = {stats['capture15_mean']}%  "
              f"pos_rate = {stats['capture15_pos_rate']}")
        print(f"capture10 [eff-10->eff]  mean = {stats['capture10_mean']}%  "
              f"pos_rate = {stats['capture10_pos_rate']}")
        print(f"runup5    [eff-6->eff-1] mean = {stats['runup5_mean']}%  "
              f"pos_rate = {stats['runup5_pos_rate']}")
        print(f"post1     [eff->eff+1]   mean = {stats['post1_mean']}%  "
              f"pos_rate = {stats['post1_pos_rate']}")
        print(f"post5     [eff->eff+5]   mean = {stats['post5_mean']}%  "
              f"pos_rate = {stats['post5_pos_rate']}")
        print(f"Pearson  r(capture10, post5) = {pearson}")
        print(f"Spearman r(capture10, post5) = {spear}")
        hi = valid[valid["capture10"] > 20]
        lo = valid[valid["capture10"] <= 20]
        print(f"HIGH capture10>20% (n={len(hi)}): mean post5 = {hi['post5'].mean():.2f}")
        print(f"LOW  capture10<=20% (n={len(lo)}): mean post5 = {lo['post5'].mean():.2f}")
        # temporal split
        valid = valid.copy()
        valid["yr"] = valid["effective_date"].str[:4].astype(int)
        for label, sub in [("2020-2022", valid[valid.yr <= 2022]),
                            ("2023-2026", valid[valid.yr >= 2023])]:
            print(f"  {label} (n={len(sub)}): capture10 mean={sub['capture10'].mean():.2f} "
                  f"post5 mean={sub['post5'].mean():.2f}")
    else:
        pearson = spear = None

    out = {
        "description": "S&P 500 quarterly-rebalance additions (2020-present), "
                       "EFFECTIVE-date-anchored abnormal returns vs SPY. Reproducible "
                       "from Wikipedia 'Selected changes' table (market-cap adds in "
                       "Mar/Jun/Sep/Dec). capture10=[eff-10,eff] front-running run-up; "
                       "post5=[eff,eff+5] post-effective reversal.",
        "source": WIKI_URL,
        "generated_by": "tools/sp500_addition_dataset.py",
        "n_total": len(events),
        "n_valid": n,
        "pearson_capture10_post5": pearson,
        "spearman_capture10_post5": spear,
        "stats": stats,
        "events": events,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    json.dump(out, open(OUT_PATH, "w"), indent=1)
    print(f"\nsaved {OUT_PATH}")


if __name__ == "__main__":
    main()
