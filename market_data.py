"""
Market data utilities — fetch historical prices and measure event impacts.

Uses yfinance for historical data, with Tiingo as fallback for delisted tickers.

IMPORTANT: All impact measurements compute ABNORMAL returns — the stock's return
minus what the benchmark (SPY) did over the same period. This isolates the event
effect from broad market moves.
"""

import math
import sys
import pandas as pd
import requests
import yfinance as yf
from datetime import datetime, timedelta
from scipy.stats import ttest_1samp, wilcoxon, norm, skew as scipy_skew

from config import TIINGO_API_KEY


# --- Tiingo fallback for delisted tickers ---

# In-process daily counter still tracked so callers can inspect it, but the
# primary rate-limit defence is now the disk cache in tools/tiingo_cache.py.
_tiingo_request_count = 0
_tiingo_request_date = None

try:
    from tools.tiingo_cache import get_tiingo_cached as _get_tiingo_cached
    _TIINGO_CACHE_AVAILABLE = True
except ImportError:
    _TIINGO_CACHE_AVAILABLE = False


def _fetch_history_tiingo(symbol, start_str, end_str):
    """
    Fetch historical OHLCV from Tiingo. Used as fallback when yfinance returns
    empty data (common for delisted tickers).

    Results are transparently cached on disk via tools/tiingo_cache.py
    (~/.tiingo_cache/) to avoid 429 rate-limit errors during rapid backtests.
    TTL: 30 days for real data, 7 days for empty/missing tickers.

    Returns a DataFrame with the same structure as yfinance output (DatetimeIndex,
    Open/High/Low/Close/Volume columns), or an empty DataFrame on failure.
    """
    global _tiingo_request_count, _tiingo_request_date

    if not TIINGO_API_KEY:
        return pd.DataFrame()

    # Delegate to caching layer when available
    if _TIINGO_CACHE_AVAILABLE:
        # Update in-process counter only on actual network calls.
        # The cache prints HIT/MISS to stderr itself.
        today = datetime.now().date()
        if _tiingo_request_date != today:
            _tiingo_request_count = 0
            _tiingo_request_date = today
        if _tiingo_request_count >= 490:
            print(f"[tiingo] Rate limit approaching ({_tiingo_request_count}/500), skipping", file=sys.stderr)
            return pd.DataFrame()

        df = _get_tiingo_cached(symbol, start_str, end_str)
        # Count as a request only when a real network call was made (cache miss).
        # We detect this heuristically: if the cache printed MISS it fetched;
        # we always increment to stay conservative (worst case: over-counts hits).
        _tiingo_request_count += 1
        return df

    # --- Fallback: direct fetch (no cache) ---
    today = datetime.now().date()
    if _tiingo_request_date != today:
        _tiingo_request_count = 0
        _tiingo_request_date = today
    if _tiingo_request_count >= 490:
        print(f"[tiingo] Rate limit approaching ({_tiingo_request_count}/500), skipping", file=sys.stderr)
        return pd.DataFrame()

    # Tiingo uses lowercase tickers, and "-" instead of "." (e.g., BRK-B not BRK.B)
    tiingo_symbol = symbol.upper().replace(".", "-")

    try:
        url = f"https://api.tiingo.com/tiingo/daily/{tiingo_symbol}/prices"
        resp = requests.get(url, params={
            "startDate": start_str,
            "endDate": end_str,
            "token": TIINGO_API_KEY,
        }, timeout=15)
        _tiingo_request_count += 1

        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()

        data = resp.json()
        if not data:
            return pd.DataFrame()

        # Build DataFrame matching yfinance structure
        rows = []
        for d in data:
            dt = pd.Timestamp(d["date"]).tz_localize(None)
            rows.append({
                "Date": dt,
                "Open": d.get("adjOpen", d.get("open", 0)),
                "High": d.get("adjHigh", d.get("high", 0)),
                "Low": d.get("adjLow", d.get("low", 0)),
                "Close": d.get("adjClose", d.get("close", 0)),
                "Volume": d.get("adjVolume", d.get("volume", 0)),
            })

        df = pd.DataFrame(rows).set_index("Date")
        print(f"[tiingo] Fetched {len(df)} days for {symbol} (delisted ticker fallback)", file=sys.stderr)
        return df

    except Exception as e:
        print(f"[tiingo] Error fetching {symbol}: {e}", file=sys.stderr)
        return pd.DataFrame()


def _fetch_stock_data(symbol, start_str, end_str):
    """Fetch stock data from yfinance, falling back to Tiingo for delisted tickers."""
    try:
        # Use yf.download (more reliable than Ticker.history which can return None)
        from tools.yfinance_utils import safe_download
        df = safe_download(symbol, start=start_str, end=end_str)
    except (ValueError, TypeError, Exception) as e:
        print(f"[yfinance] safe_download failed for {symbol}: {e}", file=sys.stderr)
        df = pd.DataFrame()
    if df.empty:
        df = _fetch_history_tiingo(symbol, start_str, end_str)
    return df


