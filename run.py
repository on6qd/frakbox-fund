#!/usr/bin/env python3
"""
Research runner — manages the hypothesis-test-learn loop.

Usage:
  python run.py --status          # show account, active experiments, research summary
  python run.py --review           # check active hypotheses against current prices
"""

import sys
import json
from datetime import datetime

from trader import get_account_summary, place_experiment, close_position
from research import (
    load_hypotheses,
    get_active_hypotheses,
    get_pending_hypotheses,
    get_research_summary,
    complete_hypothesis,
)


def print_header(text):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def show_status():
    """Show account state, active experiments, and research progress."""
    from datetime import timezone
    import zoneinfo
    now_local = datetime.now()
    now_et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    et_str = now_et.strftime("%H:%M ET")
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if market_open <= now_et <= market_close and now_et.weekday() < 5:
        market_status = "OPEN"
    elif now_et.weekday() >= 5:
        market_status = "CLOSED (weekend)"
    else:
        mins_to_open = int((market_open - now_et).total_seconds() / 60)
        if mins_to_open < 0:
            market_status = "CLOSED (after hours)"
        else:
            market_status = f"CLOSED (opens in {mins_to_open}m)"
    print_header(f"RESEARCH STATUS — {now_local.strftime('%Y-%m-%d %H:%M')} | {et_str} | Market: {market_status}")

    # Account
    summary = get_account_summary()
    print(f"\n  Account:")
    print(f"    Equity: ${summary['equity']:,.0f}")
    print(f"    Cash: ${summary['cash']:,.0f}")
    print(f"    Buying Power: ${summary['buying_power']:,.0f}")

    # Active experiments
    active = get_active_hypotheses()
    print(f"\n  Active Experiments: {len(active)}")
    for h in active:
        trade = h.get("trade", {})
        print(f"    #{h['id']} {h['expected_symbol']} ({h['expected_direction']}) — {h['event_type']}")
        print(f"       Entry: ${trade.get('entry_price', 0):.2f} | Deadline: {trade.get('deadline', 'n/a')[:10]}")
        print(f"       Thesis: {h['event_description'][:80]}")

    # Pending
    pending = get_pending_hypotheses()
    if pending:
        print(f"\n  Pending Hypotheses: {len(pending)}")
        for h in pending:
            print(f"    #{h['id']} {h['expected_symbol']} — {h['event_description'][:60]}")

    # Positions
    if summary["positions"]:
        print(f"\n  Open Positions:")
        for p in summary["positions"]:
            print(f"    {p['symbol']}: {p['qty']} shares "
                  f"(entry ${p['entry_price']:.2f}, now ${p['current_price']:.2f}, "
                  f"P&L: ${p['unrealized_pl']:+,.2f} / {p['unrealized_plpc']:+.1f}%)")

    # Research summary
    research = get_research_summary()
    print(f"\n  Research Progress:")
    print(f"    Total hypotheses: {research['total_hypotheses']}")
    print(f"    Direction accuracy: {research['direction_accuracy']}")
    if research['by_event_type']:
        print(f"    By type: {research['by_event_type']}")

    print()


