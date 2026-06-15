---
id: IT-2026-0611-insider-cluster-n6to9
title: Clusters of 6–9 insiders buying predict a 5-day positive drift — validated
status: validated
conviction: medium
asset_class: equity
universe: [screen:edgar_form4_cluster]
direction: long
horizon_days: 5
hypothesis_class: event
concept_note: CN-2026-0610-insider-cluster-n6to9
opened: 2026-06-11
decided: 2026-06-12
author: orchestrator
---

# Investment Thesis — Clusters of 6–9 insiders buying predict a 5-day positive drift

> **Recommendation:** Long the flagged name · **Conviction:** medium · **Horizon:** 5 days
> · **Status:** validated
>
> **In one paragraph.** When 6–9 distinct insiders at the same company file open-market
> Form 4 purchases within a short window, the stock earns a positive abnormal return over the
> next 5 trading days. The effect is significant in-sample (2021–2022, N=173: +3.57% at 5d,
> p=0.0001) and **strengthens out-of-sample** (2023–2025, N=178: +7.80% at 5d, p<0.0001),
> both periods passing multiple-testing correction. The 5–10 day window is where the edge
> lives; the 1-day and 20-day horizons are not reliable. Validated, conviction held to
> medium by survivorship and selection-bias caveats. Forwarded to the desk.

---

## 1. Thesis
A cluster of 6–9 insiders independently buying their own company's stock is a strong,
underpriced conviction signal. The market does not react instantly because the information is
fragmented across many small Form 4 filings that must be aggregated before the cluster is
legible. That aggregation lag — 3 to 10 days — is the source of a capturable upward drift.
The 6–9 band is deliberately chosen: below it, clusters are noise; at n≥10, counts are
inflated by reporting artifacts that dilute the signal.

## 2. Mechanism
Insiders (officers, directors) commit personal capital and face SEC scrutiny if trading on
material non-public information, so frivolous buying is disincentivized — a *broad* cluster of
independent buyers is a costly, credible signal of internal confidence. Form 4s become public
within two business days via EDGAR; data aggregators and quant scanners then surface the
cluster, generating buy pressure as the aggregated signal propagates to more participants over
5–10 days. The transmission channel is the diffusion lag, not the filing itself — which is why
entry at the next open after detection still captures the drift.

## 3. Pre-registration
*Filled in before any evidence was gathered.*

- **Hypothesis (H1):** When 6–9 distinct insiders buy within a rolling window, the stock earns
  a positive 5-day abnormal return vs. SPY, mean > 1%, with positive rate > 55%.
- **Null (H0):** 5-day abnormal return is indistinguishable from zero.
- **Test / data task:** EDGAR Form 4 cluster detection (`data_tasks.py scan-insiders`,
  n_insiders 6–9) → `backtest` with `entry_price="open"`, abnormal return vs. SPY at
  1/3/5/10/20d.
- **Samples:** discovery = 2021–2022; validation = held-out 2023–2025.
- **Success criteria:** out-of-sample 5d p < 0.05 **and** mean ≥ 1% **and** survives
  multiple-testing correction across horizons, with no sign flip between samples.
- **Kill criteria:** validation 5d p > 0.05, a sign flip between discovery and validation, or
  failure of the multiple-testing correction.

## 4. Evidence

### Discovery (in-sample) — N=173, 2021–2022
| Horizon | Mean abn. ret. | Pos. rate | p-value | task_id |
|---|---|---|---|---|
| 1d  | +0.46% | 50.3% | 0.4725 | log:bfylmnn43 |
| 3d  | +3.30% | 67.6% | <0.0001 | log:bfylmnn43 |
| 5d  | +3.57% | 59.0% | 0.0001 | log:bfylmnn43 |
| 10d | +3.59% | 56.1% | 0.0052 | log:bfylmnn43 |
| 20d | +0.88% | 51.2% | 0.8458 | log:bfylmnn43 |

### Validation (out-of-sample) — N=178, 2023–2025
| Horizon | Mean abn. ret. | Pos. rate | p-value | task_id |
|---|---|---|---|---|
| 1d  | +1.19% | 52.8% | 0.0685 | log:bfylmnn43 |
| 3d  | +4.13% | 64.6% | <0.0001 | log:bfylmnn43 |
| 5d  | +7.80% | 62.9% | <0.0001 | log:bfylmnn43 |
| 10d | +8.40% | 60.7% | <0.0001 | log:bfylmnn43 |
| 20d | +8.02% | 52.3% | 0.0281 | log:bfylmnn43 |

- **Multiple-testing:** Both periods pass correction across the 5 horizons
  (`passes_multiple_testing=True`).
- **Canonical / robustness retest:** Effect concentrated at the 3–10d horizons in both
  samples. 1d is insignificant in discovery (p=0.47) and marginal in validation (p=0.07) —
  consistent with a diffusion lag, not an instant repricing. Entry at next open, vs.-SPY
  abnormal returns.
- **Confidence score:** `medium`, via `self_review.compute_confidence_score`
  (sample_size=173, consistency=59.0%, avg_return=3.57%, stdev=12.0%, literature=partial).

## 5. Risks and limitations
- **Survivorship bias (primary risk).** EDGAR bulk data includes delisted tickers; yfinance/
  Tiingo failures drop ~20% of events (216→173 discovery, 221→178 validation). Surviving firms
  are likely outperformers, so the live effect is probably smaller than measured.
- **Selection bias.** The 6–9 cluster trigger can be inflated by post-IPO lock-up expirations
  and SPAC structures (e.g. LMACA, ROCRU, ZTAQU in the 2021 sample). The edge may be driven by
  a subset of high-quality clusters that are hard to isolate ex-ante.
- **Sector trend.** No sector-ETF adjustment applied; abnormal returns are vs. SPY only. Part
  of the effect could be sector drift. Sector-adjusted re-test is a follow-up.
- **Timing.** Form 4s publish 1–2 business days post-trade, with further vendor-surfacing lag;
  real-time entry timing carries uncertainty the backtest's next-open entry only approximates.

## 6. Falsification — what would have killed it
The leading concern was a **2021 bull-market artifact**: a signal that only works when
everything rises. That predicts a weak or absent validation result. Instead the effect was
*stronger* out-of-sample (5d +7.80% vs. +3.57%) across the mixed 2023–2025 regime — the
artifact hypothesis is refuted. A **sign flip** between samples would also have killed it;
none occurred. The one genuine wrinkle: the 20d horizon is insignificant in discovery
(p=0.85) but significant in validation (p=0.03), so the long-horizon behaviour is not stable —
which is why the recommended horizon is held to 5 days, where both samples agree.

## 7. Verdict
> **VALIDATED** on 2026-06-12.

Met every pre-registered success criterion: out-of-sample 5d return +7.80% (p<0.0001, pos-rate
62.9%), well above the 1% / 55% bar, multiple-testing-corrected, no sign flip — and the effect
strengthened out-of-sample, ruling out the bull-market confound. **Conclusion for the desk:**
go long a name when 6–9 distinct insiders file open-market purchases within the rolling
window; the edge is a 5-day drift (extending toward 10 days), entered at the open after
detection. Conviction is **medium, not high**, capped deliberately by unquantified
survivorship and selection bias — the true live effect is likely smaller than the backtest
shows. A sector-adjusted re-test and a survivorship-corrected resample are the recommended
next steps before sizing up.

---

*Researcher's note: this document is the deliverable. The trading desk decides instrument,
size, entry, stop, and timing from the conclusions above — this thesis carries no order
ticket. See `RESEARCH_DOCS.md` §8.*
