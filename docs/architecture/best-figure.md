# Best-figure policy

**Status: proposed.** Every rule on this page is a proposed default from the Phase 3 plan
(§6) or from the `impacts` domain work. None is a sourced domain fact, and all of them
wait for the maintainer to confirm them. The open questions are listed at the end.

Code: `src/yakhnama/modules/impacts/domain/best_figure.py` (`BestFigurePolicy`,
`BestFigure`). Tests: `tests/unit/modules/impacts/domain/test_best_figure.py`, which include
every worked example below.

## Why a policy

Impact figures are recorded as **claims**: one metric value from one source with one
confidence level (`AGENTS.md` §6.1, §7). Claims are append-only. A claim is never
overwritten or hard-deleted. A wrong figure is retracted, or corrected by a new claim that
supersedes it. Several sources usually report different figures for one event (a district
office, NDMA, a newspaper, a citizen), and one source often updates its own figure over
days.

The **best figure** is the single number shown for an event and a metric. It is a
**derived read model**. It is recomputed from the claims every time and never stored as a
fact, so the reason for any figure can be traced back to the exact claims behind it
(`contributing_claim_ids`). A policy that is written down and tested stops the number
from changing with whoever happens to be the moderator.

## Inputs and output

`BestFigurePolicy.compute(metric, claims)` takes:

- `metric`: the `ImpactMetric`. Its `aggregation` (`sum`, `max` or `latest`, fixed per
  metric in the registry) chooses the rule. A retired metric is accepted, because its
  old claims still need a best figure.
- `claims`: every claim of **one event** for **this metric**, active or retracted.

It returns a `BestFigure`:

