#!/usr/bin/env python3
"""
Data task dispatcher — runs formulaic data tasks without an LLM.

The orchestrator calls this CLI instead of reasoning through data work at Opus cost.
Each task stores full results in SQLite (task_results table) and prints a compact
JSON summary to stdout that the orchestrator can read cheaply.

Usage:
    python3 data_tasks.py backtest --events '[{"symbol":"AAPL","date":"2024-01-15"}]'
    python3 data_tasks.py backtest --symbol AAPL --dates '["2024-01-15","2024-04-20"]'
    python3 data_tasks.py verify-date --event "AAPL S&P 500 addition" --expected-date 2024-03-15
    python3 data_tasks.py largecap-filter --symbols '["AAPL","MSFT","TINY"]'
    python3 data_tasks.py price-history --symbol AAPL --days 90
    python3 data_tasks.py get-result --id <result_id>
"""

import argparse
import json
import sys
import uuid
from datetime import datetime

import db


def _store_result(task_type, parameters, result, summary):
    """Store full result in SQLite and return the result ID."""
    result_id = f"T-{uuid.uuid4().hex[:8]}"
    db.store_task_result(
        result_id=result_id,
        task_type=task_type,
        parameters=parameters,
        result=result,
        summary=summary,
    )
    return result_id


def _backtest_summary(result):
    """Extract compact summary from measure_event_impact result."""
    if "error" in result:
        return {"status": "error", "error": result["error"]}

    summary = {
        "status": "ok",
        "events_measured": result.get("events_measured", 0),
        "passes_multiple_testing": result.get("passes_multiple_testing", False),
        "entry_price_mode": result.get("entry_price_mode", "close"),
    }

    # Key metrics at each horizon
    for h in ["1d", "3d", "5d", "10d", "20d"]:
        avg_key = f"avg_abnormal_{h}"
        med_key = f"median_abnormal_{h}"
        pos_key = f"positive_rate_abnormal_{h}"
        p_key = f"p_value_abnormal_{h}"
        ci_key = f"bootstrap_ci_abnormal_{h}"

        if avg_key in result:
            summary[f"abnormal_{h}"] = {
                "avg": result[avg_key],
                "median": result.get(med_key),
                "positive_rate": result.get(pos_key),
                "p_value": result.get(p_key),
            }
            ci = result.get(ci_key)
            if ci and isinstance(ci, dict):
                summary[f"abnormal_{h}"]["ci_excludes_zero"] = ci.get("ci_excludes_zero")

    # Cost info
    if result.get("avg_estimated_cost_pct") is not None:
        summary["avg_cost_pct"] = result["avg_estimated_cost_pct"]

    # Data quality
    if result.get("data_quality_warning"):
        summary["warning"] = result["data_quality_warning"]

    # Sample sufficiency
    if "sample_sufficient" in result:
        summary["sample_sufficient"] = result["sample_sufficient"]

    return summary


def cmd_backtest(args):
    """Run measure_event_impact and store results."""
    import market_data

    params = {}

    if args.events:
        event_dates = json.loads(args.events)
        params["event_dates"] = event_dates
    elif args.symbol and args.dates:
        event_dates = json.loads(args.dates)
        params["symbol"] = args.symbol
        params["event_dates"] = event_dates
    else:
        print(json.dumps({"status": "error", "error": "Provide --events or --symbol + --dates"}))
        return

    kwargs = {}
    if args.symbol:
        kwargs["symbol"] = args.symbol
    kwargs["event_dates"] = event_dates
    kwargs["benchmark"] = args.benchmark or "SPY"
    if args.sector_etf:
        kwargs["sector_etf"] = args.sector_etf
    if args.entry_price:
        kwargs["entry_price"] = args.entry_price
    if args.event_timing:
        kwargs["event_timing"] = args.event_timing
    if args.event_type:
        kwargs["event_type"] = args.event_type
        kwargs["estimate_costs"] = True
    if args.estimate_costs:
        kwargs["estimate_costs"] = True
    if args.regime_filter:
        kwargs["regime_filter"] = args.regime_filter

    params.update(kwargs)

    result = market_data.measure_event_impact(**kwargs)
    summary = _backtest_summary(result)
    result_id = _store_result("backtest", params, result, json.dumps(summary))
    summary["result_id"] = result_id

    print(json.dumps(summary, indent=2))


def cmd_verify_date(args):
    """Verify an event date using the date verification tool."""
    from tools.verify_event_date import verify_event_date

    result = verify_event_date(args.event, args.expected_date)
    summary = {
        "status": "ok",
        "event": args.event,
        "expected_date": args.expected_date,
        "verified": result.get("verified", False),
        "actual_date": result.get("actual_date"),
        "source": result.get("source"),
    }
    if result.get("error"):
        summary["status"] = "error"
        summary["error"] = result["error"]

    result_id = _store_result(
        "verify_date",
        {"event": args.event, "expected_date": args.expected_date},
        result,
        json.dumps(summary),
    )
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2))


def cmd_largecap_filter(args):
    """Filter symbols to large-cap only."""
    from tools.largecap_filter import filter_to_largecap

    symbols = json.loads(args.symbols)
    import pandas as pd
    df = pd.DataFrame({"ticker": symbols})
    result_df = filter_to_largecap(df, ticker_col="ticker", verbose=False)
    largecap = result_df["ticker"].tolist()
    summary = {
        "status": "ok",
        "input_count": len(symbols),
        "output_count": len(largecap),
        "largecap_symbols": largecap,
        "filtered_out": [s for s in symbols if s not in largecap],
    }

    result_id = _store_result(
        "largecap_filter",
        {"symbols": symbols},
        {"largecap": largecap, "filtered_out": summary["filtered_out"]},
        json.dumps(summary),
    )
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2))


def cmd_price_history(args):
    """Fetch price history for a symbol."""
    import market_data

    prices = market_data.get_price_history(args.symbol, days=args.days)
    if not prices:
        summary = {"status": "error", "error": f"No data for {args.symbol}"}
        print(json.dumps(summary))
        return

    # Summary: first/last date, price range, current price
    summary = {
        "status": "ok",
        "symbol": args.symbol,
        "days_requested": args.days,
        "days_returned": len(prices),
        "first_date": prices[0]["date"],
        "last_date": prices[-1]["date"],
        "first_close": prices[0]["close"],
        "last_close": prices[-1]["close"],
        "high": max(p["high"] for p in prices),
        "low": min(p["low"] for p in prices),
        "return_pct": round((prices[-1]["close"] / prices[0]["close"] - 1) * 100, 2),
    }

    result_id = _store_result(
        "price_history",
        {"symbol": args.symbol, "days": args.days},
        prices,
        json.dumps(summary),
    )
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2))


def cmd_scan_insiders(args):
    """Scan EDGAR for insider buying clusters."""
    from tools.edgar_insider_scanner_v2 import scan_insider_clusters

    clusters = scan_insider_clusters(
        days=args.days,
        min_insiders=args.min_insiders,
        min_value_per_insider=args.min_value,
        quiet=True,
    )

    summary = {
        "status": "ok",
        "days": args.days,
        "clusters_found": len(clusters),
        "clusters": [
            {
                "ticker": c["ticker"],
                "issuer_name": c["issuer_name"][:40],
                "n_insiders": c["n_insiders"],
                "total_value": c["total_value"],
                "top_insider": c["insiders"][0]["name"] if c["insiders"] else "",
            }
            for c in clusters
        ],
    }

    result_id = _store_result(
        "scan_insiders",
        {"days": args.days, "min_insiders": args.min_insiders, "min_value": args.min_value},
        clusters,
        json.dumps(summary, default=str),
    )
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_scan_insiders_evaluate(args):
    """Scan EDGAR for insider clusters AND run GO/NO-GO evaluator on each."""
    from tools.edgar_insider_scanner_v2 import scan_insider_clusters
    from tools.insider_cluster_evaluator import evaluate_cluster

    clusters = scan_insider_clusters(
        days=args.days,
        min_insiders=args.min_insiders,
        min_value_per_insider=args.min_value,
        quiet=True,
    )

    evaluated = []
    for c in clusters:
        ticker = c.get("ticker", "")
        # Clean exchange-prefixed tickers (e.g., "NASDAQ:SVC" -> "SVC")
        if ":" in ticker:
            ticker = ticker.split(":")[-1]
        if not ticker or ticker in ("N/A", "NONE"):
            evaluated.append({
                "ticker": ticker,
                "issuer_name": c["issuer_name"][:40],
                "decision": "SKIP",
                "reason": "No ticker / not a stock",
            })
            continue

        # Determine if CEO/CFO present
        has_ceo = False
        has_cfo = False
        for ins in c.get("insiders", []):
            title = ins.get("title", "").upper()
            if "CEO" in title or "CHIEF EXECUTIVE" in title:
                has_ceo = True
            if "CFO" in title or "CHIEF FINANCIAL" in title:
                has_cfo = True

        try:
            result = evaluate_cluster(
                ticker=ticker,
                n_insiders=c["n_insiders"],
                total_value_usd=c["total_value"],
                has_ceo=has_ceo,
                has_cfo=has_cfo,
                days_since_latest_filing=c.get("days_since_latest_filing"),
                max_trans_to_filing_lag=c.get("max_trans_to_filing_lag"),
                acceptance_time=c.get("latest_accept_time"),
            )
            evaluated.append({
                "ticker": ticker,
                "issuer_name": c["issuer_name"][:40],
                "n_insiders": c["n_insiders"],
                "total_value": c["total_value"],
                "decision": result["decision"],
                "score": result["score"],
                "has_ceo": has_ceo,
                "has_cfo": has_cfo,
                "latest_filing_date": c.get("latest_filing_date"),
                "days_since_latest_filing": c.get("days_since_latest_filing"),
                "max_trans_to_filing_lag": c.get("max_trans_to_filing_lag"),
                "trigger_class": result.get("trigger_class", "unknown"),
                "blockers": result["blockers"],
                "warnings": result["warnings"],
                "market_cap_m": result["market_data"].get("market_cap_m"),
            })
        except Exception as e:
            evaluated.append({
                "ticker": ticker,
                "issuer_name": c["issuer_name"][:40],
                "decision": "ERROR",
                "reason": str(e)[:100],
            })

    summary = {
        "status": "ok",
        "days": args.days,
        "clusters_found": len(clusters),
        "go_count": sum(1 for e in evaluated if e.get("decision") == "GO"),
        "weak_go_count": sum(1 for e in evaluated if e.get("decision") == "WEAK_GO"),
        "no_go_count": sum(1 for e in evaluated if e.get("decision") in ("NO_GO", "SKIP", "ERROR")),
        "evaluated": evaluated,
    }

    result_id = _store_result(
        "scan_insiders_evaluate",
        {"days": args.days, "min_insiders": args.min_insiders, "min_value": args.min_value},
        {"clusters": clusters, "evaluations": evaluated},
        json.dumps(summary, default=str),
    )
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_get_result(args):
    """Retrieve a stored task result by ID."""
    result = db.get_task_result(args.id)
    if result is None:
        print(json.dumps({"status": "error", "error": f"No result with id {args.id}"}))
        return
    # Print the full result (may be large — orchestrator should pipe through head)
    print(json.dumps(result, indent=2, default=str))


