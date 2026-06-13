"""
Full backtest: CEO/CFO buying >$100K after stock drops >5% in 5 days
Tests across all years 2020-2024 and multiple market regimes.
"""

import pickle
import os
import json
import time
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from scipy import stats

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/sec_form4_cache')
OUTPUT_PATH = "/tmp/insider_buy_full_results.json"
TIINGO_KEY = "0ecf1cc45d0c9e24acba402a87dc5fd023b30da0"
TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"
START_DATE = "2019-01-01"   # extra history for prior-drop lookback
END_DATE = "2025-01-01"

CEO_CFO_KEYWORDS = [
    "chief executive", "ceo", "chief financial", "cfo",
    "president and ceo", "president, ceo", "president & ceo",
    "c.e.o", "chief exec"
]

MIN_BUY_VALUE = 100_000
MAX_BUY_VALUE = 200_000_000
PRIOR_DROP_WINDOW = 5       # trading days
PRIOR_DROP_THRESHOLD = 0.05 # 5%
FORWARD_WINDOWS = [5, 10, 20]

# ── Step 1: Load all pkl files ────────────────────────────────────────────────
print("=" * 60)
print("STEP 1: Loading EDGAR Form 4 cache (2020–2024)")
print("=" * 60)

all_submissions = []
all_transactions = []
all_owners = []

for year in range(2020, 2025):
    for quarter in range(1, 5):
        fname = os.path.join(CACHE_DIR, f"{year}q{quarter}_form345.pkl")
        if not os.path.exists(fname):
            print(f"  MISSING: {fname}")
            continue
        with open(fname, "rb") as f:
            data = pickle.load(f)
        subs = data["submissions"].copy()
        trans = data["nonderiv_trans"].copy()
        owners = data["reporting_owners"].copy()
        subs["source_quarter"] = f"{year}Q{quarter}"
        all_submissions.append(subs)
        all_transactions.append(trans)
        all_owners.append(owners)
        print(f"  Loaded {year}Q{quarter}: {len(trans):,} transactions, {len(owners):,} owners")

submissions = pd.concat(all_submissions, ignore_index=True)
transactions = pd.concat(all_transactions, ignore_index=True)
owners = pd.concat(all_owners, ignore_index=True)
print(f"\nTotal: {len(transactions):,} transactions, {len(owners):,} owners, {len(submissions):,} submissions")

# ── Step 2: Extract CEO/CFO open-market purchases ─────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Filtering CEO/CFO open-market purchases")
print("=" * 60)

# Filter transactions: open-market purchases
buys = transactions[
    (transactions["TRANS_CODE"] == "P") &
    (transactions["TRANS_ACQUIRED_DISP_CD"] == "A")
].copy()
print(f"Open-market purchases (TRANS_CODE=P, A): {len(buys):,}")

# Filter owners for CEO/CFO
def is_ceo_cfo(title):
    if pd.isna(title):
        return False
    t = str(title).lower().strip()
    return any(kw in t for kw in CEO_CFO_KEYWORDS)

ceo_cfo_owners = owners[owners["RPTOWNER_TITLE"].apply(is_ceo_cfo)].copy()
print(f"CEO/CFO owners: {len(ceo_cfo_owners):,}")

# Merge: transactions -> owners via ACCESSION_NUMBER
buys_with_role = buys.merge(
    ceo_cfo_owners[["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_TITLE"]],
    on="ACCESSION_NUMBER",
    how="inner"
)
print(f"CEO/CFO purchases after merge: {len(buys_with_role):,}")

# Merge with submissions to get ticker
buys_with_ticker = buys_with_role.merge(
    submissions[["ACCESSION_NUMBER", "ISSUERTRADINGSYMBOL", "ISSUERNAME"]].drop_duplicates("ACCESSION_NUMBER"),
    on="ACCESSION_NUMBER",
    how="left"
)

# Drop missing tickers
buys_with_ticker = buys_with_ticker[buys_with_ticker["ISSUERTRADINGSYMBOL"].notna()].copy()
buys_with_ticker["ISSUERTRADINGSYMBOL"] = buys_with_ticker["ISSUERTRADINGSYMBOL"].str.upper().str.strip()
print(f"After adding ticker: {len(buys_with_ticker):,}")

