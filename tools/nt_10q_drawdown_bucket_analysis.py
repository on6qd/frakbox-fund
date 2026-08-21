"""
Test whether the pre-event 30d moderate-drawdown filter (-20% to -5%) — which
rescued the NT 10-K large-cap short — also rescues the NT 10-Q large-cap short,
which was a DEAD END unfiltered (fat-tail pump-stock outliers killed the mean).

Reuses stored task results:
  IS : T-8887e2ab (NT 10-Q large-cap, 2023-01..2024-05)
  OOS: T-dda35472 (NT 10-Q large-cap, 2024-07..2025-11)
Each event already has abnormal_3d/5d/10d vs SPY. We add the missing dimension:
30d pre-event raw return, computed from yfinance.
"""
import json
import os
import tempfile
import statistics as st
import db

OUTDIR = os.environ.get("NT10Q_OUTDIR", tempfile.gettempdir())
from tools.yfinance_utils import get_close_prices
from datetime import datetime, timedelta

try:
    from scipy import stats as sps
except Exception:
    sps = None


def load_events(tid):
    r = db.get_db().execute("SELECT result FROM task_results WHERE id=?", (tid,)).fetchone()
    res = json.loads(r[0])
    out = []
    for e in res["individual_impacts"]:
        out.append({
            "symbol": e["symbol"],
            "date": e["event_date"],
            "abn3": e.get("abnormal_3d"),
            "abn5": e.get("abnormal_5d"),
            "abn10": e.get("abnormal_10d"),
        })
    return out


def pre_drawdown(symbol, event_date):
    """30 calendar-day pre-event raw return (%) = close[event] / close[event-30d] - 1."""
    ed = datetime.strptime(event_date, "%Y-%m-%d")
    start = (ed - timedelta(days=50)).strftime("%Y-%m-%d")
    end = (ed + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        px = get_close_prices(symbol, start, end)
    except Exception:
        return None
    if px is None or len(px) < 15:
        return None
    s = px.iloc[:, 0] if hasattr(px, "iloc") and px.ndim > 1 else px
    s = s.dropna()
    if len(s) < 15:
        return None
    # anchor 30 calendar days back -> ~21 trading days; use ~21 bars back
    last = float(s.iloc[-1])
    idx = max(0, len(s) - 22)
    ref = float(s.iloc[idx])
    if ref == 0:
        return None
    return (last / ref - 1.0) * 100.0


def summarize(vals, label):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        print(f"  {label}: N=0")
        return
    mean = st.mean(vals)
    med = st.median(vals)
    neg = sum(1 for v in vals if v < 0) / n * 100
    p = None
    if sps is not None and n >= 3:
        try:
            p = sps.wilcoxon(vals).pvalue
        except Exception:
            p = None
    pmax = max(vals); pmin = min(vals)
    print(f"  {label}: N={n} mean={mean:+.2f}% median={med:+.2f}% neg_rate={neg:.0f}% "
          f"wilcoxon_p={p:.4f} " if p is not None else
          f"  {label}: N={n} mean={mean:+.2f}% median={med:+.2f}% neg_rate={neg:.0f}% ", end="")
    print(f"[min={pmin:+.1f} max={pmax:+.1f}]")


def bucket(dd):
    if dd is None:
        return "nodata"
    if dd < -20:
        return "deep(<-20%)"
    if dd < -5:
        return "moderate(-20..-5%)"
    if dd < 5:
        return "flat(-5..+5%)"
    return "up(>+5%)"


for tag, tid in [("IS 2023-01..2024-05", "T-8887e2ab"), ("OOS 2024-07..2025-11", "T-dda35472")]:
    evs = load_events(tid)
    print(f"\n===== {tag} ({tid}) — {len(evs)} events =====")
    for e in evs:
        e["dd"] = pre_drawdown(e["symbol"], e["date"])
        e["bucket"] = bucket(e["dd"])
    # bucket counts
    from collections import Counter
    print("bucket counts:", dict(Counter(e["bucket"] for e in evs)))
    for b in ["moderate(-20..-5%)", "deep(<-20%)", "flat(-5..+5%)", "up(>+5%)"]:
        sub = [e for e in evs if e["bucket"] == b]
        if not sub:
            continue
        print(f" [{b}] n={len(sub)}")
        summarize([e["abn3"] for e in sub], "3d abn")
        summarize([e["abn5"] for e in sub], "5d abn")
        summarize([e["abn10"] for e in sub], "10d abn")
    # save per-event for combined pass
    db_key = "IS" if "IS" in tag else "OOS"
    with open(os.path.join(OUTDIR, f"nt10q_{db_key}.json"), "w") as f:
        json.dump(evs, f)
print("\nDONE")
