#!/usr/bin/env python3
"""Check if there's meaningful work for a research session.

Called by researcher.sh before starting a session. Exits 0 if work exists, 1 if not.
This prevents burning sessions when there's nothing to do.

Work exists if any of:
- Active hypotheses with deadlines within 24h (need monitoring/completion)
- Scanner signals waiting to be processed
- Research tasks in queue with priority >= 7
- Pending hypotheses with triggers set (ready to activate)
- Session handoff has a specific next_step
- Trade loop reported actions in the last hour
- It's been > 6 hours since last session (minimum daily presence)

Also hard-blocks sessions during Anthropic's peak-throttle window (05:00-11:00 PT
weekdays) — quota drains faster there and capacity is rationed. Set
SKIP_PEAK_HOURS=0 in the environment to override.
"""

import sys
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db

PEAK_TZ = ZoneInfo("America/Los_Angeles")
PEAK_HOUR_START = 5
PEAK_HOUR_END = 11


def in_peak_hours(now=None):
    if os.environ.get("SKIP_PEAK_HOURS", "1") == "0":
        return False
    now_pt = (now or datetime.now(PEAK_TZ)).astimezone(PEAK_TZ)
    if now_pt.weekday() >= 5:
        return False
    return PEAK_HOUR_START <= now_pt.hour < PEAK_HOUR_END

def should_run():
    db.init_db()
    reasons = []

    # 1. Active hypotheses near deadline
    active = db.get_hypotheses_by_status("active")
    now = datetime.now()
    for h in active:
        trade = h.get("trade") or {}
        deadline = trade.get("deadline", "")
        if deadline:
            try:
                dl = datetime.fromisoformat(deadline[:19])
                if dl - now < timedelta(hours=24):
                    reasons.append(f"deadline: {h.get('expected_symbol')} expires {deadline[:10]}")
            except (ValueError, TypeError):
                pass

    # 2. High-priority research tasks
    queue = db.load_queue()
    pending_tasks = [t for t in queue.get("queue", [])
                     if t.get("status") == "pending" and t.get("priority", 0) >= 7]
    if pending_tasks:
        reasons.append(f"queue: {len(pending_tasks)} high-priority tasks")

    # 3. Scanner signals waiting
    for scanner in ["52w_low", "ceo_departure", "sp500", "insider_cluster"]:
        try:
            signals = db.get_scanner_signals(scanner, limit=10)
            unprocessed = [s for s in signals if not s.get("processed")]
            if unprocessed:
                reasons.append(f"scanner: {len(unprocessed)} unprocessed {scanner} signals")
        except Exception:
            pass

    # 4. Pending hypotheses with triggers
    pending = db.get_hypotheses_by_status("pending")
    triggered = [h for h in pending if h.get("trigger")]
    if triggered:
        reasons.append(f"triggers: {len(triggered)} pending hypotheses ready")

    # 5. Session handoff has specific next_step
    handoff = queue.get("session_handoff", {})
    if isinstance(handoff, str):
        try:
            import json
            handoff = json.loads(handoff)
        except (json.JSONDecodeError, TypeError):
            handoff = {}
    next_step = handoff.get("next_step", "") if isinstance(handoff, dict) else ""
    if next_step and len(next_step) > 10:
        reasons.append(f"handoff: {next_step[:60]}")

    # 6. Trade loop had recent activity
    try:
        log_path = os.path.join(os.path.dirname(__file__), "logs", "trade_loop.log")
        if os.path.exists(log_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(log_path))
            if now - mtime < timedelta(hours=1):
                # Check if it actually did something (not just "no actions")
                with open(log_path) as f:
                    lines = f.readlines()
                    recent = [l for l in lines[-20:] if "closed" in l.lower() or "executed" in l.lower() or "STOP" in l or "DEADLINE" in l]
                    if recent:
                        reasons.append("trade_loop: recent trade activity")
    except Exception:
        pass

    # 7. Minimum daily presence — at least 1 session every 6 hours
    try:
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        today = now.strftime("%Y-%m-%d")
        session_logs = sorted([
            f for f in os.listdir(log_dir)
            if f.startswith(today) and f.endswith(".log")
        ])
        if session_logs:
            last_session_time = session_logs[-1][:15]  # "2026-03-27_0543"
            try:
                last_dt = datetime.strptime(last_session_time, "%Y-%m-%d_%H%M")
                hours_since = (now - last_dt).total_seconds() / 3600
                if hours_since >= 6:
                    reasons.append(f"heartbeat: {hours_since:.0f}h since last session")
            except ValueError:
                reasons.append("heartbeat: can't parse last session time")
        else:
            reasons.append("heartbeat: no sessions today yet")
    except Exception:
        reasons.append("heartbeat: can't check session history")

    return reasons


if __name__ == "__main__":
    if in_peak_hours():
        now_pt = datetime.now(PEAK_TZ)
        print(f"  [idle] Anthropic peak hours ({now_pt.strftime('%a %H:%M')} PT) — skipping to preserve quota")
        sys.exit(1)
    reasons = should_run()
    if reasons:
        for r in reasons:
            print(f"  [work] {r}")
        sys.exit(0)
    else:
        print("  [idle] No actionable work found")
        sys.exit(1)
