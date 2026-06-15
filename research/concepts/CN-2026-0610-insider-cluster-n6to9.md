---
id: CN-2026-0610-insider-cluster-n6to9
title: Clusters of 6–9 insiders buying predict short-horizon outperformance
status: proposed
asset_class: equity
universe: [screen:edgar_form4_cluster]
direction: long
hypothesis_class: event
opened: 2026-06-10
author: scanner
---

# Concept Note — Clusters of 6–9 insiders buying predict short-horizon outperformance

## The idea
When 6 to 9 corporate insiders at the same company file Form 4 open-market purchases within
a short window, the stock tends to outperform the market over the following week. The
6–9 band is the interesting part: it is large enough to exceed random coincidence but below
the n≥10 level where reporting artifacts (lock-up expirations, SPAC structures) inflate the
count without carrying signal.

## Proposed mechanism
Insiders buy with personal capital and under SEC scrutiny, so a broad cluster of independent
buyers signals genuine internal conviction. Form 4s are public within two business days, but
the market takes 3–10 days to aggregate the signal across separate filings and data vendors —
so the drift is slow enough to capture rather than instantly arbitraged.

## Testable prediction
When 6–9 distinct insiders buy within a rolling window, the stock earns a positive 5-day
abnormal return (vs. SPY), averaging > 1%, more than 55% of the time.

- **Trigger / condition:** 6–9 distinct insiders file Form 4 purchases in a rolling window
- **Expected reaction:** long the stock; positive abnormal drift
- **Horizon:** 5 trading days (possibly extending to 10)

## Why it might be mispriced
The signal is fragmented across many small filings and only becomes legible once aggregated.
Retail does not watch EDGAR bulk feeds; the aggregation lag is the source of the edge, and it
persists because surfacing the cluster takes days.

## Source & priority
- **Surfaced by:** EDGAR Form 4 cluster scan (`data_tasks.py scan-insiders`)
- **Priority:** high — large candidate sample, clean trigger, supporting literature
- **Known-prior check:** Related to the general insider-buying literature (Seyhun 1998;
  Cohen, Malloy & Pomorski 2012). Not a duplicate of any settled thesis; the n=6–9 band
  narrowing is the novel element.