# ---------------------------------------------------------------------------
# Non-event hypothesis commands
# ---------------------------------------------------------------------------

def _causal_summary(result):
    """Extract compact summary from any causal_tests result."""
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    summary = {
        "status": "ok",
        "test_name": result.get("test_name"),
        "hypothesis_class": result.get("hypothesis_class"),
        "effect_size": result.get("effect_size"),
        "p_value": result.get("p_value"),
        "significant": result.get("significant"),
        "r_squared": result.get("r_squared"),
        "n_observations": result.get("n_observations"),
        "oos_significant": result.get("oos_result", {}).get("significant") if result.get("oos_result") else None,
        "summary": result.get("summary"),
    }
    # Propagate scan-artifact suppression flags to the summary so orchestrator
    # can see them without re-reading full result.
    if result.get("scan_artifact_check") is not None:
        summary["scan_artifact_check"] = result["scan_artifact_check"]
        summary["scan_artifact_suppressed"] = result.get("scan_artifact_suppressed", False)
        summary["scan_artifact_reason"] = result.get("scan_artifact_reason")
    return summary


# ---------------------------------------------------------------------------
# Scan-artifact auto-suppression
# (dgs10_structural_break_scan_artifact_rule_2026_04_19,
#  dgs10_granger_lead_lag_systematic_dead_end,
#  dgs30_granger_lead_lag_extends_systematic_dead_end_2026_04_19)
# ---------------------------------------------------------------------------

# Rate-sensitive ETFs where DGS10/DGS30 breaks/lead-lag are known secular drift.
_RATE_SENSITIVE_ETFS = {
    "XLRE", "IYR", "VNQ",                       # REITs
    "HYG", "JNK", "LQD", "TLT", "IEF",          # bonds
    "GLD", "GDX", "GDXJ", "SLV",                # precious metals
    "XLE", "XOP",                               # energy
    "XLU",                                      # utilities
    "XLI", "IWM",                               # industrials / smallcap
    "EEM", "VWO", "FXI",                        # EM
    "XLV", "IBB", "XBI",                        # healthcare
    "KRE", "KBE", "XLF",                        # banks / financials
    "XLB", "XME",                               # materials
    "XHB", "ITB",                               # homebuilders
    "XLK", "SMH",                               # tech
}

_DGS_FACTORS = {"FRED:DGS10", "FRED:DGS30", "FRED:DGS2", "FRED:DGS5"}

# Canonical 2020-2024 rate-normalization break date where most scan hits cluster.
_DGS_CANONICAL_BREAK = "2022-03-16"

# Commodity factors where 2025 Liberation Day / Trump-inauguration window break
# dates are frequently scanner artifacts. Per
# commodity_factor_liberation_day_structural_break_scan_artifact_rule_2026_04_22,
# the underlying 2022 Russia-Ukraine commodity regime break dominates.
_COMMODITY_FACTORS_FOR_LIBERATION_DAY = {
    "CL=F", "BZ=F",           # crude oil (WTI, Brent)
    "HG=F",                   # copper
    "GC=F", "SI=F",           # precious metals (gold, silver)
    "NG=F",                   # natural gas
    "ZS=F", "ZW=F", "ZC=F",   # grains (soybeans, wheat, corn)
}

# Liberation-Day / Trump-inauguration window (2025-01-01..2025-06-01) where
# Chow tests cluster due to elevated volatility, not a discrete regime break.
_LIBERATION_DAY_WINDOW_START = "2025-01-01"
_LIBERATION_DAY_WINDOW_END = "2025-06-01"
# Canonical 2022 Russia-Ukraine commodity regime break anchor for alt-date
# falsification.
_COMMODITY_2022_BREAK = "2022-01-03"


# Commodity futures where Granger lead-lag on sector/industry ETFs is a
# documented systematic dead end per commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20.
# Granger significance is artifact of contemporaneous exposure with volatility clustering.
_COMMODITY_FUTURES = {
    "CL=F", "BZ=F",           # crude oil (WTI, Brent)
    "HG=F",                   # copper
    "GC=F", "SI=F",           # precious metals (gold, silver)
    "NG=F",                   # natural gas
    "ZS=F", "ZW=F", "ZC=F",   # grains (soybeans, wheat, corn)
    "KC=F", "SB=F",           # softs (coffee, sugar)
    "PL=F", "PA=F",           # industrial precious (platinum, palladium)
    "LE=F", "HE=F",           # livestock (cattle, hogs)
}

# Commodity ETFs whose returns track the same underlying as the futures above.
# Per gld_xlb_leadlag_scan_hit_autoclosed_2026_04_23: a scan hit of the form
# GLD -> XLB (gold commodity ETF -> materials sector) is the same artifact as
# GC=F -> XLB, which is already in the systematic dead-end set. Including
# commodity ETFs here closes a gap in the auto-suppression rule.
_COMMODITY_ETFS = {
    # Gold
    "GLD", "IAU", "SGOL", "GLDM", "BAR", "AAAU",
    # Silver
    "SLV", "SIVR",
    # Platinum / palladium
    "PPLT", "PALL",
    # Crude oil (long and inverse)
    "USO", "USL", "UCO", "SCO", "BNO", "OIL", "OILK",
    # Natural gas
    "UNG", "UNL", "BOIL", "KOLD",
    # Broad commodities
    "DBC", "GSG", "PDBC", "FTGC", "COMB",
    # Agriculture
    "DBA", "WEAT", "CORN", "SOYB", "CANE",
    # Copper (commodity ETNs)
    "CPER", "JJC",
}

# Sector/industry ETFs where commodity Granger lead-lag has been tested and
# repeatedly failed canonical threshold backtest.
_COMMODITY_SENSITIVE_ETFS = {
    "XLE", "XOP", "OIH",      # energy
    "XLB", "XME", "GDX", "GDXJ", "SIL",  # materials / miners
    "MOO", "DBA",             # agribusiness / agriculture
    "XLP", "XLI",             # consumer staples / industrials (food processing, fertilizer)
    # Utilities: fuel input (natural gas / oil) makes XLU commodity-sensitive while
    # also rate-sensitive. Added 2026-06-10 after CL=F -> XLU lag-3 Granger scan hit
    # (IS p=0.0064) failed the fixed-lag OOS retest: discovered lag-3 dies OOS
    # (beta=+0.0006, p=0.97) while the "OOS confirmation" was lag-1 lag-hopping; the
    # tradeable oil-up/down spread is insignificant IS (t=-0.28) and OOS (t=1.38),
    # mean abnormal returns <0.1%. Same contemporaneous-exposure + vol-clustering
    # artifact as the rest of the family. See cl_f_xlu_leadlag_canonical_dead_end_2026_06_10
    # and parent commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20.
    "XLU",                    # utilities (fuel-cost commodity sensitivity)
    "AAL", "DAL", "UAL", "LUV",  # airlines (crude exposure)
    # Commodity-sensitive broad emerging-market / country index ETFs
    # (added 2026-04-23 after CL=F -> EEM lag-4 Granger canonical retest failed;
    # see cl_f_eem_leadlag_canonical_dead_end_2026_04_23 and parent rule
    # commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20).
    # EM broad indices have ~40-60% weight in commodity-exporter economies so
    # Granger->threshold lead-lag produces the same volatility artifact.
    "EEM", "VWO", "IEMG",     # broad EM index ETFs
    "EWZ", "EZA", "EWW",      # country ETFs with heavy commodity-export weight
}


def _is_dgs_rate_sensitive_pair(factor, target):
    """Return True if this is a DGS rate -> rate-sensitive ETF pair where
    scan hits are known systematic artifacts."""
    return factor in _DGS_FACTORS and target.upper() in _RATE_SENSITIVE_ETFS


def _is_commodity_sector_pair(factor, target):
    """Return True if factor is a commodity future OR commodity ETF and
    target is a commodity-sensitive ETF/stock. Granger lead-lag on these
    pairs is a documented systematic dead end.
    Commodity ETFs added 2026-04-23 per gld_xlb_leadlag_scan_hit_autoclosed_2026_04_23
    to close the scan-hit gap where e.g. GLD->XLB was queued (GC=F->XLB was
    already suppressed).
    """
    f = (factor or "").upper()
    t = (target or "").upper()
    is_commodity_factor = (factor in _COMMODITY_FUTURES) or (f in _COMMODITY_ETFS)
    return is_commodity_factor and t in _COMMODITY_SENSITIVE_ETFS


def _is_commodity_liberation_day_break(factor, break_date):
    """Return True if this pair+date matches the Liberation Day commodity
    break pattern (commodity factor + break date in 2025-01-01..2025-06-01).
    """
    if factor not in _COMMODITY_FACTORS_FOR_LIBERATION_DAY:
        return False
    try:
        import pandas as pd
        bd = pd.Timestamp(break_date)
        return (pd.Timestamp(_LIBERATION_DAY_WINDOW_START) <= bd
                <= pd.Timestamp(_LIBERATION_DAY_WINDOW_END))
    except Exception:
        return False


def _check_commodity_sector_leadlag_artifact(factor, target):
    """Flag commodity futures -> sector ETF lead-lag tests as known-systematic
    artifacts per commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20.
    7+ confirmations across HG=F->XLB, GC=F->XLB, CL=F->XLE, CL=F->AAL,
    HG=F->XME, GC=F->GDX, ZS=F->MOO. Granger 'significance' is artifact of
    contemporaneous exposure + volatility clustering; threshold backtests show
    mean abnormal returns consistently <1% (below methodology floor).
    """
    if not _is_commodity_sector_pair(factor, target):
        return None
    factor_kind = "future" if factor in _COMMODITY_FUTURES else "ETF"
    return {
        "check": "commodity_sector_leadlag_systematic_artifact_warning",
        "rule": "commodity_sector_granger_leadlag_systematic_dead_end_2026_04_20",
        "suppressed": True,
        "reason": (
            f"Commodity {factor_kind} {factor} -> {target.upper()} Granger lead-lag is a "
            "documented systematic artifact (7+ confirmations). The relationship is "
            "contemporaneous exposure (beta test) with volatility clustering that "
            "produces spurious Granger significance. Threshold backtests of the "
            "lead-lag direction consistently yield mean abnormal returns <1% (below "
            "the methodology magnitude floor). DO NOT queue as scan hit; treat as "
            "informational only unless scan-claimed magnitude exceeds 0.5%/day or "
            "scan pair is not in the documented dead-end set."
        ),
    }


