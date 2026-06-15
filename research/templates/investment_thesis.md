---
id: IT-YYYY-MMDD-short-slug
title: One line stating the effect and its resolution
status: researching            # proposed | researching | validated | invalidated
conviction:                    # low | medium | high — set only at verdict, validated only
asset_class: equity            # equity | rates | fx | commodity | crypto | credit
universe: [TICKER]             # tickers/series, or a screen token e.g. [screen:edgar_form4_cluster]
direction: long                # long | short | long_short | market_neutral
horizon_days: 5                # expected holding period of the effect
hypothesis_class: threshold    # one of the 10 classes (see CLAUDE.md)
concept_note: CN-YYYY-MMDD-short-slug
opened: YYYY-MM-DD
decided:                       # YYYY-MM-DD — set when the verdict is filed
author: orchestrator
---

# Investment Thesis — <title>

> **Recommendation:** <Long / Short / Pass> <universe> · **Conviction:** <low/medium/high>
> · **Horizon:** <N days> · **Status:** <researching / validated / invalidated>
>
> **In one paragraph.** <State the thesis and — once decided — the verdict. What is the
> effect, how strong is it out-of-sample, and should the desk look at it? A reader who stops
> here should still get the answer.>

---

## 1. Thesis
<The variant perception in plain English: what is true about the world that the market is
not fully pricing, and how that shows up in returns. Three to five sentences.>

## 2. Mechanism
<Why this effect exists — the causal chain from cause to price. Name the agents and the
constraint: who is forced to act, who is slow to react, what flow or hedging pressure moves
the price. A thesis with strong statistics but no mechanism is a candidate for over-fitting
and should be treated with suspicion; say so explicitly if that is the case.>

## 3. Pre-registration
*Filled in before any evidence is gathered. Do not edit after testing begins.*

- **Hypothesis (H1):** <the falsifiable claim, with direction, asset, horizon>
- **Null (H0):** <no effect: e.g. abnormal return indistinguishable from zero>
- **Test / data task:** <which engine and command — e.g. `threshold --trigger "^VIX"
  --target XLU --threshold-value 30 --direction above`>
- **Samples:** discovery = <window>, validation = <held-out window>
- **Success criteria:** <what makes this VALIDATED — e.g. out-of-sample p < 0.05 AND
  |mean| ≥ 1% AND class-specific canonical retest passes>
- **Kill criteria:** <what makes this INVALIDATED — e.g. validation p > 0.05, sign flip
  between samples, or canonical retest fails>

## 4. Evidence

### Discovery (in-sample) — N=<n>, <window>
| Horizon | Mean abn. ret. | Pos. rate | p-value | task_id |
|---|---|---|---|---|
| 1d  | | | | |
| 3d  | | | | |
| 5d  | | | | |
| 10d | | | | |
| 20d | | | | |

### Validation (out-of-sample) — N=<n>, <window>
| Horizon | Mean abn. ret. | Pos. rate | p-value | task_id |
|---|---|---|---|---|
| 1d  | | | | |
| 3d  | | | | |
| 5d  | | | | |
| 10d | | | | |
| 20d | | | | |

- **Multiple-testing:** <correction applied and result — e.g. survives Holm–Bonferroni
  across the 5 horizons>
- **Canonical / robustness retest:** <class-specific check and outcome — e.g.
  `canonical_passes=True`, cluster-buffered and SPY-adjusted in both pooled and recent
  samples>
- **Confidence score:** <self_review.compute_confidence_score output, if computed>

## 5. Risks and limitations
<What could make this spurious or fragile: regime dependence, small N, crowding, capacity,
data-quality caveats (survivorship, look-ahead), correlation with a known factor. Be the
desk's skeptic.>

## 6. Falsification — what would have killed it
<State what you expected to see that would refute the thesis, and report whether you saw it.
This is the honesty section. An effect that survived a genuine attempt to kill it is worth
far more than one that was never challenged.>

## 7. Verdict
> **VALIDATED / INVALIDATED** on <YYYY-MM-DD>.

<Two to four sentences. Did it meet the pre-registered success criteria? Quote the deciding
out-of-sample numbers. If validated: the conclusion the desk needs — direction, the
condition that triggers it, the horizon, and the conviction with its justification. If
invalidated: what specifically failed, and the lesson recorded for the graveyard so the same
dead end is not re-explored.>

---

*Researcher's note: this document is the deliverable. The trading desk decides instrument,
size, entry, stop, and timing from the conclusions above — this thesis carries no order
ticket. See `RESEARCH_DOCS.md` §8.*
