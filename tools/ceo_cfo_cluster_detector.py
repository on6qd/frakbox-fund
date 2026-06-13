"""
CEO/CFO-Inclusive Insider Cluster Detector

Extends insider_cluster_detector.py to preserve role/title information
per insider, enabling segmentation of clusters by whether they include
the CEO or CFO (Cohen, Malloy & Pomorski 2012 finding: CEO/CFO purchases
are ~12x more predictive than directors/VPs).

Output columns:
    ticker, cluster_date, n_insiders, total_value, window_start, window_end,
    has_ceo, has_cfo, has_c_suite (CEO or CFO), roles_in_cluster, titles_in_cluster
"""

import os
import pickle
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sec_form4_cache")

# Title patterns for CEO/CFO classification
CEO_PATTERNS = [
    "chief executive", "ceo", "president and ceo", "president & ceo",
    "ceo and president", "chairman and ceo", "chairman & ceo",
    "executive chairman",  # Often functions as CEO-equivalent
]
CFO_PATTERNS = [
    "chief financial", "cfo", "evp and cfo", "evp & cfo",
    "svp and cfo", "svp & cfo", "vp and cfo", "vp & cfo",
    "evp & chief financial",
]


def _is_ceo(title) -> bool:
    if not title or not isinstance(title, str):
        return False
    t = title.lower().strip()
    return any(p in t for p in CEO_PATTERNS)


def _is_cfo(title) -> bool:
    if not title or not isinstance(title, str):
        return False
    t = title.lower().strip()
    return any(p in t for p in CFO_PATTERNS)


def _is_c_suite(title) -> bool:
    """Broader C-suite: CEO, CFO, COO, CTO, CMO, CLO, CMO, etc."""
    if not title or not isinstance(title, str):
        return False
    t = title.lower().strip()
    return "chief" in t or _is_ceo(t) or _is_cfo(t)


def load_quarter(year: int, quarter: int) -> dict | None:
    cache_path = os.path.join(CACHE_DIR, f"{year}q{quarter}_form345.pkl")
    if not os.path.exists(cache_path):
        return None
    with open(cache_path, "rb") as f:
        return pickle.load(f)


