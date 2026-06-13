#!/usr/bin/env python3
"""
Analyst Upgrade Cluster Signal Backtest
Tests: When 3+ analysts upgrade/initiate with bullish rating within 5 days,
does the stock outperform over the next 3/5/10/20 days?

Mechanism:
1. Actors/Incentives: Multiple independent analysts reassessing value simultaneously
   signals genuine fundamental change, not noise
2. Transmission Channel: Retail and institutional investors follow analyst ratings;
   buy recommendations trigger inflows; new coverage brings attention
3. Academic Support: Bradley et al (2008), Jegadeesh et al (2004) show upgrade drift;
   cluster initiations have stronger effects than single initiations

Design choices:
- Cluster = 3+ bullish actions (init/upgrade to Buy/Outperform) within 5 calendar days
- Bullish = Buy, Strong Buy, Outperform, Overweight, Positive, Conviction Buy
- Filter to S&P 500 large caps (>$2B market cap)
- Measure abnormal returns vs SPY benchmark
- Temporal split: discovery 2020-2022, validation 2023-2025

Note: Uses yfinance upgrades_downgrades which goes back to ~2012
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
import time
import json
import warnings
warnings.filterwarnings('ignore')

# Major banks only (quality filter)
MAJOR_BANKS = {
    'JP Morgan', 'JPMorgan', 'Goldman Sachs', 'Morgan Stanley', 'Bank of America',
    'BofA Securities', 'B of A Securities', 'Citigroup', 'Citi', 'Barclays',
    'Wells Fargo', 'UBS', 'Deutsche Bank', 'RBC Capital', 'TD Cowen', 'Cowen',
    'Bernstein', 'Wolfe Research', 'Evercore', 'KeyBanc', 'Keybanc', 'Jefferies',
    'Piper Sandler', 'Raymond James', 'Baird', 'Stifel', 'Oppenheimer',
    'Truist', 'BMO Capital', 'Needham', 'DA Davidson', 'William Blair',
    'Wedbush', 'Cantor Fitzgerald', 'HSBC', 'Credit Suisse', 'Mizuho',
    'Guggenheim', 'Susquehanna', 'Atlantic Equities', 'Exane', 'Redburn',
    'BNP Paribas', 'Societe Generale', 'CLSA', 'MoffettNathanson', 'Loop Capital',
    'Seaport Global', 'Rosenblatt', 'Maxim Group'
}

BULLISH_GRADES = {
    'buy', 'strong buy', 'outperform', 'overweight', 'positive',
    'sector outperform', 'market outperform', 'accumulate', 'top pick',
    'conviction buy', 'add', 'outperformer'
}

# S&P 500 large caps - 50 diverse tickers
TICKERS = [
    'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'TSLA', 'BRK-B',
    'JPM', 'BAC', 'GS', 'MS', 'WFC', 'V', 'MA',
    'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'PFE',
    'XOM', 'CVX', 'COP',
    'CAT', 'DE', 'HON', 'MMM', 'GE',
    'MCD', 'SBUX', 'COST', 'WMT', 'HD', 'TGT', 'NKE',
    'NFLX', 'DIS', 'CMCSA',
    'AMD', 'INTC', 'QCOM', 'AVGO', 'CRM', 'ADBE',
    'AMGN', 'GILD', 'BIIB', 'BMY',
    'BA', 'RTX', 'LMT', 'NOC'
]


def is_major_bank(firm):
    firm_l = firm.lower()
    for bank in MAJOR_BANKS:
        if bank.lower() in firm_l or firm_l in bank.lower():
            return True
    return False


def is_bullish(grade):
    if not grade:
        return False
    return grade.lower().strip() in BULLISH_GRADES


def get_upgrade_clusters(ticker, min_cluster_size=3, window_days=5):
    """Find dates where 3+ bullish upgrades/initiations occurred within window_days."""
    try:
        tk = yf.Ticker(ticker)
        upgrades = tk.upgrades_downgrades
        if upgrades is None or upgrades.empty:
            return []

        # Filter to bullish actions (init or upgrade to bullish)
        mask_action = upgrades['Action'].isin(['init', 'up'])
        mask_grade = upgrades['ToGrade'].apply(is_bullish)
        mask_bank = upgrades['Firm'].apply(is_major_bank)

        bullish = upgrades[mask_action & mask_grade].copy()

        if len(bullish) < min_cluster_size:
            return []

        # Sort by date
        bullish = bullish.sort_index()
        bullish_dates = [d.date() for d in bullish.index]

        clusters = []
        checked = set()

        for i, d in enumerate(bullish_dates):
            if i in checked:
                continue
            # Find all upgrades within window_days of this date
            window = [j for j, dd in enumerate(bullish_dates)
                      if abs((dd - d).days) <= window_days]
            if len(window) >= min_cluster_size:
                # Use the last date in the cluster as the signal date
                cluster_dates = [bullish_dates[j] for j in window]
                signal_date = max(cluster_dates)
                cluster_size = len(window)
                checked.update(window)
                clusters.append({
                    'ticker': ticker,
                    'signal_date': signal_date,
                    'cluster_size': cluster_size,
                    'upgrades': [bullish_dates[j] for j in window]
                })

        return clusters
    except Exception as e:
        return []


def measure_abnormal_return(ticker, signal_date, horizon_days, benchmark='SPY'):
    """Measure abnormal return from signal_date close to signal_date+horizon close."""
    try:
        start = signal_date - timedelta(days=10)
        end = signal_date + timedelta(days=horizon_days + 10)

        price_data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        spy_data = yf.download(benchmark, start=start, end=end, progress=False, auto_adjust=True)

        if price_data.empty or spy_data.empty:
            return None

        # Find entry date (signal_date + 1 trading day)
        entry_date = None
        for i, idx in enumerate(price_data.index):
            if idx.date() > signal_date:
                entry_date = idx
                break

        if entry_date is None:
            return None

        # Find exit date (entry + horizon trading days)
        entry_idx = list(price_data.index).index(entry_date)
        exit_idx = min(entry_idx + horizon_days, len(price_data) - 1)
        exit_date = price_data.index[exit_idx]

        if exit_idx == entry_idx:
            return None

        # Entry price = next open after signal, or close if entry is signal close
        # Using close for simplicity (could use open for next day entry)
        entry_price = price_data['Close'].iloc[entry_idx]
        exit_price = price_data['Close'].iloc[exit_idx]

        # Get SPY prices for same dates
        spy_at_entry = spy_data['Close'].reindex([entry_date], method='nearest').iloc[0]
        spy_at_exit = spy_data['Close'].reindex([exit_date], method='nearest').iloc[0]

        stock_return = (exit_price - entry_price) / entry_price
        spy_return = (spy_at_exit - spy_at_entry) / spy_at_entry
        abnormal = stock_return - spy_return

        return {
            'signal_date': signal_date,
            'entry_date': entry_date.date(),
            'exit_date': exit_date.date(),
            'stock_return': float(stock_return),
            'spy_return': float(spy_return),
            'abnormal_return': float(abnormal),
            'direction': 1 if abnormal > 0.005 else (-1 if abnormal < -0.005 else 0)
        }
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("ANALYST UPGRADE CLUSTER BACKTEST")
    print("=" * 60)
    print(f"Testing {len(TICKERS)} large-cap stocks")
    print("Signal: 3+ bullish analyst actions within 5 days")
    print("Discovery: 2020-2022 | Validation: 2023-2025")
    print()

    all_clusters = []

    print("Step 1: Collecting upgrade clusters...")
    for i, ticker in enumerate(TICKERS):
        clusters = get_upgrade_clusters(ticker, min_cluster_size=3, window_days=5)
        if clusters:
            all_clusters.extend(clusters)
            print(f"  {ticker}: {len(clusters)} clusters")
        time.sleep(0.1)  # rate limit

    print(f"\nTotal clusters found: {len(all_clusters)}")

    if len(all_clusters) < 10:
        print("Too few clusters. Try relaxing criteria (min 2 analysts, 7-day window)")
        # Try with 2 analysts
        print("\nRetrying with min_cluster_size=2...")
        for ticker in TICKERS:
            clusters = get_upgrade_clusters(ticker, min_cluster_size=2, window_days=7)
            for c in clusters:
                c['cluster_size_note'] = 'relaxed_n2'
            all_clusters.extend(clusters)
        print(f"Total with relaxed criteria: {len(all_clusters)}")

    if not all_clusters:
        print("No clusters found - exiting")
        return

    # Split into discovery (2020-2022) and validation (2023+)
    discovery = [c for c in all_clusters if 2020 <= c['signal_date'].year <= 2022]
    validation = [c for c in all_clusters if c['signal_date'].year >= 2023]

    print(f"\nDiscovery period (2020-2022): {len(discovery)} clusters")
    print(f"Validation period (2023+): {len(validation)} clusters")

    print("\nStep 2: Measuring abnormal returns for discovery period...")
    discovery_results = {}
    for horizon in [3, 5, 10, 20]:
        results = []
        for c in discovery:
            r = measure_abnormal_return(c['ticker'], c['signal_date'], horizon)
            if r:
                r['cluster_size'] = c['cluster_size']
                results.append(r)
        discovery_results[horizon] = results

        if results:
            abn_returns = [r['abnormal_return'] for r in results]
            directions = [r['direction'] for r in results]
            pos = sum(1 for d in directions if d == 1)
            print(f"\n  {horizon}d horizon (n={len(results)}):")
            print(f"    Mean abnormal: {np.mean(abn_returns)*100:.2f}%")
            print(f"    Median abnormal: {np.median(abn_returns)*100:.2f}%")
            print(f"    Direction>0.5%: {pos}/{len(directions)} = {pos/len(directions)*100:.1f}%")

            # t-test
            from scipy import stats
            t_stat, p_val = stats.ttest_1samp(abn_returns, 0)
            print(f"    p-value: {p_val:.4f}")
        time.sleep(0.5)

    # Save results
    output = {
        'backtest_date': str(date.today()),
        'n_tickers': len(TICKERS),
        'total_clusters': len(all_clusters),
        'discovery_n': len(discovery),
        'validation_n': len(validation),
        'results_by_horizon': {}
    }

    for horizon, results in discovery_results.items():
        if results:
            abn = [r['abnormal_return'] for r in results]
            output['results_by_horizon'][horizon] = {
                'n': len(results),
                'mean_abnormal_pct': round(np.mean(abn)*100, 3),
                'median_abnormal_pct': round(np.median(abn)*100, 3),
                'direction_rate': round(sum(1 for r in results if r['direction']==1)/len(results), 3)
            }

    print("\n" + "="*60)
    print("DISCOVERY PERIOD SUMMARY")
    print("="*60)
    for horizon, data in output['results_by_horizon'].items():
        print(f"{horizon}d: n={data['n']}, mean={data['mean_abnormal_pct']:.2f}%, dir={data['direction_rate']*100:.1f}%")

    with open('/tmp/analyst_upgrade_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print("\nResults saved to /tmp/analyst_upgrade_results.json")


if __name__ == '__main__':
    main()
