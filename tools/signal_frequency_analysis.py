#!/usr/bin/env python3
"""Analyze expected annual frequency for each validated signal.
Uses EDGAR EFTS to count events in the past 365 days that would have triggered each scanner."""
import json
import sys
import os
import time
from datetime import datetime, timedelta
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEADERS = {"User-Agent": "financial-researcher research@example.com"}

def count_efts_filings(form_type, days=365, item_filter=None):
    """Count EDGAR filings of a given type in the past N days."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    url = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?forms={form_type}"
        f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
    )
    if item_filter:
        url += f"&q=%22{item_filter}%22"
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            return total
    except Exception as e:
        return f"error: {e}"
    return 0


def main():
    results = {}
    
    # 1. SEO Bought Deal (424B4 filings)
    n_424b4 = count_efts_filings("424B4", days=365)
    results["seo_bought_deal_short"] = {
        "raw_filings": n_424b4,
        "estimated_large_cap_trades": "2-4/yr (424B4 + matching 8-K + >$500M)",
        "expected_return": "+2.5% per trade",
        "annual_value": "$250-500",
    }
    time.sleep(0.3)
    
    # 2. NT 10-K (late filers)
    n_nt10k = count_efts_filings("NT+10-K", days=365)
    results["nt_10k_late_filing_short"] = {
        "raw_filings": n_nt10k,
        "estimated_large_cap_trades": "estimate from scanner",
        "expected_return": "+4.5% per trade",
        "annual_value": "TBD",
    }
    time.sleep(0.3)
    
    # 3. Cybersecurity 8-K (Item 1.05)
    n_cyber = count_efts_filings("8-K", days=365, item_filter="Item+1.05")
    results["cybersecurity_8k_item_105_short"] = {
        "raw_8k_filings": n_cyber,
        "estimated_large_cap_trades": "estimate from scanner", 
        "expected_return": "+3.0% per trade",
        "annual_value": "TBD",
    }
    time.sleep(0.3)
    
    # 4. Delisting 8-K (Item 3.01)
    n_delist = count_efts_filings("8-K", days=365, item_filter="Item+3.01")
    results["delisting_8k_item_301_short"] = {
        "raw_8k_filings": n_delist,
        "estimated_large_cap_trades": "estimate from scanner",
        "expected_return": "+3.9% per trade", 
        "annual_value": "TBD",
    }
    time.sleep(0.3)
    
    # 5. S&P 500 additions (quarterly)
    results["sp500_index_addition_long"] = {
        "raw_filings": "4 announcements/year (quarterly rebalance)",
        "estimated_large_cap_trades": "8-12/yr (2-3 additions per quarter)",
        "expected_return": "+5.2% per trade",
        "annual_value": "$2,080-3,120",
    }
    
    # 6. VIX spike > 30 SPY long
    results["vix_spike_above_30_spy_long"] = {
        "raw_events": "2-4 VIX>30 first-cross events/yr",
        "estimated_trades": "2-4/yr",
        "expected_return": "+1.7% per trade (20d)",
        "annual_value": "$170-340",
    }
    
    # 7. Starboard 13D
    results["starboard_13d_filing_long"] = {
        "raw_events": "2-4 initial 13D filings/yr", 
        "estimated_trades": "2-4/yr",
        "expected_return": "+4.0% per trade",
        "annual_value": "$400-800",
    }
    
    # 8. Insider CEO/CFO cluster
    results["insider_buying_cluster_ceo_cfo"] = {
        "raw_events": "5-10 qualifying clusters/yr (CEO/CFO + VIX gate)",
        "estimated_trades": "3-7/yr (after VIX + t+1 gates)",
        "expected_return": "+3.4% per trade",
        "annual_value": "$510-1,190",
    }
    
    print(json.dumps(results, indent=2))
    
    print("\n=== SUMMARY ===")
    print(f"Total raw 424B4 filings (1yr): {n_424b4}")
    print(f"Total raw NT 10-K filings (1yr): {n_nt10k}")
    print(f"Total raw cybersecurity 8-K filings (1yr): {n_cyber}")
    print(f"Total raw delisting 8-K filings (1yr): {n_delist}")


if __name__ == "__main__":
    main()
