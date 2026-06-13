"""
Database layer — SQLite storage replacing JSON files.

Provides CRUD functions for all research data:
- hypotheses (from hypotheses.json)
- known_effects, dead_ends, literature (from knowledge_base.json)
- research_queue, event_watchlist, session_priorities, session_handoff (from research_queue.json)

All functions operate on a singleton connection per process with WAL mode
for safe concurrent access from research, trade_loop, and health_check processes.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "research.db")

# Load .env so the backend is selected correctly no matter the import order
# (db.py must not depend on config.py having been imported first). setdefault:
# real environment variables always win over .env.
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

# ── Backend selection ──────────────────────────────────────────────────────
# Storage is pluggable: a Turso (libSQL) embedded replica in production, or a
# local sqlite3 file for dev/CI. db.py is the ONLY module that opens a
# connection or runs SQL; everything downstream consumes plain dicts and never
# knows which backend is live.
TURSO_URL = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("TURSO_URL")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("TURSO_TOKEN")

# DB_BACKEND env forces 'sqlite' or 'libsql'; default auto-detects from Turso env.
_BACKEND = os.environ.get("DB_BACKEND") or ("libsql" if (TURSO_URL and TURSO_TOKEN) else "sqlite")

if _BACKEND == "libsql":
    import libsql_experimental as _libsql
    # libSQL raises ValueError (not sqlite3.IntegrityError) on constraint violations.
    _INTEGRITY_ERRORS = (sqlite3.IntegrityError, ValueError)
else:
    _libsql = None
    _INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

_local = threading.local()


def get_db():
    """Return a singleton connection for the current thread (libSQL or sqlite3)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        if _BACKEND == "libsql":
            kwargs = {}
            if TURSO_URL and TURSO_TOKEN:
                kwargs = {"sync_url": TURSO_URL, "auth_token": TURSO_TOKEN}
            conn = _libsql.connect(DB_PATH, **kwargs)
            if kwargs:
                conn.sync()  # pull latest from the Turso primary
        else:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


def close_db():
    """Close the thread-local connection if open."""
    if hasattr(_local, "conn") and _local.conn is not None:
        _local.conn.close()
        _local.conn = None


# ── Query helpers — the ONLY place a row is materialized ───────────────────
# Rows map to dicts via cursor.description (portable across sqlite3 and libSQL).
# Params are coerced to tuple because the libSQL binding rejects list params.

def _q(sql, params=()):
    """Run a SELECT; return rows as a list of dicts."""
    cur = get_db().execute(sql, tuple(params))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _q1(sql, params=()):
    """Run a SELECT; return the first row as a dict, or None."""
    cur = get_db().execute(sql, tuple(params))
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row is not None else None


def _scalar(sql, params=()):
    """Run a SELECT; return the first column of the first row, or None."""
    row = get_db().execute(sql, tuple(params)).fetchone()
    return row[0] if row is not None else None


def _exec(sql, params=()):
    """Run a write statement; return cursor.lastrowid."""
    return get_db().execute(sql, tuple(params)).lastrowid


def init_db():
    """Create all tables if they don't exist."""
    conn = get_db()
    conn.executescript(_SCHEMA)
    conn.commit()
    _run_migrations(conn)


def _table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")").fetchall()}