# Parse transaction values
buys_with_ticker["TRANS_SHARES"] = pd.to_numeric(buys_with_ticker["TRANS_SHARES"], errors="coerce")
buys_with_ticker["TRANS_PRICEPERSHARE"] = pd.to_numeric(buys_with_ticker["TRANS_PRICEPERSHARE"], errors="coerce")
buys_with_ticker["buy_value"] = buys_with_ticker["TRANS_SHARES"] * buys_with_ticker["TRANS_PRICEPERSHARE"]

# Filter by value range
buys_filtered = buys_with_ticker[
    (buys_with_ticker["buy_value"] >= MIN_BUY_VALUE) &
    (buys_with_ticker["buy_value"] <= MAX_BUY_VALUE)
].copy()
print(f"After value filter ($100K-$200M): {len(buys_filtered):,}")

# Parse TRANS_DATE
def parse_trans_date(d):
    if pd.isna(d):
        return pd.NaT
    try:
        return datetime.strptime(str(d).strip(), "%d-%b-%Y").date()
    except Exception:
        try:
            return pd.to_datetime(d).date()
        except Exception:
            return None

buys_filtered["trans_date"] = buys_filtered["TRANS_DATE"].apply(parse_trans_date)
buys_filtered = buys_filtered[buys_filtered["trans_date"].notna()].copy()
print(f"After date parse: {len(buys_filtered):,}")

# Deduplicate by ticker+date, keep max value
buys_deduped = (
    buys_filtered
    .sort_values("buy_value", ascending=False)
    .drop_duplicates(subset=["ISSUERTRADINGSYMBOL", "trans_date"])
    .reset_index(drop=True)
)
print(f"After dedup (ticker+date, max value): {len(buys_deduped):,}")

unique_tickers = sorted(buys_deduped["ISSUERTRADINGSYMBOL"].unique())
print(f"Unique tickers to download: {len(unique_tickers)}")

# ── Step 3: Download prices (yfinance batch, Tiingo fallback) ─────────────────
print("\n" + "=" * 60)
print("STEP 3: Downloading prices (yfinance batch + Tiingo fallback)")
print("=" * 60)

price_cache = {}  # ticker -> pd.Series of adjClose indexed by date (datetime.date)

def fetch_tiingo(ticker):
    """Tiingo fallback for delisted / missing yfinance tickers."""
    tiingo_sym = ticker.upper().replace(".", "-")
    url = f"{TIINGO_BASE}/{tiingo_sym}/prices"
    params = {"startDate": START_DATE, "endDate": END_DATE, "token": TIINGO_KEY}
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 404:
            return None, "404"
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        data = r.json()
        if not data:
            return None, "empty"
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.set_index("date")["adjClose"].sort_index()
        return df, "ok"
    except Exception as e:
        return None, str(e)