def _check_exposure_tradeability(factor, target):
    """Tradeability caveat for contemporaneous factor-EXPOSURE (beta) tests.

    A contemporaneous exposure beta (target ~ factor, same-day) is, by
    construction, NOT directly tradeable: you cannot observe today's factor
    close and trade the target at that same close. An exposure hit is only
    actionable if it can be converted into a predictive (lead-lag) or
    threshold/event signal that survives OOS validation.

    Rule (factor_exposure_contemporaneous_not_tradeable_2026_06_09): the
    factor-exposure screen was queuing significant contemporaneous betas as
    priority-8 "tradeable alpha." For the documented commodity->sector and
    DGS-rate->rate-sensitive-ETF families, the lead-lag/threshold conversions
    are already confirmed systematic artifacts (mean abnormal returns <1%,
    OOS non-significant). For all other pairs the exposure is still only a
    hedge/risk relationship until a predictive conversion is validated.

    Returns a dict describing the caveat (never None) so the screen always
    sees the contemporaneous-only flag.
    """
    is_commodity = _is_commodity_sector_pair(factor, target)
    is_rate = _is_dgs_rate_sensitive_pair(factor, target)
    conversion_dead_end = is_commodity or is_rate
    if conversion_dead_end:
        fam = "commodity->sector" if is_commodity else "DGS-rate->rate-sensitive-ETF"
        reason = (
            f"Contemporaneous exposure {factor} -> {target.upper()} is real but NOT "
            f"directly tradeable (same-day beta). Its predictive conversion is a "
            f"documented systematic dead end ({fam} family): lead-lag/threshold "
            f"backtests yield mean abnormal returns <1% and fail OOS. DO NOT queue "
            f"this exposure as a tradeable scan hit."
        )
    else:
        reason = (
            f"Contemporaneous exposure {factor} -> {target.upper()} is a same-day beta "
            f"(hedge/risk relationship), NOT a directly tradeable signal. Only queue "
            f"if a predictive lead-lag OR threshold conversion is validated OOS first."
        )
    return {
        "check": "exposure_contemporaneous_tradeability",
        "rule": "factor_exposure_contemporaneous_not_tradeable_2026_06_09",
        "contemporaneous_only": True,
        "tradeable_conversion_dead_end": conversion_dead_end,
        "suppressed": conversion_dead_end,
        "reason": reason,
    }


def _check_structural_break_tradeability(factor, target, break_date):
    """Tradeability caveat for ANY contemporaneous macro->sector structural break.

    A structural break in a target ~ factor regression detects that the
    CONTEMPORANEOUS beta changed magnitude at a date. That beta is, by
    construction, priced in real time (you cannot observe today's factor close
    and trade the target at the same close), so a break is a risk-model fact,
    NOT a forward trading signal. Empirically (rates_staples_structural_break_
    not_tradeable_2026_06_11): the cleanest break-pair that even passed a Granger
    lead-lag xcorr screen (FRED:DGS10 -> XLP) had ~0 predictive correlation
    (-0.034..+0.021) when converted to past-yield-change -> forward abnormal XLP
    return, with slopes flipping sign IS vs OOS. Structural breaks also cluster
    almost entirely on famous crisis dates (COVID 2020-03-15, Fed/Russia
    2022-03-16) where every macro->sector beta breaks by construction.

    Returns a dict (never None) so every structural-break result carries the
    contemporaneous-only flag. Hard-suppresses crisis-date breaks; for other
    dates flags as contemporaneous-only pending a validated predictive
    conversion.
    """
    crisis_dates = {"2020-03-15", "2020-03-16", "2020-03-23",
                    "2022-03-16", "2022-02-24", "2025-04-02"}
    on_crisis_date = str(break_date) in crisis_dates
    reason = (
        f"Structural break {factor} -> {target.upper()} at {break_date} is a change in a "
        f"CONTEMPORANEOUS beta (priced in real time), NOT a forward signal. Predictive "
        f"conversions of macro->sector break pairs are documented dead ends "
        f"(rates_staples_structural_break_not_tradeable_2026_06_11): ~0 OOS predictive "
        f"correlation. "
        + ("Break sits on a famous crisis date where every macro->sector beta breaks by "
           "construction. DO NOT queue as a tradeable scan hit."
           if on_crisis_date else
           "Only queue if a predictive lead-lag OR threshold conversion is validated OOS first.")
    )
    return {
        "check": "structural_break_contemporaneous_tradeability",
        "rule": "structural_break_contemporaneous_not_tradeable_2026_06_11",
        "contemporaneous_only": True,
        "on_crisis_date": on_crisis_date,
        "suppressed": on_crisis_date,
        "reason": reason,
    }


def _check_dgs_structural_break_artifact(target_rets, factor_rets, target, factor,
                                          break_date, target_f_stat):
    """Auto-run alt-date falsification for DGS -> rate-sensitive ETF structural breaks.
    Per dgs10_structural_break_scan_artifact_rule_2026_04_19: the target-date F-statistic
    must be >= 3x the max of alt-date F-stats for the break to be a genuine regime
    change (rather than secular drift detected by Chow at any date).
    Returns a dict of diagnostic info, or None if the check doesn't apply.
    """
    if not _is_dgs_rate_sensitive_pair(factor, target):
        return None

    # Only apply when break date is near the canonical 2022 break window.
    import pandas as pd
    try:
        bd = pd.Timestamp(break_date)
        canonical = pd.Timestamp(_DGS_CANONICAL_BREAK)
        if abs((bd - canonical).days) > 120:
            return None
    except Exception:
        return None

    import causal_tests
    alt_dates = ["2020-06-15", "2024-01-15"]
    alt_results = {}
    for alt in alt_dates:
        try:
            r = causal_tests.test_structural_break(target_rets, factor_rets, alt)
            if "error" not in r:
                alt_results[alt] = {"f_stat": float(r.get("statistic", 0.0)),
                                    "p_value": float(r.get("p_value", 1.0))}
        except Exception as e:
            alt_results[alt] = {"error": str(e)}

    valid_alt_fs = [v["f_stat"] for v in alt_results.values() if "f_stat" in v]
    if not valid_alt_fs:
        return {"check": "dgs_structural_break_alt_date_falsification",
                "alt_dates": alt_dates, "alt_results": alt_results,
                "target_f_stat": target_f_stat,
                "error": "no alt-date results succeeded"}

    max_alt_f = max(valid_alt_fs)
    ratio = target_f_stat / max_alt_f if max_alt_f > 0 else float("inf")
    suppressed = ratio < 3.0  # canonical 3x threshold per rule

    return {
        "check": "dgs_structural_break_alt_date_falsification",
        "rule": "dgs10_structural_break_scan_artifact_rule_2026_04_19",
        "target_f_stat": float(target_f_stat),
        "alt_dates": alt_dates,
        "alt_results": alt_results,
        "max_alt_f": float(max_alt_f),
        "ratio_target_over_max_alt": float(ratio),
        "threshold_ratio": 3.0,
        "suppressed": bool(suppressed),
        "reason": (
            f"Target F={target_f_stat:.2f} vs max alt-date F={max_alt_f:.2f} "
            f"(ratio={ratio:.2f}x). "
            + ("BELOW 3x threshold -> likely secular drift, not discrete break."
               if suppressed else "ABOVE 3x threshold -> meaningful break at target date.")
        ),
    }


def _check_commodity_liberation_day_structural_break_artifact(
        target_rets, factor_rets, target, factor, break_date, target_f_stat):
    """Auto-run alt-date falsification for commodity-factor structural breaks at
    Liberation Day-era dates.

    Per commodity_factor_liberation_day_structural_break_scan_artifact_rule_2026_04_22:
    Chow-test breaks on <equity/sector> ~ <commodity factor> at dates within
    2025-01-01..2025-06-01 are frequently scanner artifacts dominated by the
    2022 Russia-Ukraine commodity regime break. Require target-F >= 3x the
    2022-01-03 alt-date F, else flag as likely scan artifact.

    Evidence motivating the rule (2026-04-22):
      - airline_oil Liberation Day break: 0.56-1.19x (suppressed)
      - energy_oil Liberation Day break:  0.14-0.17x (suppressed)
      - copper_sector Liberation Day break: 0.08-0.96x (suppressed)

    Returns a diagnostic dict, or None if the pair/date doesn't match.
    """
    if not _is_commodity_liberation_day_break(factor, break_date):
        return None

    import causal_tests
    alt_dates = [_COMMODITY_2022_BREAK]
    alt_results = {}
    for alt in alt_dates:
        try:
            r = causal_tests.test_structural_break(target_rets, factor_rets, alt)
            if "error" not in r:
                alt_results[alt] = {"f_stat": float(r.get("statistic", 0.0)),
                                    "p_value": float(r.get("p_value", 1.0))}
        except Exception as e:
            alt_results[alt] = {"error": str(e)}

    valid_alt_fs = [v["f_stat"] for v in alt_results.values() if "f_stat" in v]
    if not valid_alt_fs:
        return {"check": "commodity_liberation_day_structural_break_alt_date_falsification",
                "alt_dates": alt_dates, "alt_results": alt_results,
                "target_f_stat": target_f_stat,
                "error": "no alt-date results succeeded"}

    max_alt_f = max(valid_alt_fs)
    ratio = target_f_stat / max_alt_f if max_alt_f > 0 else float("inf")
    suppressed = ratio < 3.0  # canonical 3x threshold shared across rules

    return {
        "check": "commodity_liberation_day_structural_break_alt_date_falsification",
        "rule": "commodity_factor_liberation_day_structural_break_scan_artifact_rule_2026_04_22",
        "target_f_stat": float(target_f_stat),
        "alt_dates": alt_dates,
        "alt_results": alt_results,
        "max_alt_f": float(max_alt_f),
        "ratio_target_over_max_alt": float(ratio),
        "threshold_ratio": 3.0,
        "suppressed": bool(suppressed),
        "reason": (
            f"Target F={target_f_stat:.2f} at {break_date} vs 2022-01-03 alt-date "
            f"F={max_alt_f:.2f} (ratio={ratio:.2f}x). "
            + ("BELOW 3x threshold -> Liberation Day break is dominated by the 2022 "
               "Russia-Ukraine commodity regime break. Likely scan artifact."
               if suppressed else "ABOVE 3x threshold -> break at target date is "
               "meaningfully stronger than 2022 commodity regime break.")
        ),
    }


