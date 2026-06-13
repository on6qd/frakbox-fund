#!/usr/bin/env python3
"""
Quick hypothesis audit scan — examine stored hypotheses for patterns.
Tests completeness, consistency, and metadata quality.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import db
import pandas as pd
import numpy as np

def audit_hypotheses():
    """Audit all hypotheses in database."""
    conn = db.get_db()
    cursor = conn.cursor()

    # Get all hypotheses
    rows = cursor.execute("SELECT id, status, hypothesis_class, expected_direction FROM hypotheses").fetchall()

    print(f"\n=== HYPOTHESIS AUDIT SCAN ===")
    print(f"\nTotal hypotheses: {len(rows)}")

    if len(rows) == 0:
        print("No hypotheses in database")
        return 0, 0

    tests_run = 0
    hits = []

    # Test 1: Status distribution
    statuses = {}
    for row in rows:
        status = row[1] or 'unknown'
        statuses[status] = statuses.get(status, 0) + 1

    print(f"\n🔍 Status distribution:")
    for status, count in sorted(statuses.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")

    tests_run += 1

    # Test 2: Hypothesis class distribution
    classes = {}
    for row in rows:
        cls = row[2] or 'event'
        classes[cls] = classes.get(cls, 0) + 1

    print(f"\n🔍 Hypothesis class distribution:")
    for cls, count in sorted(classes.items(), key=lambda x: -x[1]):
        print(f"  {cls}: {count}")

    tests_run += 1

    # Test 3: Expected direction distribution
    directions = {}
    for row in rows:
        direction = row[3] or 'unknown'
        directions[direction] = directions.get(direction, 0) + 1

    print(f"\n🔍 Expected direction distribution:")
    for direction, count in sorted(directions.items(), key=lambda x: -x[1]):
        print(f"  {direction}: {count}")

    tests_run += 1

    # Test 4: Completed hypotheses
    completed_rows = cursor.execute(
        "SELECT id, status FROM hypotheses WHERE status='completed' OR status='traded' OR status='dead_end'"
    ).fetchall()

    completed_count = len(completed_rows)
    completion_rate = completed_count / len(rows) * 100 if len(rows) > 0 else 0

    print(f"\n🔍 Completion metrics:")
    print(f"  Completed: {completed_count} ({completion_rate:.1f}%)")
    print(f"  Pending: {len(rows) - completed_count}")

    tests_run += 1

    if completion_rate > 50:
        hit = {
            "signal": f"High hypothesis completion rate ({completion_rate:.1f}%)",
            "class": "hypothesis_quality",
            "completed": completed_count,
            "total": len(rows),
            "completion_rate": completion_rate
        }
        hits.append(hit)
        print(f"    ✓ High completion rate (target: >50%)")

    # Test 5: Class imbalance
    max_class_count = max(classes.values())
    min_class_count = min(classes.values())
    class_balance = min_class_count / max_class_count if max_class_count > 0 else 0

    print(f"\n🔍 Class balance:")
    print(f"  Max: {max_class_count}, Min: {min_class_count}, Balance: {class_balance:.2f}")

    tests_run += 1

    if class_balance < 0.3:
        hit = {
            "signal": f"Hypothesis class imbalance detected",
            "class": "class_imbalance",
            "balance_ratio": class_balance,
            "class_distribution": classes
        }
        hits.append(hit)
        print(f"    ✓ Imbalance detected (ratio={class_balance:.2f})")

    # Test 6: Direction bias
    long_count = directions.get('up', 0) + directions.get('long', 0)
    short_count = directions.get('down', 0) + directions.get('short', 0)
    direction_bias = abs(long_count - short_count) / (long_count + short_count) if (long_count + short_count) > 0 else 0

    print(f"\n🔍 Direction bias:")
    print(f"  Long: {long_count}, Short: {short_count}, Bias: {direction_bias:.2f}")

    tests_run += 1

    if direction_bias > 0.3:
        hit = {
            "signal": f"Direction bias in hypotheses ({abs(long_count - short_count)} / {long_count + short_count})",
            "class": "direction_bias",
            "long_count": long_count,
            "short_count": short_count,
            "bias": direction_bias
        }
        hits.append(hit)
        print(f"    ✓ Bias detected (skew={direction_bias:.2f})")

    # Test 7: Research queue depth
    queue_rows = cursor.execute("SELECT id FROM research_queue WHERE status='pending'").fetchall()
    queue_depth = len(queue_rows)

    print(f"\n🔍 Research queue:")
    print(f"  Pending tasks: {queue_depth}")

    tests_run += 1

    if queue_depth > 20:
        hit = {
            "signal": f"Large research backlog ({queue_depth} pending)",
            "class": "queue_depth",
            "pending_count": queue_depth
        }
        hits.append(hit)
        print(f"    ✓ Backlog detected")

    # Test 8: Dead-end tracking
    dead_rows = cursor.execute("SELECT event_type FROM dead_ends").fetchall()
    dead_count = len(dead_rows)

    print(f"\n🔍 Dead-end knowledge base:")
    print(f"  Dead ends recorded: {dead_count}")

    tests_run += 1

    if dead_count > 10:
        hit = {
            "signal": f"Healthy dead-end knowledge base ({dead_count} entries)",
            "class": "dead_end_tracking",
            "dead_end_count": dead_count
        }
        hits.append(hit)
        print(f"    ✓ Good dead-end documentation")

    print(f"\n{'='*50}")
    print(f"Tests run: {tests_run}")
    print(f"Hits: {len(hits)}")

    # Queue hits
    if hits:
        print(f"\n📋 Queueing {len(hits)} hits...")
        for hit in hits:
            try:
                hit_clean = {}
                for k, v in hit.items():
                    if isinstance(v, (np.integer, np.floating)):
                        hit_clean[k] = float(v) if isinstance(v, np.floating) else int(v)
                    else:
                        hit_clean[k] = v

                db.add_research_task(
                    category="scan_hit",
                    question=hit["signal"],
                    priority=2,
                    reasoning=json.dumps(hit_clean),
                    depends_on=None
                )
                print(f"  ✓ Queued: {hit['signal'][:60]}")
            except Exception as e:
                print(f"  ✗ Failed: {e}")

    # Log journal
    try:
        summary = f"Audited hypothesis database: {len(rows)} total, {completion_rate:.1f}% complete, {len(hits)} patterns flagged."
        db.append_journal_entry(
            date=pd.Timestamp.now().isoformat(),
            session_type="scan",
            investigated="Hypothesis database audit: status distribution, class balance, direction bias, queue depth, dead-end tracking",
            findings=json.dumps({"hits_queued": len(hits), "tests_run": tests_run, "total_hypotheses": len(rows)}),
            category="hypothesis_audit",
            public_summary=summary
        )
        print("✓ Journal logged")
    except Exception as e:
        print(f"✗ Journal: {e}")

    return len(hits), tests_run

if __name__ == "__main__":
    hits, tests_run = audit_hypotheses()
    sys.exit(0)
