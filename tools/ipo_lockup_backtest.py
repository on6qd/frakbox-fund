"""
IPO Lockup Expiration Backtest
Tests whether stocks drop around lockup expiration (IPO date + 180 days).
"""
import sys, json
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
from tools.yfinance_utils import safe_download
import numpy as np

# Major 2023-2024 IPOs with known IPO dates
# Format: (ticker, ipo_date, lockup_days, notes)
IPOS = [
    # Late 2023 IPOs (lockup expired in 2024)
    ("ARM", "2023-09-14", 180, "ARM Holdings, chip design"),
    ("CART", "2023-09-19", 180, "Instacart, grocery delivery"),
    ("KVYO", "2023-09-20", 180, "Klaviyo, marketing automation"),
    ("BIRK", "2023-10-11", 180, "Birkenstock, footwear"),
    
    # 2024 IPOs
    ("SDHC", "2024-01-11", 180, "Smith Douglas Homes"),
    ("KSPI", "2024-01-19", 180, "Kaspi.kz, fintech"),
    ("AS", "2024-02-01", 180, "Amer Sports, sporting goods"),
    ("ALAB", "2024-03-20", 180, "Astera Labs, semiconductors"),
    ("RDDT", "2024-03-21", 180, "Reddit, social media"),
    ("PACS", "2024-04-11", 180, "PACS Group, healthcare"),
    ("ULS", "2024-04-12", 180, "UL Solutions, testing"),
    ("IBTA", "2024-04-18", 180, "Ibotta, digital promotions"),
    ("CTRI", "2024-04-18", 180, "Centuri Holdings, utility services"),
    ("RBRK", "2024-04-25", 180, "Rubrik, cybersecurity"),
    ("LOAR", "2024-04-25", 180, "Loar Holdings, aerospace"),
    ("VIK", "2024-05-01", 180, "Viking Holdings, cruise"),
    # ZK delisted, skip
    # ("ZK", "2024-05-10", 180, "ZEEKR, EV manufacturer"),
    ("TEM", "2024-06-14", 180, "Tempus AI, precision medicine"),
    ("WAY", "2024-06-07", 180, "Waystar, healthcare payments"),
    ("LINE", "2024-07-25", 180, "Lineage, cold storage REIT"),
    ("SARO", "2024-10-02", 180, "StandardAero, MRO services"),
    ("INGM", "2024-10-24", 180, "Ingram Micro, IT distribution"),
    ("TTAN", "2024-12-12", 180, "ServiceTitan, field services"),
]