def _check_dgs_leadlag_artifact(factor, target):
    """Flag DGS -> rate-sensitive ETF lead-lag tests as known-systematic artifacts.
    Per dgs10_granger_lead_lag_systematic_dead_end + dgs30 extension.
    Returns a warning dict, or None if the check doesn't apply.
    """
    if not _is_dgs_rate_sensitive_pair(factor, target):
        return None
    return {
        "check": "dgs_leadlag_systematic_artifact_warning",
        "rule": "dgs10_granger_lead_lag_systematic_dead_end "
                "+ dgs30_granger_lead_lag_extends_systematic_dead_end_2026_04_19",
        "suppressed": True,
        "reason": (
            f"DGS rate -> {target.upper()} lead-lag is a documented systematic artifact. "
            "Full-window Granger significance reflects secular drift 2020-2024, not true "
            "lead-lag. Regime-restricted retest (start >= 2022-04-01, oos >= 2024-01-01) "
            "has repeatedly shown lag wandering + OOS non-significance. DO NOT queue as "
            "scan hit without explicit regime-restricted IS/OOS validation."
        ),
    }


# ---- Same-index / same-sector lead-lag auto-suppression ----
# Per leadlag_same_index_or_same_sector_auto_suppress_rule_2026_04_22.
# Lead-lag scan hits between assets sharing an index or sector-parent
# relationship are auto-closed as dead ends because Granger "significance"
# reflects mechanical inclusion / shared-factor exposure, not true lead-lag.

# Broad market indices (a lead asset that is a component of these should be suppressed).
_BROAD_INDICES = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "RSP", "MDY"}

# Sector SPDR ETFs (components of SPY; also themselves potential "lag" targets
# that should not be driven by sub-industry ETFs or individual component stocks).
_SECTOR_SPDR_ETFS = {"XLF", "XLK", "XLE", "XLU", "XLV", "XLI", "XLP", "XLY",
                     "XLB", "XLRE", "XLC"}

# Sub-industry ETFs mapped to their parent sector SPDR.
# Lead-lag from any of these into the parent sector or any broad index is a
# mechanical-inclusion artifact.
_SUB_TO_PARENT_SECTOR = {
    "KRE": "XLF",  "KBE": "XLF",  "IAI": "XLF",  "IAT": "XLF",
    "SMH": "XLK",  "SOXX": "XLK", "IGV": "XLK",  "HACK": "XLK",
    "XBI": "XLV",  "IBB": "XLV",  "IHI": "XLV",  "IHF": "XLV",
    "OIH": "XLE",  "XOP": "XLE",  "FRAK": "XLE", "OILK": "XLE",
    "XHB": "XLY",  "ITB": "XLY",  "RETL": "XLY", "XRT": "XLY",
    "XPH": "XLV",
    "XME": "XLB",  "SLX": "XLB",  "GDX": "XLB",  "GDXJ": "XLB",
    "IYT": "XLI",  "JETS": "XLI",
}

# Major single stocks mapped to their sector SPDR (non-exhaustive; covers the
# documented known-null chains JPM/BAC/GS/WFC -> XLF -> SPY etc.).
_STOCK_TO_SECTOR_SPDR = {
    # Financials
    "JPM": "XLF", "BAC": "XLF", "WFC": "XLF", "C": "XLF", "GS": "XLF",
    "MS": "XLF", "USB": "XLF", "PNC": "XLF", "TFC": "XLF", "SCHW": "XLF",
    "BLK": "XLF", "AXP": "XLF", "SPGI": "XLF", "MCO": "XLF", "ICE": "XLF",
    "CME": "XLF", "CB": "XLF", "V": "XLF", "MA": "XLF",
    # Technology
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK", "ORCL": "XLK",
    "CRM": "XLK", "ADBE": "XLK", "CSCO": "XLK", "ACN": "XLK", "IBM": "XLK",
    "AMD": "XLK", "QCOM": "XLK", "INTC": "XLK", "TXN": "XLK", "NOW": "XLK",
    "INTU": "XLK", "TSM": "XLK",
    # Energy
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "EOG": "XLE", "SLB": "XLE",
    "MPC": "XLE", "PSX": "XLE", "VLO": "XLE", "OXY": "XLE", "PXD": "XLE",
    # Healthcare
    "LLY": "XLV", "UNH": "XLV", "JNJ": "XLV", "MRK": "XLV", "ABBV": "XLV",
    "PFE": "XLV", "ABT": "XLV", "TMO": "XLV", "DHR": "XLV", "BMY": "XLV",
    "AMGN": "XLV", "ISRG": "XLV",
    # Industrials
    "CAT": "XLI", "BA": "XLI", "HON": "XLI", "UPS": "XLI", "GE": "XLI",
    "RTX": "XLI", "LMT": "XLI", "DE": "XLI", "UNP": "XLI", "MMM": "XLI",
    # Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "MCD": "XLY", "NKE": "XLY",
    "LOW": "XLY", "SBUX": "XLY", "TJX": "XLY", "BKNG": "XLY",
    # Consumer Staples
    "WMT": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP", "COST": "XLP",
    "PM": "XLP", "MO": "XLP", "MDLZ": "XLP",
    # Communication Services
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "VZ": "XLC", "T": "XLC", "CMCSA": "XLC",
    # Utilities
    "NEE": "XLU", "DUK": "XLU", "SO": "XLU", "AEP": "XLU", "SRE": "XLU",
    "D": "XLU", "XEL": "XLU", "PCG": "XLU",
    # Materials
    "LIN": "XLB", "SHW": "XLB", "APD": "XLB", "FCX": "XLB", "NEM": "XLB",
    "DOW": "XLB", "DD": "XLB", "NUE": "XLB",
    # Real Estate
    "PLD": "XLRE", "AMT": "XLRE", "EQIX": "XLRE", "CCI": "XLRE", "PSA": "XLRE",
    "O": "XLRE", "SPG": "XLRE", "WELL": "XLRE",
}

# Stocks whose single-stock -> SPY lead-lag is documented as null via the
# sector-chain rule (stock in sector, sector->SPY known null).
_DOCUMENTED_LEADLAG_NULL_SECTORS = {"XLF"}  # bank_stocks_spy_leadlag_dead_end_2026_04_22


def _check_same_index_or_sector_leadlag_artifact(factor, target):
    """Auto-suppress lead-lag tests where the relationship is mechanical-inclusion
    or shared-factor-exposure per leadlag_same_index_or_same_sector_auto_suppress_rule_2026_04_22.

    Suppression criteria (any one triggers):
      (1) Sector SPDR ETF -> broad index (e.g., XLF->SPY): mechanical inclusion.
      (2) Sub-industry ETF -> parent sector SPDR or broad index (e.g., KRE->XLF, KRE->SPY):
          mechanical inclusion.
      (3) Single stock -> its sector SPDR (e.g., JPM->XLF): mechanical inclusion.
      (4) Single stock -> broad index when the stock's sector has documented null
          sector->SPY lead-lag (e.g., JPM->SPY via XLF->SPY null).
      (5) Sector SPDR -> another sector SPDR: shared risk-on/risk-off factor.

    Returns a dict describing the suppression, or None if rule doesn't apply.
    """
    f = (factor or "").upper()
    t = (target or "").upper()
    if not f or not t or f == t:
        return None

    def _make(reason_detail, criterion):
        return {
            "check": "same_index_or_sector_leadlag_artifact",
            "rule": "leadlag_same_index_or_same_sector_auto_suppress_rule_2026_04_22",
            "suppressed": True,
            "criterion": criterion,
            "reason": (
                f"{factor} -> {target} lead-lag is auto-suppressed per the "
                f"2026-04-22 canonical rule. {reason_detail} Granger significance "
                "on these pairs reflects mechanical inclusion / shared-factor "
                "exposure rather than true lead-lag. DO NOT queue as scan hit. "
                "Graduation requires pre-registered regression + threshold with "
                "AR1 control and beta>=0.05 + P&L>=0.5% in BOTH IS and OOS."
            ),
        }

    # (1) Sector SPDR -> broad index
    if f in _SECTOR_SPDR_ETFS and t in _BROAD_INDICES:
        return _make(
            f"{f} is a component sector SPDR of {t} (mechanical inclusion).",
            "sector_spdr_to_broad_index",
        )

    # (5) Sector SPDR -> another sector SPDR (shared risk factor)
    if f in _SECTOR_SPDR_ETFS and t in _SECTOR_SPDR_ETFS:
        return _make(
            f"{f} and {t} are both sector SPDRs of SPY and share the risk-on/"
            "risk-off factor (confirmed by xlf_granger_leadlag_known_null_2026_04_18).",
            "sector_spdr_to_sector_spdr",
        )

    # (2) Sub-industry ETF -> parent sector or broad index
    parent = _SUB_TO_PARENT_SECTOR.get(f)
    if parent is not None:
        if t == parent or t in _BROAD_INDICES:
            return _make(
                f"{f} is a sub-industry subset of {parent} "
                f"(mechanical inclusion in {t}).",
                "sub_industry_etf_to_parent_or_broad",
            )

    # (3) Single stock -> its sector SPDR
    stock_sector = _STOCK_TO_SECTOR_SPDR.get(f)
    if stock_sector is not None and t == stock_sector:
        return _make(
            f"{f} is a component stock of {stock_sector} (mechanical inclusion).",
            "single_stock_to_own_sector_spdr",
        )

    # (4) Single stock -> broad index via documented-null sector chain
    if (stock_sector is not None
            and t in _BROAD_INDICES
            and stock_sector in _DOCUMENTED_LEADLAG_NULL_SECTORS):
        return _make(
            f"{f} is in sector {stock_sector}, and {stock_sector}->{t} is a "
            "documented null lead-lag (bank_stocks_spy_leadlag_dead_end_2026_04_22).",
            "single_stock_to_broad_via_null_sector_chain",
        )

    return None


# ---- Systematic lead-lag family auto-suppression (asset-class buckets) ----
# Codes the canonical rules that were previously recorded in the knowledge base
# but NEVER implemented in the suppression cascade:
#   - leadlag_systematic_batch_closure_2026_06_16
#   - leadlag_systematic_family_classifier_extended_2026_06_17
# The 2026-04-22 same-index/same-sector check above only covers equity SPDR /
# sub-industry / single-stock chains. This adds the documented systematic
# families that slip through it: macro/rate/FX drivers, commodity legs,
# same-non-equity-asset-class pairs (treasuries, IG/HY credit, gold, FX,
# crypto), international (non-synchronous) equity leads, leveraged->underlying
# pairs, and same-US-equity-factor pairs (broad/style/sector ETFs).

# FRED macro / rate / spread series prefixes — a lead-lag whose DRIVER is a
# slow-moving macro series is a documented systematic dead end (the "lead" is
# autocorrelation + common-factor exposure, not tradeable signal).
_MACRO_FRED_PREFIXES = (
    "DGS", "DFF", "FEDFUNDS", "MORTGAGE", "BAML", "T10Y", "T5Y", "T1Y",
    "CPI", "PCE", "UNRATE", "GS", "DTB", "DPRIME", "DAAA", "DBAA", "WALCL",
    "M2", "PAYEMS", "INDPRO", "UMCSENT", "VIXCLS",
)