# --- Transaction cost estimation ---

from config import EVENT_COST_DEFAULTS as _EVENT_COST_DEFAULTS
from config import DEFAULT_EVENT_COST_PCT as _DEFAULT_COST_PCT


def estimate_transaction_cost(event_type=None, avg_daily_volume=None,
                               event_day_volume=None):
    """
    Estimate round-trip transaction cost including spread and market impact.

    When volume data is available:
        cost = 2 * (base_spread + impact_factor / sqrt(volume_ratio))
        where volume_ratio = event_day_volume / avg_daily_volume

    When volume data is unavailable, uses event-type-specific defaults.

    Returns:
        dict with 'round_trip_pct', 'spread_component', 'impact_component',
        'model_used' ("volume_based" or "event_type_default")
    """
    if avg_daily_volume and event_day_volume and avg_daily_volume > 0:
        base_spread = 0.05  # % per side
        volume_ratio = event_day_volume / avg_daily_volume
        impact = 0.15 / math.sqrt(volume_ratio) if volume_ratio > 0 else 0.15
        round_trip = 2 * (base_spread + impact)
        round_trip = max(0.05, min(1.0, round_trip))
        return {
            "round_trip_pct": round(round_trip, 3),
            "spread_component": round(2 * base_spread, 3),
            "impact_component": round(2 * impact, 3),
            "volume_ratio": round(volume_ratio, 2),
            "model_used": "volume_based",
        }

    cost = _EVENT_COST_DEFAULTS.get(event_type, _DEFAULT_COST_PCT)
    return {
        "round_trip_pct": cost,
        "spread_component": None,
        "impact_component": None,
        "volume_ratio": None,
        "model_used": "event_type_default",
    }


# Approximate weights of large constituents in sector ETFs.
# Used to warn about circular reference in sector-adjusted returns.
# Updated periodically — does not need to be exact.
SECTOR_ETF_MAJOR_CONSTITUENTS = {
    "XLK": {"AAPL": 0.22, "MSFT": 0.21, "NVDA": 0.06},
    "XLV": {"LLY": 0.12, "UNH": 0.10, "JNJ": 0.07, "ABBV": 0.07},
    "XLF": {"BRK-B": 0.14, "JPM": 0.10, "V": 0.08, "MA": 0.07},
    "XLE": {"XOM": 0.23, "CVX": 0.17},
    "XLY": {"AMZN": 0.22, "TSLA": 0.15, "HD": 0.09},
    "XLC": {"META": 0.22, "GOOGL": 0.12, "GOOG": 0.10},
    "XLI": {"GE": 0.05, "CAT": 0.05, "RTX": 0.05},
    "XLP": {"PG": 0.15, "COST": 0.11, "WMT": 0.10, "KO": 0.10},
    "XLU": {"NEE": 0.15, "SO": 0.08, "DUK": 0.07},
    "XLRE": {"PLD": 0.13, "AMT": 0.10, "EQIX": 0.08},
    "XLB": {"LIN": 0.18, "SHW": 0.08, "FCX": 0.07},
}


def get_price_history(symbol, days=90):
    """
    Fetch daily OHLCV data. Most recent last.
    Falls back to Tiingo for delisted tickers.
    """
    end = datetime.now()
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    df = _fetch_stock_data(symbol, start_str, end_str)

    if df.empty:
        return []

    prices = []
    for date, row in df.iterrows():
        prices.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": round(row["Open"], 2),
            "high": round(row["High"], 2),
            "low": round(row["Low"], 2),
            "close": round(row["Close"], 2),
            "volume": int(row["Volume"]),
        })

    return prices