def _run_migrations(conn):
    """Incremental schema migrations via introspection (backend-agnostic)."""
    hcols = _columns(conn, "hypotheses")
    if "success_criteria" not in hcols:
        conn.execute("ALTER TABLE hypotheses ADD COLUMN success_criteria TEXT")
    if "hypothesis_class" not in hcols:
        conn.execute("ALTER TABLE hypotheses ADD COLUMN hypothesis_class TEXT DEFAULT 'event'")
    if "spec_json" not in hcols:
        conn.execute("ALTER TABLE hypotheses ADD COLUMN spec_json TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hypotheses_class ON hypotheses(hypothesis_class)")
    if not _table_exists(conn, "oos_observations"):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS oos_observations (
                id TEXT PRIMARY KEY,
                hypothesis_id TEXT,
                signal_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                benchmark TEXT NOT NULL DEFAULT 'SPY',
                direction TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_benchmark_price REAL NOT NULL,
                hold_days INTEGER NOT NULL DEFAULT 5,
                success_threshold_pct REAL,
                status TEXT NOT NULL DEFAULT 'tracking',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_oos_obs_status ON oos_observations(status);
            CREATE INDEX IF NOT EXISTS idx_oos_obs_signal ON oos_observations(signal_type);

            CREATE TABLE IF NOT EXISTS oos_daily_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id TEXT NOT NULL REFERENCES oos_observations(id),
                day_number INTEGER NOT NULL,
                trade_date TEXT NOT NULL,
                symbol_close REAL NOT NULL,
                benchmark_close REAL NOT NULL,
                raw_return_pct REAL NOT NULL,
                benchmark_return_pct REAL NOT NULL,
                abnormal_return_pct REAL NOT NULL,
                UNIQUE(observation_id, day_number)
            );
            CREATE INDEX IF NOT EXISTS idx_oos_daily_obs ON oos_daily_prices(observation_id);
        """)
    conn.commit()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    created TEXT NOT NULL,
    prediction_hash TEXT,
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending',

    -- The thesis
    event_type TEXT NOT NULL,
    event_description TEXT,
    causal_mechanism TEXT,
    causal_mechanism_criteria TEXT,  -- JSON list
    expected_symbol TEXT,
    expected_direction TEXT,
    expected_magnitude_pct REAL,
    expected_timeframe_days INTEGER,
    event_timing TEXT DEFAULT 'unknown',

    -- Multi-symbol backtest evidence
    backtest_symbols TEXT,  -- JSON list
    backtest_events TEXT,   -- JSON list of dicts

    -- Research backing
    historical_evidence TEXT,  -- JSON list
    sample_size INTEGER,
    consistency_pct REAL,
    out_of_sample_split TEXT,  -- JSON dict
    confounders TEXT,          -- JSON dict
    market_regime_note TEXT,
    regime_note TEXT,
    confidence INTEGER,
    literature_reference TEXT,
    survivorship_bias_note TEXT,
    selection_bias_note TEXT,
    success_criteria TEXT,             -- pre-registered: what "valid" looks like (thresholds, benchmarks)
    passes_multiple_testing INTEGER,  -- boolean
    multiple_testing_warning TEXT,

    -- Warnings
    confounder_warnings TEXT,
    symbol_warnings TEXT,   -- JSON list
    dead_end_warnings TEXT, -- JSON list

    -- Trade fields
    trade TEXT,   -- JSON dict (entry_price, position_size, deadline, stop_loss, etc.)
    result TEXT,  -- JSON dict (exit_price, return_pct, post_mortem, etc.)

    -- Trigger fields (for trade_loop.py)
    trigger TEXT,
    trigger_position_size REAL,
    trigger_stop_loss_pct REAL,
    trigger_take_profit_pct REAL,

    -- Extra data (any additional fields as JSON)
    extra TEXT   -- JSON dict for fields like live_validation_march_2026, etc.
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status);
CREATE INDEX IF NOT EXISTS idx_hypotheses_idempotency ON hypotheses(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_hypotheses_event_type ON hypotheses(event_type);

CREATE TABLE IF NOT EXISTS known_effects (
    event_type TEXT PRIMARY KEY,
    data TEXT NOT NULL,  -- JSON dict with all effect fields
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS dead_ends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    recorded TEXT,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS literature (
    event_type TEXT PRIMARY KEY,
    data TEXT NOT NULL,  -- JSON dict with all literature fields
    recorded TEXT
);

CREATE TABLE IF NOT EXISTS research_queue (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    question TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'pending',
    reasoning TEXT,
    added TEXT,
    completed TEXT,
    findings TEXT,
    depends_on TEXT,
    implementation_notes TEXT,
    extra TEXT  -- JSON dict for any additional fields
);

CREATE INDEX IF NOT EXISTS idx_research_queue_status ON research_queue(status);

CREATE TABLE IF NOT EXISTS event_watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    expected_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    hypothesis_template TEXT,  -- JSON
    added TEXT,
    status TEXT NOT NULL DEFAULT 'watching',
    triggered_date TEXT,
    UNIQUE(event, expected_date, symbol)
);

CREATE TABLE IF NOT EXISTS session_priorities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    set_by_session TEXT
);

CREATE TABLE IF NOT EXISTS session_handoff (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    data TEXT NOT NULL,  -- JSON dict
    written_at TEXT
);

-- Append-only log tables (replacing JSONL files)
CREATE TABLE IF NOT EXISTS research_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    session_type TEXT,
    investigated TEXT,
    findings TEXT,
    surprised_by TEXT,
    next_step TEXT,
    category TEXT,
    public_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_date ON research_journal(date);

CREATE TABLE IF NOT EXISTS friction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    description TEXT,
    turns_wasted INTEGER DEFAULT 0,
    potential_fix TEXT
);
CREATE INDEX IF NOT EXISTS idx_friction_category ON friction_log(category);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    session TEXT,
    status TEXT,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_timestamp ON token_usage(timestamp);

CREATE TABLE IF NOT EXISTS trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    hypothesis_id TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    position_size REAL,
    order_id TEXT,
    trigger_type TEXT,
    error TEXT,
    extra TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pre_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id TEXT NOT NULL,
    prediction_hash TEXT,
    data TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prereg_hyp ON pre_registrations(hypothesis_id);

CREATE TABLE IF NOT EXISTS patterns (
    event_type TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS scanner_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanner TEXT NOT NULL,
    data TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scanner_name ON scanner_signals(scanner);

CREATE TABLE IF NOT EXISTS nav_snapshots (
    date TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    position_count INTEGER DEFAULT 0,
    snapshot_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_results (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    parameters TEXT,
    result TEXT,
    summary TEXT,
    status TEXT DEFAULT 'completed',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_results_type ON task_results(task_type);
CREATE INDEX IF NOT EXISTS idx_task_results_ts ON task_results(timestamp);

CREATE TABLE IF NOT EXISTS oos_observations (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT,
    signal_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    benchmark TEXT NOT NULL DEFAULT 'SPY',
    direction TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_benchmark_price REAL NOT NULL,
    hold_days INTEGER NOT NULL DEFAULT 5,
    success_threshold_pct REAL,
    status TEXT NOT NULL DEFAULT 'tracking',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oos_obs_status ON oos_observations(status);
CREATE INDEX IF NOT EXISTS idx_oos_obs_signal ON oos_observations(signal_type);

CREATE TABLE IF NOT EXISTS oos_daily_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL REFERENCES oos_observations(id),
    day_number INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    symbol_close REAL NOT NULL,
    benchmark_close REAL NOT NULL,
    raw_return_pct REAL NOT NULL,
    benchmark_return_pct REAL NOT NULL,
    abnormal_return_pct REAL NOT NULL,
    UNIQUE(observation_id, day_number)
);
CREATE INDEX IF NOT EXISTS idx_oos_daily_obs ON oos_daily_prices(observation_id);
"""

# ---------------------------------------------------------------------------
# Hypothesis CRUD
# ---------------------------------------------------------------------------

# Fields stored as JSON in the hypotheses table
_HYPOTHESIS_JSON_FIELDS = {
    "causal_mechanism_criteria", "backtest_symbols", "backtest_events",
    "historical_evidence", "out_of_sample_split", "confounders",
    "symbol_warnings", "dead_end_warnings", "trade", "result",
    "spec_json",
}

# Known top-level column names in hypotheses table
_HYPOTHESIS_COLUMNS = {
    "id", "created", "prediction_hash", "idempotency_key", "status",
    "event_type", "event_description", "causal_mechanism",
    "causal_mechanism_criteria", "expected_symbol", "expected_direction",
    "expected_magnitude_pct", "expected_timeframe_days", "event_timing",
    "backtest_symbols", "backtest_events", "historical_evidence",
    "sample_size", "consistency_pct", "out_of_sample_split", "confounders",
    "market_regime_note", "regime_note", "confidence", "literature_reference",
    "survivorship_bias_note", "selection_bias_note", "success_criteria",
    "passes_multiple_testing",
    "multiple_testing_warning", "confounder_warnings", "symbol_warnings",
    "dead_end_warnings", "trade", "result",
    "trigger", "trigger_position_size", "trigger_stop_loss_pct",
    "trigger_take_profit_pct", "extra",
    "hypothesis_class", "spec_json",
}


def _hypothesis_to_dict(row):
    """Convert a row dict to a hypothesis dict, deserializing JSON fields."""
    d = dict(row)
    # Try to deserialize any string that looks like JSON (starts with [ or {)
    for field in list(d.keys()):
        val = d.get(field)
        if isinstance(val, str) and val and val[0] in ("{", "["):
            try:
                d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    # Merge extra fields back into the top-level dict
    extra = d.pop("extra", None)
    if extra:
        try:
            extra_dict = json.loads(extra) if isinstance(extra, str) else extra
            if isinstance(extra_dict, dict):
                d.update(extra_dict)
        except (json.JSONDecodeError, TypeError):
            pass
    # Convert passes_multiple_testing from int to bool
    if d.get("passes_multiple_testing") is not None:
        d["passes_multiple_testing"] = bool(d["passes_multiple_testing"])
    return d


