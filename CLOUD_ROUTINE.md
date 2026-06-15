# Cloud Research Routine (Claude Code scheduled agent)

This is the **#13** migration step: research sessions move off the local
`researcher.sh` tmux daemon (archived) and run as a **scheduled Claude Code
agent** in the cloud, every 2 hours, sharing state with the Mac Mini through
**Turso (libSQL)**.

Each run: clone repo → install deps → read context → run ONE scan-or-investigate
session (the 6-step method) → record the work as **documents** under `research/`
→ reindex → commit and push the documents (and any code) to GitHub.

Research narrative is now **document-centric** (see `RESEARCH_DOCS.md`): every idea
is a Concept Note (`research/concepts/`), every investigation an Investment Thesis
(`research/theses/`), and a verdict files the thesis to `research/desk/` (validated)
or `research/graveyard/` (invalidated). These markdown documents are the source of
truth and ARE committed to git. The Turso database is a **derived index** rebuilt
from the documents' front-matter (`python3 research_docs.py reindex`) plus the
execution/queue tables; never record a research conclusion only in the database.

---

## Prerequisites (do these first)

1. **Code pushed to GitHub** (done #19) — repo renamed to `on6qd/frakbox-fund`,
   all commits pushed to `main`.
2. **Turso is live** (done — `frakbox-fund`, seeded + verified).
3. **Model / cadence decided** (done — two routines, see below).

---

## Web-UI configuration — TWO routines

Mirrors the old design (deep Opus investigations + a cheaper daily scan). Both
use the same repo, setup step, env vars, and allowlist (below); they differ only
in schedule, model, and prompt.

**Routine A — Investigate (deep)**

| Field | Value |
|---|---|
| **Repo** | `on6qd/frakbox-fund` |
| **Branch** | `main` |
| **Schedule** | every 2 hours |
| **Model** | Opus (deep 6-step investigations) |
| **Prompt** | the INVESTIGATE prompt below |

**Routine B — Scan (broad)**

| Field | Value |
|---|---|
| **Repo** | `on6qd/frakbox-fund` |
| **Branch** | `main` |
| **Schedule** | once a day |
| **Model** | Haiku (high-throughput, cheap) |
| **Prompt** | the SCAN prompt below |

### Setup step (both routines — runs before the prompt, installs deps)

```bash
python3 -m venv venv && . venv/bin/activate
pip install -q -r requirements.txt
```

### Environment variables (set in the routine config — NOT committed)

| Var | Source |
|---|---|
| `TURSO_DATABASE_URL` | `libsql://frakbox-fund-on6qd.aws-eu-west-1.turso.io` |
| `TURSO_AUTH_TOKEN` | Turso token (the one created for `frakbox-fund`) |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Alpaca paper account |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` |
| `TIINGO_API_KEY` | Tiingo (delisted-ticker fallback) |
| `FRED_API_KEY` | FRED (macro series) |

`db.py` reads these from the environment (real env wins over `.env`, which is
absent in the cloud), so it auto-selects the **libSQL backend** and builds a
fresh embedded replica synced from Turso each run. The replica is ephemeral —
Turso is the source of truth.

### Network allowlist

**Core (required):**
```
pypi.org, files.pythonhosted.org          # pip install
github.com, api.github.com                 # clone + push
*.turso.io                                 # database (sync to primary)
paper-api.alpaca.markets, data.alpaca.markets   # trading/quotes
query1.finance.yahoo.com, query2.finance.yahoo.com, fc.yahoo.com   # yfinance
api.stlouisfed.org, fred.stlouisfed.org    # FRED
www.sec.gov, efts.sec.gov, data.sec.gov    # SEC EDGAR
api.tiingo.com                             # Tiingo fallback
mba.tuck.dartmouth.edu                     # Fama-French factors
```

**Scanners / news enrichment (add as needed):**
```
en.wikipedia.org, raw.githubusercontent.com, cdn.finra.org,
www.treasurydirect.gov, www.nyse.com, www.spglobal.com, press.spglobal.com,
www.businesswire.com, www.prnewswire.com, search.cnbc.com,
feeds.content.dowjones.io, www.capitoltrades.com, www.insidearbitrage.com,
stockspinoffs.com, openinsider.com, www.matteoiacoviello.com
```

---

## The prompts

> Verbatim-faithful to the archived `researcher.sh` scan/investigate prompts,
> adapted for the cloud (Turso persistence, push code only). Both begin with the
> same preamble.

**Shared preamble (prepend to both):**
```
You are an autonomous research session for the frakbox_fund causal-research system.
The repo is cloned and dependencies are installed (activate venv: . venv/bin/activate).
The database is Turso (libSQL) — db.py auto-detects it from TURSO_* env vars; do NOT
create a local sqlite db.

Research is document-centric. Read RESEARCH_DOCS.md once for the standard. All research
narrative lives in markdown documents under research/ (concepts/, theses/, desk/,
graveyard/) — these are the source of truth and MUST be committed and pushed to GitHub.
Start every document from research/templates/. After creating or moving any document,
run `python3 research_docs.py reindex` (rebuilds the Turso index from front-matter and
validates folder<->status). The database holds only the derived index plus execution/
queue/journal tables — never put a research conclusion only in the database.
```

### Routine A — INVESTIGATE prompt (Opus, every 2h)
```
Run: python3 run.py --context
This is your complete state — account, trades, the RESEARCH PIPELINE (Concept Notes
awaiting research, theses under research, the desk inbox), knowledge, queue, journal,
friction, data integrity, and Steer.md (human directions). Prioritize human directions
over your own pipeline. Do NOT dump full datasets; query individual items only when you
need deep detail.

Pick ONE thing to investigate, in this priority order:
1. A high-priority Concept Note awaiting research (research/concepts/). Promote it to an
   Investment Thesis: copy research/templates/investment_thesis.md to
   research/theses/IT-<date>-<slug>.md, link the Concept Note in front-matter.
2. An Investment Thesis already in research/theses/ that needs more work.
Read API_REFERENCE.md only when you need a function signature.

Do the full 6-step method, recording ALL of it IN the thesis document: pre-registration
(success/kill criteria) BEFORE any data, then discovery and out-of-sample validation
evidence tables (each figure tagged with the data_tasks.py task_id that produced it),
risks, falsification, and a verdict. When you reach a verdict, set conviction + decided in
front-matter and MOVE the file:
  - VALIDATED  -> research/desk/      (this is the deliverable to the trading desk)
  - INVALIDATED -> research/graveyard/ (write the post-mortem; record the dead end)
You produce the report only — never set triggers, position sizes, or stops. The desk owns
execution (RESEARCH_DOCS.md §8).

ONE investigation per session. Tool-call budget ~120 — each call re-reads cached context,
so long sessions are disproportionately expensive. After ~100 calls stop new work. When done:
1. python3 research_docs.py reindex   (rebuild the index; fix any validation issues it prints)
2. db.append_journal_entry(date, type, investigated, findings, surprised_by, next_step,
   public_summary="1-2 plain-English sentences for a public audience. No jargon/IDs/filenames.")
3. git add research/ and commit + push the documents (and any code changes).
```

### Routine B — SCAN prompt (Haiku, daily)
```
High-throughput scan session. Run 30+ quick statistical tests using data_tasks.py.

Quick context: python3 run.py --context | head -90
What's already in the pipeline (don't duplicate): python3 research_docs.py list