def get_price_around_date(symbol, event_date, days_before=5, days_after=20,
                          benchmark="SPY", event_timing="unknown",
                          entry_price="close"):
    """
    Fetch prices around a specific event date and compute abnormal returns.

    Returns raw returns, benchmark returns, and abnormal returns (raw - benchmark)
    at 1d, 3d, 5d, 10d, 20d horizons.

    Args:
        event_timing: "pre_market", "intraday", "after_hours", or "unknown"
            - pre_market/intraday/unknown: reference price = close of day BEFORE event
            - after_hours: reference price = close of event day (before the event moved it)
              Post-event returns start from next trading day.
        entry_price: "close" (default) or "open". When "open", uses next-day open as
            the entry price instead of prior close. More realistic for after-hours events
            since you can't actually buy at close.
    """
    event_dt = datetime.strptime(event_date, "%Y-%m-%d")
    start = event_dt - timedelta(days=days_before + 10)
    end = event_dt + timedelta(days=days_after + 10)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    # Fetch stock data (yfinance with Tiingo fallback)
    stock_df = _fetch_stock_data(symbol, start_str, end_str)
    if stock_df.empty:
        return {"error": f"No data for {symbol} around {event_date} (tried yfinance and Tiingo)"}

    # benchmark=None -> raw-returns mode (abnormal == raw). Used for non-equity
    # targets (Treasuries/commodities/FX/crypto) where SPY-adjustment is invalid.
    # See tools/asset_class.resolve_event_benchmark.
    if benchmark is None:
        bench_df = pd.DataFrame()
    else:
        bench_df = _fetch_stock_data(benchmark, start_str, end_str) if symbol != benchmark else stock_df

    # Build date-indexed lookups (close and open)
    stock_by_date = {d.strftime("%Y-%m-%d"): round(row["Close"], 2) for d, row in stock_df.iterrows()}
    stock_open_by_date = {d.strftime("%Y-%m-%d"): round(row["Open"], 2) for d, row in stock_df.iterrows()}
    stock_volume_by_date = {d.strftime("%Y-%m-%d"): int(row["Volume"]) for d, row in stock_df.iterrows()}
    bench_by_date = {d.strftime("%Y-%m-%d"): round(row["Close"], 2) for d, row in bench_df.iterrows()}

    # Split into pre and post based on event timing
    stock_dates = sorted(stock_by_date.keys())

    if event_timing == "after_hours":
        pre_dates = [d for d in stock_dates if d <= event_date]
        post_dates = [d for d in stock_dates if d > event_date]
    else:
        pre_dates = [d for d in stock_dates if d < event_date]
        post_dates = [d for d in stock_dates if d >= event_date]

    if not pre_dates or not post_dates:
        return {"error": "Not enough data around event date"}

    # Determine entry price
    if entry_price == "open" and post_dates:
        # Use next trading day's open — realistic entry for after-hours events
        ref_price = stock_open_by_date.get(post_dates[0])
        if ref_price is None or ref_price <= 0:
            ref_price = stock_by_date[pre_dates[-1]]
            entry_price_used = "close_fallback"
        else:
            entry_price_used = "open"
    else:
        ref_price = stock_by_date[pre_dates[-1]]
        entry_price_used = "close"

    pre_event_bench = bench_by_date.get(pre_dates[-1])

    impact = {
        "symbol": symbol,
        "benchmark": benchmark,
        "event_date": event_date,
        "event_timing": event_timing,
        "pre_event_price": ref_price,
        "entry_price_type": entry_price_used,
    }

    # Volume data for transaction cost estimation
    pre_volumes = [stock_volume_by_date[d] for d in pre_dates[-20:] if d in stock_volume_by_date]
    if pre_volumes:
        avg_volume = sum(pre_volumes) / len(pre_volumes)
        event_day = post_dates[0] if post_dates else None
        event_volume = stock_volume_by_date.get(event_day, 0)
        impact["avg_daily_volume"] = int(avg_volume)
        impact["event_day_volume"] = event_volume
        if avg_volume > 0 and event_volume > 0:
            impact["volume_ratio"] = round(event_volume / avg_volume, 2)

    for horizon_label, horizon_idx in [("1d", 0), ("3d", 2), ("5d", 4), ("10d", 9), ("20d", 19)]:
        if len(post_dates) > horizon_idx:
            target_date = post_dates[horizon_idx]

            # Raw return from entry price to close at horizon
            post_price = stock_by_date[target_date]
            raw_return = ((post_price - ref_price) / ref_price) * 100

            # Benchmark return
            bench_return = 0
            if pre_event_bench and target_date in bench_by_date:
                bench_post = bench_by_date[target_date]
                bench_return = ((bench_post - pre_event_bench) / pre_event_bench) * 100

            abnormal_return = raw_return - bench_return

            impact[f"raw_{horizon_label}"] = round(raw_return, 2)
            impact[f"bench_{horizon_label}"] = round(bench_return, 2)
            impact[f"abnormal_{horizon_label}"] = round(abnormal_return, 2)

    impact["pre_prices"] = [{"date": d, "close": stock_by_date[d]} for d in pre_dates[-days_before:]]
    impact["post_prices"] = [{"date": d, "close": stock_by_date[d]} for d in post_dates[:days_after]]

    return impact


