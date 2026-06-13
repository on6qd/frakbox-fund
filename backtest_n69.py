import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_data
import pandas as pd

df = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/cluster_with_regime.csv'))
n69 = df[df['n_insiders'].between(6, 9)].copy()

discovery = n69[n69['cluster_date'].str[:4].isin(['2021', '2022'])]
validation = n69[n69['cluster_date'].str[:4].isin(['2023', '2024', '2025'])]

print(f"Discovery N={len(discovery)}, Validation N={len(validation)}")
print(f"\nDiscovery events:")
for _, row in discovery.iterrows():
    print(f"  {row['ticker']} {row['cluster_date']} n={row['n_insiders']}")
print(f"\nValidation events:")
for _, row in validation.iterrows():
    print(f"  {row['ticker']} {row['cluster_date']} n={row['n_insiders']}")

disc_events = [{"symbol": row['ticker'], "date": row['cluster_date']} for _, row in discovery.iterrows()]
val_events = [{"symbol": row['ticker'], "date": row['cluster_date']} for _, row in validation.iterrows()]

# Discovery backtest
print("\nRunning discovery backtest...")
disc_result = market_data.measure_event_impact(
    event_dates=disc_events,
    benchmark="SPY",
    entry_price="open"
)

print("=== DISCOVERY RESULTS ===")
for h in ['1d', '3d', '5d', '10d', '20d']:
    avg = disc_result.get(f'avg_abnormal_{h}', 'N/A')
    pos = disc_result.get(f'positive_rate_abnormal_{h}', 'N/A')
    p = disc_result.get(f'wilcoxon_p_abnormal_{h}', 'N/A')
    if isinstance(avg, float):
        print(f"  {h}: avg={avg:.3f}%, pos_rate={pos:.1f}%, p={p:.4f}")
    else:
        print(f"  {h}: avg={avg}, pos_rate={pos}, p={p}")
print(f"  passes_multiple_testing: {disc_result.get('passes_multiple_testing')}")
print(f"  events_measured: {disc_result.get('events_measured')}")
print(f"  data_quality_warning: {disc_result.get('data_quality_warning')}")

# Bootstrap CIs
print("\nDiscovery Bootstrap CIs:")
for h in ['1d', '3d', '5d', '10d', '20d']:
    ci = disc_result.get(f'bootstrap_ci_abnormal_{h}', {})
    if ci:
        print(f"  {h}: [{ci.get('ci_lower', 'N/A'):.3f}%, {ci.get('ci_upper', 'N/A'):.3f}%] excludes_zero={ci.get('ci_excludes_zero')}")

# Validation backtest
print("\nRunning validation backtest...")
val_result = market_data.measure_event_impact(
    event_dates=val_events,
    benchmark="SPY",
    entry_price="open"
)

print("\n=== VALIDATION RESULTS ===")
for h in ['1d', '3d', '5d', '10d', '20d']:
    avg = val_result.get(f'avg_abnormal_{h}', 'N/A')
    pos = val_result.get(f'positive_rate_abnormal_{h}', 'N/A')
    p = val_result.get(f'wilcoxon_p_abnormal_{h}', 'N/A')
    if isinstance(avg, float):
        print(f"  {h}: avg={avg:.3f}%, pos_rate={pos:.1f}%, p={p:.4f}")
    else:
        print(f"  {h}: avg={avg}, pos_rate={pos}, p={p}")
print(f"  passes_multiple_testing: {val_result.get('passes_multiple_testing')}")
print(f"  events_measured: {val_result.get('events_measured')}")
print(f"  data_quality_warning: {val_result.get('data_quality_warning')}")

# Bootstrap CIs
print("\nValidation Bootstrap CIs:")
for h in ['1d', '3d', '5d', '10d', '20d']:
    ci = val_result.get(f'bootstrap_ci_abnormal_{h}', {})
    if ci:
        print(f"  {h}: [{ci.get('ci_lower', 'N/A'):.3f}%, {ci.get('ci_upper', 'N/A'):.3f}%] excludes_zero={ci.get('ci_excludes_zero')}")

# Per-event detail
print("\n=== DISCOVERY PER-EVENT DETAIL ===")
for ev in disc_result.get('individual_impacts', []):
    print(f"  {ev['symbol']} {ev['event_date']}: 1d={ev.get('abnormal_1d', 'N/A'):.3f}% 5d={ev.get('abnormal_5d', 'N/A'):.3f}% 10d={ev.get('abnormal_10d', 'N/A'):.3f}%")

print("\n=== VALIDATION PER-EVENT DETAIL ===")
for ev in val_result.get('individual_impacts', []):
    print(f"  {ev['symbol']} {ev['event_date']}: 1d={ev.get('abnormal_1d', 'N/A'):.3f}% 5d={ev.get('abnormal_5d', 'N/A'):.3f}% 10d={ev.get('abnormal_10d', 'N/A'):.3f}%")