def build_purchases_with_titles(
    year_start: int = 2020,
    year_end: int = 2024,
    min_purchase_value: float = 50_000,
) -> pd.DataFrame:
    """
    Load all Form 4 open-market purchases with insider titles.

    Returns DataFrame with:
        ticker, filing_date, reporter_id, value, relationship, title, is_ceo, is_cfo
    """
    all_purchases = []

    for year in range(year_start, year_end + 1):
        for quarter in range(1, 5):
            qend_month = quarter * 3
            now = datetime.now()
            if year == now.year and qend_month > now.month:
                continue

            data = load_quarter(year, quarter)
            if data is None:
                print(f"  {year}Q{quarter}: not in cache, skipping")
                continue

            submissions = data["submissions"].copy()
            nonderiv = data["nonderiv_trans"].copy()
            owners = data.get("reporting_owners", pd.DataFrame()).copy()

            # Normalize column names
            submissions.columns = submissions.columns.str.lower().str.strip()
            nonderiv.columns = nonderiv.columns.str.lower().str.strip()
            if not owners.empty:
                owners.columns = owners.columns.str.lower().str.strip()

            # Filter to open-market purchases
            purchase_col = next(
                (c for c in ["trans_code", "transactioncode", "trans_code"] if c in nonderiv.columns),
                None
            )
            if purchase_col is None:
                # Try fuzzy
                for c in nonderiv.columns:
                    if "trans_code" in c or "transactioncode" in c:
                        purchase_col = c
                        break
            if purchase_col is None:
                print(f"  {year}Q{quarter}: no transaction code column")
                continue

            purchases = nonderiv[nonderiv[purchase_col] == "P"].copy()
            if len(purchases) == 0:
                continue

            # Compute value
            shares_col = next((c for c in ["trans_shares", "transactionshares"] if c in purchases.columns), None)
            price_col = next((c for c in ["trans_pricepershare", "transactionpricepershare"] if c in purchases.columns), None)
            if shares_col is None:
                for c in purchases.columns:
                    if "share" in c and "trans" in c:
                        shares_col = c
                        break
            if price_col is None:
                for c in purchases.columns:
                    if "price" in c:
                        price_col = c
                        break

            if shares_col is None or price_col is None:
                continue

            purchases = purchases.copy()
            purchases["_shares"] = pd.to_numeric(purchases[shares_col], errors="coerce")
            purchases["_price"] = pd.to_numeric(purchases[price_col], errors="coerce")
            purchases["_value"] = purchases["_shares"] * purchases["_price"]
            purchases = purchases[purchases["_value"] >= min_purchase_value].copy()

            if len(purchases) == 0:
                continue

            # Find accession number col
            acc_col = next((c for c in ["accession_number", "accession_num"] if c in purchases.columns), None)
            if acc_col is None:
                continue
            purchases = purchases.rename(columns={acc_col: "accession_num"})

            # Build submissions lookup: accession -> ticker, filing_date
            sub_acc = next((c for c in ["accession_number", "accession_num"] if c in submissions.columns), None)
            tick_col = next((c for c in ["issuertradingsymbol", "ticker"] if c in submissions.columns), None)
            date_col = next((c for c in ["periodofreport", "period_of_report", "filing_date", "datefiled"] if c in submissions.columns), None)
            if not all([sub_acc, tick_col, date_col]):
                continue

            sub_merge = submissions[[sub_acc, tick_col, date_col]].copy()
            sub_merge.columns = ["accession_num", "ticker", "filing_date"]

            merged = purchases.merge(sub_merge, on="accession_num", how="left")

            # Join reporting owners for CIK + title
            if not owners.empty:
                own_acc = next((c for c in ["accession_number", "accession_num"] if c in owners.columns), None)
                if own_acc and own_acc != "accession_num":
                    owners = owners.rename(columns={own_acc: "accession_num"})
                elif own_acc is None:
                    owners["accession_num"] = None

                reporter_col = next(
                    (c for c in ["rptownercik", "reportingownercik"] if c in owners.columns),
                    None
                )
                title_col = next(
                    (c for c in ["rptowner_title", "reportingownertitle"] if c in owners.columns),
                    None
                )
                rel_col = next(
                    (c for c in ["rptowner_relationship", "reportingownerrelationship"] if c in owners.columns),
                    None
                )

                if reporter_col:
                    keep = ["accession_num", reporter_col]
                    if title_col:
                        keep.append(title_col)
                    if rel_col:
                        keep.append(rel_col)
                    owner_sub = owners[keep].drop_duplicates("accession_num").copy()
                    rename_map = {reporter_col: "reporter_id"}
                    if title_col:
                        rename_map[title_col] = "title"
                    if rel_col:
                        rename_map[rel_col] = "relationship"
                    owner_sub = owner_sub.rename(columns=rename_map)
                    merged = merged.merge(owner_sub, on="accession_num", how="left")
                else:
                    merged["reporter_id"] = merged["accession_num"]
            else:
                merged["reporter_id"] = merged["accession_num"]

            # Parse and clean
            merged["filing_date"] = pd.to_datetime(merged["filing_date"], errors="coerce")
            merged = merged.dropna(subset=["filing_date", "ticker"])
            merged["ticker"] = merged["ticker"].str.strip().str.upper()
            merged = merged[merged["ticker"].str.len().between(1, 5)]
            merged = merged[~merged["ticker"].str.contains(r'[^A-Z.\-]', na=True)]

            # Add title flags
            if "title" not in merged.columns:
                merged["title"] = None
            if "relationship" not in merged.columns:
                merged["relationship"] = None

            merged["is_ceo"] = merged["title"].apply(_is_ceo)
            merged["is_cfo"] = merged["title"].apply(_is_cfo)

            cols = ["ticker", "filing_date", "reporter_id", "_value", "relationship", "title", "is_ceo", "is_cfo"]
            all_purchases.append(merged[cols].copy())
            print(f"  {year}Q{quarter}: {len(merged)} qualifying purchases, "
                  f"CEO={merged['is_ceo'].sum()}, CFO={merged['is_cfo'].sum()}")

    if not all_purchases:
        print("No purchase data collected!")
        return pd.DataFrame()

    df = pd.concat(all_purchases, ignore_index=True)
    df = df.sort_values(["ticker", "filing_date"])
    print(f"\nTotal purchases with titles: {len(df)}")
    print(f"CEO purchases: {df['is_ceo'].sum()}")
    print(f"CFO purchases: {df['is_cfo'].sum()}")
    return df


