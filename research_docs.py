"""
Research document index.

The documents under research/ are the source of truth for all research narrative
(see RESEARCH_DOCS.md). This module parses their YAML front-matter into a thin,
derived index table (`research_docs`) so the dashboard, the scanner's de-duplication,
and the trading desk can query state without reading every file.

The index is DISPOSABLE — it can always be rebuilt from the documents with
`reindex`. Never record a research conclusion only here; if it matters, it is in
the document.

CLI:
    python3 research_docs.py reindex     # rebuild the index from research/
    python3 research_docs.py validate    # check folder<->status, ids, links
    python3 research_docs.py summary      # compact pipeline view (used by run.py --context)
    python3 research_docs.py list [--status proposed|researching|validated|invalidated]
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

import yaml

import db

RESEARCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research")

# A document's folder IS its status (RESEARCH_DOCS.md §3). Only these folders are
# indexed; templates/ is ignored.
STATUS_BY_FOLDER = {
    "concepts": "proposed",
    "theses": "researching",
    "desk": "validated",
    "graveyard": "invalidated",
}

# Reduced set of front-matter keys mirrored into the index. Everything else stays
# in the document only.
_FIELDS = (
    "id", "title", "status", "conviction", "asset_class", "direction",
    "horizon_days", "hypothesis_class", "concept_note", "opened", "decided", "author",
)


def ensure_table():
    """Create the derived index table if it does not exist."""
    conn = db.get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_docs (
            id TEXT PRIMARY KEY,
            doc_type TEXT,            -- concept_note | investment_thesis
            title TEXT,
            status TEXT,              -- proposed | researching | validated | invalidated
            conviction TEXT,
            asset_class TEXT,
            universe TEXT,            -- JSON list
            direction TEXT,
            horizon_days INTEGER,
            hypothesis_class TEXT,
            concept_note TEXT,        -- parent Concept Note id (theses only)
            opened TEXT,
            decided TEXT,
            author TEXT,
            folder TEXT,              -- concepts | theses | desk | graveyard
            path TEXT,                -- repo-relative path
            indexed_at TEXT
        )
    """)
    conn.commit()


def _doc_type(doc_id):
    """concept_note for CN-*, investment_thesis for IT-*, else unknown."""
    if doc_id and doc_id.startswith("CN-"):
        return "concept_note"
    if doc_id and doc_id.startswith("IT-"):
        return "investment_thesis"
    return "unknown"


