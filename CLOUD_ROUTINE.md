# Cloud Research Routine (Claude Code scheduled agent)

This is the **#13** migration step: research sessions move off the local
`researcher.sh` tmux daemon (archived) and run as a **scheduled Claude Code
agent** in the cloud, every 2 hours, sharing state with the Mac Mini through
**Turso (libSQL)**.

Each run: clone repo → install deps → read context from Turso → run ONE
scan-or-investigate session (the 6-step method) → write hypotheses/queue/journal
back to Turso → push any *code* changes to GitHub. Research *data* persists to
Turso automatically (db.py writes forward to the primary); git is only for code.

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
The database is Turso (libSQL) — db.py auto-detects it from TURSO_* env vars; all
reads/writes go through db.py and persist to the cloud primary automatically. Do NOT
create a local sqlite db. Research DATA needs no git commit (it's in Turso); commit
and push only CODE changes (new tools/scripts) to GitHub.
```

### Routine A — INVESTIGATE prompt (Opus, every 2h)
```
Run: python3 run.py --context
This is your complete state — account, trades, hypotheses, knowledge, queue, journal,
friction, data integrity, and Steer.md (human directions). Prioritize human directions
over your own queue. Do NOT dump full datasets (load_hypotheses/load_knowledge/load_queue);
query individual items only when you need deep detail.

Check for scan hits first:
  python3 -c "import db; rows=db.get_db().execute(\"SELECT id,question,priority FROM research_queue WHERE category='scan_hit' AND status='pending' ORDER BY priority DESC LIMIT 5\").fetchall(); [print(f'{r[0]}: {r[1][:120]}') for r in rows]"
If a hit has priority >= 8, investigate the top one with the full 6-step method.
Otherwise take the highest-priority queue item. Read API_REFERENCE.md only when you
need a function signature.

ONE investigation per session. Tool-call budget ~120 — each call re-reads cached
context, so long sessions are disproportionately expensive. After ~100 calls stop new
work. When done:
1. Update research_queue with a handoff for the next session.
2. db.append_journal_entry(date, type, investigated, findings, surprised_by, next_step,
   public_summary="1-2 plain-English sentences for a public audience. No jargon/IDs/filenames.")
3. Commit + push any CODE changes.
```

### Routine B — SCAN prompt (Haiku, daily)
```
High-throughput scan session. Run 30+ quick statistical tests using data_tasks.py.

Quick context: python3 run.py --context | head -80
What's been scanned recently:
  python3 -c "import db; rows=db.get_db().execute(\"SELECT question FROM research_queue WHERE category='scan_hit' ORDER BY rowid DESC LIMIT 10\").fetchall(); [print(r[0][:120]) for r in rows]"

Pick a scan theme not done recently. Run as many tests as possible. Queue any
canonical-passing p<0.05 hits to research_queue (category='scan_hit'). Respect the
per-class queue guardrails in CLAUDE.md (threshold canonical retest, regime /
structural-break suppression, etc.).

Tool-call budget ~60 — after ~50, stop, write the journal entry, and exit. Log a
journal entry with session_type="scan" and a public_summary describing what you
screened and how many hits.
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