def identify_clusters_with_roles(
    purchases_df: pd.DataFrame,
    cluster_window_days: int = 30,
    min_insiders_in_cluster: int = 3,
) -> pd.DataFrame:
    """
    Identify cluster events and tag each with CEO/CFO presence.

    Returns DataFrame with:
        ticker, cluster_date, n_insiders, total_value, window_start, window_end,
        has_ceo, has_cfo, has_c_suite, roles_in_cluster, titles_in_cluster
    """
    cluster_events = []

    for ticker, group in purchases_df.groupby("ticker"):
        group = group.sort_values("filing_date").reset_index(drop=True)
        dates = group["filing_date"].tolist()
        reporters = group["reporter_id"].fillna("unknown").tolist()
        values = group["_value"].tolist()
        is_ceos = group["is_ceo"].tolist()
        is_cfos = group["is_cfo"].tolist()
        titles = group["title"].fillna("").tolist()
        relationships = group["relationship"].fillna("").tolist()

        unique_dates = sorted(set(dates))
        seen_cluster_dates = set()

        for trigger_date in unique_dates:
            window_start = trigger_date - timedelta(days=cluster_window_days)
            window_end = trigger_date

            window_reporters = {}  # reporter_id -> {value, is_ceo, is_cfo, title}
            window_dates = []

            for j in range(len(dates)):
                if window_start <= dates[j] <= window_end:
                    rid = reporters[j]
                    if rid not in window_reporters:
                        window_reporters[rid] = {
                            "value": 0, "is_ceo": False, "is_cfo": False,
                            "title": titles[j], "relationship": relationships[j]
                        }
                    window_reporters[rid]["value"] += values[j] if not pd.isna(values[j]) else 0
                    window_reporters[rid]["is_ceo"] = window_reporters[rid]["is_ceo"] or is_ceos[j]
                    window_reporters[rid]["is_cfo"] = window_reporters[rid]["is_cfo"] or is_cfos[j]
                    window_dates.append(dates[j])

            if len(window_reporters) < min_insiders_in_cluster:
                continue

            # Only record when threshold first crossed
            prev_reporters = set()
            for j in range(len(dates)):
                if window_start <= dates[j] < trigger_date:
                    prev_reporters.add(reporters[j])
            if len(prev_reporters) >= min_insiders_in_cluster:
                continue

            if trigger_date.date() in seen_cluster_dates:
                continue
            seen_cluster_dates.add(trigger_date.date())

            has_ceo = any(v["is_ceo"] for v in window_reporters.values())
            has_cfo = any(v["is_cfo"] for v in window_reporters.values())
            all_titles = [v["title"] for v in window_reporters.values() if v["title"]]
            all_rels = [v["relationship"] for v in window_reporters.values() if v["relationship"]]
            total_value = sum(v["value"] for v in window_reporters.values())

            cluster_events.append({
                "ticker": ticker,
                "cluster_date": trigger_date.date(),
                "n_insiders": len(window_reporters),
                "total_value": round(total_value, 2),
                "window_start": min(window_dates).date(),
                "window_end": max(window_dates).date(),
                "has_ceo": has_ceo,
                "has_cfo": has_cfo,
                "has_c_suite": has_ceo or has_cfo,
                "titles_in_cluster": "|".join(all_titles[:5]),  # cap for readability
                "roles_in_cluster": "|".join(sorted(set(all_rels)))[:100],
            })

    df = pd.DataFrame(cluster_events)
    if len(df) > 0:
        df = df.sort_values("cluster_date").reset_index(drop=True)
        print(f"\nTotal cluster events: {len(df)}")
        print(f"With CEO: {df['has_ceo'].sum()} ({df['has_ceo'].mean()*100:.1f}%)")
        print(f"With CFO: {df['has_cfo'].sum()} ({df['has_cfo'].mean()*100:.1f}%)")
        print(f"With CEO or CFO: {df['has_c_suite'].sum()} ({df['has_c_suite'].mean()*100:.1f}%)")
    return df


if __name__ == "__main__":
    print("=== CEO/CFO Cluster Detector ===")
    print("Loading purchases with titles 2020-2024...\n")

    purchases = build_purchases_with_titles(year_start=2020, year_end=2024)
    if len(purchases) == 0:
        print("No purchases found!")
        exit(1)

    print("\nIdentifying 3+ insider clusters...")
    clusters = identify_clusters_with_roles(purchases, min_insiders_in_cluster=3)

    if len(clusters) > 0:
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data/clusters_with_roles.csv')
        clusters.to_csv(out, index=False)
        print(f"\nSaved {len(clusters)} clusters to {out}")
        print("\nSample:")
        print(clusters.head(10).to_string())