def run_backtest():
    results = []
    
    for ticker, ipo_date_str, lockup_days, notes in IPOS:
        ipo_date = datetime.strptime(ipo_date_str, "%Y-%m-%d")
        lockup_expiry = ipo_date + timedelta(days=lockup_days)
        
        # Need price data around lockup expiry
        start = (lockup_expiry - timedelta(days=30)).strftime("%Y-%m-%d")
        end = (lockup_expiry + timedelta(days=30)).strftime("%Y-%m-%d")
        
        try:
            stock = safe_download(ticker, start, end)
        except (ValueError, Exception):
            stock = None
        try:
            bench = safe_download("SPY", start, end)
        except (ValueError, Exception):
            bench = None

        if stock is None or bench is None or len(stock) < 10 or len(bench) < 10:
            results.append({
                "ticker": ticker, "ipo_date": ipo_date_str, 
                "lockup_expiry": lockup_expiry.strftime("%Y-%m-%d"),
                "notes": notes, "status": "NO_DATA"
            })
            continue
        
        # Find the closest trading day to lockup expiry
        lockup_str = lockup_expiry.strftime("%Y-%m-%d")
        stock_dates = stock.index.strftime("%Y-%m-%d").tolist()
        
        # Find closest date on or after lockup expiry
        target_idx = None
        for i, d in enumerate(stock_dates):
            if d >= lockup_str:
                target_idx = i
                break
        
        if target_idx is None or target_idx < 5:
            results.append({
                "ticker": ticker, "ipo_date": ipo_date_str,
                "lockup_expiry": lockup_str, "notes": notes, "status": "NO_DATA_AROUND_DATE"
            })
            continue
        
        # Calculate returns for multiple horizons
        # Entry: close on day before lockup expiry (T-1)
        # This simulates shorting the day before
        entry_idx = target_idx - 1
        entry_price = float(stock['Close'].iloc[entry_idx])
        
        horizons = {}
        for h_name, h_days in [("3d", 3), ("5d", 5), ("10d", 10)]:
            exit_idx = target_idx + h_days - 1  # -1 because target_idx is already day 0
            if exit_idx < len(stock):
                exit_price = float(stock['Close'].iloc[exit_idx])
                raw_return = (exit_price - entry_price) / entry_price * 100
                
                # SPY benchmark
                spy_dates = bench.index.strftime("%Y-%m-%d").tolist()
                spy_entry_date = stock_dates[entry_idx]
                spy_exit_date = stock_dates[exit_idx]
                
                if spy_entry_date in spy_dates and spy_exit_date in spy_dates:
                    spy_entry_idx = spy_dates.index(spy_entry_date)
                    spy_exit_idx = spy_dates.index(spy_exit_date)
                    spy_entry_p = float(bench['Close'].iloc[spy_entry_idx])
                    spy_exit_p = float(bench['Close'].iloc[spy_exit_idx])
                    bench_return = (spy_exit_p - spy_entry_p) / spy_entry_p * 100
                    abn_return = raw_return - bench_return
                else:
                    bench_return = None
                    abn_return = None
                
                horizons[h_name] = {
                    "raw": round(raw_return, 2),
                    "bench": round(bench_return, 2) if bench_return else None,
                    "abnormal": round(abn_return, 2) if abn_return else None
                }
        
        results.append({
            "ticker": ticker,
            "ipo_date": ipo_date_str,
            "lockup_expiry": lockup_str,
            "notes": notes,
            "entry_price": round(entry_price, 2),
            "status": "OK",
            "horizons": horizons
        })
    
    # Summary statistics
    valid = [r for r in results if r["status"] == "OK"]
    
    print(f"\n=== IPO LOCKUP EXPIRATION BACKTEST ===")
    print(f"Total IPOs: {len(IPOS)}, Valid data: {len(valid)}, No data: {len(IPOS) - len(valid)}")
    
    for h in ["3d", "5d", "10d"]:
        abn_returns = [r["horizons"][h]["abnormal"] for r in valid 
                       if h in r["horizons"] and r["horizons"][h]["abnormal"] is not None]
        raw_returns = [r["horizons"][h]["raw"] for r in valid 
                       if h in r["horizons"] and r["horizons"][h]["raw"] is not None]
        
        if abn_returns:
            neg_count = sum(1 for x in abn_returns if x < -0.5)
            avg = np.mean(abn_returns)
            median = np.median(abn_returns)
            direction = neg_count / len(abn_returns) * 100
            from scipy import stats
            t_stat, p_val = stats.ttest_1samp(abn_returns, 0)
            
            print(f"\n{h} Horizon (N={len(abn_returns)}):")
            print(f"  Avg abnormal: {avg:.2f}%  Median: {median:.2f}%")
            print(f"  Avg raw: {np.mean(raw_returns):.2f}%")
            print(f"  Direction (negative >0.5%): {neg_count}/{len(abn_returns)} = {direction:.0f}%")
            print(f"  t-stat: {t_stat:.2f}  p-value: {p_val:.4f}")
    
    print(f"\n=== INDIVIDUAL RESULTS ===")
    for r in valid:
        h5 = r["horizons"].get("5d", {})
        abn_val = h5.get('abnormal', 'N/A')
        raw_val = h5.get('raw', 'N/A')
        abn_str = f"{abn_val:>7.2f}" if isinstance(abn_val, (int, float)) else f"{'N/A':>7s}"
        raw_str = f"{raw_val:>7.2f}" if isinstance(raw_val, (int, float)) else f"{'N/A':>7s}"
        print(f"  {r['ticker']:6s} lockup={r['lockup_expiry']}  5d_abn={abn_str}%  raw={raw_str}%  | {r['notes']}")
    
    for r in results:
        if r["status"] != "OK":
            print(f"  {r['ticker']:6s} {r['status']}")
    
    return results

if __name__ == "__main__":
    results = run_backtest()