def _hypothesis_from_dict(d):
    """Convert a hypothesis dict to column values for INSERT/UPDATE."""
    row = {}
    extra = {}
    for key, val in d.items():
        if key in _HYPOTHESIS_COLUMNS:
            if key == "passes_multiple_testing" and val is not None:
                row[key] = 1 if val else 0
            elif isinstance(val, (list, dict)):
                # Always serialize complex types to JSON
                row[key] = json.dumps(val)
            else:
                row[key] = val
        else:
            # Store unknown fields in extra
            extra[key] = val
    if extra:
        row["extra"] = json.dumps(extra)
    return row


def load_hypotheses():
    """Load all hypotheses as a list of dicts (same format as old JSON file)."""
    init_db()
    rows = _q("SELECT * FROM hypotheses ORDER BY created")
    return [_hypothesis_to_dict(row) for row in rows]


def save_hypotheses(hypotheses):
    """Replace all hypotheses (bulk save, used for backward compatibility)."""
    conn = get_db()
    init_db()
    conn.execute("DELETE FROM hypotheses")
    for h in hypotheses:
        _upsert_hypothesis(h, conn)
    conn.commit()


def save_hypothesis(hypothesis):
    """Upsert a single hypothesis."""
    conn = get_db()
    init_db()
    _upsert_hypothesis(hypothesis, conn)
    conn.commit()


def _upsert_hypothesis(h, conn):
    """Insert or replace a hypothesis row."""
    row = _hypothesis_from_dict(h)
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)
    conn.execute(
        f"INSERT OR REPLACE INTO hypotheses ({col_names}) VALUES ({placeholders})",
        tuple(row[c] for c in columns),
    )


def get_hypotheses_by_status(status):
    """Load hypotheses filtered by status."""
    init_db()
    rows = _q(
        "SELECT * FROM hypotheses WHERE status = ? ORDER BY created", (status,)
    )
    return [_hypothesis_to_dict(row) for row in rows]


def get_hypothesis_by_id(hypothesis_id):
    """Load a single hypothesis by ID."""
    init_db()
    row = _q1(
        "SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)
    )
    return _hypothesis_to_dict(row) if row else None


def find_hypothesis_by_idempotency_key(key):
    """Find a hypothesis by idempotency key (for duplicate detection)."""
    init_db()
    row = _q1(
        "SELECT * FROM hypotheses WHERE idempotency_key = ?", (key,)
    )
    return _hypothesis_to_dict(row) if row else None


def update_hypothesis_fields(hypothesis_id, **fields):
    """Update specific fields on a hypothesis."""
    conn = get_db()
    # Load existing to merge extra fields correctly
    existing = get_hypothesis_by_id(hypothesis_id)
    if not existing:
        raise ValueError(f"Hypothesis {hypothesis_id} not found")
    # Guard: don't mark completed without a result
    if fields.get("status") == "completed" and not fields.get("result") and not existing.get("result"):
        raise ValueError(f"Cannot mark {hypothesis_id} completed without a result")
    existing.update(fields)
    _upsert_hypothesis(existing, conn)
    conn.commit()


def count_hypotheses_by_status(status):
    """Count hypotheses with a given status (without loading full data)."""
    init_db()
    return _scalar(
        "SELECT COUNT(*) FROM hypotheses WHERE status = ?", (status,)
    )


# ---------------------------------------------------------------------------
# Knowledge Base CRUD
# ---------------------------------------------------------------------------

def load_knowledge():
    """Load the full knowledge base as a dict (backward-compatible format)."""
    conn = get_db()
    init_db()
    kb = {"literature": {}, "known_effects": {}, "dead_ends": []}

    for row in _q("SELECT * FROM literature"):
        data = json.loads(row["data"])
        data["recorded"] = row["recorded"]
        kb["literature"][row["event_type"]] = data

    for row in _q("SELECT * FROM known_effects"):
        data = json.loads(row["data"])
        if isinstance(data, str):
            data = {"description": data}
        data["last_updated"] = row["last_updated"]
        kb["known_effects"][row["event_type"]] = data

    for row in _q("SELECT * FROM dead_ends ORDER BY id"):
        entry = {
            "event_type": row["event_type"],
            "reason": row["reason"],
            "recorded": row["recorded"],
        }
        if row["updated"]:
            entry["updated"] = row["updated"]
        kb["dead_ends"].append(entry)

    return kb


def save_knowledge(kb):
    """Save the full knowledge base (bulk replacement, backward compatibility)."""
    conn = get_db()
    init_db()

    conn.execute("DELETE FROM literature")
    for event_type, data in kb.get("literature", {}).items():
        recorded = data.pop("recorded", datetime.now().isoformat())
        conn.execute(
            "INSERT INTO literature (event_type, data, recorded) VALUES (?, ?, ?)",
            (event_type, json.dumps(data), recorded),
        )
        data["recorded"] = recorded  # restore the dict

    conn.execute("DELETE FROM known_effects")
    for event_type, data in kb.get("known_effects", {}).items():
        last_updated = data.pop("last_updated", datetime.now().isoformat())
        conn.execute(
            "INSERT INTO known_effects (event_type, data, last_updated) VALUES (?, ?, ?)",
            (event_type, json.dumps(data), last_updated),
        )
        data["last_updated"] = last_updated

    conn.execute("DELETE FROM dead_ends")
    for de in kb.get("dead_ends", []):
        conn.execute(
            "INSERT INTO dead_ends (event_type, reason, recorded, updated) VALUES (?, ?, ?, ?)",
            (de["event_type"], de["reason"], de.get("recorded"), de.get("updated")),
        )

    conn.commit()


def record_literature(event_type, findings):
    """Store literature review findings for an event type."""
    conn = get_db()
    init_db()
    recorded = datetime.now().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO literature (event_type, data, recorded) VALUES (?, ?, ?)",
        (event_type, json.dumps(findings), recorded),
    )
    conn.commit()


def record_known_effect(event_type, effect):
    """Record a validated causal effect."""
    conn = get_db()
    init_db()
    last_updated = datetime.now().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO known_effects (event_type, data, last_updated) VALUES (?, ?, ?)",
        (event_type, json.dumps(effect), last_updated),
    )
    conn.commit()