# Fixed-income / credit ETFs that classify_asset() does NOT already bucket as
# "treasury" (it folds most bonds into "treasury", but misses some HY/credit).
_CREDIT_FIXED_INCOME = {
    "ANGL", "FALN", "BKLN", "SJNK", "SHYG", "USHY", "HYLB", "HYS", "SRLN",
    "PFF", "PGX", "PSK", "PFFD",  # preferreds (rate+credit)
}

# International equity ETFs (single-country / regional / developed-ex-US).
# A non-US equity lead is non-synchronous with US sessions, so daily Granger
# "lead" is a stale-price / time-zone artifact, not a tradeable signal.
_INTL_EQUITY = {
    "VEA", "VWO", "VXUS", "VEU", "VGK", "VPL", "EFA", "IEFA", "EEM", "IEMG",
    "EZU", "EWZ", "EWA", "EWJ", "EWG", "EWU", "EWC", "EWY", "EWT", "EWH",
    "EWW", "EWP", "EWQ", "EWI", "EWL", "EWN", "EWD", "EWS", "EWM", "FXI",
    "MCHI", "KWEB", "INDA", "EPI", "EWZS", "ILF", "GXC", "ASHR", "FLKR",
    "ACWI", "ACWX", "VT",
}

# Leveraged / inverse ETFs mapped to the underlying they mechanically track.
# A lead-lag between a leveraged ETF and its own underlying is a pure
# mechanical-tracking artifact.
_LEVERAGED_TO_UNDERLYING = {
    "UPRO": "SPY", "SPXL": "SPY", "SSO": "SPY", "SH": "SPY", "SPXU": "SPY",
    "SDS": "SPY", "TQQQ": "QQQ", "SQQQ": "QQQ", "QLD": "QQQ", "PSQ": "QQQ",
    "TNA": "IWM", "TZA": "IWM", "UDOW": "DIA", "SDOW": "DIA",
    "TMF": "TLT", "TMV": "TLT", "UGL": "GLD", "NUGT": "GDX", "DUST": "GDX",
}

# US equity universe sharing the dominant US-equity-market factor: broad
# indices, size/style/factor cuts, and sector ETFs (SPDR + Vanguard). Two legs
# both inside this set are a same-class pair per the 2026-06-16 rule.
_US_EQUITY_FACTOR_UNIVERSE = (
    _BROAD_INDICES | _SECTOR_SPDR_ETFS | {
        "IVV", "SPLG", "ITOT", "SCHB", "SCHX", "SCHA", "SCHM", "SCHV", "SCHG",
        "VV", "VO", "VB", "VTV", "VUG", "VBR", "VBK", "VOE", "VOT", "MGK",
        "IWB", "IWV", "IWD", "IWF", "IWN", "IWO", "IWP", "IWR", "IWS",
        "QUAL", "MTUM", "USMV", "VLUE", "SIZE", "SPHQ", "SPLV", "SPYV", "SPYG",
        "VGT", "VHT", "VFH", "VDE", "VAW", "VIS", "VCR", "VDC", "VPU", "VOX", "VNQ",
    }
)


def _classify_leadlag_bucket(symbol):
    """Coarse fixed-income/credit bucket helper that augments classify_asset."""
    from tools.asset_class import classify_asset
    s = (symbol or "").upper()
    if s in _CREDIT_FIXED_INCOME:
        return "fixed_income"
    cls = classify_asset(s)
    if cls == "treasury":  # classify_asset folds all bonds (IG/HY/treasury) here
        return "fixed_income"
    return cls


def _check_systematic_leadlag_family_artifact(factor, target):
    """Auto-suppress lead-lag scan hits belonging to documented systematic
    dead-end families (macro/rate/FX driver, commodity leg, same non-equity
    asset class, international non-synchronous lead, leveraged->underlying,
    same US-equity factor). Returns a suppression dict or None.

    Per leadlag_systematic_batch_closure_2026_06_16 and
    leadlag_systematic_family_classifier_extended_2026_06_17. Statistical
    Granger significance on these pairs reflects autocorrelation / shared-factor
    exposure / non-synchronous trading, not tradeable lead-lag. Graduation still
    requires a pre-registered regression+threshold with AR1 control and
    beta>=0.05 + directional edge>50% + P&L>=0.5% in BOTH IS and OOS.
    """
    from tools.asset_class import classify_asset

    f_raw = (factor or "").strip()
    t_raw = (target or "").strip()
    f = f_raw.upper().replace("FRED:", "")
    t = t_raw.upper().replace("FRED:", "")
    if not f or not t or f == t:
        return None

    def _make(detail, criterion):
        return {
            "check": "systematic_leadlag_family",
            "rule": "leadlag_systematic_batch_closure_2026_06_16",
            "suppressed": True,
            "criterion": criterion,
            "reason": (
                f"{f_raw} -> {t_raw} lead-lag is a documented SYSTEMATIC dead-end "
                f"family. {detail} Granger significance here reflects "
                "autocorrelation / shared-factor exposure / non-synchronous "
                "trading, not tradeable lead-lag. DO NOT queue as a scan hit "
                "(record DEAD_END_SYSTEMATIC_LEADLAG). Graduation requires a "
                "pre-registered regression+threshold with AR1 control and "
                "beta>=0.05 + directional edge>50% + P&L>=0.5% in BOTH IS and OOS."
            ),
        }

    # (1) Macro / rate / spread driver (FRED: prefix or a known macro series).
    if f_raw.upper().startswith("FRED:") or any(f.startswith(p) for p in _MACRO_FRED_PREFIXES):
        return _make(f"The lead leg {f_raw} is a slow-moving macro/rate/spread "
                     "series.", "macro_rate_fx_driver")

    fc, tc = classify_asset(f), classify_asset(t)

    # (2) Either leg is a commodity (commodity->anything lead-lag is a documented
    #     systematic dead end; commodity_sector_granger_leadlag_systematic_dead_end).
    if "commodity" in (fc, tc):
        return _make(f"One leg is a commodity ({f if fc=='commodity' else t}).",
                     "commodity_leg")

    # (3) FX driver / same FX class.
    if fc == "fx":
        return _make(f"The lead leg {f_raw} is an FX series.", "fx_driver")

    # (4) Leveraged / inverse ETF <-> its own underlying (mechanical tracking).
    if _LEVERAGED_TO_UNDERLYING.get(f) == t or _LEVERAGED_TO_UNDERLYING.get(t) == f:
        return _make(f"{f} and {t} are a leveraged/inverse ETF and its own "
                     "underlying (mechanical tracking).", "leveraged_to_underlying")

    # (5) International (non-synchronous) equity lead, or cross-region equity pair.
    if f in _INTL_EQUITY:
        return _make(f"The lead leg {f} is an international equity ETF "
                     "(non-synchronous with US sessions).", "intl_nonsynchronous_lead")
    if (f in _INTL_EQUITY or t in _INTL_EQUITY) and is_equity(f) and is_equity(t):
        return _make(f"{f} and {t} are a cross-region equity pair "
                     "(shared global-equity factor / non-synchronous).",
                     "cross_region_equity_pair")

    # (6) Same non-equity asset class (both fixed income, both crypto, both fx).
    fb, tb = _classify_leadlag_bucket(f), _classify_leadlag_bucket(t)
    if fb == tb and fb in {"fixed_income", "crypto", "fx"}:
        return _make(f"Both legs are the same asset class ({fb}).",
                     f"same_asset_class_{fb}")

    # (7) Same US-equity factor (broad index / style / sector ETFs).
    if f in _US_EQUITY_FACTOR_UNIVERSE and t in _US_EQUITY_FACTOR_UNIVERSE:
        return _make(f"{f} and {t} both belong to the US-equity universe and "
                     "share the dominant US-equity-market factor.",
                     "same_us_equity_factor")

    return None


def is_equity(symbol):
    from tools.asset_class import is_equity as _ie
    return _ie(symbol)


