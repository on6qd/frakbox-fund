"""
Scan-hit dead-end de-duplication helper.

Scanners should call `is_known_dead_end(signal_key, tickers=...)` before queuing
a scan_hit into research_queue. The check consults known_effects for any entry
whose status contains DEAD_END and whose data/finding mentions the same
tickers/pair. This prevents the orchestrator from wasting a session on a
signal that was already resolved.

Driven by the 2026-04-24 BA-RTX re-discovery (see
cointegration_scan_hit_batch_closure_2026_04_24).

Usage (in a scanner script):
    from tools.scan_hit_dead_end_check import is_known_dead_end
    hit, reason = is_known_dead_end(pair=("BA", "RTX"))
    if hit:
        print(f"SKIP BA-RTX: {reason}")
        continue
    add_research_task("scan_hit", "...", priority, reasoning)
"""

import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db


def _all_dead_end_entries():
    """Return list of (source_id, blob_text) where blob_text is a text blob
    containing the entry's dead-end context. Draws from:
      - known_effects where status contains DEAD_END/DISPROVEN/REJECTED
      - research_queue closed scan_hits with DEAD_END status
    """
    conn = db.get_db()
    out = []

    # known_effects
    for r in conn.execute("SELECT event_type, data FROM known_effects").fetchall():
        try:
            d = json.loads(r[1]) if r[1] else {}
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {"raw": str(d)}
        status = str(d.get("status", "")).upper()
        name_status = r[0].upper()
        blob = status + " " + name_status
        if "DEAD_END" in blob or "DISPROVEN" in blob or "REJECTED" in blob:
            out.append((r[0], r[0] + " " + json.dumps(d), d.get("status")))

    # closed scan_hits that reached a dead-end terminal status
    for r in conn.execute(
        "SELECT id, question, status, findings FROM research_queue "
        "WHERE category='scan_hit' AND status LIKE '%DEAD_END%'"
    ).fetchall():
        blob = " ".join([r[0] or "", r[1] or "", r[3] or ""])
        out.append((f"scan_hit:{r[0]}", blob, r[2]))

    return out


def _mentions_all(text, tokens):
    """Word-boundary AND match: all tokens must appear in text as whole words.
    Tickers are short and uppercase, so plain substring matching produces false
    positives (e.g. 'V' matches inside 'Voluntary', 'MA' inside 'margin').
    """
    if not text or not tokens:
        return False
    for t in tokens:
        # word boundary, exact case preserved for tickers (they should appear
        # in uppercase in the stored payload)
        patt = r"\b" + re.escape(t) + r"\b"
        if not re.search(patt, text):
            return False
    return True


def is_known_dead_end(signal_key=None, pair=None, tickers=None):
    """
    Check if a candidate signal or pair is already a recorded dead end.

    Args:
        signal_key: optional string — e.g. 'vix30_xlk_threshold' — matched against
            event_type keys in known_effects.
        pair: optional tuple/list of 2 tickers — e.g. ('BA', 'RTX').
        tickers: optional list of tickers to match (used if the hypothesis
            involves a single or N-asset signal).

    Returns:
        (is_dead_end: bool, reason: str) — reason is empty when not dead.
    """
    entries = _all_dead_end_entries()
    tokens = []
    if pair:
        tokens = [pair[0], pair[1]]
    elif tickers:
        tokens = list(tickers)

    # Direct key match
    if signal_key:
        lk = signal_key.lower()
        for name, blob, status in entries:
            if lk in name.lower() or name.lower() in lk:
                return True, f"event_type match: {name} (status={status})"

    # Token (ticker) co-occurrence match in event_type or data blobs
    if tokens:
        for name, blob, status in entries:
            if _mentions_all(blob, tokens):
                return True, (
                    f"tickers {'/'.join(tokens)} mentioned in dead-end record "
                    f"{name} (status={status})"
                )

    return False, ""


def _cli():
    """CLI for ad-hoc checks:
        python3 tools/scan_hit_dead_end_check.py --pair BA RTX
        python3 tools/scan_hit_dead_end_check.py --tickers XLU TLT
        python3 tools/scan_hit_dead_end_check.py --signal-key vix30_xlk_threshold
    """
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--pair", nargs=2, metavar=("A", "B"))
    p.add_argument("--tickers", nargs="+")
    p.add_argument("--signal-key", default=None)
    args = p.parse_args()

    db.init_db()
    hit, reason = is_known_dead_end(
        signal_key=args.signal_key, pair=args.pair, tickers=args.tickers
    )
    print(json.dumps({"is_dead_end": hit, "reason": reason}, indent=2))


if __name__ == "__main__":
    _cli()