def record_dead_end(event_type, reason):
    """Record a research direction that didn't pan out."""
    conn = get_db()
    init_db()
    # Check if already exists — update if so
    existing = _q1(
        "SELECT id FROM dead_ends WHERE event_type = ?", (event_type,)
    )
    if existing:
        conn.execute(
            "UPDATE dead_ends SET reason = ?, updated = ? WHERE event_type = ?",
            (reason, datetime.now().isoformat(), event_type),
        )
    else:
        conn.execute(
            "INSERT INTO dead_ends (event_type, reason, recorded) VALUES (?, ?, ?)",
            (event_type, reason, datetime.now().isoformat()),
        )
    conn.commit()


def get_dead_ends():
    """Get all dead ends."""
    init_db()
    return _q("SELECT * FROM dead_ends ORDER BY id")


def get_known_effect(event_type):
    """Get a single known effect by event type."""
    init_db()
    row = _q1(
        "SELECT * FROM known_effects WHERE event_type = ?", (event_type,)
    )
    if row:
        data = json.loads(row["data"])
        if isinstance(data, str):
            data = {"description": data}
        data["last_updated"] = row["last_updated"]
        return data
    return None


# ---------------------------------------------------------------------------
# Research Queue CRUD
# ---------------------------------------------------------------------------

def load_queue():
    """Load the full research queue state (backward-compatible dict format)."""
    conn = get_db()
    init_db()
    q = {"queue": [], "event_watchlist": [], "next_session_priorities": []}

    for row in _q("SELECT * FROM research_queue ORDER BY priority, added"):
        task = {
            "id": row["id"],
            "category": row["category"],
            "question": row["question"],
            "priority": row["priority"],
            "status": row["status"],
            "reasoning": row["reasoning"],
            "added": row["added"],
        }
        if row["completed"]:
            task["completed"] = row["completed"]
        if row["findings"]:
            task["findings"] = row["findings"]
        if row["depends_on"]:
            task["depends_on"] = row["depends_on"]
        if row["implementation_notes"]:
            task["implementation_notes"] = row["implementation_notes"]
        if row["extra"]:
            try:
                task.update(json.loads(row["extra"]))
            except (json.JSONDecodeError, TypeError):
                pass
        q["queue"].append(task)

    for row in _q("SELECT * FROM event_watchlist ORDER BY id"):
        entry = {
            "event": row["event"],
            "expected_date": row["expected_date"],
            "symbol": row["symbol"],
            "added": row["added"],
            "status": row["status"],
        }
        if row["hypothesis_template"]:
            try:
                entry["hypothesis_template"] = json.loads(row["hypothesis_template"])
            except (json.JSONDecodeError, TypeError):
                entry["hypothesis_template"] = row["hypothesis_template"]
        if row["triggered_date"]:
            entry["triggered_date"] = row["triggered_date"]
        q["event_watchlist"].append(entry)

    for row in _q("SELECT * FROM session_priorities ORDER BY id"):
        q["next_session_priorities"].append({
            "task": row["task"],
            "set_by_session": row["set_by_session"],
        })

    handoff_row = _q1(
        "SELECT * FROM session_handoff WHERE id = 1"
    )
    if handoff_row:
        try:
            q["session_handoff"] = json.loads(handoff_row["data"])
            q["session_handoff"]["written_at"] = handoff_row["written_at"]
        except (json.JSONDecodeError, TypeError):
            pass

    return q


def save_queue(q):
    """Save the full research queue state (bulk replacement, backward compatibility)."""
    conn = get_db()
    init_db()

    conn.execute("DELETE FROM research_queue")
    for task in q.get("queue", []):
        _insert_research_task(task, conn)

    conn.execute("DELETE FROM event_watchlist")
    for entry in q.get("event_watchlist", []):
        _insert_watchlist_entry(entry, conn)

    conn.execute("DELETE FROM session_priorities")
    for p in q.get("next_session_priorities", []):
        if isinstance(p, dict):
            task_str = str(p.get("task", ""))
            session_str = str(p.get("set_by_session", "")) or None
            conn.execute(
                "INSERT INTO session_priorities (task, set_by_session) VALUES (?, ?)",
                (task_str, session_str),
            )
        else:
            conn.execute(
                "INSERT INTO session_priorities (task) VALUES (?)", (str(p),)
            )

    handoff = q.get("session_handoff")
    if handoff:
        # handoff may be a dict (with a "written_at" key) or a plain string;
        # load_queue tolerates both, so save must too.
        if isinstance(handoff, dict):
            written_at = handoff.pop("written_at", datetime.now().isoformat())
            data = json.dumps(handoff)
        else:
            written_at = datetime.now().isoformat()
            data = json.dumps(handoff)
        conn.execute(
            "INSERT OR REPLACE INTO session_handoff (id, data, written_at) VALUES (1, ?, ?)",
            (data, written_at),
        )
        if isinstance(handoff, dict):
            handoff["written_at"] = written_at

    conn.commit()


def _insert_research_task(task, conn):
    """Insert a single research task row."""
    known_cols = {"id", "category", "question", "priority", "status",
                  "reasoning", "added", "completed", "findings", "depends_on",
                  "implementation_notes"}
    extra = {k: v for k, v in task.items() if k not in known_cols}
    conn.execute(
        """INSERT OR REPLACE INTO research_queue
           (id, category, question, priority, status, reasoning, added,
            completed, findings, depends_on, implementation_notes, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task.get("id"), task["category"], task["question"],
            task.get("priority", 3), task.get("status", "pending"),
            task.get("reasoning"), task.get("added"),
            task.get("completed"), task.get("findings"),
            task.get("depends_on"), task.get("implementation_notes"),
            json.dumps(extra) if extra else None,
        ),
    )


def _insert_watchlist_entry(entry, conn):
    """Insert a single event watchlist entry."""
    template = entry.get("hypothesis_template")
    if template is not None and not isinstance(template, str):
        template = json.dumps(template)
    conn.execute(
        """INSERT OR IGNORE INTO event_watchlist
           (event, expected_date, symbol, hypothesis_template, added, status, triggered_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry["event"], entry["expected_date"], entry["symbol"],
            template, entry.get("added"),
            entry.get("status", "watching"), entry.get("triggered_date"),
        ),
    )


def add_research_task(category, question, priority, reasoning, depends_on=None):
    """Add a research task. Returns task dict or None if duplicate."""
    conn = get_db()
    init_db()
    import uuid

    # Deduplication
    existing = _q1(
        "SELECT id FROM research_queue WHERE status = 'pending' AND category = ? AND question = ?",
        (category, question),
    )
    if existing:
        return None

    task_id = uuid.uuid4().hex[:8]
    task = {
        "id": task_id,
        "category": category,
        "question": question,
        "priority": priority,
        "status": "pending",
        "reasoning": reasoning,
        "added": datetime.now().isoformat(),
    }
    if depends_on:
        task["depends_on"] = depends_on

    _insert_research_task(task, conn)
    conn.commit()
    return task