def cmd_regression(args):
    """Run exposure, lead-lag, or structural break regression test."""
    from tools.timeseries import get_returns, get_aligned_returns
    import causal_tests

    identifiers = [args.target, args.factor]
    if args.controls:
        identifiers.extend(args.controls.split(","))

    try:
        rets = get_aligned_returns(identifiers, args.start, args.end)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return

    target_rets = rets[args.target]
    factor_rets = rets[args.factor]
    control_rets = None
    if args.controls:
        control_cols = args.controls.split(",")
        control_rets = rets[control_cols]

    test_type = args.test_type or "exposure"
    params = {"target": args.target, "factor": args.factor, "test_type": test_type,
              "controls": args.controls, "start": args.start, "end": args.end, "oos_start": args.oos_start}

    if test_type == "exposure":
        result = causal_tests.test_exposure(target_rets, factor_rets, control_rets, oos_start=args.oos_start)
        # Contemporaneous exposure betas are not directly tradeable. Tag every
        # exposure result with a tradeability caveat; hard-suppress documented
        # commodity->sector and DGS-rate->ETF families whose predictive
        # conversions are confirmed artifacts.
        # (factor_exposure_contemporaneous_not_tradeable_2026_06_09)
        if isinstance(result, dict) and "error" not in result:
            caveat = _check_exposure_tradeability(args.factor, args.target)
            result["scan_artifact_check"] = caveat
            result["scan_artifact_suppressed"] = caveat.get("suppressed", False)
            result["scan_artifact_reason"] = caveat.get("reason")
            tag = " | SCAN_ARTIFACT_SUPPRESSED: " if caveat.get("suppressed") \
                else " | EXPOSURE_CONTEMPORANEOUS_NOT_TRADEABLE: "
            result["summary"] = (result.get("summary", "") or "") + tag + caveat["reason"]
    elif test_type == "lead_lag":
        max_lags = args.max_lags or 10
        result = causal_tests.test_lead_lag(factor_rets, target_rets, max_lags=max_lags, oos_start=args.oos_start)
        params["max_lags"] = max_lags
        # Auto-suppression cascade (first match wins):
        #   (a) DGS -> rate-sensitive ETF (documented secular-drift artifact)
        #   (b) Commodity future -> commodity-sensitive ETF (documented Granger artifact)
        #   (c) Same-index / same-sector mechanical inclusion
        #       (XLF->SPY, JPM->XLF, JPM->SPY, KRE->XLF, XLY->XLF, etc.)
        artifact = _check_dgs_leadlag_artifact(args.factor, args.target)
        if artifact is None:
            # Fall through to commodity -> sector check.
            artifact = _check_commodity_sector_leadlag_artifact(args.factor, args.target)
        if artifact is None:
            # Fall through to same-index / same-sector mechanical-inclusion check.
            artifact = _check_same_index_or_sector_leadlag_artifact(args.factor, args.target)
        if artifact is None:
            # Fall through to the systematic asset-class family check (macro/rate
            # driver, commodity leg, same-non-equity-class, intl non-synchronous,
            # leveraged->underlying, same US-equity factor).
            artifact = _check_systematic_leadlag_family_artifact(args.factor, args.target)
        if artifact is not None:
            result["scan_artifact_check"] = artifact
            result["scan_artifact_suppressed"] = artifact.get("suppressed", False)
            result["scan_artifact_reason"] = artifact.get("reason")
            # Tag the summary so the orchestrator sees the warning inline.
            if artifact.get("suppressed"):
                result["summary"] = (result.get("summary", "") or "") + \
                    " | SCAN_ARTIFACT_SUPPRESSED: " + artifact["reason"]
    elif test_type == "structural_break":
        if not args.break_date:
            print(json.dumps({"status": "error", "error": "--break-date required for structural_break test"}))
            return
        result = causal_tests.test_structural_break(target_rets, factor_rets, args.break_date)
        params["break_date"] = args.break_date
        # Auto-suppression: DGS10/DGS30 -> rate-sensitive ETF structural breaks near
        # 2022-03-16 are recurring scan artifacts. Run alt-date falsification and
        # require target-F >= 3x max(alt-F) or flag as likely secular drift.
        if "error" not in result:
            target_f = result.get("statistic", 0.0)
            artifact = _check_dgs_structural_break_artifact(
                target_rets, factor_rets, args.target, args.factor,
                args.break_date, target_f,
            )
            if artifact is None:
                # Commodity factor + Liberation Day window -> alt-date check
                # against 2022 Russia-Ukraine commodity regime break.
                artifact = _check_commodity_liberation_day_structural_break_artifact(
                    target_rets, factor_rets, args.target, args.factor,
                    args.break_date, target_f,
                )
            if artifact is None:
                # Universal fallback: ANY contemporaneous macro->sector structural
                # break is a risk-model fact, not a forward signal. Crisis-date
                # breaks are hard-suppressed.
                # (structural_break_contemporaneous_not_tradeable_2026_06_11)
                artifact = _check_structural_break_tradeability(
                    args.factor, args.target, args.break_date)
            if artifact is not None:
                result["scan_artifact_check"] = artifact
                result["scan_artifact_suppressed"] = artifact.get("suppressed", False)
                result["scan_artifact_reason"] = artifact.get("reason")
                if artifact.get("suppressed"):
                    result["summary"] = (result.get("summary", "") or "") + \
                        " | SCAN_ARTIFACT_SUPPRESSED: " + artifact["reason"]
    elif test_type == "regime":
        # Regime test: factor is used as regime indicator (LEVEL, not return).
        # Rule (2026-04-11 methodology update):
        #   - Fit tercile thresholds on IS factor LEVELS only (not full sample).
        #   - Apply same thresholds to OOS, compute OOS regime coverage.
        #   - Refuse sign-preservation validation if any OOS regime has <15% obs.
        from tools.timeseries import get_series
        import pandas as pd
        import numpy as np

        indicator = get_series(args.factor, args.start, args.end)
        aligned_full = pd.DataFrame({"returns": target_rets, "factor_level": indicator}).dropna()

        # Auto-pick an OOS cutoff if user didn't supply one (default: last 30% of sample)
        oos_start = args.oos_start
        if not oos_start:
            split_idx = int(len(aligned_full) * 0.70)
            oos_start = str(aligned_full.index[split_idx].date())
            params["oos_start_auto"] = oos_start

        is_df = aligned_full[aligned_full.index < oos_start].copy()
        oos_df = aligned_full[aligned_full.index >= oos_start].copy()

        if len(is_df) < 60 or len(oos_df) < 30:
            result = {
                "test_name": "regime_comparison",
                "hypothesis_class": "regime",
                "error": f"Insufficient data IS={len(is_df)} OOS={len(oos_df)}",
                "summary": f"Regime test aborted: insufficient data IS={len(is_df)} OOS={len(oos_df)}.",
            }
        else:
            # Fit terciles on IS factor levels only
            q33, q66 = is_df["factor_level"].quantile([1 / 3, 2 / 3]).values

            def _label(x):
                if pd.isna(x):
                    return None
                if x <= q33:
                    return "low"
                if x <= q66:
                    return "mid"
                return "high"

            is_df["regime"] = is_df["factor_level"].apply(_label)
            oos_df["regime"] = oos_df["factor_level"].apply(_label)

            # Run IS regime comparison
            result = causal_tests.test_regime(is_df["returns"], is_df["regime"])

            # Compute OOS stats + coverage
            oos_counts = oos_df["regime"].value_counts().to_dict()
            n_oos = int(len(oos_df))
            oos_coverage = {
                r: {"n": int(oos_counts.get(r, 0)),
                    "pct": float(oos_counts.get(r, 0) / n_oos) if n_oos else 0.0}
                for r in ("low", "mid", "high")
            }
            min_coverage_pct = min(v["pct"] for v in oos_coverage.values())
            coverage_ok = min_coverage_pct >= 0.15

            # Per-regime OOS mean/std/n
            oos_stats = {}
            for r in ("low", "mid", "high"):
                g = oos_df[oos_df["regime"] == r]["returns"]
                if len(g) >= 5:
                    oos_stats[r] = {
                        "mean": float(g.mean()),
                        "std": float(g.std()),
                        "n": int(len(g)),
                    }

            # Sign preservation: IS high-low spread sign vs OOS high-low spread sign
            is_regime_stats = result.get("details", {}).get("regime_stats", {})
            is_spread = None
            if "high" in is_regime_stats and "low" in is_regime_stats:
                is_spread = is_regime_stats["high"]["mean"] - is_regime_stats["low"]["mean"]
            oos_spread = None
            if "high" in oos_stats and "low" in oos_stats:
                oos_spread = oos_stats["high"]["mean"] - oos_stats["low"]["mean"]

            spread_sign_match = (
                is_spread is not None and oos_spread is not None
                and np.sign(is_spread) == np.sign(oos_spread)
                and abs(oos_spread) > 1e-6
            )

            # GATE: require IS significance AND meaningful effect size before
            # accepting sign-preservation as validation. Sign-match on a null
            # IS result (p>0.05 Kruskal-Wallis) is meaningless — you are just
            # preserving noise. Require raw IS p<0.05 AND |IS spread| >= 0.05%/day
            # (~12.5%/year) before sign-preservation can validate.
            # (regime_validation_is_gate_rule_2026_04_20)
            is_significant = bool(result.get("significant"))
            is_spread_meaningful = (
                is_spread is not None and abs(is_spread) >= 0.05
            )

            # REFUSE sign-preservation validation if coverage fails OR IS not significant
            oos_validated = bool(
                spread_sign_match and coverage_ok
                and is_significant and is_spread_meaningful
            )
            validation_refused_reason = None
            if spread_sign_match and not coverage_ok:
                validation_refused_reason = (
                    f"OOS regime coverage imbalance: min regime pct={min_coverage_pct:.1%} "
                    f"(<15% threshold). Refusing sign-preservation validation."
                )
            elif spread_sign_match and coverage_ok and not is_significant:
                validation_refused_reason = (
                    f"IS Kruskal-Wallis p={result.get('p_value'):.4f} >= 0.05. "
                    f"Sign-preservation without IS significance is noise-matching. "
                    f"Refusing validation."
                )
            elif spread_sign_match and coverage_ok and is_significant and not is_spread_meaningful:
                validation_refused_reason = (
                    f"IS spread {is_spread:.3f}%/day below 0.05%/day minimum effect. "
                    f"Refusing validation."
                )

            result["oos_result"] = {
                "oos_start": oos_start,
                "n_oos": n_oos,
                "coverage": oos_coverage,
                "min_coverage_pct": float(min_coverage_pct),
                "coverage_ok": bool(coverage_ok),
                "oos_stats": oos_stats,
                "is_high_low_spread": float(is_spread) if is_spread is not None else None,
                "oos_high_low_spread": float(oos_spread) if oos_spread is not None else None,
                "spread_sign_match_raw": bool(spread_sign_match),
                "oos_validated": oos_validated,
                "validation_refused_reason": validation_refused_reason,
                "significant": oos_validated,  # For _causal_summary convenience
            }
            # Append coverage note to summary
            coverage_note = (
                f" | OOS coverage: low={oos_coverage['low']['pct']:.0%} "
                f"mid={oos_coverage['mid']['pct']:.0%} high={oos_coverage['high']['pct']:.0%}"
                f" (min={min_coverage_pct:.0%}, ok={coverage_ok})"
            )
            if validation_refused_reason:
                coverage_note += f" | VALIDATION REFUSED: {validation_refused_reason}"
            elif oos_validated:
                coverage_note += " | OOS sign-preserved AND coverage ok -> VALIDATED"
            elif is_spread is not None and oos_spread is not None:
                coverage_note += " | OOS sign-flipped"

            # TRADEABLE RETEST (lookahead_regime_lag_fix_2026_06_11):
            # The base regime test classifies each day by that SAME-DAY (contemporaneous)
            # factor level. For VIX/vol indicators, VIX spikes ON down days, so high-VIX
            # days ARE down days by construction -> a mechanical, NON-TRADEABLE artifact
            # that OOS sign-preservation does NOT catch (the contemporaneous correlation
            # is present in every period, so it "validates" automatically). To be
            # tradeable, today's position must be set from a regime KNOWN at decision time
            # -> classify by the 1-day-LAGGED factor level. Re-run on lagged labels; the
            # effect must survive. Discovered via COIN/^VIX scan hit (engine p=0.0008 but
            # lagged p=0.31, high-VIX flips positive). Does NOT change any validation gate;
            # only adds a tradeability flag the scan-hit guard consults.
            try:
                lag_df = aligned_full.copy()
                lag_df["factor_level"] = lag_df["factor_level"].shift(1)
                lag_df = lag_df.dropna()
                lq33, lq66 = lag_df["factor_level"].quantile([1 / 3, 2 / 3]).values
                lag_df["regime"] = lag_df["factor_level"].apply(
                    lambda x: "low" if x <= lq33 else ("mid" if x <= lq66 else "high")
                )
                lag_res = causal_tests.test_regime(lag_df["returns"], lag_df["regime"])
                lag_rs = lag_res.get("details", {}).get("regime_stats", {})
                lag_spread = None
                if "high" in lag_rs and "low" in lag_rs:
                    lag_spread = lag_rs["high"]["mean"] - lag_rs["low"]["mean"]
                lag_sig = bool(lag_res.get("significant"))
                lag_meaningful = lag_spread is not None and abs(lag_spread) >= 0.05
                sign_holds = (
                    is_spread is not None and lag_spread is not None
                    and np.sign(is_spread) == np.sign(lag_spread)
                )
                tradeable = bool(lag_sig and lag_meaningful and sign_holds)
                result["tradeable_retest"] = {
                    "lagged_p_value": lag_res.get("p_value"),
                    "lagged_high_low_spread": float(lag_spread) if lag_spread is not None else None,
                    "contemporaneous_high_low_spread": float(is_spread) if is_spread is not None else None,
                    "lagged_significant": lag_sig,
                    "lagged_meaningful": bool(lag_meaningful),
                    "sign_holds_vs_contemporaneous": bool(sign_holds),
                    "tradeable": tradeable,
                }
                if not tradeable:
                    coverage_note += (
                        f" | TRADEABLE RETEST FAILED (lagged p={lag_res.get('p_value'):.3g}, "
                        f"lagged spread={lag_spread if lag_spread is None else round(lag_spread,3)}): "
                        f"contemporaneous regime is look-ahead, effect does not survive 1-day lag."
                    )
                else:
                    coverage_note += " | TRADEABLE RETEST PASSED (survives 1-day lag)"
            except Exception as _e:
                result["tradeable_retest"] = {"error": str(_e), "tradeable": None}

            result["summary"] = (result.get("summary", "") or "") + coverage_note
    elif test_type == "network":
        spokes = args.controls.split(",") if args.controls else []
        if not spokes:
            print(json.dumps({"status": "error", "error": "--controls must list spoke symbols for network test"}))
            return
        hub_rets = rets[args.target]
        spoke_rets = rets[spokes]
        result = causal_tests.test_network(hub_rets, spoke_rets, max_lag=args.max_lags or 5)
    else:
        print(json.dumps({"status": "error", "error": f"Unknown test_type: {test_type}"}))
        return

    summary = _causal_summary(result)

    # Regime scan-hit guard (enforces regime_validation_is_gate_rule_2026_04_20,
    # vix30_threshold_audit_complete_2026_04_20,
    # spy_vix_regime_scan_hit_duplicate_closure_2026_04_24 at the scan-queue
    # boundary). Regime tests on macro indicators (VIX, yields) are IS-significant
    # by construction — time-varying beta is a textbook stylized fact — so raw IS
    # significance is NOT queue-worthy. The scanner reads this output; surfacing a
    # DO_NOT_QUEUE recommendation stops it from re-queuing IS-only regime hits as
    # scan_hits (the recurring 2026-04-24 duplicate-closure bug). Does not change
    # any validation gate — only the queuing recommendation.
    if test_type == "regime" and summary.get("status") == "ok":
        if not summary.get("oos_significant"):
            summary["queue_recommendation"] = "DO_NOT_QUEUE"
            summary["known_dead_end"] = True
            summary["queue_block_reason"] = (
                "Regime test is IS-significant but NOT OOS-validated. IS-only regime "
                "significance (time-varying beta) belongs to an audited dead-end "
                "family (vix30_threshold_audit_complete_2026_04_20). Do not queue as "
                "a scan hit. Only OOS-validated regime relationships are queue-worthy."
            )
        elif result.get("tradeable_retest", {}).get("tradeable") is False:
            # OOS-"validated" but FAILS the 1-day-lagged tradeable retest -> the
            # contemporaneous regime classification is look-ahead (e.g. VIX spikes ON
            # down days). OOS sign-preservation cannot catch this. Do not queue.
            # (lookahead_regime_lag_fix_2026_06_11)
            tr = result["tradeable_retest"]
            summary["queue_recommendation"] = "DO_NOT_QUEUE"
            summary["known_dead_end"] = True
            summary["queue_block_reason"] = (
                "Regime effect is OOS-validated by sign-preservation but FAILS the "
                f"1-day-lagged tradeable retest (lagged p={tr.get('lagged_p_value')}, "
                f"lagged spread={tr.get('lagged_high_low_spread')}). The contemporaneous "
                "factor classification is look-ahead (factor and same-day return are "
                "mechanically correlated); the effect does not survive when the regime is "
                "known at decision time. NOT TRADEABLE. See lookahead_regime_lag_fix_2026_06_11."
            )
        else:
            # OOS-validated: still consult the dead-end registry by signal key.
            try:
                from tools.scan_hit_dead_end_check import is_known_dead_end
                _key = (f"{args.target}_{args.factor}_regime"
                        .replace("^", "").replace("FRED:", "").lower())
                _dead, _reason = is_known_dead_end(signal_key=_key)
                if _dead:
                    summary["queue_recommendation"] = "DO_NOT_QUEUE"
                    summary["known_dead_end"] = True
                    summary["queue_block_reason"] = _reason
            except Exception:
                pass

    result_id = _store_result(f"regression_{test_type}", params, result, json.dumps(summary, default=str))
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_cointegration(args):
    """Run Engle-Granger cointegration test."""
    from tools.timeseries import get_series
    import causal_tests

    # Structural dead-end guard (enforces cointegration_scan_hit_batch_closure_2026_04_24
    # and cointegration_common_shock_anchor_rule_2026_04_19). The scanner reads this
    # output, so surfacing the dead-end status here prevents re-queuing resolved pairs
    # (BA-RTX, V-MA, TSLA-F, etc.) as scan hits.
    try:
        from tools.scan_hit_dead_end_check import is_known_dead_end
        _dead, _reason = is_known_dead_end(pair=(args.series_a, args.series_b))
        if _dead:
            print(json.dumps({
                "status": "KNOWN_DEAD_END",
                "known_dead_end": True,
                "pair": f"{args.series_a}/{args.series_b}",
                "warning": "DO NOT QUEUE AS SCAN HIT — this pair is a recorded dead end.",
                "reason": _reason,
            }, indent=2))
            return
    except Exception:
        pass  # never let the guard block a legitimate test

    try:
        series_a = get_series(args.series_a, args.start, args.end)
        series_b = get_series(args.series_b, args.start, args.end)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return

    result = causal_tests.test_cointegration(series_a, series_b, oos_start=args.oos_start)
    params = {"series_a": args.series_a, "series_b": args.series_b,
              "start": args.start, "end": args.end, "oos_start": args.oos_start}

    summary = _causal_summary(result)

    # OOS-validation queue guard (enforces
    # cointegration_scan_hit_is_only_oos_fail_do_not_queue_rule_2026_06_10).
    # Engle-Granger cointegration is IS-significant for many co-moving peer pairs
    # by construction (shared sector/common shock), but the spread is only
    # TRADEABLE if it remains stationary out-of-sample. Scan hits that are
    # IS-significant with oos_significant != True (e.g. BA/LMT OOS ADF p=0.154,
    # CLF/MT OOS ADF p=0.986) have repeatedly been queued at P8 and resolved as
    # dead ends, wasting orchestrator sessions. The scanner reads this field:
    # only queue cointegration hits where queue_recommendation == "QUEUE_OK".
    if args.oos_start and summary.get("significant"):
        if summary.get("oos_significant") is True:
            summary["queue_recommendation"] = "QUEUE_OK"
        else:
            summary["queue_recommendation"] = "DO_NOT_QUEUE"
            summary["queue_guard_reason"] = (
                "IS-significant but spread NOT stationary out-of-sample "
                "(oos_significant != True). Not tradeable. Record as "
                "DEAD_END_COINTEGRATION_OOS_FAIL instead of queuing."
            )

    result_id = _store_result("cointegration", params, result, json.dumps(summary, default=str))
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_threshold(args):
    """Run threshold-triggered event study.

    When the raw test is significant (p<0.05), automatically runs a canonical
    retest using first-close cluster-buffered event counting + SPY-adjusted
    abnormal returns on two samples (pooled + post-2020 recency). This guards
    against raw-crossing inflation and regime-specific signals.

    See threshold_scan_hit_canonical_retest_rule_2026_04_18.
    """
    from tools.timeseries import get_series, get_returns
    import causal_tests

    try:
        trigger = get_series(args.trigger, args.start, args.end)
        target_rets = get_returns(args.target, args.start, args.end)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return

    horizons = [int(h) for h in args.horizons.split(",")] if args.horizons else [5, 10, 20]
    result = causal_tests.test_threshold(trigger, target_rets, args.threshold_value,
                                          direction=args.direction, horizons=horizons)
    params = {"trigger": args.trigger, "target": args.target, "threshold": args.threshold_value,
              "direction": args.direction, "horizons": horizons, "start": args.start, "end": args.end}

    summary = _causal_summary(result)

    # Auto-run canonical retest when raw test is significant (unless explicitly skipped)
    skip_canonical = getattr(args, "skip_canonical", False)
    raw_sig = result.get("p_value") is not None and result.get("p_value") < 0.05
    if raw_sig and not skip_canonical:
        canonical_horizons = sorted(set([1, 3] + horizons))
        # The canonical retest exists to verify deep-history robustness via a
        # pooled (2010+) vs recent (2020+) split. It must ALWAYS reach into deep
        # history regardless of the raw test's start date. If a scanner runs the
        # raw test on a recent window (e.g. start=2024-01-01), inheriting that
        # start collapses pooled==recent and defeats the recency-robustness guard
        # -> recent-regime-only artifacts falsely PASS canonical. So clamp the
        # canonical start to the earlier of the raw start and the deep default.
        # See: threshold_canonical_retest_start_date_contamination_bug_2026_06_09.
        canonical_default_start = "2010-01-01"
        canonical_start = canonical_default_start
        if args.start and args.start < canonical_default_start:
            canonical_start = args.start
        canonical = causal_tests.canonical_retest_threshold(
            trigger_identifier=args.trigger,
            target_symbol=args.target,
            threshold=args.threshold_value,
            direction=args.direction,
            horizons=canonical_horizons,
            start=canonical_start,
            end=args.end,
        )
        # Keep the canonical result inside `result` for storage, and expose a compact
        # summary on the top-level summary so scanners can gate on `canonical_passes`.
        result["canonical_retest"] = canonical
        if "error" in canonical:
            summary["canonical_passes"] = False
            summary["canonical_error"] = canonical["error"]
        else:
            summary["canonical_passes"] = canonical.get("passes", False)
            summary["canonical_fail_reason"] = canonical.get("fail_reason")
            summary["canonical_benchmark"] = canonical.get("benchmark")
            summary["canonical_benchmark_mode"] = canonical.get("benchmark_mode")
            summary["canonical_target_class"] = canonical.get("target_class")
            summary["canonical_n_pooled"] = canonical.get("n_events_pooled")
            summary["canonical_n_recent"] = canonical.get("n_events_recent")
            pooled = canonical.get("pooled", {})
            recent = canonical.get("recent", {})
            if pooled.get("best_horizon"):
                bh = pooled["best_horizon"]
                hs = pooled["horizons"][bh]
                summary["canonical_pooled_best"] = {
                    "horizon": bh,
                    "mean": hs["abnormal_mean"],
                    "p_value": hs["p_value"],
                    "positive_rate": hs["positive_rate"],
                }
            if recent.get("best_horizon"):
                bh = recent["best_horizon"]
                hs = recent["horizons"][bh]
                summary["canonical_recent_best"] = {
                    "horizon": bh,
                    "mean": hs["abnormal_mean"],
                    "p_value": hs["p_value"],
                    "positive_rate": hs["positive_rate"],
                }
            summary["canonical_summary"] = canonical.get("summary")
    elif not raw_sig:
        summary["canonical_passes"] = False
        summary["canonical_skipped"] = "raw_not_significant"

    result_id = _store_result("threshold", params, result, json.dumps(summary, default=str))
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_calendar(args):
    """Run calendar anomaly test."""
    from tools.timeseries import get_returns
    import causal_tests

    try:
        rets = get_returns(args.symbol, args.start, args.end)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return

    pattern_spec = None
    if args.pattern_month:
        pattern_spec = {"month": int(args.pattern_month)}

    result = causal_tests.test_calendar(rets, args.pattern, pattern_spec=pattern_spec,
                                         oos_start_year=args.oos_start_year)
    params = {"symbol": args.symbol, "pattern": args.pattern, "pattern_spec": pattern_spec,
              "oos_start_year": args.oos_start_year, "start": args.start, "end": args.end}

    summary = _causal_summary(result)
    result_id = _store_result("calendar", params, result, json.dumps(summary, default=str))
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_fetch_series(args):
    """Fetch and display time series data."""
    from tools.timeseries import get_aligned_series

    identifiers = [s.strip() for s in args.identifiers.split(",")]
    try:
        df = get_aligned_series(identifiers, args.start, args.end)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return

    summary = {
        "status": "ok",
        "identifiers": identifiers,
        "start": str(df.index[0].date()),
        "end": str(df.index[-1].date()),
        "n_days": len(df),
        "stats": {},
    }
    for col in df.columns:
        s = df[col]
        summary["stats"][col] = {
            "mean": round(float(s.mean()), 4),
            "std": round(float(s.std()), 4),
            "min": round(float(s.min()), 4),
            "max": round(float(s.max()), 4),
            "last": round(float(s.iloc[-1]), 4),
        }

    # df.to_dict() produces Timestamp keys which json.dumps rejects. Convert index to strings first.
    df_serializable = df.copy()
    df_serializable.index = df_serializable.index.astype(str)
    result_id = _store_result("fetch_series", {"identifiers": identifiers, "start": args.start, "end": args.end},
                               df_serializable.to_dict(), json.dumps(summary, default=str))
    summary["result_id"] = result_id
    print(json.dumps(summary, indent=2, default=str))