def parse_front_matter(path):
    """Return the YAML front-matter of a markdown file as a dict, or None."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else None


def iter_docs():
    """Yield (folder, path, front_matter) for every indexed document."""
    for folder in STATUS_BY_FOLDER:
        for path in sorted(glob.glob(os.path.join(RESEARCH_DIR, folder, "*.md"))):
            fm = parse_front_matter(path)
            if fm is not None:
                yield folder, path, fm


def reindex():
    """Rebuild the index table from the documents. Returns a per-folder count."""
    ensure_table()
    conn = db.get_db()
    conn.execute("DELETE FROM research_docs")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {}
    for folder, path, fm in iter_docs():
        rel = os.path.relpath(path, os.path.dirname(RESEARCH_DIR))
        row = {k: fm.get(k) for k in _FIELDS}
        row["doc_type"] = _doc_type(fm.get("id"))
        row["folder"] = folder
        row["path"] = rel
        row["universe"] = json.dumps(fm.get("universe") or [])
        row["indexed_at"] = now
        cols = list(row.keys())
        conn.execute(
            f"INSERT OR REPLACE INTO research_docs ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            tuple(row[c] for c in cols),
        )
        counts[folder] = counts.get(folder, 0) + 1
    conn.commit()
    return counts


def validate():
    """Check the documents for structural problems. Returns a list of issue strings."""
    issues = []
    seen_ids = {}
    concept_ids = set()
    thesis_concept_refs = []
    for folder, path, fm in iter_docs():
        rel = os.path.relpath(path, os.path.dirname(RESEARCH_DIR))
        doc_id = fm.get("id")
        # required fields
        for req in ("id", "title", "status"):
            if not fm.get(req):
                issues.append(f"{rel}: missing required field '{req}'")
        # duplicate ids
        if doc_id:
            if doc_id in seen_ids:
                issues.append(f"{rel}: duplicate id '{doc_id}' (also in {seen_ids[doc_id]})")
            seen_ids[doc_id] = rel
        # folder <-> status
        expected = STATUS_BY_FOLDER[folder]
        if fm.get("status") != expected:
            issues.append(
                f"{rel}: status '{fm.get('status')}' does not match folder "
                f"'{folder}/' (expected '{expected}')"
            )
        # id prefix <-> doc type
        dt = _doc_type(doc_id)
        if dt == "unknown":
            issues.append(f"{rel}: id '{doc_id}' has no CN-/IT- prefix")
        if folder == "concepts" and dt != "concept_note":
            issues.append(f"{rel}: concept note must use a CN- id")
        # universe shape
        uni = fm.get("universe")
        if uni is not None and not isinstance(uni, list):
            issues.append(f"{rel}: 'universe' must be a list, got {type(uni).__name__}")
        # collect for cross-checks
        if dt == "concept_note":
            concept_ids.add(doc_id)
        if dt == "investment_thesis" and fm.get("concept_note"):
            thesis_concept_refs.append((rel, fm.get("concept_note")))
    # thesis -> concept-note links resolve
    for rel, ref in thesis_concept_refs:
        if ref not in concept_ids and ref not in seen_ids:
            issues.append(f"{rel}: concept_note '{ref}' not found among documents")
    return issues


def _rows(status=None):
    ensure_table()
    if status:
        return db._q("SELECT * FROM research_docs WHERE status=? ORDER BY opened DESC", (status,))
    return db._q("SELECT * FROM research_docs ORDER BY opened DESC")


def summary(stream=sys.stdout):
    """Print a compact pipeline view. Safe to call from run.py --context."""
    ensure_table()
    counts = {f: 0 for f in STATUS_BY_FOLDER}
    for r in db._q("SELECT folder, COUNT(*) AS n FROM research_docs GROUP BY folder"):
        counts[r["folder"]] = r["n"]
    print(
        f"--- RESEARCH PIPELINE (concepts:{counts['concepts']} "
        f"theses:{counts['theses']} desk:{counts['desk']} graveyard:{counts['graveyard']}) ---",
        file=stream,
    )
    desk = _rows("validated")
    if desk:
        print("  DESK INBOX (validated, awaiting the trading desk):", file=stream)
        for r in desk:
            uni = ", ".join(json.loads(r["universe"] or "[]"))
            print(
                f"    {r['id']} [{r.get('conviction') or '?'}] "
                f"{r.get('direction') or '?'} {uni} {r.get('horizon_days') or '?'}d "
                f"— {r.get('title') or ''}",
                file=stream,
            )
    proposed = _rows("proposed")
    if proposed:
        print(f"  CONCEPT NOTES awaiting research ({len(proposed)}):", file=stream)
        for r in proposed[:8]:
            print(f"    {r['id']} — {r.get('title') or ''}", file=stream)
    researching = _rows("researching")
    if researching:
        print(f"  THESES under research ({len(researching)}):", file=stream)
        for r in researching[:8]:
            print(f"    {r['id']} — {r.get('title') or ''}", file=stream)


def _cmd_list(args):
    for r in _rows(args.status):
        uni = ", ".join(json.loads(r["universe"] or "[]"))
        print(f"{r['id']:40s} {r['status']:13s} {r.get('direction') or '?':12s} "
              f"{uni:28s} {r.get('title') or ''}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Research document index")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex", help="rebuild the index from research/")
    sub.add_parser("validate", help="check folder<->status, ids, links")
    sub.add_parser("summary", help="compact pipeline view")
    lp = sub.add_parser("list", help="list indexed documents")
    lp.add_argument("--status", choices=list(STATUS_BY_FOLDER.values()))
    args = p.parse_args(argv)

    if args.cmd == "reindex":
        counts = reindex()
        total = sum(counts.values())
        print(f"Reindexed {total} documents: " +
              ", ".join(f"{k}={v}" for k, v in counts.items()))
        issues = validate()
        if issues:
            print(f"\n{len(issues)} validation issue(s):")
            for i in issues:
                print(f"  ! {i}")
    elif args.cmd == "validate":
        issues = validate()
        if not issues:
            print("OK — no issues.")
        else:
            print(f"{len(issues)} issue(s):")
            for i in issues:
                print(f"  ! {i}")
            sys.exit(1)
    elif args.cmd == "summary":
        summary()
    elif args.cmd == "list":
        _cmd_list(args)


if __name__ == "__main__":
    main()
