# Market Causal Research

Discover and test any causal influence of the world on the markets. Paper trading on Alpaca ($100K, $5K/experiment).

## Multi-Agent Architecture

The system uses a scan/investigate loop inspired by Karpathy's AutoResearch: high-throughput screening (breadth) alternates with deep investigation (depth).

| Agent | Model | Role |
|---|---|---|
| **Scanner** | Sonnet | High-throughput screening — runs 30-50 quick tests per session, queues hits |
| **Orchestrator** | Opus | Deep investigation — full 6-step hypothesis lifecycle on promising scan hits |
| **Reviewer** | Sonnet | Self-review, post-mortems, confidence scoring, methodology updates |
| **Data Worker** | Haiku | Interprets SEC filings, news, text extraction (rare — most data work is pure Python) |

Session rotation: every 3rd session is a scan (Sonnet, 25 min, cheap), the rest are investigations (Opus, 50 min, deep). Scanner results land in `research_queue` with `category='scan_hit'`; the orchestrator picks up the highest-priority hits.

Data-heavy tasks (backtesting, scanning, price fetching) run as pure Python via `data_tasks.py` — no LLM needed.

## Files

| File | Purpose |
|---|---|
| `db.py` | SQLite — all CRUD for hypotheses, knowledge, queue, task_results |
| `research.py` | Hypothesis lifecycle, knowledge base (uses db.py) |
| `market_data.py` | Prices, event impact, power analysis |
| `causal_tests.py` | Statistical engines for non-event hypotheses (regression, cointegration, Granger, etc.) |
| `data_tasks.py` | CLI dispatcher — runs backtests/scans/regressions without LLM, stores results in SQLite |
| `oos_tracker.py` | Automated OOS observation tracking — register, update daily prices, compute abnormal returns |
| `self_review.py` | Confidence scoring, methodology |
| `research_queue.py` | Task queue, watchlist, handoffs (uses db.py) |
| `trader.py` | Paper trades via Alpaca |
| `trade_loop.py` | Deterministic loop — triggers, stops, reconciliation |
| `run.py` | `--status`, `--review`, `--context` (compressed state) |
| `email_report.py` | HTML email digest (uses db.py) |
| `config.py` | Risk parameters, subagent model config |
| `tools/` | Custom tools (insider scanners, largecap filter, date verifier, yfinance_utils) |
| `tools/timeseries.py` | Unified fetcher for any time series (yfinance, FRED, Fama-French) |

## Storage

SQLite `research.db` (WAL mode). Tables: hypotheses, known_effects, dead_ends, literature, research_queue, event_watchlist, session_priorities, session_handoff, task_results, oos_observations, oos_daily_prices.

## Standard Backtest Workflow

Use `data_tasks.py` instead of calling `measure_event_impact()` directly — it stores full results in SQLite and returns compact summaries:

```bash
# Multi-symbol backtest
python3 data_tasks.py backtest --events '[{"symbol":"AAPL","date":"2024-01-15"}]'

# Single-symbol with multiple dates
python3 data_tasks.py backtest --symbol AAPL --dates '["2024-01-15","2024-04-20"]' --entry-price open

# Verify dates, filter symbols, fetch prices
python3 data_tasks.py verify-date --event "AAPL S&P 500 addition" --expected-date 2024-03-15
python3 data_tasks.py largecap-filter --symbols '["AAPL","MSFT","TINY"]'
python3 data_tasks.py price-history --symbol AAPL --days 90

# Scan EDGAR for insider buying clusters (replaces OpenInsider)
python3 data_tasks.py scan-insiders --days 14 --min-insiders 3 --min-value 50000

# Scan + auto-evaluate GO/NO-GO (preferred — one step)
python3 data_tasks.py scan-insiders-evaluate --days 7 --min-insiders 3 --min-value 50000

# Retrieve full stored result if summary isn't enough
python3 data_tasks.py get-result --id T-abc12345
```

For direct Python use (in custom tools):
```python
from tools.verify_event_date import verify_event_date    # verify dates BEFORE backtesting
from tools.largecap_filter import filter_to_largecap      # filter >500M cap
from tools.yfinance_utils import safe_download, get_close_prices  # ALWAYS use (not raw yf.download)
result = market_data.measure_event_impact(event_dates=[...], entry_price="open")  # open for after-hours
```

## OOS Observation Tracking

Track out-of-sample signal observations with automated daily price tracking:

```bash
# Register a new OOS observation
python3 data_tasks.py oos register --signal-type nt_10k_late_filing_short \
  --symbol AAPL --entry-date 2026-04-15 --hold-days 5 --direction short \
  --threshold -2.5 --hypothesis-id abc123

# Update all active observations (run daily or after market close)
python3 data_tasks.py oos update

# Check status of all active/expired observations
python3 data_tasks.py oos status
python3 data_tasks.py oos status --all  # include completed

# Close an observation with result
python3 data_tasks.py oos close --id OOS-abc12345 --result validated
python3 data_tasks.py oos close --id OOS-abc12345 --result failed
```