def cmd_oos(args):
    """OOS observation tracker dispatcher."""
    import oos_tracker

    if args.oos_command == "register":
        result = oos_tracker.register_observation(
            signal_type=args.signal_type,
            symbol=args.symbol,
            entry_date=args.entry_date,
            hold_days=args.hold_days,
            direction=args.direction,
            threshold=args.threshold,
            benchmark=args.benchmark,
            hypothesis_id=args.hypothesis_id,
            notes=args.notes,
        )
    elif args.oos_command == "update":
        result = oos_tracker.update_all_active()
    elif args.oos_command == "status":
        result = oos_tracker.get_status_summary(
            signal_type=args.signal_type,
            include_completed=args.show_all,
        )
    elif args.oos_command == "close":
        result = oos_tracker.close_observation(args.id, args.result)
    else:
        result = {"status": "error", "error": f"Unknown oos command: {args.oos_command}"}

    print(json.dumps(result, indent=2, default=str))


def main():
    db.init_db()

    parser = argparse.ArgumentParser(description="Data task dispatcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # backtest
    bt = subparsers.add_parser("backtest", help="Run measure_event_impact")
    bt.add_argument("--events", help="JSON list of {symbol, date, timing} dicts")
    bt.add_argument("--symbol", help="Single symbol (use with --dates)")
    bt.add_argument("--dates", help="JSON list of date strings (use with --symbol)")
    bt.add_argument("--benchmark", default="SPY")
    bt.add_argument("--sector-etf")
    bt.add_argument("--entry-price", choices=["close", "open"])
    bt.add_argument("--event-timing", choices=["pre_market", "intraday", "after_hours", "unknown"])
    bt.add_argument("--event-type", help="Event type for cost estimation")
    bt.add_argument("--estimate-costs", action="store_true")
    bt.add_argument("--regime-filter")

    # verify-date
    vd = subparsers.add_parser("verify-date", help="Verify an event date")
    vd.add_argument("--event", required=True)
    vd.add_argument("--expected-date", required=True)

    # largecap-filter
    lf = subparsers.add_parser("largecap-filter", help="Filter to large-cap symbols")
    lf.add_argument("--symbols", required=True, help="JSON list of symbols")

    # price-history
    ph = subparsers.add_parser("price-history", help="Fetch price history")
    ph.add_argument("--symbol", required=True)
    ph.add_argument("--days", type=int, default=90)

    # get-result
    gr = subparsers.add_parser("get-result", help="Get stored result by ID")
    gr.add_argument("--id", required=True)

    # scan-insiders
    si = subparsers.add_parser("scan-insiders", help="Scan EDGAR for insider buying clusters")
    si.add_argument("--days", type=int, default=14, help="Days to look back")
    si.add_argument("--min-insiders", type=int, default=3)
    si.add_argument("--min-value", type=int, default=50000)

    # scan-insiders-evaluate
    sie = subparsers.add_parser("scan-insiders-evaluate", help="Scan + GO/NO-GO evaluate clusters")
    sie.add_argument("--days", type=int, default=14, help="Days to look back")
    sie.add_argument("--min-insiders", type=int, default=3)
    sie.add_argument("--min-value", type=int, default=50000)

    # --- Non-event hypothesis commands ---

    # regression (exposure / lead_lag / structural_break / regime / network)
    reg = subparsers.add_parser("regression", help="Run regression-based causal test")
    reg.add_argument("--target", required=True, help="Target symbol or series ID")
    reg.add_argument("--factor", required=True, help="Factor/driver symbol or series ID")
    reg.add_argument("--controls", help="Comma-separated control symbols (e.g., SPY,XLE)")
    reg.add_argument("--test-type", default="exposure",
                     choices=["exposure", "lead_lag", "structural_break", "regime", "network"],
                     help="Type of regression test")
    reg.add_argument("--start", default="2020-01-01", help="Start date")
    reg.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="End date")
    reg.add_argument("--oos-start", help="OOS validation start date (default: auto 70/30)")
    reg.add_argument("--max-lags", type=int, help="Max lags for lead_lag/network (default 10)")
    reg.add_argument("--break-date", help="Break date for structural_break test")

    # cointegration
    coint = subparsers.add_parser("cointegration", help="Run Engle-Granger cointegration test")
    coint.add_argument("--series-a", required=True, help="First series identifier")
    coint.add_argument("--series-b", required=True, help="Second series identifier")
    coint.add_argument("--start", default="2020-01-01")
    coint.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    coint.add_argument("--oos-start", help="OOS validation start date")

    # threshold
    thr = subparsers.add_parser("threshold", help="Threshold-triggered event study")
    thr.add_argument("--trigger", required=True, help="Trigger series (e.g., ^VIX)")
    thr.add_argument("--target", required=True, help="Target series for measuring returns")
    thr.add_argument("--threshold-value", required=True, type=float)
    thr.add_argument("--direction", default="above", choices=["above", "below"])
    thr.add_argument("--horizons", default="5,10,20", help="Comma-separated horizons in days")
    thr.add_argument("--start", default="2015-01-01")
    thr.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    thr.add_argument("--skip-canonical", action="store_true",
                     help="Skip auto-canonical-retest (for debugging / raw-only runs)")

    # calendar
    cal = subparsers.add_parser("calendar", help="Calendar anomaly test")
    cal.add_argument("--symbol", required=True)
    cal.add_argument("--pattern", required=True, choices=["monthly", "dow", "tom"])
    cal.add_argument("--pattern-month", type=int, help="Specific month to test (1-12)")
    cal.add_argument("--oos-start-year", type=int, help="Year to start OOS validation")
    cal.add_argument("--start", default="2005-01-01")
    cal.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))

    # fetch-series
    fs = subparsers.add_parser("fetch-series", help="Fetch and display time series data")
    fs.add_argument("--identifiers", required=True, help="Comma-separated series identifiers")
    fs.add_argument("--start", default="2020-01-01")
    fs.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))

    # oos — OOS observation tracker
    oos = subparsers.add_parser("oos", help="OOS observation tracker")
    oos_sub = oos.add_subparsers(dest="oos_command", required=True)

    oos_reg = oos_sub.add_parser("register", help="Register new OOS observation")
    oos_reg.add_argument("--signal-type", required=True)
    oos_reg.add_argument("--symbol", required=True)
    oos_reg.add_argument("--entry-date", required=True, help="ISO date, e.g. 2026-04-15")
    oos_reg.add_argument("--hold-days", type=int, default=5)
    oos_reg.add_argument("--direction", required=True, choices=["long", "short"])
    oos_reg.add_argument("--threshold", type=float, default=None, help="Success threshold (e.g., -2.5 for short)")
    oos_reg.add_argument("--benchmark", default="SPY")
    oos_reg.add_argument("--hypothesis-id", default=None)
    oos_reg.add_argument("--notes", default=None)

    oos_sub.add_parser("update", help="Update all active OOS observations with latest prices")

    oos_st = oos_sub.add_parser("status", help="Show OOS observation status")
    oos_st.add_argument("--signal-type", default=None)
    oos_st.add_argument("--all", action="store_true", dest="show_all", help="Include completed")

    oos_cl = oos_sub.add_parser("close", help="Close an OOS observation")
    oos_cl.add_argument("--id", required=True)
    oos_cl.add_argument("--result", required=True, choices=["validated", "failed"])

    args = parser.parse_args()

    commands = {
        "backtest": cmd_backtest,
        "verify-date": cmd_verify_date,
        "largecap-filter": cmd_largecap_filter,
        "price-history": cmd_price_history,
        "get-result": cmd_get_result,
        "scan-insiders": cmd_scan_insiders,
        "scan-insiders-evaluate": cmd_scan_insiders_evaluate,
        "regression": cmd_regression,
        "cointegration": cmd_cointegration,
        "threshold": cmd_threshold,
        "calendar": cmd_calendar,
        "fetch-series": cmd_fetch_series,
        "oos": cmd_oos,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
