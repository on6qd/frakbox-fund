---
id: CN-YYYY-MMDD-short-slug
title: One line naming the effect, not the asset
status: proposed
asset_class: equity            # equity | rates | fx | commodity | crypto | credit
universe: [TICKER]             # tickers/series, or a screen token e.g. [screen:edgar_form4_cluster]
direction: long                # long | short | long_short | market_neutral
hypothesis_class: threshold    # event | exposure | lead_lag | cointegration | regime |
                               # structural_break | threshold | network | calendar | cross_section
opened: YYYY-MM-DD
author: scanner                # scanner | orchestrator | human
---

# Concept Note — <title>

## The idea
<Two or three sentences. What is the proposed effect of the world on the market? State it
plainly enough that someone could test it without asking you a question.>

## Proposed mechanism
<Why might this exist? The economic or behavioural story — flows, constraints, attention,
hedging pressure, forced selling, slow information diffusion. If you cannot name a
mechanism, say so: a pattern with no cause is a flag for over-fitting.>

## Testable prediction
<The falsifiable claim, with a direction and a horizon. E.g. "When VIX closes above 30,
XLU earns a positive 5-day abnormal return (vs. SPY) averaging > 1%, more than 55% of the
time.">

- **Trigger / condition:** <what has to be true>
- **Expected reaction:** <direction, asset, magnitude if guessable>
- **Horizon:** <N days>

## Why it might be mispriced
<One or two sentences: who is on the other side, and why the effect could persist rather
than be arbitraged away.>

## Source & priority
- **Surfaced by:** <scan hit T-xxxx / paper / news / human suggestion>
- **Priority:** <low | medium | high> — <one-line justification>
- **Known-prior check:** <Searched the graveyard and known effects? Is this a duplicate or
  a variant of something already settled? Reference the IDs if so.>
