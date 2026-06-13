"""Check whether DXY>100 crossings overlap with VIX>30 spikes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.timeseries import get_aligned_series
import pandas as pd
import numpy as np

# Fetch DXY, VIX, SPY, IWM levels
data = get_aligned_series(['DX-Y.NYB', '^VIX', 'SPY', 'IWM'], start='2005-01-01', end='2026-04-15')

# Rename for clarity
data.columns = ['DXY', 'VIX', 'SPY', 'IWM']

# Find DXY>100 crossings (first day above 100 in 30-day cluster)
dxy = data['DXY']
above_100 = dxy > 100
crossing = above_100 & (~above_100.shift(1).fillna(False))

# Cluster within 30 days
crossing_dates = []
last_crossing = None
for dt in crossing[crossing].index:
    if last_crossing is None or (dt - last_crossing).days > 30:
        crossing_dates.append(dt)
        last_crossing = dt

print(f"=== DXY > 100 CROSSINGS (clustered 30d): {len(crossing_dates)} ===")
print()

# For each crossing, check VIX level and 20d SPY/IWM returns
print(f"{'Date':<12} {'DXY':>6} {'VIX':>6} {'VIX>25?':>8} {'VIX>30?':>8} {'SPY_20d':>8} {'IWM_20d':>8}")
print("-" * 70)

vix_above_25_count = 0
vix_above_30_count = 0
spy_returns = []
iwm_returns = []

for dt in crossing_dates:
    pos = data.index.get_loc(dt)
    if pos + 20 >= len(data):
        continue
    dxy_val = data['DXY'].iloc[pos]
    vix_val = data['VIX'].iloc[pos]
    spy_ret = (data['SPY'].iloc[pos + 20] / data['SPY'].iloc[pos] - 1) * 100
    iwm_ret = (data['IWM'].iloc[pos + 20] / data['IWM'].iloc[pos] - 1) * 100
    
    vix_25 = vix_val > 25
    vix_30 = vix_val > 30
    if vix_25: vix_above_25_count += 1
    if vix_30: vix_above_30_count += 1
    spy_returns.append(spy_ret)
    iwm_returns.append(iwm_ret)
    
    print(f"{dt.strftime('%Y-%m-%d'):<12} {dxy_val:6.1f} {vix_val:6.1f} {'YES' if vix_25 else 'no':>8} {'YES' if vix_30 else 'no':>8} {spy_ret:+7.2f}% {iwm_ret:+7.2f}%")

print()
print(f"Total crossings: {len(crossing_dates)}")
print(f"VIX > 25 on crossing day: {vix_above_25_count}/{len(crossing_dates)} ({vix_above_25_count/len(crossing_dates)*100:.0f}%)")
print(f"VIX > 30 on crossing day: {vix_above_30_count}/{len(crossing_dates)} ({vix_above_30_count/len(crossing_dates)*100:.0f}%)")
print(f"Avg SPY 20d return: {np.mean(spy_returns):+.2f}%")
print(f"Avg IWM 20d return: {np.mean(iwm_returns):+.2f}%")

# Split: VIX>25 vs VIX<=25 on crossing day
print()
print("=== CONDITIONAL ON VIX ===")
vix_high_spy = []
vix_low_spy = []
vix_high_iwm = []
vix_low_iwm = []
for i, dt in enumerate(crossing_dates):
    if i >= len(spy_returns): break
    pos = data.index.get_loc(dt)
    vix_val = data['VIX'].iloc[pos]
    if vix_val > 25:
        vix_high_spy.append(spy_returns[i])
        vix_high_iwm.append(iwm_returns[i])
    else:
        vix_low_spy.append(spy_returns[i])
        vix_low_iwm.append(iwm_returns[i])

if vix_high_spy:
    print(f"DXY>100 AND VIX>25: n={len(vix_high_spy)}, SPY={np.mean(vix_high_spy):+.2f}%, IWM={np.mean(vix_high_iwm):+.2f}%")
if vix_low_spy:
    print(f"DXY>100 AND VIX<=25: n={len(vix_low_spy)}, SPY={np.mean(vix_low_spy):+.2f}%, IWM={np.mean(vix_low_iwm):+.2f}%")

# Check reverse: VIX>30 events that DON'T coincide with DXY>100
print()
print("=== VIX > 30 SPIKES (for comparison) ===")
vix = data['VIX']
vix_spike = (vix > 30) & (~(vix.shift(1) > 30).fillna(False))
vix_spike_dates = []
last_spike = None
for dt in vix_spike[vix_spike].index:
    if last_spike is None or (dt - last_spike).days > 30:
        vix_spike_dates.append(dt)
        last_spike = dt

vix_with_dxy100 = 0
for dt in vix_spike_dates:
    pos = data.index.get_loc(dt)
    if data['DXY'].iloc[pos] > 100:
        vix_with_dxy100 += 1

print(f"Total VIX>30 spikes: {len(vix_spike_dates)}")
print(f"VIX>30 WITH DXY>100: {vix_with_dxy100} ({vix_with_dxy100/len(vix_spike_dates)*100:.0f}%)")
print(f"VIX>30 WITHOUT DXY>100: {len(vix_spike_dates) - vix_with_dxy100} ({(len(vix_spike_dates)-vix_with_dxy100)/len(vix_spike_dates)*100:.0f}%)")

