# Research Architecture — Migration TODO

Work plan for moving the researcher to the **document-centric** architecture. Pick these up
in any order within a phase. Goal: get the *researcher's* architecture completely sound
**before** building the trading-desk consumer (that is deliberately parked — see Phase 3).

Read `RESEARCH_DOCS.md` first — it is the standard these tasks implement.

---

## Decisions already locked (do not re-litigate)

- **Document-centric.** Research narrative lives in markdown under `research/`; the DB is a
  *derived index*, not the source of truth.
- **Hybrid storage.** Documents are canonical for narrative; a thin index (`research_docs`
  table, built from front-matter) feeds dashboard / dedup / desk. Execution + knowledge base
  + session journal stay in the DB.
- **House style:** blend — investment-memo frame carrying quant evidence (discovery vs.
  out-of-sample, multiple-testing, pre-registration).
- **Separation of concerns.** The researcher's deliverable ends at a validated thesis in
  `research/desk/`. A *separate* trading desk consumes it and owns execution. The researcher
  never writes triggers/sizes/stops.
- **Folder = status.** `concepts/`=proposed, `theses/`=researching, `desk/`=validated,
  `graveyard/`=invalidated.
- **Screen-based universes** use a `screen:<name>` token (e.g. `[screen:edgar_form4_cluster]`)
  instead of a fixed ticker.

## Already done this session (context for a cold start)

- `RESEARCH_DOCS.md` — the standard (lifecycle, folder layout, front-matter schema, house style).
- `research/templates/{concept_note,investment_thesis}.md` — the two templates.
- `research/concepts/` `theses/` `desk/` `graveyard/` — created with `.gitkeep`.
- Worked example (react-test, real n=6–9 insider-cluster numbers):
  `research/concepts/CN-2026-0610-insider-cluster-n6to9.md` and
  `research/desk/IT-2026-0611-insider-cluster-n6to9.md`.
- `research_docs.py` — front-matter→index builder. CLI: `reindex`, `validate`, `summary`,
  `list`. Wired into `run.py --context` (RESEARCH PIPELINE section).
- `CLOUD_ROUTINE.md`, `.claude/agents/orchestrator.md`, `.claude/agents/scanner.md` — updated
  to the document-centric flow (scanner writes Concept Notes; orchestrator promotes to theses,
  files to desk/graveyard, reindexes, commits `research/`).
- **Not committed yet** — all of the above is uncommitted working-tree changes.

---

## Phase 1 — Make the researcher sound (do these)

### 1.1 Backfill: migrate existing DB research into documents
Build a **one-time, non-destructive** `migrate_to_docs.py` that renders existing DB rows into
the document tree, then runs `research_docs.py reindex`. Leave the DB intact as a fallback.

**Scope (from a read-only DB inventory taken 2026-06-15):**
- **72 hypotheses → Investment Theses.** All 72 have `causal_mechanism` text and an
  `out_of_sample_split`, so they render with real content. Status → folder:
  - `completed` & `result.direction_correct=1` (4) → `desk/`
  - `completed` & `=0` (7), `invalidated`/`disproven`/`abandoned`/`superseded` (56) → `graveyard/`
  - `pending` (4) → `theses/`
- **10 pending `research_queue` items → Concept Notes** in `concepts/`.

**Field → section mapping** (see the table in this session's notes / `RESEARCH_DOCS.md` §6):
`event_type`/`event_description`→title+summary; `causal_mechanism`(+`_criteria`)→§2;
`success_criteria`/`prediction_hash`→§3; `historical_evidence`,`sample_size`,`consistency_pct`,
`out_of_sample_split`,`passes_multiple_testing`→§4; `confounders`,`survivorship_bias_note`,
`selection_bias_note`→§5; `result`→§7 verdict; `confidence`→front-matter `conviction`;
`expected_symbol/direction/timeframe`→`universe/direction/horizon_days`.

**Explicitly DO NOT turn these into per-row documents** (they are derived/operational, ~1,300
rows — they would bury the ~82 real theses):
- `known_effects` (506) and `dead_ends` (355) — the knowledge base. Keep in DB. *Optional later:*
  distill into a single `KNOWN_EFFECTS.md` / dead-ends rollup.
- `research_journal` (499) — session log. Keep in DB.

**Acceptance:** ~82 documents created; `python3 research_docs.py validate` passes; DB untouched;
script is idempotent (re-runnable without dupes) and prints a per-row report of any field gaps.

### 1.2 Change the research cadence (investigate ≠ multiple times/day)
Research on historical data is time-of-day idempotent — the every-2h `investigate` cadence is a
leftover from the old continuous daemon, a throughput dial, not an information requirement.
- Keep **Scan = daily** (justified: new EOD data / filings).
- Move **Investigate = once daily, or on-demand when `concepts/` is non-empty** (backlog-driven,
  not wall-clock).
- Leave deterministic time-sensitive jobs on their own schedules (trade_loop every 2 min,
  `oos update`, daily scanners) — they need no LLM.

**Acceptance:** `CLOUD_ROUTINE.md` (and the scheduled-agent config) updated; rationale noted in
the "Notes / decisions" section.

---

## Phase 2 — Audit fixes (from the 2026-06-15 project audit)

### 2.1 Dashboard reflects documents (HIGH)
`dashboard/export.py` builds every export from `db.get_hypotheses_by_status()` / `load_hypotheses()`
— it does not read the `research_docs` index, so the public dashboard will diverge from the real
document-held research state. Point the research-pipeline portions of the export at `research_docs`.

### 2.2 Decide `research_queue`'s fate (MED)
The scanner now writes Concept Notes, but `research_queue` is still live (`db.py`,
`research_queue.py`, `email_report.py:735`, `data_tasks.py` scan-hit guards) and `run.py --context`
shows **both** the pipeline and the queue. Decide: do Concept Notes *replace* `scan_hit` queue rows
or supplement them? Then remove the dual display / retire the redundant path.

### 2.3 Quick wins (LOW)
- Fix the `research_docs.py` module docstring — it claims the dashboard/scanner/desk consume the
  index; none do yet. Make it accurate.
- Move root one-off scripts to `archive/`: `backtest_n69.py`, `backtest_material_weakness.py`,
  `preregister_n69.py`, `preregister_n69_v2.py`, `migrate.py`.
- Add `pytest` to a dev-requirements file and write a smoke test for `research_docs.py`
  (parse / folder↔status validation / reindex round-trip).
- Track the band-aid at `trade_loop.py:351` (insider-cluster position cut to $2,500 "intraday
  scanner not yet built") so it isn't forgotten.
- Verify `MAX_POSITION_PCT` (5%) is enforced against live equity, not just the static $5K default.

---

## Phase 3 — Parked until Phases 1–2 are done

### 3.1 Trading-desk consumer (DEFERRED ON PURPOSE)
Nothing reads `research/desk/` yet; `trade_loop.py` still fires off `trigger*` columns in the
`hypotheses` table. Until the researcher's architecture is sound, do **not** build the consumer.
When ready: a component that reads validated theses from `research/desk/` and either sets the
`hypotheses` triggers `trade_loop` already understands, **or** teaches `trade_loop` to read
`desk/` directly. Keep the orchestrator's "Trading Safety" transition note in force until then so
trading doesn't stall.

---

## Suggested order
1.1 backfill → 1.2 cadence → 2.1 dashboard → 2.2 queue decision → 2.3 quick wins → (commit) → 3.1.