| field | meaning |
|-------|---------|
| `metric` | The metric, by code. |
| `value` | The best value (a `ClaimValue` of the metric's kind), or null. |
| `basis` | `sum`, `max`, `latest`, or `none` when no claim is active. |
| `contributing_claim_ids` | The claims the value is computed from, in recording order. |
| `confidence` | The lowest confidence among the contributing claims, or null. |
| `computed_at` | When the figure was computed (UTC, from the injected clock). |

The policy refuses to compute when the input is inconsistent, rather than guess:

- `ClaimMetricMismatchError`: a claim of another metric, or claims of more than one event.
- `ClaimValueKindMismatchError` or `ClaimValueUnitMismatchError`: a value whose kind, unit
  or currency differs from the metric's.

## Rules

### R1. Only active claims count

Retracted claims, including those superseded by a correction, never contribute.
*Rationale:* retraction is how the record says "do not use this figure" without deleting
it.

### R2. "Newest" and tie-breaking

The rules below often need the newest claim. Claims are ordered by:

1. `claimed_at` (when the source made the claim), **floored to the start of its
   precision period** (`DateWithPrecision.truncate`, in UTC). *Rationale:* a claim made
   "in August 2022" (month precision) is not known to be later than one made on
   2 August 2022, so it is treated as 1 August.
2. `SourceRank` of the source type, higher first: government > research > satellite >
   dataset > organisation > news > citizen. *Rationale:* when two claims are equally
   recent, the source with the more formal collection process is preferred. The plan
   ranks government, research, organisation, news and citizen. Placing satellite and
   dataset between research and organisation is a further proposal.
3. Recording order in Yakhnama (`created_at`, then the time-ordered UUIDv7 `id`), later
   first. *Rationale:* it is deterministic, and the later entry was made knowing about
   the earlier one.

The source ranking only breaks ties. It never overrides a newer claim or a larger value.

### R3. `sum`: one claim per source and scope, then add

Claims are grouped by `(source_id, scope)`. Only the newest claim of each group (R2) is
kept, and the kept claims are added up.

*Rationale:* a source that reports 10 deaths and later 14 is updating its own figure. It
is not reporting 24. Claims from different sources, or from one source about different
scopes (places or assets), are assumed to be disjoint parts of the event and are added.

- `count` totals are whole numbers.
- `measurement` totals are in the metric's SI unit.
- `monetary` totals are nominal amounts in the metric's currency, with no inflation or
  exchange-rate conversion. The total carries the **latest price year** among its parts
  (an open question: mixing price years is only nominally meaningful).

**Known limitation.** Two sources reporting the same people, or one source reporting a
district and a village inside it, are still added. Removing that overlap needs the place
hierarchy and a moderator's judgement. The policy does not guess. Moderators retract the
overlapping claim instead.

### R4. `max`: the largest value

The contributing claim is the one with the largest value. Equal values are broken by
`SourceRank` first, then by R2. *Rationale:* peak quantities such as flooded area or lake
volume. The largest credible observation is the figure, and smaller claims usually
describe earlier or partial states.

### R5. `latest`: the newest claim

The contributing claim is the newest by R2. *Rationale:* for metrics where each claim
restates the whole figure (for example people currently displaced), the most recent
statement supersedes the older ones.

### R6. Confidence is the minimum

The figure's `confidence` is the lowest (`low` < `medium` < `high`) among the contributing
claims. *Rationale:* a total is only as trustworthy as its weakest part. For `max` and
`latest` there is only one contributing claim, so the figure has that claim's confidence.

### R7. No active claim

When no claim is active, `value`, `confidence` and `contributing_claim_ids` are empty and
`basis` is `none`. The domain model makes a partly empty figure impossible.

## Worked examples

Every example uses one event. "D" is a district office (government), "N" a newspaper
(news), "C" a citizen, "R" a research group. Dates are day precision unless stated.

### Example 1: `sum`, a source updates its own figure (`deaths`)

| claim | source | scope | claimed | value | confidence | status |
|-------|--------|-------|---------|-------|------------|--------|
| a | D | whole event | 1 Aug | 10 | high | active |
| b | D | whole event | 3 Aug | 14 | high | active |
| c | N | Hunza | 1 Aug | 3 | low | active |

Groups: (D, whole event) keeps **b** because it is newer than a. (N, Hunza) keeps **c**.
Best figure: **14 + 3 = 17**, basis `sum`, contributing `[b, c]`, confidence **low**.

### Example 2: `sum` with a retraction and a correction

| claim | source | scope | claimed | value | status |
|-------|--------|-------|---------|-------|--------|
| a | D | whole event | 1 Aug | 10 | retracted, superseded by b |
| b | D | whole event | 1 Aug | 12 | active, `supersedes_id = a` |
| c | C | whole event | 2 Aug | 5 | retracted ("duplicate") |

Only b is active. Best figure: **12**, contributing `[b]`.

### Example 3: `sum` of money with mixed price years (`economic_loss`, PKR)

Two sources report PKR 100.25 (price year 2020) and PKR 100.25 (price year 2022) for
different scopes. Best figure: **PKR 200.50, price year 2022**, nominal and unconverted.

### Example 4: `max`, equal values (`lake_volume`, cubic metres)

| claim | source type | claimed | value |
|-------|-------------|---------|-------|
| a | citizen | 4 Aug | 5 |
| b | government | 1 Aug | 5 |

The values are equal, so the rank decides: **b** (government). A later claim of 6 from
any source would win, because a larger value beats rank.

### Example 5: `latest`, same day

| claim | source type | claimed | value |
|-------|-------------|---------|-------|
| a | news | 1 Aug | 8 |
| b | research | 1 Aug | 6 |

Both were made on the same day, so the rank decides: **b**, value **6**.

### Example 6: `latest`, precision

| claim | claimed | precision | value |
|-------|---------|-----------|-------|
| a | 21 Aug | month (floored to 1 Aug) | 1 |
| b | 2 Aug | day | 2 |

a is only known to be from "August", which floors to 1 August, earlier than b. Best
figure: **b**, value **2**.

### Example 7: `latest`, full tie

Same claimed day, same source type: the claim recorded later in Yakhnama wins.

## Corrections and the append-only rule

`ImpactClaim.correct` never changes a stored claim's value. In one change it creates a
**new** claim whose `supersedes_id` names the old one, and it retracts the old claim with
a reason. The event, metric, source and scope carry over, and the handler saves both
aggregates and both events (`ImpactClaimRetracted` with `superseded_by_id`, then
`ImpactClaimCorrected`) in one unit of work. Corrections chain (a ← b ← c), and only the
last claim in a chain is active. This works like a report revision. A figure from a
different source is a new claim, not a correction.

## Open questions

| question | why it matters | proposed default | blocking |
|----------|----------------|------------------|----------|
| Is `sum` over `(source_id, scope)` the right deduplication, and how should nested scopes (district and village) be handled? | Double counting inflates casualty figures. | Newest claim per source and scope; nested scopes are the moderator's job (retract). | no |
| Where do `satellite` and `dataset` rank? | Ties between them and other types need an order. | research > satellite > dataset > organisation. | no |
| Should `claimed_at` be floored before comparing? | It decides which claim counts as "latest". | Floor to the start of the precision period, in UTC. | no |
| Is the minimum the right combined confidence? | It sets the confidence shown on public figures. | Minimum of contributing claims. | no |
| Monetary sums with different price years. | A nominal sum across years misstates value. | Nominal sum labelled with the latest price year; no conversion. | no |
| May a retired metric be corrected? | Old errors under a retired metric could not be fixed. | No: a retired metric accepts no new claims, corrections included. | no |
| Should the source rank ever override recency or size? | It changes which source "wins". | No: rank only breaks ties. | no |
