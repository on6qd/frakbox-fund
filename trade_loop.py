"""
Deterministic trading loop — runs independently of the LLM research agent.

The LLM creates hypotheses and sets triggers. This loop checks triggers
and executes trades without waiting for an LLM session.

Trigger types:
  "immediate"              — execute now (market hours only)
  "next_market_open"       — execute at next market open (9:30 ET)
  "after_hours_immediate"  — execute now as an extended-hours limit order
                             (works 4:00 PM – 8:00 PM ET on weekdays)
  "2026-06-07T09:30"       — execute at specific datetime (market hours)
  None                     — no trigger, LLM will activate manually

Also handles:
  - Stop-loss / take-profit / deadline enforcement
  - Position reconciliation (Alpaca vs hypotheses)
  - Trade result logging

Runs every 2 minutes via launchd. No LLM needed.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent

ET = ZoneInfo("America/New_York")

# Add project to path
sys.path.insert(0, str(BASE_DIR))

import db as _db


def _load_hypotheses():
    return _db.load_hypotheses()


def _log_trade_action(action):
    """Append a trade action to the SQLite trade log."""
    action["timestamp"] = datetime.now().isoformat()
    _db.append_trade_log(action)


def _market_is_open():
    """Check if US stock market is currently open (9:30-16:00 ET, weekdays)."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday/Sunday
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def _is_near_open():
    """Check if we're within 5 minutes of market open."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    diff = (now_et - market_open).total_seconds()
    return 0 <= diff <= 300  # within 5 minutes after open


def _extended_hours_is_available():
    """
    Check if the after-hours session is currently active.

    Alpaca supports extended-hours limit orders during:
      - Pre-market:  4:00 AM – 9:30 AM ET
      - After-hours: 4:00 PM – 8:00 PM ET
    on weekdays only.

    For CEO departure shorts we target the after-hours window only
    (news typically drops after the close), so we check 4:00–8:00 PM ET.
    """
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday/Sunday
        return False
    after_hours_start = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    after_hours_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
    return after_hours_start <= now_et <= after_hours_end


def _trading_deadline(n_trading_days: int) -> datetime:
    """
    Compute the deadline as the close of market on the N-th trading day from now.
    Uses calendar days but skips weekends so deadline doesn't fall on Sat/Sun.
    Deadline is set to 15:55 ET on the target trading day (5 min before close).
    """
    ET = ZoneInfo("America/New_York")
    now_et = datetime.now(ET)
    trading_days = 0
    candidate = now_et

    while trading_days < n_trading_days:
        candidate = candidate + timedelta(days=1)
        # Skip weekends (0=Monday, 6=Sunday)
        if candidate.weekday() < 5:
            trading_days += 1

    # Set to 15:55 ET on the target day (5 minutes before close)
    deadline = candidate.replace(hour=15, minute=55, second=0, microsecond=0)
    return deadline


def _trigger_is_ready(trigger):
    """
    Check if a trigger condition is met.

    Returns a tuple (ready: bool, use_extended_hours: bool) so callers can
    decide which order type to use.
    """
    if trigger is None:
        return False, False

    if trigger == "immediate":
        return _market_is_open(), False

    if trigger == "next_market_open":
        return _is_near_open(), False

    if trigger == "after_hours_immediate":
        return _extended_hours_is_available(), True

    # Specific datetime trigger: "2026-06-07T09:30"
    try:
        trigger_dt = datetime.fromisoformat(trigger)
        # If no timezone, assume ET
        if trigger_dt.tzinfo is None:
            trigger_dt = trigger_dt.replace(tzinfo=ET)
        now = datetime.now(ET)
        # Trigger if we're past the time and market is open
        return now >= trigger_dt and _market_is_open(), False
    except (ValueError, TypeError):
        return False, False


def _send_trade_email(subject, actions):
    """Send email notification for trade actions."""
    try:
        from email_report import send_email
        rows = ""
        for a in actions:
            color = "#2e7d32" if a.get("success") else "#c62828"
            rows += f"""
            <tr>
                <td style="padding: 6px;">{a.get('symbol', '?')}</td>
                <td style="padding: 6px;">{a.get('action', '?')}</td>
                <td style="padding: 6px; color: {color};">{a.get('detail', '')}</td>
            </tr>"""

        html = f"""
        <html><body style="font-family: -apple-system, Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Trade Execution</h2>
        <p style="color: #888;">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background: #f0f0f0;">
                <th style="padding: 6px; text-align: left;">Symbol</th>
                <th style="padding: 6px; text-align: left;">Action</th>
                <th style="padding: 6px; text-align: left;">Detail</th>
            </tr>
            {rows}
        </table>
        <hr>
        <p style="color: #aaa; font-size: 11px;">Sent by trade_loop.py</p>
        </body></html>
        """
        send_email(subject, html)
    except Exception as e:
        print(f"Email failed: {e}", file=sys.stderr)


def _hypothesis_signal_type(h):
    """Return the canonical signal_type string for family classification."""
    return (h.get("signal_type") or h.get("event_type") or "").lower()


def _initial_family_deployed(hypotheses, alpaca_positions):
    """Compute already-deployed $ per signal family before this cycle.

    Counts dollars from currently-active hypotheses with a known family AND their
    symbol is held in Alpaca (treats cost_basis as the deployed amount).
    """
    from config import classify_signal_family
    deployed = {}
    active_syms_cost = {}
    try:
        for p in alpaca_positions:
            try:
                active_syms_cost[p.symbol] = abs(float(p.cost_basis))
            except Exception:
                pass
    except Exception:
        return deployed
    for h in hypotheses:
        if h.get("status") != "active":
            continue
        fam = classify_signal_family(_hypothesis_signal_type(h))
        if not fam:
            continue
        sym = h.get("expected_symbol")
        cost = active_syms_cost.get(sym)
        if cost is None:
            # Fall back to trade record
            tr = h.get("trade") or {}
            cost = float(tr.get("position_size") or 0)
        deployed[fam] = deployed.get(fam, 0.0) + cost
    return deployed


def _family_sort_key(h):
    """Sort key so family-preferred symbols fire before diversifiers in the same cycle.

    Lower tuple = earlier execution. Non-family hypotheses get a default (keeps existing order).
    """
    from config import classify_signal_family, SIGNAL_FAMILY_BUDGETS
    fam = classify_signal_family(_hypothesis_signal_type(h))
    if not fam:
        return (1, 0, h.get("created", ""))
    cfg = SIGNAL_FAMILY_BUDGETS.get(fam, {})
    preferred = cfg.get("preferred_symbols", [])
    sym = h.get("expected_symbol", "")
    if sym in preferred:
        # Preferred: sort by position in preferred list
        return (0, preferred.index(sym), h.get("created", ""))
    # Diversifier: after all preferred in the family
    return (0, 1000, h.get("created", ""))


def execute_pending_triggers():
    """Check all pending hypotheses for ready triggers and execute trades."""
    from trader import place_experiment, check_portfolio_drawdown, get_current_price
    from config import (
        DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_PCT, MIN_STOP_LOSS_PCT,
        MAX_CONCURRENT_EXPERIMENTS, SIGNAL_FAMILY_BUDGETS, classify_signal_family,
    )

    hypotheses = _load_hypotheses()
    hyp_active_count = sum(1 for h in hypotheses if h.get("status") == "active")

    # Also count actual Alpaca positions (catches untracked positions like CTAS)
    # Take the max to prevent silent capacity overflow
    alpaca_positions = []
    try:
        from trader import get_positions
        alpaca_positions = list(get_positions())
        alpaca_count = len(alpaca_positions)
    except Exception:
        alpaca_count = 0
    active_count = max(hyp_active_count, alpaca_count)
    if alpaca_count > hyp_active_count:
        print(f"[TRADE LOOP] WARNING: Alpaca has {alpaca_count} positions but only {hyp_active_count} active in hypothesis DB — untracked positions detected. Using {active_count} for capacity check.")

    # Signal-family budget tracker: pre-load $ already deployed to each family
    # (from currently-active positions), then increment as we activate trades this cycle.
    family_deployed = _initial_family_deployed(hypotheses, alpaca_positions)
    family_diversifier_count = {}  # family -> count of non-preferred symbols allocated this cycle

    actions = []

    # Sort so family-preferred symbols fire before diversifiers inside the same cycle.
    # This ensures the orthogonal core (e.g. XLB, EFA for vix30_basket) gets funded
    # before the correlated cluster consumes the family budget.
    ordered_hypotheses = sorted(hypotheses, key=_family_sort_key)

    for h in ordered_hypotheses:
        if h.get("status") != "pending":
            continue

        trigger = h.get("trigger")
        if not trigger:
            continue

        trigger_ready, use_extended_hours = _trigger_is_ready(trigger)
        if not trigger_ready:
            continue

        symbol = h.get("expected_symbol")
        if not symbol or symbol == "TBD":
            actions.append({
                "action": "skip",
                "symbol": "TBD",
                "hypothesis_id": h["id"],
                "detail": "Symbol still TBD — cannot execute",
                "success": False,
            })
            continue

        # Enforce max concurrent experiments
        if active_count >= MAX_CONCURRENT_EXPERIMENTS:
            actions.append({
                "action": "blocked",
                "symbol": symbol,
                "hypothesis_id": h["id"],
                "detail": f"Max {MAX_CONCURRENT_EXPERIMENTS} concurrent experiments reached",
                "success": False,
            })
            continue

        direction = h.get("expected_direction", "long")
        position_size = h.get("trigger_position_size", 5000)

        # Signal-family budget enforcement (see config.SIGNAL_FAMILY_BUDGETS).
        # Prevents N sibling hypotheses sharing one parent trigger from aggregating into
        # N*$5K of exposure to the same systemic signal (see vix30_basket_internal_correlation
        # _concentration_risk_2026_04_19).
        fam = classify_signal_family(_hypothesis_signal_type(h))
        if fam and fam in SIGNAL_FAMILY_BUDGETS:
            cfg = SIGNAL_FAMILY_BUDGETS[fam]
            max_total = cfg.get("max_total_usd", float("inf"))
            preferred = cfg.get("preferred_symbols", [])
            div_cap = cfg.get("diversifier_max_count", None)
            used = family_deployed.get(fam, 0.0)
            is_preferred = symbol in preferred
            # Diversifier cap: only take top-N from non-preferred symbols
            if not is_preferred and div_cap is not None:
                div_so_far = family_diversifier_count.get(fam, 0)
                if div_so_far >= div_cap:
                    actions.append({
                        "action": "blocked",
                        "symbol": symbol,
                        "hypothesis_id": h["id"],
                        "detail": f"Signal family {fam}: diversifier cap {div_cap} reached (correlated cluster saturated)",
                        "success": False,
                    })
                    continue
            # Budget cap: deployed + this position must not exceed max_total
            if used + position_size > max_total:
                remaining = max_total - used
                actions.append({
                    "action": "blocked",
                    "symbol": symbol,
                    "hypothesis_id": h["id"],
                    "detail": f"Signal family {fam}: budget ${max_total:.0f} exhausted (already deployed ${used:.0f}; remaining ${remaining:.0f})",
                    "success": False,
                })
                continue

        # Insider cluster signal: cap at $2,500 until intraday EDGAR scanner is built.
        # Canonical benchmark (insider_cluster_canonical_benchmark_2026_04_08) shows
        # scanner_t_plus_1 entry collapses to 42.5% pos rate / +1.26% mean abnormal —
        # half the historical edge. Reduce sizing until intraday cadence restores edge.
        signal_type = (h.get("signal_type") or "").lower()
        if "insider_cluster" in signal_type or "insider_buying" in signal_type:
            if position_size > 2500:
                print(f"[TRADE LOOP] insider cluster cap: reducing position size for {h['id']} from ${position_size} to $2500 (intraday scanner not yet built)")
                position_size = 2500

        # Portfolio drawdown check
        dd = check_portfolio_drawdown()
        if not dd.get("safe_to_trade"):
            actions.append({
                "action": "blocked",
                "symbol": symbol,
                "hypothesis_id": h["id"],
                "detail": f"Portfolio drawdown {dd.get('drawdown_pct', '?')}% exceeds limit",
                "success": False,
            })
            continue

        # Enforce minimum stop loss — every trade MUST have one
        stop_loss = h.get("trigger_stop_loss_pct")
        if stop_loss is None or stop_loss < MIN_STOP_LOSS_PCT:
            stop_loss = DEFAULT_STOP_LOSS_PCT

        # Guard: check for existing position in the same symbol to prevent
        # duplicate orders (bug found 2026-04-14: SPY trade doubled to $10K
        # because both activate_vix_spy_trade.py and trade_loop placed orders).
        try:
            from trader import get_positions
            existing_positions = {p.symbol: p for p in get_positions()}
            if symbol in existing_positions:
                pos = existing_positions[symbol]
                actions.append({
                    "action": "blocked",
                    "symbol": symbol,
                    "hypothesis_id": h["id"],
                    "detail": f"DUPLICATE GUARD: already holding {pos.qty} shares of {symbol} (cost ${float(pos.cost_basis):.0f}). Skipping to prevent double entry.",
                    "success": False,
                })
                print(f"[TRADE LOOP] DUPLICATE GUARD: {symbol} already in portfolio, skipping order")
                continue
        except Exception as e:
            print(f"[TRADE LOOP] WARNING: could not check existing positions: {e}")

        # Place the trade (extended_hours=True for after_hours_immediate trigger)
        if use_extended_hours:
            print(f"[TRADE LOOP] after_hours_immediate trigger for {symbol} — using extended hours limit order")
        result = place_experiment(symbol, direction, position_size, extended_hours=use_extended_hours)

        if result.get("success"):
            # Get current SPY price for context
            spy_price = get_current_price("SPY")

            # Activate the hypothesis
            h["status"] = "active"
            h["trade"] = {
                "entry_price": result["price_at_order"],
                "position_size": position_size,
                "entry_time": datetime.now().isoformat(),
                "order_id": result.get("order_id"),
                "deadline": h["exit_deadline"] if h.get("exit_deadline") else _trading_deadline(h.get("expected_timeframe_days", 5)).isoformat(),
                "stop_loss_pct": stop_loss,
                "take_profit_pct": h.get("trigger_take_profit_pct", DEFAULT_TAKE_PROFIT_PCT),
                "spy_at_entry": spy_price,
                "vix_at_entry": None,
                "sector_etf_at_entry": None,
                "activated_by": "trade_loop",
                "extended_hours": use_extended_hours,
                "limit_price": result.get("limit_price"),
            }
            h["trigger"] = None  # consumed

            # Save THIS hypothesis only (avoids bulk overwrite race condition)
            _db.save_hypothesis(h)
            active_count += 1

            # Increment family budget tracker
            if fam and fam in SIGNAL_FAMILY_BUDGETS:
                family_deployed[fam] = family_deployed.get(fam, 0.0) + position_size
                cfg = SIGNAL_FAMILY_BUDGETS[fam]
                preferred = cfg.get("preferred_symbols", [])
                if symbol not in preferred:
                    family_diversifier_count[fam] = family_diversifier_count.get(fam, 0) + 1

            actions.append({
                "action": "activated",
                "symbol": symbol,
                "hypothesis_id": h["id"],
                "detail": f"{direction.upper()} ${position_size} @ ${result['price_at_order']:.2f}",
                "success": True,
            })
            _log_trade_action({
                "type": "activate",
                "hypothesis_id": h["id"],
                "symbol": symbol,
                "direction": direction,
                "entry_price": result["price_at_order"],
                "position_size": position_size,
                "order_id": result.get("order_id"),
                "trigger": trigger,
            })
        else:
            actions.append({
                "action": "failed",
                "symbol": symbol,
                "hypothesis_id": h["id"],
                "detail": result.get("error", "Unknown error"),
                "success": False,
            })
            _log_trade_action({
                "type": "activate_failed",
                "hypothesis_id": h["id"],
                "symbol": symbol,
                "error": result.get("error"),
            })

    return actions


def reconcile_positions():
    """Check that Alpaca positions match hypothesis state. Returns warnings."""
    from trader import get_positions

    hypotheses = _load_hypotheses()
    active_symbols = {
        h["expected_symbol"]: h["id"]
        for h in hypotheses if h.get("status") == "active" and h.get("expected_symbol")
    }

    warnings = []
    try:
        positions = {p.symbol: p for p in get_positions()}
    except Exception as e:
        return [f"Could not connect to Alpaca: {e}"]

    # Hypotheses with no matching position
    for symbol, hyp_id in active_symbols.items():
        if symbol not in positions:
            warnings.append(f"Orphan hypothesis: {hyp_id[:8]} expects {symbol} but no Alpaca position found")

    # Positions with no matching hypothesis — auto-close when market is open
    for symbol in positions:
        if symbol not in active_symbols:
            if _market_is_open():
                # Auto-close orphan positions to prevent capacity blockage
                print(f"[RECONCILE] Auto-closing orphan position: {symbol}")
                try:
                    from trader import close_position
                    result = close_position(symbol)
                    if result.get("success"):
                        warnings.append(f"AUTO-CLOSED orphan position: {symbol} (no active hypothesis tracking it)")
                    else:
                        warnings.append(f"FAILED to close orphan position: {symbol} — {result.get('error', 'unknown')}")
                except Exception as e:
                    warnings.append(f"ERROR closing orphan {symbol}: {e}")
            else:
                warnings.append(f"Orphan position: {symbol} in Alpaca but no active hypothesis tracks it (will auto-close at next market open)")

    return warnings


def run_trading_cycle():
    """One full trading cycle: triggers, stops, reconciliation."""
    from trader import check_stop_losses

    all_actions = []

    # 1. Execute pending triggers
    trigger_actions = execute_pending_triggers()
    all_actions.extend(trigger_actions)

    # 2. Stop-loss / take-profit / deadline enforcement
    stop_actions = check_stop_losses()
    for a in stop_actions:
        if a["action"] in ("closed", "close_failed", "drawdown_alert"):
            all_actions.append({
                "action": a["action"],
                "symbol": a.get("symbol", ""),
                "hypothesis_id": a.get("hypothesis_id", ""),
                "detail": a.get("reason", a.get("message", "")),
                "success": a["action"] == "closed",
            })

    # 3. Position reconciliation
    recon_warnings = reconcile_positions()
    for w in recon_warnings:
        all_actions.append({
            "action": "reconciliation_warning",
            "symbol": "",
            "detail": w,
            "success": False,
        })

    # Email if anything happened
    if all_actions:
        subject_parts = []
        for a in all_actions:
            if a["action"] == "activated":
                subject_parts.append(f"Opened {a['symbol']}")
            elif a["action"] == "closed":
                subject_parts.append(f"Closed {a['symbol']}")
            elif a["action"] == "failed":
                subject_parts.append(f"FAILED {a['symbol']}")
        if subject_parts:
            _send_trade_email(f"Trade: {', '.join(subject_parts[:3])}", all_actions)
        else:
            # Only warnings/recon — don't email unless serious
            serious = [a for a in all_actions if a["action"] in ("close_failed", "drawdown_alert")]
            if serious:
                _send_trade_email("Trade alert", all_actions)

    return all_actions


if __name__ == "__main__":
    from config import load_env
    load_env()

    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        # Show what would happen without executing
        hypotheses = _load_hypotheses()
        print(f"Market open: {_market_is_open()}")
        print(f"Near open: {_is_near_open()}")
        print(f"Extended hours available: {_extended_hours_is_available()}")
        print()
        for h in hypotheses:
            trigger = h.get("trigger")
            if trigger:
                ready, ext = _trigger_is_ready(trigger)
                ext_flag = " [ext-hours]" if ext else ""
                print(f"  {h['id'][:8]} | {h.get('expected_symbol','TBD'):6s} | trigger={trigger} | ready={ready}{ext_flag}")
        active = [h for h in hypotheses if h.get("status") == "active"]
        if active:
            print(f"\nActive positions: {len(active)}")
            for h in active:
                print(f"  {h['id'][:8]} | {h['expected_symbol']} | deadline={str((h.get('trade') or {}).get('deadline','?'))[:10]}")
        recon = reconcile_positions()
        if recon:
            print(f"\nReconciliation warnings:")
            for w in recon:
                print(f"  {w}")
    else:
        actions = run_trading_cycle()
        if actions:
            for a in actions:
                print(f"[{a['action']}] {a.get('symbol', '')} — {a.get('detail', '')}")
        else:
            print(f"No actions. Market {'open' if _market_is_open() else 'closed'}.")
