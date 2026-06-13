#!/usr/bin/env python3
"""
Analyst Initiation Coverage Signal Research
Tests: When major investment banks initiate coverage with BUY/Outperform,
does the stock outperform the market over 5-20 days?
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# 40 large-cap S&P 500 stocks across sectors
TICKERS = [
    # Tech
    'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'AMD', 'INTC', 'CRM', 'ADBE',
    # Finance
    'JPM', 'BAC', 'GS', 'MS', 'WFC', 'BLK', 'AXP', 'V', 'MA',
    # Healthcare
    'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'TMO', 'ABT',
    # Industrials/Consumer
    'CAT', 'DE', 'UPS', 'NKE', 'MCD', 'COST', 'WMT', 'HD', 'TGT',
    # Energy/Materials
    'XOM', 'CVX', 'COP', 'LIN', 'APD',
]

# Major investment banks (quality filter - avoid boutique initiations)
MAJOR_BANKS = {
    'JP Morgan', 'JPMorgan', 'Goldman Sachs', 'Morgan Stanley', 'Bank of America',
    'BofA Securities', 'Citigroup', 'Citi', 'Barclays', 'Wells Fargo', 'UBS',
    'Deutsche Bank', 'Credit Suisse', 'RBC Capital', 'TD Cowen', 'Cowen',
    'Bernstein', 'Wolfe Research', 'Evercore', 'KeyBanc', 'Jefferies',
    'Piper Sandler', 'Raymond James', 'Baird', 'Stifel', 'Oppenheimer',
    'Truist', 'BMO Capital', 'Needham', 'DA Davidson', 'William Blair',
    'Wedbush', 'Cantor Fitzgerald'
}

# Bullish grades
BULLISH_GRADES = {
    'Buy', 'Strong Buy', 'Outperform', 'Overweight', 'Positive',
    'Sector Outperform', 'Market Outperform', 'Accumulate', 'Top Pick',
    'Conviction Buy', 'Add'
}

def is_major_bank(firm):
    firm_lower = firm.lower()
    for bank in MAJOR_BANKS:
        if bank.lower() in firm_lower:
            return True
    return False

def is_bullish(grade):
    if not grade:
        return False
    grade_lower = grade.lower()
    for bg in BULLISH_GRADES:
        if bg.lower() in grade_lower:
            return True
    return False

# Collect initiation events
all_events = []
print(f"Collecting analyst initiation events from {len(TICKERS)} tickers...")

for i, ticker in enumerate(TICKERS):
    try:
        t = yf.Ticker(ticker)
        recs = t.upgrades_downgrades
        if recs is None or len(recs) == 0:
            continue

        # Filter for initiations
        inits = recs[recs['Action'].str.lower() == 'init'].copy()

        # Filter for bullish rating
        bullish_inits = inits[inits['ToGrade'].apply(is_bullish)].copy()

        # Filter for major banks only (quality filter)
        major_bank_inits = bullish_inits[bullish_inits['Firm'].apply(is_major_bank)].copy()

        for date, row in major_bank_inits.iterrows():
            event_date = date.strftime('%Y-%m-%d')
            all_events.append({
                'symbol': ticker,
                'date': event_date,
                'firm': row['Firm'],
                'grade': row['ToGrade'],
                'timing': 'pre_market'  # Analyst notes typically released premarket
            })

        if (i+1) % 10 == 0:
            print(f"  Processed {i+1}/{len(TICKERS)} tickers, {len(all_events)} events so far")
        time.sleep(0.3)  # Rate limiting

    except Exception as e:
        print(f"  {ticker}: error - {e}")
        continue

print(f"\nTotal bullish initiation events from major banks: {len(all_events)}")

# Show distribution
if all_events:
    df = pd.DataFrame(all_events)
    print(f"\nDate range: {df['date'].min()} to {df['date'].max()}")
    print(f"\nTop firms:")
    print(df['firm'].value_counts().head(10))
    print(f"\nTop grades:")
    print(df['grade'].value_counts().head(10))
    print(f"\nEvents per ticker (top 10):")
    print(df['symbol'].value_counts().head(10))

    # Only use events from 2020+ for signal testing (avoid COVID disruption: exclude 2020)
    df['date_dt'] = pd.to_datetime(df['date'])
    df_filtered = df[df['date_dt'] >= '2021-01-01'].copy()
    print(f"\nEvents from 2021+: {len(df_filtered)}")

    # Save events for backtest
    import json
    events_for_backtest = [
        {'symbol': row['symbol'], 'date': row['date'], 'timing': 'pre_market'}
        for _, row in df_filtered.iterrows()
    ]

    # Keep only events older than 30 days to avoid recent contamination
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    events_for_backtest = [e for e in events_for_backtest if e['date'] < cutoff]
    print(f"Events with 30+ day lookback (for clean backtest): {len(events_for_backtest)}")

    print("\nSample events:")
    for e in events_for_backtest[:10]:
        print(f"  {e['symbol']} on {e['date']}")

    # ---- BACKTEST ----
    if len(events_for_backtest) >= 15:
        import market_data
        print("\n\nRunning backtest...")
        result = market_data.measure_event_impact(
            event_dates=events_for_backtest,
            benchmark="SPY",
            entry_price="open"  # Analyst notes are pre-market, enter at open
        )

        print("\n=== BACKTEST RESULTS ===")
        for horizon in ['1d', '3d', '5d', '10d', '20d']:
            avg = result.get(f'avg_abnormal_{horizon}')
            pct = result.get(f'positive_rate_abnormal_{horizon}')
            p = result.get(f'wilcoxon_p_abnormal_{horizon}')
            avg_str = f"{avg:.3f}%" if avg is not None else "N/A"
            pct_str = f"{pct:.1f}%" if pct is not None else "N/A"
            p_str = f"{p:.4f}" if p is not None else "N/A"
            print(f"{horizon}: avg={avg_str} pos_rate={pct_str} wilcoxon_p={p_str}")

        print(f"\nEvents measured: {result.get('events_measured')}")
        print(f"Passes multiple testing: {result.get('passes_multiple_testing')}")

        ci_5d = result.get('bootstrap_ci_abnormal_5d')
        print(f"Bootstrap CI 5d: {ci_5d}")

        dq = result.get('data_quality_warning')
        if dq:
            print(f"Data quality warning: {dq}")

        # Print individual impacts for transparency
        print("\n=== INDIVIDUAL EVENT IMPACTS (sample, up to 20) ===")
        individual = result.get('individual_impacts', [])
        for ev in individual[:20]:
            sym = ev.get('symbol', '?')
            dt = ev.get('event_date', '?')
            a1 = ev.get('abnormal_1d')
            a5 = ev.get('abnormal_5d')
            a10 = ev.get('abnormal_10d')
            a1_str = f"{a1:.2f}%" if a1 is not None else "N/A"
            a5_str = f"{a5:.2f}%" if a5 is not None else "N/A"
            a10_str = f"{a10:.2f}%" if a10 is not None else "N/A"
            print(f"  {sym} {dt}: 1d={a1_str} 5d={a5_str} 10d={a10_str}")

    else:
        print(f"\nInsufficient events ({len(events_for_backtest)}) for backtest. Need >= 15.")
else:
    print("No events found.")