Pick a scan theme not done recently. Run as many tests as possible. Respect the per-class
queue guardrails in CLAUDE.md (threshold canonical retest, regime / structural-break
suppression, etc.) — only act on canonical-passing p<0.05 hits.

For each genuine hit, create a Concept Note: copy research/templates/concept_note.md to
research/concepts/CN-<date>-<slug>.md and fill it (idea, proposed mechanism, testable
prediction, the data_tasks.py task_id, priority). Do NOT open theses or run the deep method
— that is the orchestrator's job. Skip a hit if a Concept Note or thesis already covers it.

Tool-call budget ~60 — after ~50, stop. Then:
1. python3 research_docs.py reindex   (rebuild the index; fix any validation issues)
2. Log a journal entry with session_type="scan" and a public_summary describing what you
   screened and how many Concept Notes you created.
3. git add research/ and commit + push the new Concept Notes.
```

---

## Notes / decisions

- **Two routines** (chosen): Opus "investigate" every 2h + Haiku "scan" daily —
  mirrors the old Haiku-scan / Opus-investigate split.
- **Trade execution stays local.** These routines only do research. The trade
  loop runs on the Mac Mini via launchd (#14) against the same Turso DB.
- **Concurrency:** the cloud routine writes research tables; the local trade loop
  writes trade tables — no shared-row conflicts. Both sync to the same primary.
- **Cost control:** the prompt keeps the tool-call budgets from the old design,
  since each call re-reads cached context.
```
