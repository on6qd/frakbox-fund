"""Small-cap post-earnings-announcement drift (PEAD) backtest.

Motivation: PEAD (Ball & Brown 1968; Bernard & Thomas 1989) is the canonical
limits-to-arbitrage anomaly. Prior testing in THIS system found it fully
arbitraged away in large-caps (SP100). The literature is clear that it persists
and is strongest in small-caps with low analyst coverage / high idiosyncratic
risk. This tool tests the small-cap cell that was never examined.

Design (no lookahead):
  - Earnings dates + analyst surprise from yfinance get_earnings_dates.
  - Classify each event BEAT (Reported>Estimate) or MISS (Reported<Estimate).
  - Entry at the OPEN of the first trading day STRICTLY AFTER the announcement
    calendar date (handles AMC/BMO ambiguity conservatively).
  - Forward return measured to the close h trading days later.
  - Abnormal = stock forward return - IWM forward return (strips small-cap beta).
  - Report IS (entry < oos_start) vs OOS (entry >= oos_start) separately.
  - Secondary: price-based surprise = announcement-window abnormal jump, sorted
    into terciles, to test monotonic drift independent of EPS-denominator noise.

Survivorship note: the universe is current-listed names, so delisted losers are
absent. This BIASES THE SHORT (miss) SIDE WEAKER than reality. A miss-drift
signal found despite this bias is conservative.

Usage:
  python3 tools/pead_smallcap.py --oos-start 2024-01-01 --hold 20
"""
import warnings
warnings.filterwarnings("ignore")
import argparse
import json
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, ".")
from tools.yfinance_utils import safe_download  # noqa: E402

# Diversified small/mid-cap universe (sectors spread to avoid single-factor tilt).
UNIVERSE = [
    # consumer / retail
    "SHAK", "CALM", "FIZZ", "BJRI", "PLAY", "CAKE", "CENT", "HELE", "SHOO",
    "CRI", "GES", "ZUMZ", "BKE", "SCVL", "BOOT",
    # industrials / materials
    "MTX", "KOP", "HWKN", "BCPC", "SXT", "IOSP", "ROG", "MYRG", "GVA", "ASTE",
    # tech / semi / software
    "PRGS", "BL", "SMTC", "FORM", "POWI", "SLAB", "AMBA", "COHU", "CALX",
    "DIOD", "YELP", "BAND",
    # health
    "NEOG", "OMCL", "CHE", "AMED", "IRTC", "TNDM", "PNTG", "CYRX",
    # utilities / other
    "MGEE", "OGS", "NWE", "AVA", "BKH", "CWT", "MSEX", "SJW", "AWR",
]