def measure_event_impact(symbol=None, event_dates=None, benchmark="SPY", sector_etf=None,
                         event_timing="unknown", known_events=None, regime_filter=None,
                         entry_price="close", estimate_costs=False, event_type=None,
                         check_factors=True, check_seasonal=True):
    """
    Measure abnormal price impact across multiple instances of the same event type.

    Supports two calling conventions:
    1. Single-symbol: measure_event_impact("AAPL", ["2024-01-15", "2024-04-20"])
    2. Multi-symbol:  measure_event_impact(event_dates=[
           {"symbol": "AAPL", "date": "2024-01-15"},
           {"symbol": "MSFT", "date": "2024-04-20", "timing": "after_hours"},
       ])

    Args:
        symbol: Ticker to measure (None for multi-symbol mode)
        event_dates: List of date strings, or list of dicts with symbol/date/timing keys
        benchmark: Market benchmark (default SPY)
        sector_etf: Optional sector ETF (e.g., XLV for healthcare, XLF for financials)
        event_timing: Default timing if event_dates are strings (not dicts)
        known_events: Optional list of {"symbol", "date"} dicts for contamination checking
        regime_filter: VIX regime filter string ("calm", "elevated", "crisis") or dict
                    for multi-filter: {"vix": "calm", "yield_curve": "inverted", "rate": "hiking"}
        entry_price: "close" (default) or "open". "open" uses next-day open as entry —
                    more realistic for after-hours events.
        estimate_costs: If True, estimate per-event transaction costs using volume data
        event_type: Event type string for cost estimation defaults (e.g., "sp500_index_addition")
    """
    if event_dates is None:
        return {"error": "event_dates is required"}

    impacts = []
    errors = []
    symbols_seen = set()
    regime_filtered_count = 0

    # Normalize regime_filter to dict format (backward compatible)
    regime_filter_dict = None
    if isinstance(regime_filter, str):
        regime_filter_dict = {"vix": regime_filter}
    elif isinstance(regime_filter, dict):
        regime_filter_dict = regime_filter

    # Pre-fetch filter data for all active regime filters
    vix_by_date = {}
    vix_thresholds = {"calm": (0, 20), "elevated": (20, 30), "crisis": (30, 999)}
    yc_spread_by_date = {}
    yc_thresholds = {"normal": (0.5, 999), "flat": (-0.5, 0.5), "inverted": (-999, -0.5)}
    rate_regime_by_date = {}

    if regime_filter_dict:
        # Collect all event dates for range calculation
        all_dates = []
        for de in event_dates:
            d = de["date"] if isinstance(de, dict) else de
            all_dates.append(d)

        if not all_dates:
            regime_filter_dict = None

    if regime_filter_dict and all_dates:
        min_date = min(all_dates)
        max_date = max(all_dates)
        range_start = (datetime.strptime(min_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
        range_end = (datetime.strptime(max_date, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d")

        # VIX filter (existing logic)
        if "vix" in regime_filter_dict:
            if regime_filter_dict["vix"] not in vix_thresholds:
                return {"error": f"Invalid vix regime '{regime_filter_dict['vix']}'. Use 'calm', 'elevated', or 'crisis'."}
            vix_df = yf.Ticker("^VIX").history(start=range_start, end=range_end, interval="1d")
            if not vix_df.empty:
                vix_by_date = {d.strftime("%Y-%m-%d"): round(row["Close"], 2) for d, row in vix_df.iterrows()}

        # Yield curve filter (requires tools/fred_data.py)
        if "yield_curve" in regime_filter_dict:
            if regime_filter_dict["yield_curve"] not in yc_thresholds:
                return {"error": f"Invalid yield_curve regime '{regime_filter_dict['yield_curve']}'. Use 'normal', 'flat', or 'inverted'."}
            try:
                from tools.fred_data import get_yield_curve_spread
                spread = get_yield_curve_spread(range_start, range_end)
                if not spread.empty:
                    yc_spread_by_date = {d.strftime("%Y-%m-%d"): round(v, 4) for d, v in spread.items()}
            except (ImportError, Exception) as e:
                print(f"[regime_filter] yield_curve filter unavailable: {e}", file=sys.stderr)

        # Rate regime filter (requires tools/fred_data.py)
        if "rate" in regime_filter_dict:
            if regime_filter_dict["rate"] not in ("hiking", "cutting", "holding"):
                return {"error": f"Invalid rate regime '{regime_filter_dict['rate']}'. Use 'hiking', 'cutting', or 'holding'."}
            try:
                from tools.fred_data import get_rate_regime
                # Compute rate regime for each unique event date
                for d in set(all_dates):
                    rate_regime_by_date[d] = get_rate_regime(d)
            except (ImportError, Exception) as e:
                print(f"[regime_filter] rate filter unavailable: {e}", file=sys.stderr)

    for date_entry in event_dates:
        # Resolve symbol and date from the entry
        if isinstance(date_entry, dict):
            event_symbol = date_entry.get("symbol", symbol)
            date = date_entry["date"]
            timing = date_entry.get("timing", event_timing)
        elif isinstance(date_entry, (list, tuple)) and len(date_entry) == 2:
            event_symbol, date = date_entry
            timing = event_timing
        else:
            event_symbol = symbol
            date = date_entry
            timing = event_timing

        if event_symbol is None:
            errors.append({"date": date, "error": "No symbol specified"})
            continue

        # Regime filter: skip events outside ALL specified regime conditions
        if regime_filter_dict:
            skip_event = False

            # VIX check
            if "vix" in regime_filter_dict and vix_by_date:
                vix_val = vix_by_date.get(date)
                if vix_val is None:
                    prior_dates = [d for d in sorted(vix_by_date.keys()) if d <= date]
                    if prior_dates:
                        vix_val = vix_by_date[prior_dates[-1]]
                if vix_val is not None:
                    lo, hi = vix_thresholds[regime_filter_dict["vix"]]
                    if not (lo <= vix_val < hi):
                        skip_event = True

            # Yield curve check
            if "yield_curve" in regime_filter_dict and yc_spread_by_date and not skip_event:
                spread_val = yc_spread_by_date.get(date)
                if spread_val is None:
                    prior_dates = [d for d in sorted(yc_spread_by_date.keys()) if d <= date]
                    if prior_dates:
                        spread_val = yc_spread_by_date[prior_dates[-1]]
                if spread_val is not None:
                    lo, hi = yc_thresholds[regime_filter_dict["yield_curve"]]
                    if not (lo <= spread_val <= hi):
                        skip_event = True

            # Rate regime check
            if "rate" in regime_filter_dict and rate_regime_by_date and not skip_event:
                rate = rate_regime_by_date.get(date)
                if rate is not None and rate != regime_filter_dict["rate"]:
                    skip_event = True

            if skip_event:
                regime_filtered_count += 1
                continue

        symbols_seen.add(event_symbol)

        # Per-event entry_price override
        evt_entry = date_entry.get("entry_price", entry_price) if isinstance(date_entry, dict) else entry_price

        try:
            impact = get_price_around_date(event_symbol, date, benchmark=benchmark,
                                           event_timing=timing, entry_price=evt_entry)
            if "error" not in impact:
                # Transaction cost estimation
                if estimate_costs:
                    cost = estimate_transaction_cost(
                        event_type=event_type,
                        avg_daily_volume=impact.get("avg_daily_volume"),
                        event_day_volume=impact.get("event_day_volume"),
                    )
                    impact["estimated_cost"] = cost
                # Sector-adjusted returns with circular reference correction
                if sector_etf and sector_etf != event_symbol:
                    sector_impact = get_price_around_date(sector_etf, date,
                                                         benchmark=benchmark,
                                                         event_timing=timing)
                    if "error" not in sector_impact:
                        # Check for circular reference: is this stock a major constituent?
                        weight = SECTOR_ETF_MAJOR_CONSTITUENTS.get(sector_etf, {}).get(event_symbol, 0)
                        for h in ["1d", "3d", "5d", "10d", "20d"]:
                            raw_key = f"raw_{h}"
                            if raw_key in impact and raw_key in sector_impact:
                                sector_return = sector_impact[raw_key]
                                if weight > 0.05:
                                    # Correct for circular reference:
                                    # sector_return includes the stock's own move
                                    # Remove the stock's contribution to get a clean sector return
                                    stock_return = impact[raw_key]
                                    adjusted_sector = (sector_return - weight * stock_return) / (1 - weight)
                                    impact[f"sector_adj_{h}"] = round(
                                        impact[raw_key] - adjusted_sector, 2
                                    )
                                else:
                                    impact[f"sector_adj_{h}"] = round(
                                        impact[raw_key] - sector_return, 2
                                    )
                        if weight > 0.05:
                            impact["sector_adjustment_note"] = (
                                f"{event_symbol} is ~{weight:.0%} of {sector_etf}. "
                                f"Sector return adjusted to remove {event_symbol}'s contribution."
                            )
                impacts.append(impact)
            else:
                errors.append({"date": date, "symbol": event_symbol, "error": impact["error"]})
        except Exception as e:
            errors.append({"date": date, "symbol": event_symbol, "error": str(e)})
            continue

    if not impacts:
        return {"error": "Could not measure any events", "attempted": len(event_dates),
                "errors": errors}

    # Data quality check (exclude intentionally regime-filtered events from drop rate)
    eligible_events = len(event_dates) - regime_filtered_count
    drop_rate = (eligible_events - len(impacts)) / eligible_events * 100 if eligible_events > 0 else 0
    data_quality_warning = None
    if drop_rate > 30:
        data_quality_warning = (
            f"WARNING: {drop_rate:.0f}% of eligible events failed to produce data "
            f"({len(impacts)}/{eligible_events} succeeded"
            f"{f', {regime_filtered_count} excluded by regime filter' if regime_filtered_count else ''}). "
            f"Results may be unreliable — investigate data quality before forming hypotheses."
        )

    stats = {
        "symbol": symbol if symbol else None,
        "symbols": sorted(symbols_seen),
        "multi_symbol": len(symbols_seen) > 1,
        "benchmark": benchmark,
        "sector_etf": sector_etf,
        "event_timing": event_timing,
        "regime_filter": regime_filter_dict,
        "regime_filtered_count": regime_filtered_count if regime_filter_dict else None,
        "events_measured": len(impacts),
        "events_attempted": len(event_dates),
        "events_failed": len(errors),
        "drop_rate_pct": round(drop_rate, 1),
        "data_quality_warning": data_quality_warning,
        "errors": errors if errors else None,
        "individual_impacts": impacts,
    }

    # Aggregate both raw and abnormal returns
    for return_type in ["raw", "abnormal", "sector_adj"]:
        for horizon in ["1d", "3d", "5d", "10d", "20d"]:
            key = f"{return_type}_{horizon}"
            returns = [i[key] for i in impacts if key in i]
            if returns:
                positive = sum(1 for r in returns if r > 0)
                stats[f"avg_{key}"] = round(sum(returns) / len(returns), 2)
                sorted_returns = sorted(returns)
                mid = len(sorted_returns) // 2
                if len(sorted_returns) % 2 == 0:
                    stats[f"median_{key}"] = round((sorted_returns[mid - 1] + sorted_returns[mid]) / 2, 2)
                else:
                    stats[f"median_{key}"] = round(sorted_returns[mid], 2)
                stats[f"positive_rate_{key}"] = round(positive / len(returns) * 100, 1)
                stats[f"min_{key}"] = round(min(returns), 2)
                stats[f"max_{key}"] = round(max(returns), 2)
                stats[f"stdev_{key}"] = round(_stdev(returns), 2)

    # Aggregate transaction cost estimates
    if estimate_costs:
        costs = [i["estimated_cost"]["round_trip_pct"] for i in impacts if "estimated_cost" in i]
        if costs:
            stats["avg_estimated_cost_pct"] = round(sum(costs) / len(costs), 3)
            stats["cost_model_breakdown"] = {
                "volume_based": sum(1 for i in impacts if i.get("estimated_cost", {}).get("model_used") == "volume_based"),
                "event_type_default": sum(1 for i in impacts if i.get("estimated_cost", {}).get("model_used") == "event_type_default"),
            }

    # Entry price mode
    stats["entry_price_mode"] = entry_price

    # Statistical significance for abnormal returns using scipy
    significant_horizons = []
    for horizon in ["1d", "3d", "5d", "10d", "20d"]:
        key = f"abnormal_{horizon}"
        returns = [i[key] for i in impacts if key in i]
        if len(returns) >= 3:
            t_stat, p_value = ttest_1samp(returns, 0)
            stats[f"t_stat_{key}"] = round(float(t_stat), 3)
            stats[f"p_value_{key}"] = round(float(p_value), 4)
            stats[f"significant_{key}"] = p_value < 0.05
            if p_value < 0.05:
                significant_horizons.append(horizon)

            # Skewness warning — t-test unreliable on highly skewed small samples
            skewness = float(scipy_skew(returns))
            stats[f"skewness_{key}"] = round(skewness, 2)
            if abs(skewness) > 1.0:
                stats[f"skewness_warning_{key}"] = (
                    f"High skewness ({skewness:.2f}) — t-test may be unreliable. "
                    f"Check Wilcoxon p-value for robustness."
                )

            # Wilcoxon signed-rank test (non-parametric robustness check)
            nonzero_returns = [r for r in returns if r != 0]
            if len(nonzero_returns) >= 6:
                try:
                    _, wilcoxon_p = wilcoxon(nonzero_returns)
                    stats[f"wilcoxon_p_{key}"] = round(float(wilcoxon_p), 4)
                    # Flag divergence between t-test and Wilcoxon
                    t_sig = p_value < 0.05
                    w_sig = wilcoxon_p < 0.05
                    if t_sig and not w_sig:
                        stats[f"robustness_warning_{key}"] = (
                            f"t-test significant (p={p_value:.4f}) but Wilcoxon not "
                            f"(p={wilcoxon_p:.4f}). Significance may be driven by outliers."
                        )
                except ValueError:
                    pass  # Wilcoxon can fail with identical values

    # Bootstrap confidence intervals for abnormal returns
    for horizon in ["1d", "3d", "5d", "10d", "20d"]:
        key = f"abnormal_{horizon}"
        returns = [i[key] for i in impacts if key in i]
        if len(returns) >= 3:
            ci = bootstrap_ci(returns, n_bootstrap=10000, ci=95)
            stats[f"bootstrap_ci_{key}"] = ci

    # Multiple testing correction summary
    stats["significant_horizons"] = significant_horizons
    stats["num_significant_horizons"] = len(significant_horizons)

    if len(significant_horizons) >= 2:
        stats["passes_multiple_testing"] = True
        stats["multiple_testing_note"] = (
            f"{len(significant_horizons)} horizons significant at p<0.05 — passes multi-horizon check."
        )
    elif len(significant_horizons) == 1:
        h = significant_horizons[0]
        p = stats.get(f"p_value_abnormal_{h}", 1.0)
        if p < 0.01:
            stats["passes_multiple_testing"] = True
            stats["multiple_testing_note"] = (
                f"1 horizon ({h}) significant at p<0.01 — passes Bonferroni-adjusted threshold."
            )
        else:
            stats["passes_multiple_testing"] = False
            stats["multiple_testing_note"] = (
                f"Only 1 horizon ({h}) significant at p={p:.4f}, which does not survive "
                f"multiple testing correction (need p<0.01 for single-horizon or 2+ horizons at p<0.05). "
                f"This may be a false positive."
            )
    else:
        stats["passes_multiple_testing"] = False
        stats["multiple_testing_note"] = "No horizons reached significance at p<0.05."

    # Power analysis: given the observed effect and variance, how many samples do we need?
    for horizon in ["1d", "3d", "5d", "10d", "20d"]:
        key = f"abnormal_{horizon}"
        avg_key = f"avg_{key}"
        stdev_key = f"stdev_{key}"
        if avg_key in stats and stdev_key in stats and stats[stdev_key] > 0:
            recommended_n = compute_required_sample_size(
                abs(stats[avg_key]), stats[stdev_key]
            )
            stats[f"recommended_n_{key}"] = recommended_n
            stats[f"sample_sufficient_{key}"] = len(impacts) >= recommended_n

    # Cross-event contamination check
    if known_events:
        contamination = check_event_contamination(
            [{"symbol": i["symbol"], "date": i["event_date"]} for i in impacts],
            known_events=known_events,
        )
        if contamination:
            stats["contamination_warnings"] = contamination

    # Factor exposure check — is the "alpha" just a known factor in disguise?
    if check_factors and len(symbols_seen) <= 5 and impacts:
        try:
            from tools.fama_french_data import compute_factor_exposure
            all_dates_list = [i["event_date"] for i in impacts]
            date_min = min(all_dates_list)
            date_max = max(all_dates_list)
            factor_exposures = {}
            for sym in sorted(symbols_seen):
                exp = compute_factor_exposure(sym, date_min, date_max)
                if "error" not in exp:
                    factor_exposures[sym] = exp
            if factor_exposures:
                stats["factor_exposures"] = factor_exposures
                # Warn if alpha looks like a known factor
                warnings = []
                for sym, exp in factor_exposures.items():
                    if abs(exp.get("smb_beta", 0)) > 0.4:
                        warnings.append(f"{sym}: high size exposure (SMB beta {exp['smb_beta']})")
                    if abs(exp.get("hml_beta", 0)) > 0.4:
                        warnings.append(f"{sym}: high value exposure (HML beta {exp['hml_beta']})")
                    if abs(exp.get("mom_beta", 0) or 0) > 0.4:
                        warnings.append(f"{sym}: high momentum exposure (Mom beta {exp['mom_beta']})")
                if warnings:
                    stats["factor_warning"] = (
                        "Alpha may be explained by known factors: " + "; ".join(warnings)
                    )
        except (ImportError, Exception) as e:
            print(f"[measure_event_impact] factor check unavailable: {e}", file=sys.stderr)

    # Seasonal headwind warning — does the trade fight historical seasonal patterns?
    if check_seasonal and impacts:
        try:
            from tools.seasonal_analyzer import monthly_seasonality
            # Check the most common event month
            event_months = [datetime.strptime(i["event_date"], "%Y-%m-%d").month for i in impacts]
            from collections import Counter
            common_month = Counter(event_months).most_common(1)[0][0]
            check_sym = symbol if symbol else sorted(symbols_seen)[0]
            season = monthly_seasonality(check_sym, years=15)
            if not season.empty:
                month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                month_name = month_names[common_month - 1]
                row = season.loc[month_name]
                if row["p_value"] is not None and row["p_value"] < 0.05:
                    avg = row["mean_pct"]
                    stats["seasonal_context"] = {
                        "month": month_name,
                        "historical_avg_pct": avg,
                        "p_value": row["p_value"],
                    }
                    # Warn if trading against a significant seasonal pattern
                    avg_abnormal = stats.get("avg_abnormal_5d") or stats.get("avg_abnormal_1d")
                    if avg_abnormal is not None:
                        trade_dir = "long" if avg_abnormal > 0 else "short"
                        seasonal_dir = "bullish" if avg > 0 else "bearish"
                        if (trade_dir == "long" and avg < -1) or (trade_dir == "short" and avg > 1):
                            stats["seasonal_warning"] = (
                                f"Trade is {trade_dir} but {month_name} is historically "
                                f"{seasonal_dir} for {check_sym} ({avg:+.1f}%, p={row['p_value']:.3f})"
                            )
        except (ImportError, Exception) as e:
            print(f"[measure_event_impact] seasonal check unavailable: {e}", file=sys.stderr)

    return stats


def check_event_contamination(events, known_events=None, window_days=20):
    """
    Check for overlapping events that could contaminate measurement windows.

    Args:
        events: List of {"symbol": str, "date": str} dicts being measured
        known_events: Additional known events to check against. If None, checks
                     only within the events list itself.
        window_days: Size of measurement window in calendar days

    Returns:
        List of warning dicts describing contaminated event pairs
    """
    all_events = list(events)
    if known_events:
        all_events.extend(known_events)

    warnings = []
    for i, ev in enumerate(events):
        ev_date = datetime.strptime(ev["date"], "%Y-%m-%d")
        for j, other in enumerate(all_events):
            if ev["symbol"] != other["symbol"]:
                continue
            # Skip self-comparison (same index in the original events list)
            if j < len(events) and j == i:
                continue
            other_date = datetime.strptime(other["date"], "%Y-%m-%d")
            gap = abs((ev_date - other_date).days)
            if 0 < gap <= window_days:
                warnings.append({
                    "event": ev,
                    "conflicting_event": other,
                    "gap_days": gap,
                    "warning": (
                        f"{ev['symbol']} has events on {ev['date']} and {other['date']} "
                        f"({gap} days apart). Measurement windows overlap — "
                        f"price impact may be contaminated."
                    ),
                })
    return warnings


def compute_required_sample_size(effect_size, stdev, alpha=0.05, power=0.8):
    """
    Compute required sample size for a one-sample t-test.

    Given the observed effect size and standard deviation, how many samples
    do we need to detect this effect with the specified power?

    Args:
        effect_size: Expected mean abnormal return (absolute value)
        stdev: Standard deviation of abnormal returns
        alpha: Significance level (default 0.05)
        power: Desired statistical power (default 0.80)

    Returns:
        Required sample size (integer, minimum 3)
    """
    if effect_size <= 0 or stdev <= 0:
        return 999  # Cannot compute — need positive values

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    n = math.ceil(((z_alpha + z_beta) * stdev / effect_size) ** 2)
    return max(3, n)


def apply_cross_category_fdr(category_p_values, alpha=0.05):
    """
    Apply Benjamini-Hochberg FDR correction across multiple event categories.

    When testing N categories, some will be significant by chance. This adjusts
    p-values to control the false discovery rate.

    Args:
        category_p_values: Dict of {category_name: min_p_value_across_horizons}
        alpha: Desired FDR level (default 0.05)

    Returns:
        Dict of {category_name: {"raw_p": float, "adjusted_p": float, "significant": bool}}
    """
    if not category_p_values:
        return {}

    # Sort by p-value
    sorted_cats = sorted(category_p_values.items(), key=lambda x: x[1])
    m = len(sorted_cats)
    results = {}

    for rank, (cat, raw_p) in enumerate(sorted_cats, 1):
        # BH adjusted p-value: p * m / rank
        adjusted_p = min(1.0, raw_p * m / rank)
        results[cat] = {
            "raw_p": round(raw_p, 4),
            "adjusted_p": round(adjusted_p, 4),
            "bh_rank": rank,
            "significant": adjusted_p < alpha,
        }

    return results


def bootstrap_ci(returns, n_bootstrap=10000, ci=95, statistic="mean"):
    """
    Compute bootstrap confidence interval for the mean (or median) of returns.

    Resamples with replacement to estimate the sampling distribution of the
    mean, then reports the percentile-based confidence interval.

    Args:
        returns: List of observed returns
        n_bootstrap: Number of bootstrap samples (default 10,000)
        ci: Confidence level as integer (default 95 = 95% CI)
        statistic: "mean" or "median"

    Returns:
        dict with 'point_estimate', 'ci_lower', 'ci_upper', 'ci_level',
        'ci_excludes_zero' (True if the CI does not contain zero),
        'n_bootstrap'
    """
    import numpy as np

    arr = np.array(returns, dtype=float)
    n = len(arr)
    if n < 3:
        est = float(np.mean(arr)) if statistic == "mean" else float(np.median(arr))
        return {
            "point_estimate": round(est, 4),
            "ci_lower": None,
            "ci_upper": None,
            "ci_level": ci,
            "ci_excludes_zero": None,
            "n_bootstrap": 0,
            "note": "Too few observations for bootstrap (need >= 3)",
        }

    # Generate all bootstrap samples at once
    rng = np.random.default_rng()
    indices = rng.integers(0, n, size=(n_bootstrap, n))
    samples = arr[indices]

    if statistic == "mean":
        boot_stats = samples.mean(axis=1)
        point_est = float(np.mean(arr))
    else:
        boot_stats = np.median(samples, axis=1)
        point_est = float(np.median(arr))

    alpha = (100 - ci) / 2
    lower = float(np.percentile(boot_stats, alpha))
    upper = float(np.percentile(boot_stats, 100 - alpha))

    return {
        "point_estimate": round(point_est, 4),
        "ci_lower": round(lower, 4),
        "ci_upper": round(upper, 4),
        "ci_level": ci,
        "ci_excludes_zero": (lower > 0) or (upper < 0),
        "n_bootstrap": n_bootstrap,
    }


def _stdev(values):
    """Standard deviation (sample)."""
    if len(values) < 2:
        return 0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return variance ** 0.5
