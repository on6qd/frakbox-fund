# Research Documentation Standard

How research is recorded at frakbox_fund. Every idea, every investigation, and every
verdict lives in a **readable document** — not scattered across database rows. A working
quantitative researcher should be able to open any file in `research/` and feel at home:
a clear thesis, pre-registered tests, in-sample and out-of-sample evidence, an honest
verdict.

The database does not disappear — it becomes a **thin index** built from the documents
(see [Documents vs. the index](#documents-vs-the-index)). The documents are the source of
truth.

---

## 1. Principles

1. **The document is the unit of research.** One idea → one Concept Note → one Investment
   Thesis. All work on that idea is recorded in its thesis, start to finish.
2. **Pre-register before you test.** Success and failure criteria are written down *before*
   the evidence is gathered. A thesis that moves the goalposts after seeing the data is
   worthless.
3. **Discovery is not validation.** Every claim is tested in-sample (discovery) and then
   re-tested on a held-out out-of-sample (validation) window. Both must be reported.
4. **Invalid is a result, not a failure.** A clean kill — documented in the graveyard —
   is as valuable as a validated thesis. No signal is permanent.
5. **Separation of concerns.** The researcher produces a finished report and stops there.
   What to trade, how much, when, and with what stop is the **trading desk's** decision.
   Research documents carry *conclusions and evidence*, never order tickets.

---

## 2. The research pipeline

```
   suggested / scanned
          │
          ▼
   ┌──────────────┐     researcher
   │ Concept Note │     picks it up
   │  concepts/   │ ───────────────┐
   └──────────────┘                │
                                   ▼
                          ┌──────────────────┐
                          │ Investment Thesis│  living document —
                          │     theses/      │  all research recorded here
                          └──────────────────┘
                                   │
                    verdict ───────┴──────── verdict
                  validated                invalidated
                       │                        │
                       ▼                        ▼
                 ┌──────────┐            ┌────────────┐
                 │   desk/  │            │ graveyard/ │
                 │ (inbox)  │            │(post-mortem)│
                 └──────────┘            └────────────┘
                       │
                       ▼
              read by the trading desk
              (a separate consumer — owns execution)
```

A **Concept Note** is an idea before any work. The researcher promotes it to an
**Investment Thesis**, which is the living document where the investigation happens. When
the thesis reaches a verdict it is finalized and filed: `desk/` if validated, `graveyard/`
if invalidated.

---

## 3. Folder layout

```
research/
├── concepts/      Concept Notes — proposed ideas, not yet worked
├── theses/        Investment Theses — under active research
├── desk/          Validated theses, finalized — the trading desk's inbox
├── graveyard/     Invalidated theses — post-mortems
└── templates/     concept_note.md, investment_thesis.md
```

A document's **folder is its status.** Promoting or deciding a thesis means moving the
file. `ls research/desk/` answers "what is waiting for the trading desk?" without a query.

---

## 4. Document types

| Type | Lives in | Length | Written by | Purpose |
|---|---|---|---|---|
| **Concept Note** | `concepts/` | ~½ page | scanner / orchestrator / human | Capture an idea and its testable prediction before any work |
| **Investment Thesis** | `theses/` → `desk/` or `graveyard/` | 1–3 pages | orchestrator (researcher) | The full investigation, ending in a verdict. *This finished thesis is the final report.* |

There is no separate "trade recommendation" document. The validated Investment Thesis **is**
the deliverable to the desk. The desk produces its own order tickets on its own terms.

---

## 5. Naming and IDs

```
CN-YYYY-MMDD-short-slug     Concept Note     e.g. CN-2026-0612-vix-utilities-overshoot
IT-YYYY-MMDD-short-slug     Investment Thesis e.g. IT-2026-0613-vix-utilities-overshoot
```

- The date is the day the document was **opened**.
- The slug is lowercase, hyphenated, ≤ 5 words, descriptive of the mechanism.
- A thesis keeps its ID for life — through every folder move. Links never break.
- The thesis records its parent in front-matter (`concept_note: CN-...`).

---

## 6. Front-matter schema

Every document opens with a YAML block. This is the **only** machine-readable part; the
index ([§9](#documents-vs-the-index)) is built from it. Everything below the front-matter is
prose for human readers.

```yaml
---
id: IT-2026-0613-vix-utilities-overshoot
title: Utility sector overshoots on VIX spikes and mean-reverts in 5 days
status: researching            # proposed | researching | validated | invalidated
conviction: medium             # low | medium | high   (omit until a verdict is reached)
asset_class: equity            # equity | rates | fx | commodity | crypto | credit
universe: [XLU]                # tickers / series the thesis concerns
direction: long                # long | short | long_short | market_neutral
horizon_days: 5                # expected holding period of the effect
hypothesis_class: threshold    # one of the 10 classes (see CLAUDE.md)
concept_note: CN-2026-0612-vix-utilities-overshoot
opened: 2026-06-13
decided:                       # YYYY-MM-DD, set when a verdict is filed
author: orchestrator           # agent or person who owns the document
---
```

**Field rules**

- `status` must match the folder the file is in (`validated` → `desk/`, `invalidated` →
  `graveyard/`, `proposed` → `concepts/`, anything else → `theses/`).
- `conviction` is left blank while researching; it is set at verdict time and only for
  validated theses.
- `hypothesis_class` is one of the ten classes in `CLAUDE.md` (event, exposure, lead_lag,
  cointegration, regime, structural_break, threshold, network, calendar, cross_section).
- `universe` is a list. For a **single-name** thesis, list the tickers/series
  (`[XLU]`, `[KO, PEP]`). For a **screen-based** strategy whose universe is dynamic — the
  candidate is whatever a scan flags, not a fixed ticker — use a single screen token
  `screen:<name>` (e.g. `[screen:edgar_form4_cluster]`). The desk reads the token to know
  which screen produces the tradable names.
- Concept Notes use a reduced subset: `id, title, status, asset_class, universe,
  direction, hypothesis_class, opened, author`.

---

## 7. Status lifecycle

| Transition | Trigger | Action |
|---|---|---|
| _(none)_ → `proposed` | Idea suggested or scan hit queued | Create Concept Note in `concepts/` |
| `proposed` → `researching` | Researcher picks it up | Create Investment Thesis in `theses/`; link the Concept Note |
| `researching` → `validated` | Thesis passes its pre-registered criteria | Set `conviction`, `decided`; move file to `desk/` |
| `researching` → `invalidated` | Thesis fails its criteria | Write the post-mortem; set `decided`; move file to `graveyard/` |

A Concept Note that is never worked stays in `concepts/`. A Concept Note that is rejected on
sight (duplicate, known dead end) is moved straight to `graveyard/` with a one-line reason —
no thesis is opened.

**Validation bar.** A thesis may only be filed to `desk/` if it clears the same gates the
data engine enforces: out-of-sample significance (not just in-sample), multiple-testing
correction where applicable, and the class-specific canonical retest (e.g. threshold hits
must have `canonical_passes=True`; structural breaks are contemporaneous-only and are never
validated as standalone signals). See `CLAUDE.md` for the per-class rules.

---

## 8. The trading-desk handoff

`research/desk/` is an **inbox the researcher writes to and the desk reads from.** That is the
entire interface. The researcher's responsibility ends when a validated thesis lands there.

The desk (`trade_loop.py` and any trader agent) decides instrument, size, entry, stop,
target, and timing using its own risk framework and the conclusions in the report. The
researcher does **not** set triggers, position sizes, or stops in the document. If the desk
needs a field to act on, it reads it from the front-matter (`direction`, `horizon_days`,
`universe`, `conviction`) and the verdict section — it does not expect an order ticket.

---

## 9. Documents vs. the index

The documents are canonical. The database is a **derived index**, regenerated from
front-matter, that exists so the dashboard, the scanner's de-duplication, and the desk can
query state quickly without parsing every file.

```
research/**/*.md  ──(parse front-matter)──▶  index (db)  ──▶  dashboard, scanner dedup, desk
   (source of truth)                          (derived, disposable)
```

Rules:

- Never record a research conclusion only in the database. If it matters, it is in the
  document.
- The index can always be rebuilt by re-reading `research/`. Treat it as a cache.
- Execution state (open positions, fills, OOS daily prices) belongs to the **desk** and
  stays in its own tables — it is not research documentation and is out of scope here.

---

## 10. House style

Write the way a buy-side research note reads: a **memo frame** carrying **quant evidence**.

- Lead with the conclusion. The first paragraph states the thesis and, once decided, the
  verdict. A reader should get the point in 20 seconds.
- Quantify everything. "Strong reversion" is not a finding; "mean +2.1% over 5 days,
  pos-rate 63%, p=0.004, N=178" is.
- Always show **discovery and validation** numbers side by side. An in-sample-only result is
  a hypothesis, not a finding.
- State the **mechanism** — why this effect should exist in the world. A pattern with no
  economic story is a candidate for over-fitting, and the thesis should say so.
- Write the **falsification** honestly: what you expected to see that would kill the thesis,
  and whether you saw it.
- Prefer tables for evidence, prose for reasoning. **Every evidence figure must be
  reproducible:** record the `task_id` of the `data_tasks.py` run that produced it (retrieve
  later with `python3 data_tasks.py get-result --id T-...`). For a number that predates the
  task store, a `log:<file>` reference is an acceptable fallback — but new work should always
  carry a real `task_id`.

The two templates in `research/templates/` encode this structure. Start from them.