def yf_series(ticker):
    """Download adjusted close from yfinance, return pd.Series or None."""
    try:
        df = yf.download(ticker, start=START_DATE, end=END_DATE,
                         auto_adjust=True, progress=False, threads=False)
        if df.empty:
            return None
        close = df["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        close.index = pd.to_datetime(close.index).date
        close = close.sort_index().dropna()
        if len(close) < 20:
            return None
        return close
    except Exception:
        return None

# ── 3a: batch yfinance download (all tickers at once, much faster) ────────────
all_tickers_to_dl = unique_tickers + ["SPY"]
print(f"Batch downloading {len(all_tickers_to_dl)} tickers via yfinance...")
try:
    batch_df = yf.download(
        all_tickers_to_dl,
        start=START_DATE, end=END_DATE,
        auto_adjust=True, progress=True, threads=True
    )
    # multi-ticker download: columns are (field, ticker)
    if isinstance(batch_df.columns, pd.MultiIndex):
        close_df = batch_df["Close"]
    else:
        close_df = batch_df[["Close"]]
    close_df.index = pd.to_datetime(close_df.index).date

    batch_ok = 0
    batch_empty = []
    for col in close_df.columns:
        s = close_df[col].dropna()
        if len(s) >= 20:
            price_cache[str(col).upper()] = s
            batch_ok += 1
        else:
            batch_empty.append(str(col).upper())
    print(f"  yfinance batch: {batch_ok} tickers loaded, {len(batch_empty)} empty/short")
except Exception as e:
    print(f"  yfinance batch failed: {e}. Will fall back to per-ticker downloads.")
    batch_empty = all_tickers_to_dl

# ── 3b: Per-ticker yfinance for any missed, then Tiingo fallback ──────────────
need_fallback = [t for t in batch_empty if t not in price_cache]
print(f"  Tickers needing per-ticker download or Tiingo fallback: {len(need_fallback)}")

skipped_404 = []
skipped_other = []

for i, ticker in enumerate(need_fallback):
    if ticker in price_cache:
        continue
    # Try yfinance individually first
    s = yf_series(ticker)
    if s is not None:
        price_cache[ticker] = s
    else:
        # Tiingo fallback
        prices, status = fetch_tiingo(ticker)
        if status == "ok":
            price_cache[ticker] = prices
        elif status == "404":
            skipped_404.append(ticker)
        else:
            skipped_other.append((ticker, status))
        time.sleep(0.5)  # rate-limit only for Tiingo calls

    if (i + 1) % 50 == 0:
        print(f"  [{i+1}/{len(need_fallback)}] fallback progress — "
              f"cache: {len(price_cache)}, 404s: {len(skipped_404)}, errors: {len(skipped_other)}")

# Ensure SPY is loaded
if "SPY" not in price_cache:
    print("SPY not in cache after yfinance, trying Tiingo...")
    spy_prices_t, spy_status = fetch_tiingo("SPY")
    if spy_prices_t is not None:
        price_cache["SPY"] = spy_prices_t
    else:
        raise RuntimeError(f"Could not load SPY from any source: {spy_status}")

spy_prices = price_cache["SPY"]
print(f"\nDownload complete: {len(price_cache)} tickers total in cache")
print(f"  SPY: {len(spy_prices)} days")
print(f"  Skipped (404/not found): {len(skipped_404)}")
print(f"  Skipped (other errors): {len(skipped_other)}")
if skipped_other:
    for t, e in skipped_other[:10]:
        print(f"    {t}: {e}")

# ── Step 4: Compute prior drop and forward abnormal returns ──────────────────
print("\n" + "=" * 60)
print("STEP 4: Computing prior-drop filter and forward returns")
print("=" * 60)

def get_return(price_series, start_date, n_days, direction="forward"):
    """Get n-day return starting from first available date on/after start_date."""
    dates = price_series.index
    if direction == "forward":
        avail = [d for d in dates if d >= start_date]
    else:
        avail = [d for d in dates if d <= start_date]

    if len(avail) < 2:
        return None

    if direction == "forward":
        entry_date = avail[0]
        entry_pos = list(dates).index(entry_date)
        exit_pos = entry_pos + n_days
        if exit_pos >= len(dates):
            return None
        exit_price = price_series.iloc[exit_pos]
        entry_price = price_series.iloc[entry_pos]
    else:
        # prior n_days ending at avail[-1]
        end_date = avail[-1]
        end_pos = list(dates).index(end_date)
        start_pos = end_pos - n_days
        if start_pos < 0:
            return None
        entry_price = price_series.iloc[start_pos]
        exit_price = price_series.iloc[end_pos]

    if entry_price == 0 or pd.isna(entry_price) or pd.isna(exit_price):
        return None
    return (exit_price / entry_price) - 1.0


events = []
n_no_prices = 0
n_no_prior_drop = 0
n_qualifies = 0

for _, row in buys_deduped.iterrows():
    ticker = row["ISSUERTRADINGSYMBOL"]
    trans_date = row["trans_date"]

    if ticker not in price_cache:
        n_no_prices += 1
        continue

    prices = price_cache[ticker]

    # Prior 5-day return: use close prices ending at/before trans_date
    prior_ret = get_return(prices, trans_date, PRIOR_DROP_WINDOW, direction="backward")
    if prior_ret is None:
        n_no_prior_drop += 1
        continue

    # Must be a drop > 5%
    if prior_ret > -PRIOR_DROP_THRESHOLD:
        n_no_prior_drop += 1
        continue

    # Entry = first trading day AFTER trans_date
    entry_date = trans_date + timedelta(days=1)

    # Forward abnormal returns
    ar_5d = ar_10d = ar_20d = None
    for window in [5, 10, 20]:
        stock_ret = get_return(prices, entry_date, window, direction="forward")
        spy_ret = get_return(spy_prices, entry_date, window, direction="forward")
        if stock_ret is not None and spy_ret is not None:
            ar = stock_ret - spy_ret
        else:
            ar = None
        if window == 5:
            ar_5d = ar
        elif window == 10:
            ar_10d = ar
        elif window == 20:
            ar_20d = ar

    # Only keep events with at least ar_5d
    if ar_5d is None:
        continue

    n_qualifies += 1
    events.append({
        "ticker": ticker,
        "date": str(trans_date),
        "prior_drop": round(prior_ret, 6),
        "value": round(float(row["buy_value"]), 2),
        "ar_5d": round(ar_5d, 6) if ar_5d is not None else None,
        "ar_10d": round(ar_10d, 6) if ar_10d is not None else None,
        "ar_20d": round(ar_20d, 6) if ar_20d is not None else None,
        "owner_title": str(row["RPTOWNER_TITLE"]),
    })

print(f"Total purchases evaluated: {len(buys_deduped):,}")
print(f"  No price data: {n_no_prices:,}")
print(f"  No/insufficient prior drop: {n_no_prior_drop:,}")
print(f"  Qualified events: {n_qualifies:,}")

# ── Step 5: Analysis ──────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 5: Statistical Analysis")
print("=" * 60)

ev_df = pd.DataFrame(events)
ev_df["date"] = pd.to_datetime(ev_df["date"])
ev_df["year"] = ev_df["date"].dt.year

def analyze_subset(df, label, window="ar_20d"):
    results = {}
    for w in ["ar_5d", "ar_10d", "ar_20d"]:
        arr = df[w].dropna().values
        n = len(arr)
        if n < 5:
            results[w] = {"n": n, "mean": None, "median": None, "dir_rate": None, "pvalue": None}
            continue
        mean_ar = float(np.mean(arr))
        median_ar = float(np.median(arr))
        dir_rate = float(np.mean(arr > 0.005))  # AR > 0.5%
        t_stat, pvalue = stats.ttest_1samp(arr, 0)
        results[w] = {
            "n": n,
            "mean": round(mean_ar * 100, 3),      # in %
            "median": round(median_ar * 100, 3),
            "dir_rate": round(dir_rate * 100, 1),
            "pvalue": round(float(pvalue), 4),
            "t_stat": round(float(t_stat), 3),
        }
    return results


def fmt_row(label, stats_dict):
    lines = []
    for w in ["ar_5d", "ar_10d", "ar_20d"]:
        s = stats_dict.get(w, {})
        if not s or s.get("n", 0) < 5:
            lines.append(f"  {label:30s} [{w}] n={s.get('n',0):4d}  (insufficient data)")
        else:
            lines.append(
                f"  {label:30s} [{w}] "
                f"n={s['n']:4d}  mean={s['mean']:+6.2f}%  "
                f"median={s['median']:+6.2f}%  "
                f"dir={s['dir_rate']:5.1f}%  p={s['pvalue']:.4f}"
            )
    return "\n".join(lines)


analysis = {}

# a) Full sample
full_stats = analyze_subset(ev_df, "Full sample")
analysis["full"] = full_stats
print("\n--- Full Sample ---")
print(fmt_row("Full sample (2020-2024)", full_stats))

# b) COVID 2020
covid_df = ev_df[ev_df["year"] == 2020]
covid_stats = analyze_subset(covid_df, "COVID 2020")
analysis["covid_2020"] = covid_stats
print("\n--- COVID 2020 ---")
print(fmt_row("COVID 2020", covid_stats))

# c) Non-COVID 2021-2024
noncovid_df = ev_df[ev_df["year"] >= 2021]
noncovid_stats = analyze_subset(noncovid_df, "Non-COVID 2021-2024")
analysis["non_covid"] = noncovid_stats
print("\n--- Non-COVID 2021-2024 ---")
print(fmt_row("Non-COVID 2021-2024", noncovid_stats))

# d) Discovery (2020-2022) vs OOS (2023-2024)
disc_df = ev_df[ev_df["year"] <= 2022]
oos_df = ev_df[ev_df["year"] >= 2023]
disc_stats = analyze_subset(disc_df, "Discovery 2020-2022")
oos_stats = analyze_subset(oos_df, "OOS 2023-2024")
analysis["discovery"] = disc_stats
analysis["oos"] = oos_stats
print("\n--- Discovery vs Out-of-Sample ---")
print(fmt_row("Discovery 2020-2022", disc_stats))
print(fmt_row("OOS 2023-2024", oos_stats))

# e) By drop magnitude
by_drop = {}
thresholds = [0.05, 0.07, 0.10, 0.15]
print("\n--- By Drop Magnitude ---")
for thresh in thresholds:
    sub = ev_df[ev_df["prior_drop"] <= -thresh]
    label = f"Drop >{int(thresh*100)}%"
    key = f"drop_gt_{int(thresh*100)}pct"
    s = analyze_subset(sub, label)
    by_drop[key] = s
    print(fmt_row(label, s))

analysis["by_drop_threshold"] = by_drop

# Also break down by year
print("\n--- By Year ---")
by_year = {}
for yr in sorted(ev_df["year"].unique()):
    yr_df = ev_df[ev_df["year"] == yr]
    s = analyze_subset(yr_df, str(yr))
    by_year[str(yr)] = s
    print(fmt_row(str(yr), s))
analysis["by_year"] = by_year

# ── Step 6: Save results ──────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 6: Saving results")
print("=" * 60)

# Convert events to JSON-serializable format
events_out = []
for e in events:
    e2 = dict(e)
    e2["date"] = str(e2["date"])[:10] if hasattr(e2["date"], "strftime") else str(e2["date"])[:10]
    events_out.append(e2)

results = {
    "metadata": {
        "generated": datetime.now().isoformat(),
        "total_events": len(events),
        "date_range": "2020-2024",
        "criteria": {
            "roles": "CEO, CFO",
            "min_buy_value": MIN_BUY_VALUE,
            "prior_drop_window": PRIOR_DROP_WINDOW,
            "prior_drop_threshold": PRIOR_DROP_THRESHOLD,
            "entry": "first trading day after trans_date"
        }
    },
    "events": events_out,
    "analysis": analysis,
}

with open(OUTPUT_PATH, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"Results saved to {OUTPUT_PATH}")

# ── Final summary table ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("FINAL SUMMARY TABLE (20-day Abnormal Returns)")
print("=" * 60)
print(f"{'Segment':<30} {'N':>5} {'Mean AR':>9} {'Median AR':>10} {'Dir%':>7} {'p-val':>8}")
print("-" * 70)

def summary_row(label, s):
    w = "ar_20d"
    d = s.get(w, {})
    if not d or d.get("n", 0) < 5:
        return f"  {label:<28} {d.get('n',0):>5}   (insufficient)"
    return (f"  {label:<28} {d['n']:>5} {d['mean']:>+8.2f}%"
            f" {d['median']:>+9.2f}%  {d['dir_rate']:>5.1f}%  {d['pvalue']:>7.4f}")

rows = [
    ("Full 2020-2024", analysis["full"]),
    ("COVID 2020", analysis["covid_2020"]),
    ("Non-COVID 2021-2024", analysis["non_covid"]),
    ("Discovery 2020-2022", analysis["discovery"]),
    ("OOS 2023-2024", analysis["oos"]),
    ("Drop >5%", analysis["by_drop_threshold"]["drop_gt_5pct"]),
    ("Drop >7%", analysis["by_drop_threshold"]["drop_gt_7pct"]),
    ("Drop >10%", analysis["by_drop_threshold"]["drop_gt_10pct"]),
    ("Drop >15%", analysis["by_drop_threshold"]["drop_gt_15pct"]),
]
for yr in sorted(ev_df["year"].unique()):
    rows.append((f"Year {yr}", analysis["by_year"][str(yr)]))

for label, s in rows:
    print(summary_row(label, s))

print("\nDone.")
