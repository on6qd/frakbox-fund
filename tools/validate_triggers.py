"""
Pre-trade Trigger Validator
============================
Validates all hypotheses with future triggers to ensure they have
all required fields for trade_loop.py to execute correctly.

Run before session end to catch missing fields.

Usage:
    python3 tools/validate_triggers.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import db

REQUIRED_FIELDS = [
    ('expected_symbol', 'Symbol to trade (e.g., SPY, WFC)'),
    ('expected_direction', 'Direction: long or short'),
    ('trigger', 'Trigger condition or datetime (e.g., 2026-04-06T09:30)'),
    ('trigger_position_size', 'Position size in dollars (e.g., 5000)'),
    ('trigger_stop_loss_pct', 'Stop loss percentage (e.g., 10)'),
]


def validate_all_triggers():
    db.init_db()
    rows = db._q("""
        SELECT id, status, event_type, expected_symbol, expected_direction,
               trigger, trigger_position_size, trigger_stop_loss_pct,
               trigger_take_profit_pct
        FROM hypotheses
        WHERE trigger IS NOT NULL AND status = 'pending'
        ORDER BY trigger ASC
    """)

    now = datetime.now()
    issues = []
    valid = []

    print("=" * 70)
    print("PRE-TRADE TRIGGER VALIDATION")
    print(f"As of: {now.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    print()

    for row in rows:
        hyp_id = row['id'][:8]
        trigger = row['trigger']
        symbol = row['expected_symbol']
        direction = row['expected_direction']
        size = row['trigger_position_size']
        stop = row['trigger_stop_loss_pct']
        signal_type = row['event_type']

        row_issues = []

        if not symbol or symbol == 'TBD':
            row_issues.append('expected_symbol is None/TBD')
        if not direction:
            row_issues.append('expected_direction is None')
        if not trigger:
            row_issues.append('trigger is None')
        if not size:
            row_issues.append('trigger_position_size is None')
        if not stop:
            row_issues.append('trigger_stop_loss_pct is None')

        # Check if trigger is in future (if it's a datetime)
        trigger_str = str(trigger)
        try:
            trigger_dt = datetime.fromisoformat(trigger_str)
            if trigger_dt < now:
                row_issues.append(f'TRIGGER ALREADY PASSED: {trigger_str} is in the past!')
        except (ValueError, TypeError):
            pass  # Non-datetime triggers (e.g., 'next_market_open') are OK

        status = '✓ VALID' if not row_issues else '✗ INVALID'
        print(f"{status} | {hyp_id} | {symbol or 'NO_SYMBOL'} {direction or 'NO_DIR'} | trigger={trigger}")
        print(f"  Signal: {signal_type}")
        print(f"  Size: ${size or 'MISSING'} | Stop: {stop or 'MISSING'}%")

        if row_issues:
            for issue in row_issues:
                print(f"  ⚠️  {issue}")
            issues.append({'id': hyp_id, 'trigger': trigger, 'issues': row_issues})
        else:
            valid.append(hyp_id)
        print()

    print("=" * 70)
    print(f"SUMMARY: {len(valid)} valid, {len(issues)} with issues")
    print()

    if issues:
        print("ACTION REQUIRED:")
        for issue in issues:
            print(f"  {issue['id']}: {', '.join(issue['issues'])}")
        print()
        print("Fix by updating hypothesis fields:")
        print("  db.update_hypothesis_fields('<id>', expected_symbol='TICKER', ")
        print("       expected_direction='long', trigger_position_size=5000, ")
        print("       trigger_stop_loss_pct=10)")
    else:
        print("All triggers are valid! Ready for trade_loop.py execution.")

    return len(issues) == 0


if __name__ == '__main__':
    valid = validate_all_triggers()
    sys.exit(0 if valid else 1)