def review_experiments():
    """Check active experiments — have they hit their target or deadline?"""
    print_header(f"EXPERIMENT REVIEW — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    active = get_active_hypotheses()
    if not active:
        print("\n  No active experiments to review.")
        return

    summary = get_account_summary()
    positions = {p["symbol"]: p for p in summary["positions"]}

    for h in active:
        symbol = h["expected_symbol"]
        trade = h.get("trade", {})
        entry_price = trade.get("entry_price", 0)
        deadline = trade.get("deadline", "")

        print(f"\n  Experiment #{h['id']}: {symbol} ({h['expected_direction']})")
        print(f"  Event: {h['event_description'][:80]}")
        print(f"  Expected: {h['expected_magnitude_pct']:+.1f}% within {h['expected_timeframe_days']} days")

        if symbol in positions:
            p = positions[symbol]
            print(f"  Current: {p['unrealized_plpc']:+.1f}% (${p['unrealized_pl']:+,.2f})")
            print(f"  Deadline: {deadline[:10]}")

            # Check if deadline passed
            if deadline and datetime.now().isoformat() > deadline:
                print(f"  >>> DEADLINE PASSED — should close and record result")
        else:
            print(f"  WARNING: No position found for {symbol}")

    print()


def show_context():
    """Print a compressed session context (~3K tokens instead of ~210K raw).

    Designed to be the ONLY state load an agent needs at session start.
    """
    import os
    import zoneinfo
    from collections import Counter

    import db as _db

    now_et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    et_str = now_et.strftime("%H:%M ET")
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if market_open <= now_et <= market_close and now_et.weekday() < 5:
        market_status = "OPEN"
    elif now_et.weekday() >= 5:
        market_status = "CLOSED (weekend)"
    elif now_et < market_open:
        market_status = f"CLOSED (opens in {int((market_open - now_et).total_seconds() / 60)}m)"
    else:
        market_status = "CLOSED (after hours)"

    # Account
    print(f"=== CONTEXT {now_et.strftime('%Y-%m-%d')} {et_str} | Market: {market_status} ===")
    try:
        summary = get_account_summary()
        print(f"Equity: ${summary['equity']:,.0f} | Cash: ${summary['cash']:,.0f} | Buying Power: ${summary['buying_power']:,.0f}")
        if summary["positions"]:
            print(f"\n--- POSITIONS ({len(summary['positions'])}) ---")
            for p in summary["positions"]:
                print(f"  {p['symbol']}: {p['qty']} shares @ ${p['entry_price']:.2f} "
                      f"now ${p['current_price']:.2f} ({p['unrealized_plpc']:+.1f}%)")
    except Exception as e:
        print(f"Alpaca unavailable: {e}")

    # Steer
    steer_path = os.path.join(os.path.dirname(__file__), "steer.md")
    if os.path.exists(steer_path):
        with open(steer_path) as f:
            steer = f.read().strip()
        if steer:
            print(f"\n--- STEER ---\n{steer}")

    # Active hypotheses — compact but with trade details
    active = _db.get_hypotheses_by_status("active")
    if active:
        print(f"\n--- ACTIVE TRADES ({len(active)}) ---")
        for h in active:
            trade = h.get("trade") or {}
            trigger = h.get("trigger") or ""
            line = f"  #{h['id']} {h.get('expected_symbol','?')} {h.get('expected_direction','?')}"
            if trade:
                line += f" @ ${trade.get('entry_price', 0):.2f}"
                line += f" size=${trade.get('position_size_usd', trade.get('position_size', '?'))}"
                dl = str(trade.get('deadline', ''))[:10]
                if dl:
                    line += f" deadline={dl}"
                sl = trade.get('stop_loss_pct') if trade.get('stop_loss_pct') is not None else h.get('trigger_stop_loss_pct')
                if sl is not None:
                    line += f" stop={sl}%"
            elif trigger:
                line += f" trigger={trigger}"
            line += f" | {h['event_type']}"
            desc = h.get('event_description', '')
            if desc:
                line += f" | {desc[:80]}"
            print(line)

    # Pending hypotheses — one line each
    pending = _db.get_hypotheses_by_status("pending")
    if pending:
        print(f"\n--- PENDING HYPOTHESES ({len(pending)}) ---")
        for h in pending:
            trigger = h.get("trigger", "")
            trigger_str = f" [trigger={trigger}]" if trigger else ""
            print(f"  #{h['id']} {h.get('expected_symbol','TBD')} "
                  f"{h.get('expected_direction','?')} +{h.get('expected_magnitude_pct',0)}% "
                  f"{h.get('expected_timeframe_days','?')}d "
                  f"conf:{h.get('confidence','?')}{trigger_str} | {h['event_type']}")

    # Completed/abandoned/superseded — summary only
    other_statuses = []
    for status in ("completed", "abandoned", "superseded"):
        items = _db.get_hypotheses_by_status(status)
        if items:
            correct = sum(1 for h in items if isinstance(h.get("result"), dict) and h["result"].get("direction_correct"))
            total = len(items)
            types = Counter(h.get("event_type", "unknown") for h in items)
            type_str = ", ".join(f"{t}:{n}" for t, n in types.most_common(5))
            other_statuses.append(f"  {status}: {total} ({correct}/{total} correct) [{type_str}]")
    if other_statuses:
        print(f"\n--- OTHER HYPOTHESES ---")
        for line in other_statuses:
            print(line)

    # Knowledge — names and counts only
    kb = _db.load_knowledge()
    known = kb.get("known_effects", {})
    dead = kb.get("dead_ends", [])
    lit = kb.get("literature", {})
    print(f"\n--- KNOWLEDGE ({len(known)} signals, {len(dead)} dead ends, {len(lit)} lit reviews) ---")
    if known:
        signals = []
        for name, data in known.items():
            if "DEPRECATED" in name.upper():
                continue
            status = data.get("status", data.get("verdict", ""))
            mag = next((data.get(k) for k in ("avg_magnitude_pct", "avg_abnormal_3d", "avg_1d_abnormal") if data.get(k) is not None), None)
            parts = [name]
            if mag is not None and isinstance(mag, (int, float)):
                parts.append(f"{mag:+.1f}%")
            if status:
                parts.append(str(status)[:20])
            signals.append(" ".join(parts))
        print(f"  Signals: {'; '.join(signals)}")
    if dead:
        dead_names = [d.get("event_type", "?") for d in dead[-20:]]  # last 20 only
        print(f"  Dead ends ({len(dead)} total): {', '.join(dead_names)}")

    # Research pipeline — the document inbox (canonical research state; see RESEARCH_DOCS.md)
    try:
        import research_docs
        print()
        research_docs.summary()
    except Exception as e:
        print(f"\n--- RESEARCH PIPELINE: unavailable ({e}) ---")

    # Research queue — priorities, handoff, top pending tasks
    rq = _db.load_queue()
    handoff = rq.get("session_handoff", {})
    priorities = rq.get("next_session_priorities", [])
    pending_tasks = [t for t in rq.get("queue", []) if t.get("status") == "pending"]
    completed_tasks = [t for t in rq.get("queue", []) if t.get("status") == "completed"]

    print(f"\n--- QUEUE ({len(pending_tasks)} pending, {len(completed_tasks)} completed) ---")
    if isinstance(handoff, str):
        if handoff.strip():
            print(f"  Handoff: {handoff.strip()[:300]}")
    elif isinstance(handoff, dict):
        if handoff.get("next_step"):
            print(f"  Handoff: {handoff['next_step']}")
        if handoff.get("blockers"):
            print(f"  BLOCKED: {handoff['blockers']}")
    if priorities:
        print("  Priorities:")
        for i, p in enumerate(priorities[:5], 1):
            task_text = p.get("task", p) if isinstance(p, dict) else p
            print(f"    {i}. {task_text}")
    if pending_tasks:
        print("  Top pending:")
        for t in sorted(pending_tasks, key=lambda x: x.get("priority", 99))[:5]:
            print(f"    [P{t.get('priority',3)}] {t['category']}: {t['question'][:80]}")

    # Watchlist — only watching status, sorted by date, cap at 15
    watchlist = sorted(
        [w for w in rq.get("event_watchlist", []) if w.get("status") == "watching"],
        key=lambda w: w.get("expected_date", "9999"),
    )
    if watchlist:
        shown = watchlist[:15]
        overflow = len(watchlist) - len(shown)
        print(f"\n--- WATCHLIST ({len(watchlist)} watching) ---")
        for w in shown:
            print(f"  {w.get('expected_date','?')} {w.get('symbol','?')} — {w.get('event','?')[:80]}")
        if overflow > 0:
            print(f"  ... and {overflow} more")

    # Journal — last 5 entries from SQLite
    journal_count = _db.count_journal_entries()
    recent = _db.get_recent_journal(5)
    if recent:
        print(f"\n--- LAST {len(recent)} JOURNAL ENTRIES (of {journal_count}) ---")
        for e in recent:
            inv = str(e.get("investigated") or "")[:100]
            raw_findings = e.get("findings")
            print(f"  {e.get('date','?')}: {inv}")
            if raw_findings:
                print(f"    -> {str(raw_findings)[:100]}")

    # Friction — top categories from SQLite
    friction_count = _db.count_friction_entries()
    friction_top = _db.get_friction_summary(3)
    if friction_top:
        print(f"\n--- FRICTION (top categories, {friction_count} total) ---")
        for item in friction_top:
            print(f"  {item['category']}: {item['count']}x — latest: {(item.get('latest_description') or '?')[:80]}")

    # Data integrity check
    from research import verify_data_integrity
    integrity = verify_data_integrity()
    if integrity.get("ok"):
        print(f"\n--- DATA INTEGRITY: OK ---")
    else:
        print(f"\n--- DATA INTEGRITY ISSUES ---")
        for issue in integrity.get("issues", []):
            print(f"  ! {issue}")


if __name__ == "__main__":
    if "--review" in sys.argv:
        review_experiments()
    elif "--context" in sys.argv:
        show_context()
    else:
        show_status()