The `oos update` command is automatically called by `daily_scanner.py`. Observations reaching their hold period are marked "expired" for human review.

## Hypothesis Classes

The system supports 10 hypothesis classes — not just discrete events:

| Class | Shape | Data Task |
|---|---|---|
| `event` | Discrete event -> price reaction | `backtest` |
| `exposure` | Factor -> asset sensitivity (beta) | `regression --test-type exposure` |
| `lead_lag` | Series A leads B by N days | `regression --test-type lead_lag` |
| `cointegration` | Two series share long-run equilibrium | `cointegration` |
| `regime` | Returns differ across states | `regression --test-type regime` |
| `structural_break` | Relationship reset at date | `regression --test-type structural_break` |
| `threshold` | Indicator crosses level -> reaction | `threshold` |
| `network` | Shock propagates to connected assets | `regression --test-type network` |
| `calendar` | Seasonal/day-of-week anomaly | `calendar` |
| `cross_section` | Factor sorts stocks into quintiles | `regression --test-type cross_section` |

Use `create_hypothesis(hypothesis_class='exposure', spec_json={...})` for non-event classes. The `spec_json` dict holds class-specific fields (driver series, expected beta, lag, etc.).

## Non-Event Data Tasks

```bash
# Regression: test factor exposure (oil -> airline)
python3 data_tasks.py regression --target AAL --factor CL=F --controls SPY --oos-start 2024-01-01

# Lead-lag: does copper lead industrial stocks?
python3 data_tasks.py regression --target XLI --factor HG=F --test-type lead_lag --max-lags 10

# Cointegration: pairs trade candidate?
python3 data_tasks.py cointegration --series-a GLD --series-b GDX --oos-start 2024-01-01

# Threshold: VIX > 30 mean-reversion
#   Auto-runs canonical retest when raw test is significant. Summary includes:
#     - canonical_passes (bool) — PASS = first-close cluster-buffered + SPY-adjusted
#       passes p<0.05 AND |mean|>=1% in BOTH pooled (2010+) AND recent (2020+) samples
#     - canonical_fail_reason — pooled_sample_fails | recency_subset_fails_regime_dependent |
#       sign_flip_between_samples
#   ONLY queue threshold scan hits where canonical_passes=True. If False, record as
#   DEAD_END_CANONICAL_FAILED. See threshold_scan_hit_canonical_retest_rule_2026_04_18.
python3 data_tasks.py threshold --trigger "^VIX" --target SPY --threshold-value 30 --direction above

# Calendar: January effect
python3 data_tasks.py calendar --symbol SPY --pattern monthly --pattern-month 1 --oos-start-year 2020

# Regime: rate hiking -> utility stocks
#   Regime tests are IS-significant by construction (time-varying beta is a textbook
#   stylized fact). ONLY queue a regime scan hit when oos_significant=True. The summary
#   now carries queue_recommendation: if it is "DO_NOT_QUEUE" (set whenever the test is
#   not OOS-validated, with known_dead_end=True), DO NOT queue it — record as
#   DEAD_END_DUPLICATE_OF_AUDITED_FAMILY. See
#   regime_scan_hit_is_only_dead_end_batch_and_guard_2026_06_10.
python3 data_tasks.py regression --target XLU --factor "FRED:FEDFUNDS" --test-type regime

# Network: AAPL shock -> suppliers
python3 data_tasks.py regression --target AAPL --factor AAPL --test-type network --controls AVGO,QCOM,TSM

# Fetch any time series (prices, FRED, Fama-French)
python3 data_tasks.py fetch-series --identifiers "CL=F,AAL,FRED:DGS10" --start 2020-01-01
```

Series identifiers: `AAPL` (equity), `CL=F` (oil future), `^VIX` (index), `EURUSD=X` (FX), `BTC-USD` (crypto), `FRED:DGS10` (FRED series), `FF:Mkt-RF` (Fama-French factor).

## Trade Execution

Set triggers — `trade_loop.py` (every 2 min) handles execution, stops, reconciliation, emails.

```python
import db
db.update_hypothesis_fields(hypothesis_id,
    trigger="next_market_open",        # or "immediate" or "2026-06-07T09:30"
    trigger_position_size=5000,
    trigger_stop_loss_pct=10,
    trigger_take_profit_pct=15,        # optional, default None
)
```

## Data Sources

- **yfinance**: Historical prices (free) — always use `tools/yfinance_utils.py`
- **Tiingo**: Fallback for delisted tickers (needs `TIINGO_API_KEY`)
- **SEC EDGAR**: Form 4 bulk data (free) — use `display_names` not `entity_name`

## API Reference

For full function signatures (`create_hypothesis`, `measure_event_impact`, `complete_hypothesis`, etc.): **read `API_REFERENCE.md`** — only when you need a specific signature.
