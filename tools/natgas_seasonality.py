#!/usr/bin/env python3
"""Natural-gas Sep-Oct seasonality: spot vs tradeable-instrument decomposition.

Documents the canonical finding (2026-06-13): the pre-winter (Sep->Oct)
natural-gas run-up is strong, well-powered and OOS-robust in the FRONT-MONTH
commodity (NG=F), but is NOT tradeable in any liquid instrument because the
same seasonality is priced into the futures term structure as steep contango.
A long futures-tracking ETF (UNG/BOIL) pays the entire seasonal gain back as
roll cost, and natural-gas E&P equities show no Sep-Oct edge (already priced +
oil/market beta confound).

Usage:
    python3 tools/natgas_seasonality.py                 # full report
    python3 tools/natgas_seasonality.py --months        # monthly seasonality table only
"""
import argparse
import os
import sys

import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.yfinance_utils import get_close_prices

END = "2026-06-13"


def _series(sym, start="2000-01-01"):
    px = get_close_prices(sym, start=start, end=END)
    return (px[sym] if sym in px.columns else px.iloc[:, 0]).dropna()


def monthly_table(sym="NG=F"):
    s = _series(sym)
    ret = s.resample("ME").last().pct_change().dropna()
    df = pd.DataFrame({"ret": ret})
    df["month"] = df.index.month
    print(f"=== {sym} monthly seasonality 2000-2026 ===")
    print(f"{'mo':>3} {'n':>3} {'mean%':>7} {'t':>6} {'p':>6} {'dir%':>5}")
    for mo in range(1, 13):
        r = df[df.month == mo]["ret"]
        if len(r) < 3:
            continue
        t, p = stats.ttest_1samp(r, 0)
        print(f"{mo:>3} {len(r):>3} {r.mean()*100:>7.2f} {t:>6.2f} {p:>6.3f} {(r>0).mean()*100:>5.0f}")


def sep_oct_window(sym):
    """Return per-year Sep->Oct window return (last-close Aug -> last-close Oct)."""
    s = _series(sym)
    out = []
    for yr in range(2000, 2026):
        aug = s[(s.index >= f"{yr}-08-25") & (s.index <= f"{yr}-08-31")]
        octc = s[(s.index >= f"{yr}-10-25") & (s.index <= f"{yr}-10-31")]
        if len(aug) and len(octc):
            out.append((yr, octc.iloc[-1] / aug.iloc[-1] - 1))
    return pd.DataFrame(out, columns=["year", "ret"]).set_index("year")["ret"]


def report():
    print("Natural-gas Sep->Oct seasonal: spot vs tradeable instruments\n")
    for sym in ["NG=F", "UNG", "FCG", "BOIL", "EQT", "RRC", "CTRA"]:
        try:
            r = sep_oct_window(sym)
            if len(r) < 5:
                print(f"{sym:>5}: n={len(r)} too short")
                continue
            t, p = stats.ttest_1samp(r, 0)
            ros = r[r.index >= 2015]
            print(
                f"{sym:>5}: n={len(r)} mean={r.mean()*100:>6.2f}% t={t:>5.2f} "
                f"p={p:.3f} dir={(r>0).mean()*100:>3.0f}% | OOS(2015+) "
                f"mean={ros.mean()*100:>5.1f}% dir={(ros>0).mean()*100:.0f}%"
            )
        except Exception as e:  # noqa: BLE001
            print(f"{sym:>5}: ERR {str(e)[:50]}")

    ng, ung = sep_oct_window("NG=F"), sep_oct_window("UNG")
    gap = ((ng - ung) * 100).dropna()
    print(f"\nMean Sep->Oct roll-decay gap NG=F minus UNG: {gap.mean():.1f} pp "
          f"(median {gap.median():.1f} pp) -> entire seasonal lost to contango.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", action="store_true", help="print monthly table only")
    args = ap.parse_args()
    if args.months:
        monthly_table()
    else:
        report()