def complete_research_task(task_id, findings_summary):
    """Mark a research task as completed. Returns True if found and updated."""
    conn = get_db()
    init_db()
    now = datetime.now().isoformat()

    # Try by ID first
    result = conn.execute(
        "UPDATE research_queue SET status = 'completed', completed = ?, findings = ? "
        "WHERE id = ? AND status IN ('pending', 'in_progress')",
        (now, findings_summary, task_id),
    )
    if result.rowcount > 0:
        conn.commit()
        return True

    # Fallback: match by category. Use a subquery for the ORDER BY/LIMIT —
    # `UPDATE ... ORDER BY ... LIMIT` needs the SQLITE_ENABLE_UPDATE_DELETE_LIMIT
    # compile flag, which neither stock sqlite3 nor libSQL ships with.
    result = conn.execute(
        "UPDATE research_queue SET status = 'completed', completed = ?, findings = ? "
        "WHERE id = (SELECT id FROM research_queue "
        "WHERE category = ? AND status IN ('pending', 'in_progress') "
        "ORDER BY priority LIMIT 1)",
        (now, findings_summary, task_id),
    )
    if result.rowcount > 0:
        conn.commit()
        return True

    return False


def add_event_to_watchlist(event_description, expected_date, symbol, hypothesis_template):
    """Add an event to the watchlist. Returns entry dict or None if duplicate."""
    conn = get_db()
    init_db()

    template = hypothesis_template
    if template is not None and not isinstance(template, str):
        template = json.dumps(template)

    try:
        conn.execute(
            """INSERT INTO event_watchlist
               (event, expected_date, symbol, hypothesis_template, added, status)
               VALUES (?, ?, ?, ?, ?, 'watching')""",
            (event_description, expected_date, symbol, template,
             datetime.now().isoformat()),
        )
        conn.commit()
        return {
            "event": event_description,
            "expected_date": expected_date,
            "symbol": symbol,
            "hypothesis_template": hypothesis_template,
            "added": datetime.now().isoformat(),
            "status": "watching",
        }
    except _INTEGRITY_ERRORS:
        return None  # Duplicate


def set_next_session_priorities(priorities, handoff=None):
    """Set what the next session should focus on."""
    conn = get_db()
    init_db()
    now = datetime.now().isoformat()

    conn.execute("DELETE FROM session_priorities")
    for p in priorities:
        conn.execute(
            "INSERT INTO session_priorities (task, set_by_session) VALUES (?, ?)",
            (p, now),
        )

    if handoff:
        conn.execute(
            "INSERT OR REPLACE INTO session_handoff (id, data, written_at) VALUES (1, ?, ?)",
            (json.dumps(handoff), now),
        )

    conn.commit()


def get_next_research_task():
    """Get the highest-priority pending research task whose dependencies are met."""
    init_db()
    completed_ids_rows = _q(
        "SELECT id FROM research_queue WHERE status = 'completed'"
    )
    completed_ids = {row["id"] for row in completed_ids_rows}

    tasks = _q(
        "SELECT * FROM research_queue WHERE status = 'pending' ORDER BY priority, added"
    )

    for task in tasks:
        dep = task["depends_on"]
        if dep and dep not in completed_ids:
            continue
        return dict(task)
    return None


