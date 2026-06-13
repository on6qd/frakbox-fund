"""
Quick backtest: Major bank stocks pre-earnings 5d drift
Context: Q1 2026 earnings starting April 11 (JPM), 14 (WFC, C, GS), 15 (BAC, MS)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.yfinance_utils import safe_download
import pandas as pd
import numpy as np

bank_q1_earnings = {
    'JPM': ['2018-04-13','2019-04-12','2020-04-14','2021-04-14','2022-04-13','2023-04-14','2024-04-12','2025-04-11'],
    'BAC': ['2018-04-16','2019-04-16','2020-04-15','2021-04-15','2022-04-18','2023-04-18','2024-04-16','2025-04-15'],
    'C':   ['2018-04-13','2019-04-15','2020-04-15','2021-04-14','2022-04-14','2023-04-14','2024-04-12','2025-04-14'],
    'WFC': ['2018-04-13','2019-04-12','2020-04-14','2021-04-14','2022-04-14','2023-04-14','2024-04-12','2025-04-11'],
    'GS':  ['2018-04-17','2019-04-15','2020-04-15','2021-04-14','2022-04-14','2023-04-18','2024-04-15','2025-04-14'],
}

spy_data = safe_download('SPY', start='2018-01-01', end='2026-04-02')
spy_data.index = pd.to_datetime(spy_data.index).normalize()

results = []
for bank, dates in bank_q1_earnings.items():
    bank_data = safe_download(bank, start='2018-01-01', end='2026-04-02')
    if bank_data is None:
        continue
    bank_data.index = pd.to_datetime(bank_data.index).normalize()
    
    for date_str in dates:
        target = pd.Timestamp(date_str)
        diffs = abs(bank_data.index - target)
        idx = diffs.argmin()
        if idx < 7:
            continue
        
        entry_idx = idx - 5
        exit_idx = idx
        
        entry_date = bank_data.index[entry_idx]
        exit_date = bank_data.index[exit_idx]
        
        entry_bank = bank_data['Close'].iloc[entry_idx]
        exit_bank = bank_data['Close'].iloc[exit_idx]
        bank_return = (exit_bank - entry_bank) / entry_bank * 100
        
        spy_entry_idx = abs(spy_data.index - entry_date).argmin()
        spy_exit_idx = abs(spy_data.index - exit_date).argmin()
        entry_spy = spy_data['Close'].iloc[spy_entry_idx]
        exit_spy = spy_data['Close'].iloc[spy_exit_idx]
        spy_return = (exit_spy - entry_spy) / entry_spy * 100
        
        abnormal = bank_return - spy_return
        results.append({'bank': bank, 'date': date_str, 'bank_ret': bank_return,
                        'spy_ret': spy_return, 'abnormal': abnormal})

df = pd.DataFrame(results)
print(f'N={len(df)} bank Q1 pre-earnings 5d windows (2018-2025)')
print(f'Avg abnormal: {df["abnormal"].mean():.2f}%  std={df["abnormal"].std():.2f}%')
print(f'Direction>0.5%: {(df["abnormal"] > 0.5).mean():.1%}')
from scipy import stats
t, p = stats.ttest_1samp(df['abnormal'], 0)
print(f't={t:.2f} p={p:.3f}')
print()
print('By year (date[:4]):')
df['year'] = df['date'].str[:4]
for yr, g in df.groupby('year'):
    print(f'  {yr}: avg={g["abnormal"].mean():.2f}% n={len(g)}')
