#!/usr/bin/env python3
"""
Weekly Research Calendar

Shows all upcoming events, deadlines, and tasks for the next 7-14 days.
Pulls from: event_watchlist, session_priorities, hypotheses (triggers/deadlines),
active positions, and research_queue.

Usage:
    python3 tools/weekly_calendar.py [--days 14]
"""

import os
import sys
import json
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


def get_calendar(days=14):
    """Build a day-by-day calendar of upcoming events and tasks."""
    today = datetime.now().date()
    end = today + timedelta(days=days)

    calendar = defaultdict(list)

    # 1. Event watchlist
    rows = db._q(
        "SELECT symbol, expected_date, event FROM event_watchlist "
        "WHERE expected_date BETWEEN ? AND ? AND status='watching' "
        "ORDER BY expected_date",
        (today.isoformat(), end.isoformat())
    )
    for r in rows:
        calendar[r['expected_date']].append({
            'type': 'WATCHLIST',
            'symbol': r['symbol'],
            'detail': r['event'][:150]
        })

    # 2. Active positions with deadlines
    rows = db._q(
        "SELECT id, event_type, expected_symbol, extra FROM hypotheses "
        "WHERE status='active'"
    )
    for r in rows:
        extra = json.loads(r['extra']) if r['extra'] else {}
        trade = extra.get('trade', {})
        deadline = trade.get('deadline', '')
        if deadline:
            d = deadline[:10]
            if today.isoformat() <= d <= end.isoformat():
                calendar[d].append({
                    'type': 'DEADLINE',
                    'symbol': r['expected_symbol'],
                    'detail': f"Position deadline: {r['event_type']} ({r['id'][:8]})"
                })

    # 3. Pending hypotheses with triggers
    rows = db._q(
        "SELECT id, event_type, expected_symbol, extra FROM hypotheses "
        "WHERE status='pending'"
    )
    for r in rows:
        extra = json.loads(r['extra']) if r['extra'] else {}
        trigger = extra.get('trigger', '')
        if trigger and 'T' in str(trigger):  # datetime trigger
            d = str(trigger)[:10]
            if today.isoformat() <= d <= end.isoformat():
                calendar[d].append({
                    'type': 'TRIGGER',
                    'symbol': r['expected_symbol'],
                    'detail': f"Trigger: {r['event_type']} ({r['id'][:8]})"
                })

    return calendar


def print_calendar(days=14):
    """Print formatted calendar."""
    calendar = get_calendar(days)
    today = datetime.now().date()

    print(f"\n{'='*60}")
    print(f"RESEARCH CALENDAR: {today.isoformat()} to {(today + timedelta(days=days)).isoformat()}")
    print(f"{'='*60}")

    for d in range(days + 1):
        date = today + timedelta(days=d)
        date_str = date.isoformat()
        day_name = date.strftime('%A')
        is_weekend = date.weekday() >= 5

        events = calendar.get(date_str, [])

        if not events and is_weekend:
            continue  # skip empty weekends

        marker = '  ' if not events else '→ '
        weekend_tag = ' (weekend)' if is_weekend else ''
        today_tag = ' ← TODAY' if d == 0 else ''

        print(f"\n{marker}{date_str} {day_name}{weekend_tag}{today_tag}")

        if not events:
            print(f"    (no events)")
        else:
            for e in events:
                icon = {'WATCHLIST': '📅', 'DEADLINE': '⏰', 'TRIGGER': '🎯'}.get(e['type'], '•')
                print(f"    {icon} [{e['type']}] {e.get('symbol', '?')}: {e['detail'][:120]}")

    print(f"\n{'='*60}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Weekly research calendar')
    parser.add_argument('--days', type=int, default=14, help='Days to look ahead (default: 14)')
    args = parser.parse_args()
    print_calendar(args.days)