def get_due_events(today=None):
    """Get watchlist events that are due today or overdue."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    init_db()
    return _q(
        "SELECT * FROM event_watchlist WHERE status = 'watching' AND expected_date <= ?",
        (today,),
    )


def mark_event_triggered(event_description):
    """Mark a watchlist event as triggered."""
    conn = get_db()
    init_db()
    conn.execute(
        "UPDATE event_watchlist SET status = 'triggered', triggered_date = ? "
        "WHERE event = ? AND status = 'watching'",
        (datetime.now().isoformat(), event_description),
    )
    conn.commit()


def expire_old_events():
    """Mark overdue watchlist events as expired (2-day grace period)."""
    conn = get_db()
    init_db()
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    conn.execute(
        "UPDATE event_watchlist SET status = 'expired' "
        "WHERE status = 'watching' AND expected_date < ?",
        (cutoff,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Migration from JSON files
# ---------------------------------------------------------------------------

def migrate_from_json(base_dir=None):
    """
    One-time migration from JSON files to SQLite.

    Reads hypotheses.json, knowledge_base.json, and research_queue.json,
    then inserts all data into the SQLite database. Idempotent — safe to
    run multiple times (uses INSERT OR REPLACE).

    Returns a summary dict.
    """
    if base_dir is None:
        base_dir = os.path.dirname(__file__)

    init_db()
    summary = {"hypotheses": 0, "literature": 0, "known_effects": 0,
               "dead_ends": 0, "queue_tasks": 0, "watchlist": 0}

    # 1. Hypotheses
    hyp_path = os.path.join(base_dir, "hypotheses.json")
    if os.path.exists(hyp_path):
        with open(hyp_path) as f:
            hypotheses = json.load(f)
        save_hypotheses(hypotheses)
        summary["hypotheses"] = len(hypotheses)

    # 2. Knowledge base
    kb_path = os.path.join(base_dir, "knowledge_base.json")
    if os.path.exists(kb_path):
        with open(kb_path) as f:
            kb = json.load(f)
        save_knowledge(kb)
        summary["literature"] = len(kb.get("literature", {}))
        summary["known_effects"] = len(kb.get("known_effects", {}))
        summary["dead_ends"] = len(kb.get("dead_ends", []))

    # 3. Research queue
    rq_path = os.path.join(base_dir, "research_queue.json")
    if os.path.exists(rq_path):
        with open(rq_path) as f:
            rq = json.load(f)
        save_queue(rq)
        summary["queue_tasks"] = len(rq.get("queue", []))
        summary["watchlist"] = len(rq.get("event_watchlist", []))

    return summary


# ---------------------------------------------------------------------------
# Research Journal
# ---------------------------------------------------------------------------

def append_journal_entry(date, session_type, investigated, findings,
                         surprised_by=None, next_step=None, category=None,
                         public_summary=None):
    """Append one research journal entry. Returns the row id.

    Args:
        public_summary: 1-2 sentence plain-English summary for the public dashboard.
            Should be jargon-free, no IDs, no file names, no ALL CAPS labels.
    """
    conn = get_db()
    init_db()
    rowid = _exec(
        "INSERT INTO research_journal (date, session_type, investigated, findings, "
        "surprised_by, next_step, category, public_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (date, session_type, investigated, findings, surprised_by, next_step,
         category, public_summary),
    )
    conn.commit()
    return rowid


def get_recent_journal(n=5):
    """Get the N most recent journal entries (newest first)."""
    init_db()
    rows = _q(
        "SELECT * FROM research_journal ORDER BY id DESC LIMIT ?", (n,)
    )
    return [dict(r) for r in reversed(rows)]  # return in chronological order


def count_journal_entries():
    init_db()
    return _scalar("SELECT COUNT(*) FROM research_journal")


# ---------------------------------------------------------------------------
# Friction Log
# ---------------------------------------------------------------------------

def append_friction(date, category, description, turns_wasted=0, potential_fix=None):
    """Append one friction log entry. Returns the row id."""
    conn = get_db()
    init_db()
    rowid = _exec(
        "INSERT INTO friction_log (date, category, description, turns_wasted, potential_fix) "
        "VALUES (?, ?, ?, ?, ?)",
        (date, category, description, turns_wasted, potential_fix),
    )
    conn.commit()
    return rowid


def get_friction_summary(top_n=3):
    """Get top friction categories with counts and latest description."""
    init_db()
    rows = _q(
        "SELECT category, COUNT(*) as cnt FROM friction_log GROUP BY category ORDER BY cnt DESC LIMIT ?",
        (top_n,),
    )
    result = []
    for row in rows:
        latest = _q1(
            "SELECT description FROM friction_log WHERE category = ? ORDER BY id DESC LIMIT 1",
            (row["category"],),
        )
        result.append({
            "category": row["category"],
            "count": row["cnt"],
            "latest_description": latest["description"] if latest else "",
        })
    return result


def count_friction_entries():
    init_db()
    return _scalar("SELECT COUNT(*) FROM friction_log")


# ---------------------------------------------------------------------------
# Token Usage
# ---------------------------------------------------------------------------

def append_token_usage(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                       cache_creation_tokens=0, total_tokens=0, api_calls=0,
                       session=None, status=None, timestamp=None):
    """Append one token usage record."""
    conn = get_db()
    init_db()
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO token_usage (input_tokens, output_tokens, cache_read_tokens, "
        "cache_creation_tokens, total_tokens, api_calls, session, status, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
         total_tokens, api_calls, session, status, timestamp),
    )
    conn.commit()


def get_daily_token_usage(date_str=None):
    """Sum token usage for all sessions on a given date."""
    conn = get_db()
    init_db()
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    row = _q1(
        "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens, "
        "COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens, "
        "COALESCE(SUM(cache_creation_tokens),0) AS cache_creation_tokens, "
        "COALESCE(SUM(total_tokens),0) AS total_tokens, "
        "COALESCE(SUM(api_calls),0) AS api_calls, COUNT(*) AS sessions "
        "FROM token_usage WHERE timestamp LIKE ?",
        (date_str + "%",),
    )
    return {
        "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
        "cache_read_tokens": row["cache_read_tokens"],
        "cache_creation_tokens": row["cache_creation_tokens"],
        "total_tokens": row["total_tokens"], "api_calls": row["api_calls"],
        "sessions": row["sessions"],
    }


# ---------------------------------------------------------------------------
# Trade Log
# ---------------------------------------------------------------------------

def append_trade_log(action):
    """Append a trade action. Extracts known columns, puts rest in extra."""
    conn = get_db()
    init_db()
    known = {"type", "hypothesis_id", "symbol", "direction", "entry_price",
             "position_size", "order_id", "trigger_type", "error", "timestamp"}
    extra = {k: v for k, v in action.items() if k not in known}
    # Map 'trigger' key to 'trigger_type' column to avoid SQL keyword
    trigger_type = action.get("trigger_type") or action.get("trigger")
    conn.execute(
        "INSERT INTO trade_log (type, hypothesis_id, symbol, direction, entry_price, "
        "position_size, order_id, trigger_type, error, extra, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (action.get("type", "unknown"), action.get("hypothesis_id"),
         action.get("symbol"), action.get("direction"),
         action.get("entry_price"), action.get("position_size"),
         action.get("order_id"), trigger_type,
         action.get("error"), json.dumps(extra) if extra else None,
         action.get("timestamp", datetime.now().isoformat())),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Pre-registrations (replaces results.jsonl)
# ---------------------------------------------------------------------------

def append_pre_registration(hypothesis_id, prediction_hash, data):
    """Log a pre-registration entry."""
    conn = get_db()
    init_db()
    conn.execute(
        "INSERT INTO pre_registrations (hypothesis_id, prediction_hash, data, timestamp) "
        "VALUES (?, ?, ?, ?)",
        (hypothesis_id, prediction_hash, json.dumps(data) if isinstance(data, dict) else data,
         datetime.now().isoformat()),
    )
    conn.commit()


def get_pre_registrations():
    """Get all pre-registration entries."""
    init_db()
    rows = _q("SELECT * FROM pre_registrations ORDER BY id")
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["data"] = json.loads(d["data"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Patterns (replaces patterns.json)
# ---------------------------------------------------------------------------

def load_patterns():
    """Load all patterns as a dict keyed by event_type."""
    init_db()
    rows = _q("SELECT * FROM patterns")
    result = {}
    for r in rows:
        try:
            result[r["event_type"]] = json.loads(r["data"])
        except (json.JSONDecodeError, TypeError):
            result[r["event_type"]] = r["data"]
    return result


def save_patterns(patterns):
    """Save patterns dict (full replacement)."""
    conn = get_db()
    init_db()
    conn.execute("DELETE FROM patterns")
    for event_type, data in patterns.items():
        conn.execute(
            "INSERT INTO patterns (event_type, data) VALUES (?, ?)",
            (event_type, json.dumps(data) if isinstance(data, (dict, list)) else str(data)),
        )
    conn.commit()


def save_pattern(event_type, data):
    """Upsert a single pattern."""
    conn = get_db()
    init_db()
    conn.execute(
        "INSERT OR REPLACE INTO patterns (event_type, data) VALUES (?, ?)",
        (event_type, json.dumps(data) if isinstance(data, (dict, list)) else str(data)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# KV State (replaces peak_equity.json, scanner states, etc.)
# ---------------------------------------------------------------------------

def get_state(key):
    """Get a state value by key. Returns parsed dict/value or None."""
    init_db()
    row = _q1("SELECT value FROM kv_state WHERE key = ?", (key,))
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_state(key, value):
    """Set a state value (upsert). Value can be dict, list, or scalar."""
    conn = get_db()
    init_db()
    conn.execute(
        "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(value) if isinstance(value, (dict, list)) else str(value),
         datetime.now().isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Scanner Signals (replaces scanner JSONL files)
# ---------------------------------------------------------------------------

def append_scanner_signal(scanner, data):
    """Append a scanner signal."""
    conn = get_db()
    init_db()
    conn.execute(
        "INSERT INTO scanner_signals (scanner, data, timestamp) VALUES (?, ?, ?)",
        (scanner, json.dumps(data) if isinstance(data, (dict, list)) else str(data),
         datetime.now().isoformat()),
    )
    conn.commit()


def get_scanner_signals(scanner, limit=50):
    """Get recent signals for a scanner."""
    init_db()
    rows = _q(
        "SELECT * FROM scanner_signals WHERE scanner = ? ORDER BY id DESC LIMIT ?",
        (scanner, limit),
    )
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["data"] = json.loads(d["data"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return list(reversed(result))


# ---------------------------------------------------------------------------
# Migration from JSONL/JSON log files
# ---------------------------------------------------------------------------

def migrate_logs(base_dir=None):
    """One-time migration of JSONL/JSON log files into SQLite tables.

    Idempotent — skips tables that already have data.
    Returns a summary dict.
    """
    if base_dir is None:
        base_dir = os.path.dirname(__file__)

    init_db()
    conn = get_db()
    summary = {}

    def _read_jsonl(path):
        entries = []
        if not os.path.exists(path):
            return entries
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def _read_json(path):
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return None

    def _str(val):
        """Convert any value to string for TEXT columns."""
        if val is None:
            return None
        if isinstance(val, (list, dict)):
            return json.dumps(val)
        return str(val)

    # 1. Research journal
    if _scalar("SELECT COUNT(*) FROM research_journal") == 0:
        entries = _read_jsonl(os.path.join(base_dir, "logs", "research_journal.jsonl"))
        for e in entries:
            conn.execute(
                "INSERT INTO research_journal (date, session_type, investigated, findings, surprised_by, next_step, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_str(e.get("date")), _str(e.get("session_type")), _str(e.get("investigated")),
                 _str(e.get("findings")), _str(e.get("surprised_by")), _str(e.get("next_step")),
                 _str(e.get("category"))),
            )
        summary["journal"] = len(entries)

    # 2. Friction log
    if _scalar("SELECT COUNT(*) FROM friction_log") == 0:
        entries = _read_jsonl(os.path.join(base_dir, "logs", "friction_log.jsonl"))
        for e in entries:
            conn.execute(
                "INSERT INTO friction_log (date, category, description, turns_wasted, potential_fix) "
                "VALUES (?, ?, ?, ?, ?)",
                (e.get("date"), e.get("category", "other"), e.get("description"),
                 e.get("turns_wasted", 0), e.get("potential_fix")),
            )
        summary["friction"] = len(entries)

    # 3. Token usage
    if _scalar("SELECT COUNT(*) FROM token_usage") == 0:
        entries = _read_jsonl(os.path.join(base_dir, "logs", "token_usage.jsonl"))
        for e in entries:
            conn.execute(
                "INSERT INTO token_usage (input_tokens, output_tokens, cache_read_tokens, "
                "cache_creation_tokens, total_tokens, api_calls, session, status, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.get("input_tokens", 0), e.get("output_tokens", 0),
                 e.get("cache_read_tokens", 0), e.get("cache_creation_tokens", 0),
                 e.get("total_tokens", 0), e.get("api_calls", 0),
                 e.get("session"), e.get("status"), e.get("timestamp", "")),
            )
        summary["token_usage"] = len(entries)

    # 4. Trade log
    if _scalar("SELECT COUNT(*) FROM trade_log") == 0:
        entries = _read_jsonl(os.path.join(base_dir, "logs", "trade_log.jsonl"))
        for e in entries:
            known = {"type", "hypothesis_id", "symbol", "direction", "entry_price",
                     "position_size", "order_id", "trigger", "error", "timestamp"}
            extra = {k: v for k, v in e.items() if k not in known}
            conn.execute(
                "INSERT INTO trade_log (type, hypothesis_id, symbol, direction, entry_price, "
                "position_size, order_id, trigger_type, error, extra, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.get("type", "unknown"), e.get("hypothesis_id"), e.get("symbol"),
                 e.get("direction"), e.get("entry_price"), e.get("position_size"),
                 e.get("order_id"), e.get("trigger"), e.get("error"),
                 json.dumps(extra) if extra else None,
                 e.get("timestamp", "")),
            )
        summary["trade_log"] = len(entries)

    # 5. Pre-registrations (results.jsonl)
    if _scalar("SELECT COUNT(*) FROM pre_registrations") == 0:
        entries = _read_jsonl(os.path.join(base_dir, "results.jsonl"))
        for e in entries:
            conn.execute(
                "INSERT INTO pre_registrations (hypothesis_id, prediction_hash, data, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (e.get("hypothesis_id", e.get("id", "")),
                 e.get("prediction_hash"),
                 json.dumps(e),
                 e.get("timestamp", e.get("created", ""))),
            )
        summary["pre_registrations"] = len(entries)

    # 6. Patterns
    if _scalar("SELECT COUNT(*) FROM patterns") == 0:
        data = _read_json(os.path.join(base_dir, "patterns.json"))
        if data and isinstance(data, dict):
            for event_type, pattern_data in data.items():
                conn.execute(
                    "INSERT INTO patterns (event_type, data) VALUES (?, ?)",
                    (event_type, json.dumps(pattern_data)),
                )
            summary["patterns"] = len(data)

    # 7. KV state — peak_equity
    if get_state("peak_equity") is None:
        data = _read_json(os.path.join(base_dir, "logs", "peak_equity.json"))
        if data:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("peak_equity", json.dumps(data), data.get("updated", datetime.now().isoformat())),
            )
            summary["peak_equity"] = 1

    # 8. KV state — scanner states
    for key, path in [
        ("52w_scanner", os.path.join(base_dir, "logs", "52w_low_scanner_state.json")),
        ("sp500_scanner", os.path.join(base_dir, "logs", "sp500_scanner_state.json")),
        ("sp500_announcements", os.path.join(base_dir, "logs", "sp500_announcement_state.json")),
        ("health_state", os.path.join(base_dir, "logs", "health_state.json")),
        ("session_state", os.path.join(base_dir, "logs", "session_state.json")),
        ("clinical_disqualified", os.path.join(base_dir, "logs", "clinical_disqualified_events.json")),
        ("scanner_blacklist", os.path.join(base_dir, "tools", "scanner_blacklist.json")),
        ("volume_disposition", os.path.join(base_dir, "logs", "volume_disposition_results.json")),
    ]:
        if get_state(key) is None:
            data = _read_json(path)
            if data is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(data), datetime.now().isoformat()),
                )
                summary[key] = 1

    # 9. Scanner signals from JSONL files
    if _scalar("SELECT COUNT(*) FROM scanner_signals") == 0:
        for scanner, path in [
            ("52w_low", os.path.join(base_dir, "logs", "52w_low_signals.jsonl")),
            ("sp500_additions", os.path.join(base_dir, "logs", "sp500_additions_detected.jsonl")),
            ("ceo_departure", os.path.join(base_dir, "logs", "ceo_departure_alerts.jsonl")),
        ]:
            entries = _read_jsonl(path)
            for e in entries:
                conn.execute(
                    "INSERT INTO scanner_signals (scanner, data, timestamp) VALUES (?, ?, ?)",
                    (scanner, json.dumps(e), e.get("timestamp", e.get("date", ""))),
                )
            if entries:
                summary[f"signals_{scanner}"] = len(entries)

    conn.commit()
    return summary


# ---------------------------------------------------------------------------
# NAV Snapshots
# ---------------------------------------------------------------------------

def snapshot_nav(date, equity, cash, position_count=0):
    """Upsert today's NAV into nav_snapshots. Idempotent per date."""
    conn = get_db()
    init_db()
    conn.execute(
        "INSERT OR REPLACE INTO nav_snapshots (date, equity, cash, position_count, snapshot_time) "
        "VALUES (?, ?, ?, ?, ?)",
        (date, equity, cash, position_count, datetime.now().isoformat()),
    )
    conn.commit()


