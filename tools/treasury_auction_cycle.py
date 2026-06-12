"""Test the Treasury auction concession cycle (Lou-Yan-Zhang 2013) on TLT.

Fetches 10yr-note and 30yr-bond auction dates from TreasuryDirect, classifies each
TLT trading day by signed distance to the nearest such auction, and compares window
mean daily returns vs non-window baseline. Discovery vs OOS split.
"""
import sys, json, urllib.request, datetime as dt
import numpy as np
sys.path.insert(0, '/Users/frakbox/Bots/financial_researcher')
from tools.yfinance_utils import get_close_prices

def fetch_auctions():
    """Return sorted list of date objects for 10yr Note and 30yr Bond auctions."""
    dates = set()
    for typ in ("Note", "Bond"):
        url = f"https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&type={typ}"
        req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
        data = json.load(urllib.request.urlopen(req, timeout=40))
        for rec in data:
            term = rec.get("securityTerm", "")
            ad = rec.get("auctionDate", "")[:10]
            if not ad:
                continue
            # Long-end auctions that move TLT: 30-Year Bond, 10-Year Note (incl 9-Yr 11-Mo reopenings), 20-Year Bond
            keep = False
            if typ == "Bond" and ("30-Year" in term or "20-Year" in term):
                keep = True
            if typ == "Note" and ("10-Year" in term or "9-Year 11-Month" in term):
                keep = True
            if keep:
                dates.add(dt.date.fromisoformat(ad))
    return sorted(dates)

def main():
    auctions = fetch_auctions()
    print(f"Fetched {len(auctions)} long-end auctions, {auctions[0]} .. {auctions[-1]}")

    today = dt.date(2026, 6, 12).isoformat()
    px = get_close_prices("TLT", "2010-01-01", today)
    if hasattr(px, 'columns') and 'TLT' in getattr(px, 'columns', []):
        px = px['TLT']
    px = px.dropna()
    rets = px.pct_change().dropna() * 100.0  # daily % returns
    tdates = [d.date() if hasattr(d, 'date') else d for d in rets.index]
    tdates = [dt.date(d.year, d.month, d.day) for d in tdates]

    auc_arr = np.array([d.toordinal() for d in auctions])

    # For each trading day, signed offset (in trading-day steps) to nearest auction.
    # Use calendar nearest auction, then map to trading-day index distance.
    # Build index map: trading day ordinal -> position
    ord_list = [d.toordinal() for d in tdates]
    pos_of = {o: i for i, o in enumerate(ord_list)}

    # For each auction, find the trading-day position on or just before the auction date
    def nearest_tpos(auc_ord):
        # largest trading-day ordinal <= auction ordinal
        lo, hi = 0, len(ord_list) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if ord_list[mid] <= auc_ord:
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
        return best

    # offset[i] = trading-day distance from day i to its nearest auction-anchor (signed)
    n = len(tdates)
    offset = [None] * n
    for auc in auctions:
        ap = nearest_tpos(auc.toordinal())
        if ap is None:
            continue
        for k in range(-6, 7):
            j = ap + k
            if 0 <= j < n:
                if offset[j] is None or abs(k) < abs(offset[j]):
                    offset[j] = k
    rvals = rets.values

    def window_stats(lo, hi, mask_dates):
        sel = [i for i in range(n) if offset[i] is not None and lo <= offset[i] <= hi and mask_dates(tdates[i])]
        base = [i for i in range(n) if (offset[i] is None or not (lo <= offset[i] <= hi)) and mask_dates(tdates[i])]
        if not sel:
            return None
        a = rvals[sel]; b = rvals[base]
        # Welch t-test of window mean vs baseline mean
        ma, mb = a.mean(), b.mean()
        va, vb = a.var(ddof=1), b.var(ddof=1)
        se = np.sqrt(va/len(a) + vb/len(b))
        t = (ma - mb) / se if se > 0 else 0.0
        # two-sided p via normal approx
        from math import erf, sqrt
        p = 2 * (1 - 0.5 * (1 + erf(abs(t)/sqrt(2))))
        return dict(n=len(a), mean=round(ma,4), base_mean=round(mb,4),
                    diff=round(ma-mb,4), t=round(t,2), p=round(p,4))

    samples = {
        "discovery_2010_2019": lambda d: d.year <= 2019,
        "oos_2020_2026": lambda d: d.year >= 2020,
        "full_2010_2026": lambda d: True,
    }
    windows = {"pre[-3,-1]": (-3,-1), "auction_day[0,0]": (0,0), "post[+1,+3]": (1,3),
               "pre1[-1,-1]": (-1,-1), "post5[+1,+5]": (1,5)}

    print("\n=== TLT daily % return by auction window (mean vs non-window baseline) ===")
    out = {}
    for sname, mask in samples.items():
        out[sname] = {}
        print(f"\n-- {sname} --")
        for wname, (lo,hi) in windows.items():
            s = window_stats(lo, hi, mask)
            out[sname][wname] = s
            if s:
                print(f"  {wname:18s} n={s['n']:4d} mean={s['mean']:+.4f}% base={s['base_mean']:+.4f}% "
                      f"diff={s['diff']:+.4f}% t={s['t']:+.2f} p={s['p']:.4f}")
    print("\nJSON:", json.dumps(out))

if __name__ == "__main__":
    main()
