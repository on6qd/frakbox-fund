"""
Insider Cluster GO/NO-GO Evaluator
===================================
Automates the evaluation of detected insider buying clusters against
validated criteria from the N=5,364 feature analysis (April 2026).

Codifies the GO/NO-GO decision so sessions can auto-activate trades
faster and miss fewer entry windows.

Usage:
    python3 tools/insider_cluster_evaluator.py --ticker ZBIO --n-insiders 3 \
        --total-value 9300000 --has-ceo --has-cfo --detection-price 20.36

    # Programmatic use:
    from tools.insider_cluster_evaluator import evaluate_cluster
    result = evaluate_cluster(
        ticker="ZBIO", n_insiders=3, total_value_usd=9_300_000,
        has_ceo=True, has_cfo=True, detection_price=20.36
    )
    print(result["decision"])  # "GO", "WEAK_GO", "NO_GO"

Criteria source: insider_cluster_feature_analysis_n5364,
    insider_cluster_vix30_gate_revision, insider_cluster_multihorizon_analysis
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.yfinance_utils import safe_download


# ==========================================================================
# Validated thresholds from feature analysis (N=5,364 clusters, 2004-2026)
# ==========================================================================

# CEO/CFO + 3-5 insiders is the optimal profile
# avg=+3.52%, 56.1% positive, p<0.0001 (full dataset)
# Large-cap (>500M): avg=+2.42%, 57.6% positive at 5d

OPTIMAL_N_RANGE = (3, 5)       # 6+ insiders actually underperform
MIN_MARKET_CAP_M = 500         # Large-cap filter
# Prior-drawdown boost (4c) is a micro/small/mid-cap effect that vanishes
# (and slightly inverts) at large cap. Cap-condition the boost: clusters with
# market cap >= this threshold get NO drawdown score. See
# deep_drawdown_insider_outperformance_microcap_only_survivorship_inflated_rule_2026_06_20
# and the 20-day-drawdown cap-split confirmation (2026-06-21): large >=$10B dip<=-10%
# earns +0.45% (vs +0.87% no-dip) — no edge, vs micro +4.56% / small +4.39%.
DRAWDOWN_BOOST_MAX_CAP_M = 10000
CHASE_FILTER_PCT = 30          # Max % above detection price
MAX_POSITIONS = 5              # Portfolio capacity
HOLD_DAYS = 5                  # Optimal hold period
POSITION_SIZE = 5000           # Standard experiment size
STOP_LOSS_PCT = 15.0           # Standard stop
TAKE_PROFIT_PCT = 20.0         # Standard TP

# VIX tier expected values (CEO/CFO + 3-5 insiders, 5d)
# NOTE: These are TRANS_DATE-based historical numbers (overstate real-time returns
# by ~2-3pp due to filing lag drift). The canonical real-time benchmark
# (insider_cluster_canonical_benchmark_2026_04_08) puts t+1 entry at +1.26% mean,
# 42.5% pos rate. Reduce sizing accordingly when filing_lag>=1d forces t+1 entry.
VIX_TIERS = {
    "low":     {"range": (0, 20),   "ev": 7.01, "pos_rate": 60.0, "note": "Full strength"},
    "medium":  {"range": (20, 25),  "ev": 3.24, "pos_rate": 56.0, "note": "Reduced but positive"},
    "elevated":{"range": (25, 30),  "ev": 6.79, "pos_rate": 55.0, "note": "Historically strong, regime caution"},
    "high":    {"range": (30, 100), "ev": 3.49, "pos_rate": 55.1, "note": "CEO conviction amplified in panic"},
}

# Canonical real-time t+1 benchmark (insider_cluster_canonical_benchmark_2026_04_08)
REAL_TIME_T1_EV = 1.26   # mean 5d abnormal at filing_date+1 entry, CEO/CFO + n[3,5] + lag<=1
REAL_TIME_T1_POS = 42.5


def get_vix() -> float:
    """Get latest VIX close."""
    import yfinance as yf
    vix = yf.Ticker("^VIX")
    hist = vix.history(period="5d")
    if hist.empty:
        return float("nan")
    return float(hist["Close"].iloc[-1])


def get_market_cap(ticker: str) -> Optional[float]:
    """Get market cap in millions."""
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
        cap = info.get("marketCap", 0)
        return cap / 1e6 if cap else None
    except Exception:
        return None


def get_current_price(ticker: str) -> Optional[float]:
    """Get latest close price."""
    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    df = safe_download(ticker, start=start, end=end)
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


def get_spy_vs_ma() -> dict:
    """Check SPY vs 20d moving average."""
    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    df = safe_download("SPY", start=start, end=end)
    if df is None or df.empty:
        return {"spy": None, "ma20": None, "pct_vs_ma": None}
    closes = df["Close"]
    spy = float(closes.iloc[-1])
    ma20 = float(closes.tail(20).mean())
    pct = (spy - ma20) / ma20 * 100
    return {"spy": spy, "ma20": ma20, "pct_vs_ma": round(pct, 2)}


def get_active_position_count() -> int:
    """Count active hypothesis positions."""
    try:
        import db
        db.init_db()
        hyps = db.load_hypotheses()
        return sum(1 for h in hyps if h.get("status") == "active")
    except Exception:
        return 0


def classify_trigger_class(acceptance_time_str: Optional[str] = None) -> str:
    """
    Classify a filing's trigger class based on EDGAR acceptanceDateTime.

    Source: insider_cluster_intraday_replay_2026_04_09
    - intraday_same_day: Filed during market hours (9:30-16:00 ET). ~17% of clusters.
      EV: +1.87% mean, 62.2% pos rate. Same-session entry achievable.
    - pre_open_same_day: Filed before 9:30 ET. ~8% of clusters.
      EV: +2.52% mean, 61.1% pos rate. Open-entry achievable.
    - after_close_same_day: Filed after 16:00 ET. ~71% of clusters.
      EV: filing+1 5d mean +1.35%, 45.9% pos rate. MUST WAIT until next open.
    - unknown: No acceptance time available.
    """
    if not acceptance_time_str:
        return "unknown"

    try:
        from datetime import datetime
        # EDGAR acceptanceDateTime format: "2026-04-09T14:32:15.000000-04:00" or "2026-04-09 14:32:15"
        clean = acceptance_time_str.replace("T", " ").split(".")[0].split("-04:00")[0].split("-05:00")[0]
        dt = datetime.strptime(clean.strip(), "%Y-%m-%d %H:%M:%S")
        hour = dt.hour
        minute = dt.minute
        market_open = (9, 30)
        market_close = (16, 0)

        if (hour, minute) < market_open:
            return "pre_open_same_day"
        elif (hour, minute) < market_close:
            return "intraday_same_day"
        else:
            return "after_close_same_day"
    except Exception:
        return "unknown"


# Trigger class expected values from intraday replay analysis (April 2026)
TRIGGER_CLASS_EV = {
    "intraday_same_day":    {"ev_5d": 1.87, "pos_rate": 62.2, "entry": "same_session",   "tradeable": True},
    "pre_open_same_day":    {"ev_5d": 2.52, "pos_rate": 61.1, "entry": "open_same_day",  "tradeable": True},
    "after_close_same_day": {"ev_5d": 1.35, "pos_rate": 45.9, "entry": "next_open",      "tradeable": False},
    "unknown":              {"ev_5d": 1.04, "pos_rate": 45.5, "entry": "unknown",         "tradeable": False},
}


def evaluate_cluster(
    ticker: str,
    n_insiders: int,
    total_value_usd: float,
    has_ceo: bool = False,
    has_cfo: bool = False,
    detection_price: Optional[float] = None,
    insiders_detail: Optional[str] = None,
    days_since_latest_filing: Optional[int] = None,
    max_trans_to_filing_lag: Optional[int] = None,
    acceptance_time: Optional[str] = None,
) -> dict:
    """
    Evaluate an insider buying cluster for trade activation.

    Returns dict with:
        decision: "GO", "WEAK_GO", "NO_GO"
        reasons: list of strings explaining the decision
        trade_plan: dict with entry params (if GO/WEAK_GO)
        score: 0-10 signal quality score
    """
    reasons = []
    warnings = []
    blockers = []
    score = 0

    # --- Fetch market data ---
    vix = get_vix()
    market_cap = get_market_cap(ticker)
    current_price = get_current_price(ticker)
    spy_info = get_spy_vs_ma()
    positions = get_active_position_count()

    # --- VIX tier ---
    vix_tier = None
    vix_ev = 0
    for tier_name, tier in VIX_TIERS.items():
        lo, hi = tier["range"]
        if lo <= vix < hi:
            vix_tier = tier_name
            vix_ev = tier["ev"]
            break

    # --- Evaluate criteria ---

    # 1. CEO/CFO presence (most important feature)
    has_csuite = has_ceo or has_cfo
    if has_ceo and has_cfo:
        score += 3
        reasons.append(f"✓ CEO+CFO present (strongest signal, +12x predictive)")
    elif has_ceo:
        score += 2.5
        reasons.append(f"✓ CEO present (strong conviction signal)")
    elif has_cfo:
        score += 2
        reasons.append(f"✓ CFO present (financial officer conviction)")
    else:
        score += 0.5
        warnings.append(f"⚠ No CEO/CFO — weaker signal (baseline only)")

    # 2. Number of insiders (3-5 optimal, 6+ underperforms)
    if OPTIMAL_N_RANGE[0] <= n_insiders <= OPTIMAL_N_RANGE[1]:
        score += 2
        reasons.append(f"✓ n={n_insiders} insiders (optimal range 3-5)")
    elif n_insiders < OPTIMAL_N_RANGE[0]:
        blockers.append(f"✗ n={n_insiders} below minimum 3 insiders")
    else:
        score += 0.5
        warnings.append(f"⚠ n={n_insiders} above optimal (6+ underperforms historically)")

    # 3. Total value
    total_m = total_value_usd / 1e6
    if total_value_usd >= 5_000_000:
        score += 2
        reasons.append(f"✓ Total value ${total_m:.1f}M (very strong conviction)")
    elif total_value_usd >= 1_000_000:
        score += 1.5
        reasons.append(f"✓ Total value ${total_m:.1f}M (good conviction)")
    elif total_value_usd >= 200_000:
        score += 0.5
        warnings.append(f"⚠ Total value ${total_m:.2f}M (modest — need CEO/CFO for confidence)")
    else:
        warnings.append(f"⚠ Total value ${total_m:.2f}M (very low)")

    # 4. Market cap (large-cap filter)
    if market_cap is not None:
        if market_cap >= MIN_MARKET_CAP_M:
            score += 1
            reasons.append(f"✓ Market cap ${market_cap:.0f}M (≥${MIN_MARKET_CAP_M}M)")
        else:
            blockers.append(f"✗ Market cap ${market_cap:.0f}M (below ${MIN_MARKET_CAP_M}M filter)")
    else:
        warnings.append(f"⚠ Market cap unavailable — manual check needed")

    # 4b. IPO/recent listing filter (stocks with <20 trading days are likely IPOs)
    try:
        import yfinance as yf
        hist = yf.download(ticker, period="3mo", progress=False)
        trading_days = len(hist) if hist is not None else 0
        if trading_days < 20:
            blockers.append(f"✗ Only {trading_days} trading days — likely recent IPO/listing (need ≥20)")
    except Exception:
        pass  # Fail open — other checks will catch issues

    # 4c. Prior stock drawdown — strongest quality predictor per N=5364 analysis
    # CEO + VIX<25 + prior_drop>10%: avg 4.55%, pos_rate 59.6% (best subgroup)
    prior_drawdown_pct = None
    try:
        from datetime import datetime, timedelta
        _end = datetime.now().strftime('%Y-%m-%d')
        _start = (datetime.now() - timedelta(days=40)).strftime('%Y-%m-%d')
        hist = safe_download(ticker, _start, _end)
        if hist is not None and len(hist) >= 10:
            closes = hist['Close'] if 'Close' in hist.columns else hist['close']
            peak_20d = closes.iloc[-20:].max() if len(closes) >= 20 else closes.max()
            current_close = closes.iloc[-1]
            prior_drawdown_pct = round((current_close - peak_20d) / peak_20d * 100, 1)
            # Cap-condition the boost: the drawdown edge is micro/small/mid-cap only
            # and disappears at large cap (see DRAWDOWN_BOOST_MAX_CAP_M note). For large
            # caps we still REPORT the drawdown but add no score (do not treat a
            # large-cap deep drawdown — e.g. FISV's -73% from 52w high — as a tailwind).
            dd_boost_eligible = (market_cap is None) or (market_cap < DRAWDOWN_BOOST_MAX_CAP_M)
            if not dd_boost_eligible:
                reasons.append(
                    f"○ Prior drawdown {prior_drawdown_pct}% — NOT scored "
                    f"(large cap ${market_cap:.0f}M ≥ ${DRAWDOWN_BOOST_MAX_CAP_M}M: "
                    f"drawdown boost is micro/small/mid-cap only)")
            elif prior_drawdown_pct <= -15:
                score += 1.5
                reasons.append(f"✓ Prior drawdown {prior_drawdown_pct}% (deep dip — insiders buying conviction)")
            elif prior_drawdown_pct <= -10:
                score += 1
                reasons.append(f"✓ Prior drawdown {prior_drawdown_pct}% (meaningful dip — good conviction)")
            elif prior_drawdown_pct <= -5:
                score += 0.5
                reasons.append(f"✓ Prior drawdown {prior_drawdown_pct}% (moderate dip)")
            else:
                reasons.append(f"○ Prior drawdown {prior_drawdown_pct}% (no significant dip — neutral)")
    except Exception:
        pass  # Fail open

    # 5. VIX regime
    if vix_tier:
        if has_csuite:
            score += 1
            reasons.append(f"✓ VIX={vix:.1f} ({vix_tier}): CEO/CFO signal valid at all VIX levels. EV=+{vix_ev}%")
        elif vix < 25:
            score += 1
            reasons.append(f"✓ VIX={vix:.1f} ({vix_tier}): Acceptable for non-CEO clusters")
        else:
            warnings.append(f"⚠ VIX={vix:.1f} ({vix_tier}): Non-CEO clusters weaker at elevated VIX")

    # 6. Chase filter
    if detection_price and current_price:
        chase_pct = (current_price - detection_price) / detection_price * 100
        if chase_pct > CHASE_FILTER_PCT:
            blockers.append(f"✗ Price moved +{chase_pct:.1f}% from detection (>{CHASE_FILTER_PCT}% chase filter)")
        elif chase_pct > 15:
            warnings.append(f"⚠ Price moved +{chase_pct:.1f}% from detection (high but within filter)")
        else:
            reasons.append(f"✓ Price {'+' if chase_pct >= 0 else ''}{chase_pct:.1f}% from detection (within filter)")

    # 7. Portfolio capacity
    if positions >= MAX_POSITIONS:
        blockers.append(f"✗ Portfolio at {positions}/{MAX_POSITIONS} capacity")
    else:
        reasons.append(f"✓ Portfolio {positions}/{MAX_POSITIONS} (capacity available)")

    # 8. SPY regime
    if spy_info["pct_vs_ma"] is not None:
        if spy_info["pct_vs_ma"] < -3:
            warnings.append(f"⚠ SPY {spy_info['pct_vs_ma']}% vs 20d MA (acute selloff risk)")
        else:
            reasons.append(f"✓ SPY {spy_info['pct_vs_ma']:+.1f}% vs 20d MA")

    # 9. Filing freshness — HARD BLOCK on any t+1 or later entry
    # Source: insider_cluster_canonical_benchmark_2026_04_08 (RETIREMENT decision 2026-04-08)
    # Real-time scanner_t_plus_1 cadence = +1.26% mean, 42.5% pos rate (fails 50% directional threshold,
    # only marginal vs round-trip costs). Only filing_day same-day intraday entry is tradeable
    # (+3.28% mean, 58.8% pos rate). Until an intraday EDGAR poller exists, t+1 entries are NO_GO.
    # ZBIO (placed pre-retirement at filing_lag=1) is grandfathered — let it run, do not open new t+1s.
    trigger_class = classify_trigger_class(acceptance_time)
    trigger_ev = TRIGGER_CLASS_EV.get(trigger_class, TRIGGER_CLASS_EV["unknown"])

    if days_since_latest_filing is not None:
        if days_since_latest_filing > 1:
            blockers.append(
                f"✗ Latest filing is {days_since_latest_filing} business days old "
                f"(>1bd hard block — alpha decayed; see insider_cluster_filing_lag_drift)"
            )
        elif days_since_latest_filing == 1:
            blockers.append(
                f"✗ Latest filing is 1 business day old — t+1 entry RETIRED "
                f"(canonical benchmark 42.5% pos rate, fails 50% directional threshold). "
                f"Build intraday scanner to capture this signal."
            )
        else:
            # Filing is today (lag=0) — check trigger class for realistic entry timing
            if trigger_class in ("intraday_same_day", "pre_open_same_day"):
                score += 1.5
                reasons.append(
                    f"✓ Filing today, trigger={trigger_class}: same-session entry achievable "
                    f"(EV={trigger_ev['ev_5d']}%, pos_rate={trigger_ev['pos_rate']}%)"
                )
            elif trigger_class == "after_close_same_day":
                # After-close filings force next-open entry — much weaker
                # EV +1.35%, pos_rate 45.9% — below 50% directional threshold
                warnings.append(
                    f"⚠ Filing today but AFTER MARKET CLOSE: must enter at next open. "
                    f"EV={trigger_ev['ev_5d']}%, pos_rate={trigger_ev['pos_rate']}% "
                    f"(below 50% threshold). Downgrading to WEAK_GO at best."
                )
                # Reduce score penalty for after-close
                score -= 0.5
            else:
                # Unknown trigger class — conservative treatment
                score += 0.5
                warnings.append(
                    f"⚠ Filing today but trigger class unknown — "
                    f"cannot confirm same-session entry. Conservative EV={trigger_ev['ev_5d']}%"
                )
    else:
        warnings.append("⚠ days_since_latest_filing unavailable — manual freshness check required")

    # 10. Trans-to-filing lag — secondary penalty for slow filers
    if max_trans_to_filing_lag is not None:
        if max_trans_to_filing_lag > 5:
            warnings.append(
                f"⚠ Max trans-to-filing lag = {max_trans_to_filing_lag}bd (>5bd: stale insider intent)"
            )
        elif max_trans_to_filing_lag <= 1:
            score += 0.5
            reasons.append(f"✓ Trans-to-filing lag ≤1bd (insiders filed promptly)")

    # --- Decision ---
    if blockers:
        decision = "NO_GO"
    elif trigger_class == "after_close_same_day":
        # After-close filings: EV=+1.35%, pos_rate=45.9% — below directional threshold.
        # Source: insider_cluster_intraday_replay_2026_04_09
        # Cap at WEAK_GO regardless of score — pos_rate < 50% means not confidently tradeable.
        if score >= 5 and has_csuite:
            decision = "WEAK_GO"
            warnings.append(
                "↓ Capped at WEAK_GO (after-close filing, pos_rate < 50%)"
            )
        else:
            decision = "NO_GO"
            warnings.append(
                "↓ After-close filing with insufficient score → NO_GO"
            )
    elif score >= 7 and not warnings:
        decision = "GO"
    elif score >= 5:
        decision = "GO" if has_csuite else "WEAK_GO"
    elif score >= 3 and has_csuite:
        decision = "WEAK_GO"
    else:
        decision = "NO_GO"

    # --- Trade plan ---
    trade_plan = None
    if decision in ("GO", "WEAK_GO"):
        shares = int(POSITION_SIZE / current_price) if current_price else 0
        # Use trigger-class-aware EV (from intraday replay analysis)
        # Only intraday_same_day and pre_open_same_day get the higher filing-day EV.
        # After-close and unknown get the weaker next-open EV.
        if trigger_class in ("intraday_same_day", "pre_open_same_day"):
            ev_used = trigger_ev["ev_5d"]
            ev_basis = f"trigger_class_{trigger_class}"
        elif days_since_latest_filing is not None and days_since_latest_filing == 0:
            # Filing today but after close or unknown timing — use trigger class EV
            ev_used = trigger_ev["ev_5d"]
            ev_basis = f"trigger_class_{trigger_class}_next_open"
        else:
            ev_used = REAL_TIME_T1_EV
            ev_basis = "canonical_t_plus_1_real_time"
        trade_plan = {
            "symbol": ticker,
            "direction": "long",
            "position_size": POSITION_SIZE,
            "shares": shares,
            "entry_price": current_price,
            "stop_loss_pct": STOP_LOSS_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "hold_days": HOLD_DAYS,
            "vix_at_eval": round(vix, 1),
            "vix_tier": vix_tier,
            "trigger_class": trigger_class,
            "expected_return_pct": ev_used,
            "expected_return_basis": ev_basis,
            "real_time_t1_pos_rate": REAL_TIME_T1_POS,
        }

    return {
        "ticker": ticker,
        "decision": decision,
        "score": round(score, 1),
        "reasons": reasons,
        "warnings": warnings,
        "blockers": blockers,
        "trade_plan": trade_plan,
        "market_data": {
            "vix": round(vix, 1),
            "vix_tier": vix_tier,
            "market_cap_m": round(market_cap, 0) if market_cap else None,
            "current_price": current_price,
            "spy_vs_ma_pct": spy_info["pct_vs_ma"],
            "prior_drawdown_pct": prior_drawdown_pct,
            "drawdown_boost_scored": (prior_drawdown_pct is not None) and (
                (market_cap is None) or (market_cap < DRAWDOWN_BOOST_MAX_CAP_M)),
            "active_positions": positions,
        },
        "cluster_profile": {
            "n_insiders": n_insiders,
            "total_value_usd": total_value_usd,
            "has_ceo": has_ceo,
            "has_cfo": has_cfo,
            "detection_price": detection_price,
        },
        "trigger_class": trigger_class,
        "trigger_class_ev": trigger_ev,
    }


def main():
    parser = argparse.ArgumentParser(description="Insider Cluster GO/NO-GO Evaluator")
    parser.add_argument("--ticker", required=True, help="Stock ticker")
    parser.add_argument("--n-insiders", type=int, required=True, help="Number of unique insiders")
    parser.add_argument("--total-value", type=float, required=True, help="Total purchase value in USD")
    parser.add_argument("--has-ceo", action="store_true", help="CEO is among buyers")
    parser.add_argument("--has-cfo", action="store_true", help="CFO is among buyers")
    parser.add_argument("--detection-price", type=float, help="Price when cluster was detected")
    parser.add_argument("--days-since-filing", type=int, help="Business days since latest Form 4 filing")
    parser.add_argument("--max-filing-lag", type=int, help="Max business days between transaction and filing")
    parser.add_argument("--acceptance-time", type=str,
                        help="EDGAR acceptanceDateTime of latest Form 4 (e.g., '2026-04-09T14:32:15')")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = evaluate_cluster(
        ticker=args.ticker,
        n_insiders=args.n_insiders,
        total_value_usd=args.total_value,
        has_ceo=args.has_ceo,
        has_cfo=args.has_cfo,
        detection_price=args.detection_price,
        days_since_latest_filing=args.days_since_filing,
        max_trans_to_filing_lag=args.max_filing_lag,
        acceptance_time=args.acceptance_time,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return

    # Pretty print
    d = result["decision"]
    color = {"GO": "\033[92m", "WEAK_GO": "\033[93m", "NO_GO": "\033[91m"}
    reset = "\033[0m"

    print(f"\n{'='*60}")
    print(f"INSIDER CLUSTER EVALUATION: {args.ticker}")
    print(f"{'='*60}")
    print(f"Decision: {color.get(d, '')}{d}{reset} (score: {result['score']}/10)")
    print()

    if result["reasons"]:
        print("Positives:")
        for r in result["reasons"]:
            print(f"  {r}")

    if result["warnings"]:
        print("\nWarnings:")
        for w in result["warnings"]:
            print(f"  {w}")

    if result["blockers"]:
        print("\nBlockers:")
        for b in result["blockers"]:
            print(f"  {b}")

    if result["trade_plan"]:
        tp = result["trade_plan"]
        print(f"\nTrade Plan:")
        print(f"  LONG {tp['shares']} shares of {tp['symbol']} @ ~${tp['entry_price']:.2f} = ${tp['position_size']}")
        print(f"  Stop: {tp['stop_loss_pct']}% | TP: {tp['take_profit_pct']}% | Hold: {tp['hold_days']}d")
        print(f"  VIX: {tp['vix_at_eval']} ({tp['vix_tier']}) | Expected: +{tp['expected_return_pct']}%")
        tc = tp.get("trigger_class", "unknown")
        print(f"  Trigger class: {tc} | EV basis: {tp.get('expected_return_basis', '?')}")

    # Trigger class summary
    tc = result.get("trigger_class", "unknown")
    tc_ev = result.get("trigger_class_ev", {})
    if tc != "unknown":
        entry_type = tc_ev.get("entry", "unknown")
        tradeable = "YES" if tc_ev.get("tradeable") else "NO"
        print(f"\nTrigger Class: {tc}")
        print(f"  Entry timing: {entry_type} | Tradeable: {tradeable}")
        print(f"  Historical EV: +{tc_ev.get('ev_5d', '?')}% | Pos rate: {tc_ev.get('pos_rate', '?')}%")

    print()


if __name__ == "__main__":
    main()