def get_nav_history():
    """Return all NAV snapshots ordered by date."""
    init_db()
    return _q(
        "SELECT date, equity, cash, position_count FROM nav_snapshots ORDER BY date"
    )


# ---------------------------------------------------------------------------
# Task Results (for data_tasks.py — stores full results, returns summaries)
# ---------------------------------------------------------------------------

def store_task_result(result_id, task_type, parameters, result, summary):
    """Store a data task result. Parameters and result are dicts, summary is a string."""
    conn = get_db()
    init_db()
    conn.execute(
        "INSERT INTO task_results (id, task_type, parameters, result, summary, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            result_id,
            task_type,
            json.dumps(parameters, default=str) if isinstance(parameters, (dict, list)) else parameters,
            json.dumps(result, default=str) if isinstance(result, (dict, list)) else result,
            summary,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def get_task_result(result_id):
    """Retrieve a task result by ID. Returns full result dict or None."""
    init_db()
    row = _q1(
        "SELECT * FROM task_results WHERE id = ?", (result_id,)
    )
    if row is None:
        return None
    d = dict(row)
    for field in ("parameters", "result"):
        val = d.get(field)
        if isinstance(val, str) and val and val[0] in ("{", "["):
            try:
                d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def get_task_summary(result_id):
    """Retrieve just the summary string for a task result."""
    init_db()
    return _scalar(
        "SELECT summary FROM task_results WHERE id = ?", (result_id,)
    )


def get_recent_task_results(task_type=None, limit=20):
    """Retrieve recent task results, optionally filtered by type."""
    init_db()
    if task_type:
        return _q(
            "SELECT id, task_type, summary, timestamp FROM task_results "
            "WHERE task_type = ? ORDER BY timestamp DESC LIMIT ?",
            (task_type, limit),
        )
    return _q(
        "SELECT id, task_type, summary, timestamp FROM task_results "
        "ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    )


# ---------------------------------------------------------------------------
# OOS Observations CRUD
# ---------------------------------------------------------------------------

def create_oos_observation(obs_id, signal_type, symbol, benchmark, direction,
                            entry_date, entry_price, entry_benchmark_price,
                            hold_days, success_threshold_pct=None,
                            hypothesis_id=None, notes=None):
    """Create a new OOS observation. Returns the inserted dict."""
    conn = get_db()
    init_db()
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO oos_observations
           (id, hypothesis_id, signal_type, symbol, benchmark, direction,
            entry_date, entry_price, entry_benchmark_price, hold_days,
            success_threshold_pct, status, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'tracking', ?, ?, ?)""",
        (obs_id, hypothesis_id, signal_type, symbol, benchmark, direction,
         entry_date, entry_price, entry_benchmark_price, hold_days,
         success_threshold_pct, notes, now, now),
    )
    conn.commit()
    return get_oos_observation(obs_id)


def get_oos_observation(obs_id):
    """Fetch a single OOS observation by ID."""
    init_db()
    return _q1("SELECT * FROM oos_observations WHERE id = ?", (obs_id,))


def get_active_oos_observations():
    """Get all OOS observations with status='tracking'."""
    init_db()
    return _q(
        "SELECT * FROM oos_observations WHERE status = 'tracking' ORDER BY entry_date",
    )


def get_oos_observations(status=None, signal_type=None):
    """Get OOS observations with optional filters."""
    init_db()
    query = "SELECT * FROM oos_observations WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if signal_type:
        query += " AND signal_type = ?"
        params.append(signal_type)
    query += " ORDER BY entry_date DESC"
    return _q(query, params)


def update_oos_status(obs_id, status):
    """Update the status of an OOS observation."""
    conn = get_db()
    init_db()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE oos_observations SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, obs_id),
    )
    conn.commit()


def upsert_oos_daily_price(observation_id, day_number, trade_date,
                            symbol_close, benchmark_close,
                            raw_return_pct, benchmark_return_pct,
                            abnormal_return_pct):
    """Insert or update a daily price record for an OOS observation."""
    conn = get_db()
    init_db()
    conn.execute(
        """INSERT INTO oos_daily_prices
           (observation_id, day_number, trade_date, symbol_close,
            benchmark_close, raw_return_pct, benchmark_return_pct,
            abnormal_return_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(observation_id, day_number) DO UPDATE SET
            trade_date = excluded.trade_date,
            symbol_close = excluded.symbol_close,
            benchmark_close = excluded.benchmark_close,
            raw_return_pct = excluded.raw_return_pct,
            benchmark_return_pct = excluded.benchmark_return_pct,
            abnormal_return_pct = excluded.abnormal_return_pct""",
        (observation_id, day_number, trade_date, symbol_close,
         benchmark_close, raw_return_pct, benchmark_return_pct,
         abnormal_return_pct),
    )
    conn.commit()


def get_oos_daily_prices(observation_id):
    """Get all daily prices for an OOS observation, ordered by day_number."""
    init_db()
    return _q(
        "SELECT * FROM oos_daily_prices WHERE observation_id = ? ORDER BY day_number",
        (observation_id,),
    )