def get_events(ticker):
    """Return list of (ann_date, beat_sign, surprise_pct) for a ticker."""
    out = []
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=24)
    except Exception:
        return out
    if df is None or len(df) == 0:
        return out
    for ts, row in df.iterrows():
        est = row.get("EPS Estimate")
        rep = row.get("Reported EPS")
        if pd.isna(est) or pd.isna(rep):
            continue  # future / unreported
        ann_date = pd.Timestamp(ts).tz_localize(None).normalize()
        diff = float(rep) - float(est)
        if diff == 0:
            continue
        sign = 1 if diff > 0 else -1
        sp = row.get("Surprise(%)")
        out.append((ann_date, sign, float(sp) if not pd.isna(sp) else np.nan))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oos-start", default="2024-01-01")
    ap.add_argument("--hold", type=int, default=20, help="trading-day hold")
    ap.add_argument("--start", default="2019-09-01")
    ap.add_argument("--end", default="2026-06-13")
    args = ap.parse_args()
    h = args.hold
    oos = pd.Timestamp(args.oos_start)

    tickers = UNIVERSE + ["IWM"]
    print(f"Downloading prices for {len(tickers)} tickers...", file=sys.stderr)
    px = safe_download(tickers, start=args.start, end=args.end,
                       group_by="column", threads=True)
    # px columns are Open_TICKER / Close_TICKER ...
    idx = px.index

    def series(field, tkr):
        col = f"{field}_{tkr}"
        return px[col] if col in px.columns else None

    iwm_open = series("Open", "IWM")
    iwm_close = series("Close", "IWM")
    if iwm_open is None:
        print("FATAL: no IWM data", file=sys.stderr)
        sys.exit(1)

    rows = []  # dict per event
    for tkr in UNIVERSE:
        o = series("Open", tkr)
        c = series("Close", tkr)
        if o is None or c is None or o.dropna().empty:
            continue
        events = get_events(tkr)
        for ann_date, sign, sp in events:
            # first trading day strictly after announcement date
            future = idx[idx > ann_date]
            if len(future) < h + 2:
                continue
            d0 = future[0]
            p0 = idx.get_loc(d0)
            if p0 + h >= len(idx):
                continue
            entry = o.iloc[p0]
            exit_ = c.iloc[p0 + h - 1]
            if pd.isna(entry) or pd.isna(exit_) or entry <= 0:
                continue
            ret = exit_ / entry - 1.0
            bo = iwm_open.iloc[p0]
            bc = iwm_close.iloc[p0 + h - 1]
            if pd.isna(bo) or pd.isna(bc) or bo <= 0:
                continue
            bret = bc / bo - 1.0
            abn = ret - bret
            # price-based surprise: announcement-window abnormal jump,
            # measured close[ann-1] -> entry open (info available pre-entry)
            pj = np.nan
            if p0 - 1 >= 0:
                cprev = c.iloc[p0 - 1]
                if not pd.isna(cprev) and cprev > 0:
                    sj = entry / cprev - 1.0
                    bprev = iwm_close.iloc[p0 - 1]
                    if not pd.isna(bprev) and bprev > 0:
                        bj = bo / bprev - 1.0
                        pj = sj - bj
            rows.append(dict(ticker=tkr, date=d0, sign=sign, surprise=sp,
                             abn=abn, jump=pj))

    df = pd.DataFrame(rows)
    if df.empty:
        print(json.dumps({"error": "no events"}))
        return

    def stats(s):
        s = s.dropna()
        n = len(s)
        if n < 3:
            return dict(n=n, mean=None, dir=None, p=None)
        from scipy import stats as st
        t, p = st.ttest_1samp(s, 0.0)
        return dict(n=n, mean=round(float(s.mean()) * 100, 3),
                    dir=round(float((s > 0).mean()) * 100, 1),
                    p=round(float(p), 4))

    def block(d, label):
        beats = d[d.sign > 0]
        miss = d[d.sign < 0]
        res = dict(label=label, n_events=len(d),
                   beat=stats(beats.abn), miss=stats(miss.abn))
        # long-short spread = beat_abn - miss_abn (per-event pooled)
        res["beat_minus_miss_pp"] = (
            None if res["beat"]["mean"] is None or res["miss"]["mean"] is None
            else round(res["beat"]["mean"] - res["miss"]["mean"], 3))
        # price-jump terciles: does drift align with announcement jump sign?
        dd = d.dropna(subset=["jump"])
        if len(dd) >= 30:
            q = dd["jump"].quantile([1 / 3, 2 / 3]).values
            top = dd[dd.jump > q[1]].abn
            bot = dd[dd.jump < q[0]].abn
            res["jump_top_tercile"] = stats(top)
            res["jump_bot_tercile"] = stats(bot)
            if not top.dropna().empty and not bot.dropna().empty:
                res["jump_spread_pp"] = round(
                    float(top.mean() - bot.mean()) * 100, 3)
        return res

    df["date"] = pd.to_datetime(df["date"])
    is_d = df[df.date < oos]
    oos_d = df[df.date >= oos]
    summary = dict(
        hold_days=h, oos_start=args.oos_start,
        total_events=len(df), tickers_with_events=df.ticker.nunique(),
        IS=block(is_d, f"IS (<{args.oos_start})"),
        OOS=block(oos_d, f"OOS (>={args.oos_start})"),
        ALL=block(df, "ALL"),
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
