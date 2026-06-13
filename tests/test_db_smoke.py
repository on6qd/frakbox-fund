#!/usr/bin/env python3
"""
Dual-backend smoke test for the db.py data-access layer.

Runs the same CRUD exercises against BOTH storage backends:
    DB_BACKEND=sqlite   python tests/test_db_smoke.py
    DB_BACKEND=libsql   python tests/test_db_smoke.py   # local libSQL file, no sync

The libSQL embedded-replica binding runs as a plain local file when no
TURSO_URL/TOKEN are set, so this verifies the production code path offline.

Exercises the things that differ between backends:
  - dict row mapping (no sqlite3.Row)
  - list-vs-tuple parameter binding (the hypothesis upsert passes dynamic params)
  - JSON (de)serialization round-trip
  - PRAGMA-based migrations
"""
import os
import sys
import tempfile

# Hermetic: neutralize Turso creds BEFORE importing db so this test NEVER touches
# the production cloud primary (db.py auto-loads .env via setdefault — setting these
# to "" keeps it from filling them in, forcing a local-only backend).
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""
os.environ["TURSO_URL"] = ""
os.environ["TURSO_TOKEN"] = ""

# Point the DB at a throwaway file BEFORE importing db (DB_PATH is read at connect time).
_TMP = tempfile.mkdtemp(prefix="db_smoke_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

db.DB_PATH = os.path.join(_TMP, "smoke.db")
db.close_db()

BACKEND = getattr(db, "_BACKEND", "?")
_failures = []


def check(name, cond, detail=""):
    status = "OK  " if cond else "FAIL"
    if not cond:
        _failures.append(name)
    print(f"  [{BACKEND:6}] {status} {name}" + (f"  ({detail})" if detail and not cond else ""))


# ── schema + migrations ────────────────────────────────────────────────────
db.init_db()
check("init_db + migrations run", True)

# ── hypotheses: upsert (dynamic list params), read, JSON round-trip ─────────
hyp = {
    "id": "SMOKE-1",
    "created": "2026-06-12T00:00:00",
    "status": "pending",
    "event_type": "smoke_event",   # NOT NULL column
    "thesis": "smoke test thesis",
    "spec_json": {"driver": "CL=F", "beta": 1.2},   # dict -> JSON column round-trip
    "tags": ["a", "b"],                              # unknown field -> stored in `extra`
}
db.save_hypothesis(hyp)
got = db.get_hypothesis_by_id("SMOKE-1")
check("hypothesis upsert + get_by_id", got is not None)
check("dict field access (str key)", got and got["thesis"] == "smoke test thesis")
check("JSON column deserialized to dict", got and isinstance(got.get("spec_json"), dict)
      and got["spec_json"]["beta"] == 1.2)
check("extra field merged back", got and got.get("tags") == ["a", "b"])

check("get_hypotheses_by_status", any(h["id"] == "SMOKE-1" for h in db.get_hypotheses_by_status("pending")))
check("count_hypotheses_by_status", db.count_hypotheses_by_status("pending") >= 1)
check("load_hypotheses returns dicts", all(isinstance(h, dict) for h in db.load_hypotheses()))

db.update_hypothesis_fields("SMOKE-1", thesis="updated thesis")
check("update_hypothesis_fields", db.get_hypothesis_by_id("SMOKE-1")["thesis"] == "updated thesis")

# ── knowledge base ──────────────────────────────────────────────────────────
db.record_dead_end("smoke_event", "no edge found")
db.record_literature("smoke_event", {"summary": "nothing here"})
check("record_dead_end + get_dead_ends", any("smoke_event" in str(d) for d in db.get_dead_ends()))
kb = db.load_knowledge()
check("load_knowledge shape", isinstance(kb, dict) and "dead_ends" in kb and "literature" in kb)

# ── research queue ──────────────────────────────────────────────────────────
db.add_research_task("scan_hit", "smoke question?", 5, "smoke reasoning")
nxt = db.get_next_research_task()
check("add_research_task + get_next_research_task", nxt is not None and isinstance(nxt, dict))

# complete_research_task: pass a non-matching id so it falls through to the
# category-fallback UPDATE (the portable ORDER BY/LIMIT subquery path).
db.add_research_task("smoke_cat", "fallback question?", 5, "r")
check("complete_research_task category fallback", db.complete_research_task("smoke_cat", "done") is True)

# ── read-only paths ─────────────────────────────────────────────────────────
check("get_due_events returns list", isinstance(db.get_due_events(), list))
check("count_journal_entries returns int", isinstance(db.count_journal_entries(), int))
check("get_recent_journal returns list", isinstance(db.get_recent_journal(3), list))

# ── result ──────────────────────────────────────────────────────────────────
print(f"\n[{BACKEND}] {'ALL PASSED' if not _failures else 'FAILURES: ' + ', '.join(_failures)}")
sys.exit(1 if _failures else 0)
