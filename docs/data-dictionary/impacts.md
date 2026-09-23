# Data dictionary: impacts

The `impacts` module owns the **impact metric registry**: the list of metrics an impact
claim can report (`deaths`, `houses_destroyed`, ...). A metric is a definition, not a
figure. Impact claims (one value, one source, one confidence level), damage records and
the best-figure read model arrive in Phase 3; they store `metric code + value`, long and
narrow, so the metric decides what every stored value means.

Code: `src/yakhnama/modules/impacts/domain/`. Reference data: `data/reference/impact_metrics.yaml`
(task T8), validated by `ImpactMetricReferenceFile`.

**Status of the content.** Every category, Sendai mapping, DesInventar mapping, the
monetary convention, the aggregation defaults and the ban on negative values below are
**proposed defaults**, not sourced domain facts. They are listed as open questions at the
end of this page and must be confirmed by the maintainer before the dataset is published.

## Stability rules

- A metric `code` is never changed, deleted or reused. Retired codes stay in the
  registry so nothing new can take them.
- `category`, `value_kind`, `unit`, `currency` and `aggregation` never change once a
  metric exists: stored claims would silently change meaning. A different definition is
  a new code, and the old metric is retired with `retirement.replaced_by` naming it.
- Only `labels` change in place (`ImpactMetricRelabelled`), and only while active.

## `ImpactMetric` (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Database identity of the metric. Not published as its key; `code` is. | Generated (`IdGenerator`) | Phase 1 |
| `code` | `str`, `^[a-z][a-z0-9_]{1,63}$` | — | Stable public key used by claims and exports. Immutable, never reused. | Reference file / moderator | Phase 1 |
| `labels` | `LocalizedText` | — | Display names per language; an English (`en`) entry is required. No local-language label without a source. | Reference file | Phase 1 |
| `description` | `LocalizedText` or null | — | Exact meaning: what is counted or measured, over what period, what is excluded. | Reference file | Phase 1 |
| `category` | `MetricCategory` | — | Group of the metric (see below). Immutable. | Reference file (proposed) | Phase 1 |
| `value_kind` | `ValueKind` | — | `count`, `measurement` or `monetary`; fixes the unit rule. Immutable. | Reference file | Phase 1 |
| `unit` | `str` (a `KNOWN_UNITS` name) or null | per row | `count` for counts; an SI unit from `SiUnit` (`metre`, `square_metre`, `cubic_metre`, `kilogram`, `second`, `kelvin`) for measurements; null for money. Immutable. | Reference file | Phase 1 |
| `currency` | `str`, ISO 4217 `^[A-Z]{3}$`, or null | currency | Set only for monetary metrics; default `PKR`. Values are nominal (no inflation or exchange-rate conversion); the claim records the price year. Immutable. | Reference file (proposed) | Phase 1 |
| `sendai` | `SendaiIndicator` or null | — | Sendai Framework global indicator code (`^[A-G]-[0-9]{1,2}[a-z]?$`, for example `A-1`). Null unless the mapping is sourced. | Reference file (proposed) | Phase 1 |
| `desinventar` | `DesInventarField` or null | — | DesInventar effect field name (1 to 64 characters). Null unless sourced. | Reference file (proposed) | Phase 1 |
| `aggregation` | `sum` \| `max` \| `latest` | — | How the Phase 3 best-figure policy combines claims of this metric. Immutable. | Reference file (proposed defaults below) | Phase 1 |
| `status` | `MetricStatus` | — | `active` (new claims may use it) or `retired` (kept for old claims, never reused). | Moderator / reference file | Phase 1 |
| `retirement` | `RetirementReason` or null | — | Set exactly when `status` is `retired`. | Moderator / reference file | Phase 1 |
| `version` | `int` ≥ 1 | — | Optimistic-concurrency version; 1 on creation, +1 per change. | Computed | Phase 1 |
| `created_at` | `datetime` (UTC) | — | When the metric was created in Yakhnama. Precision `exact` (system time). | `Clock` | Phase 1 |
| `updated_at` | `datetime` (UTC) | — | When it last changed; never earlier than `created_at`. Precision `exact`. | `Clock` | Phase 1 |

### Value kinds and value rule

| `value_kind` | `unit` | `currency` | accepted values (`is_valid_value`) |
|--------------|--------|------------|------------------------------------|
| `count` | `count` (required) | null | finite, ≥ 0, whole numbers |
| `measurement` | a `KNOWN_UNITS` unit other than `count` (required) | null | finite, ≥ 0 |
| `monetary` | null | ISO 4217 code (required, default `PKR`) | finite, ≥ 0, nominal amount |

Negative values are rejected for every kind (proposed): a metric records a loss or damage,
and a decrease is a later, lower claim rather than a negative one.

### `MetricCategory` (proposed)

| value | meaning | proposed alignment (unconfirmed) |
|-------|---------|----------------------------------|
| `human` | People killed, missing, injured, affected, displaced | Sendai targets A and B |
| `economic` | Direct economic loss not attributed to one sector | Sendai target C |
| `infrastructure` | Damage to roads, bridges, power, water and other critical infrastructure | Sendai target D |
| `housing` | Houses damaged or destroyed | Sendai housing indicators; DesInventar housing effects |
| `agriculture` | Crops, farmland, livestock, irrigation channels | Sendai agricultural loss; DesInventar crop and livestock effects |
| `environment` | Effects without a Sendai indicator (for example land or forest lost) | DesInventar only |
| `services` | Disruption of basic services (health, education, other) | Sendai target D |

### Aggregation (proposed defaults)

| value | meaning | proposed default for |
|-------|---------|---------------------|
| `sum` | Add claims covering disjoint parts (places, periods) of the event | `count`, `monetary` |
| `max` | Take the largest claim, for peak quantities (flooded area, lake volume) | `measurement` |
| `latest` | Take the most recent claim | chosen explicitly per metric |

Deduplicating overlapping claims from different sources about the same people or assets is
the best-figure policy's job (Phase 3), not the aggregation's.

## `RetirementReason`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `explanation` | `str`, 1 to 1000 characters | — | Why the metric was retired. | Moderator / reference file | Phase 1 |
| `replaced_by` | metric code or null | — | Successor metric, if any; never the metric itself. | Moderator / reference file | Phase 1 |

## `ImpactMetricRef`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `code` | metric code | — | Reference to a metric by its stable code, held by claims (Phase 3). | Caller | Phase 1 |

## Domain events

All carry `event_id`, `occurred_at` (UTC), `aggregate_id` (the metric `id`) and
`aggregate_type = "impact_metric"`.

| `event_type` | extra fields | emitted when |
|--------------|--------------|--------------|
| `impacts.impact_metric_created` | `code`, `category`, `value_kind`, `unit`, `currency` | A metric is created |
| `impacts.impact_metric_retired` | `code`, `reason` (`RetirementReason`) | A metric is retired |
| `impacts.impact_metric_relabelled` | `code`, `labels` | An active metric's labels change |

## Reference file (`ImpactMetricReferenceFile`)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `schema_version` | `int`, only `1` | — | Layout version of the file. | Maintainers | Phase 1 |
| `data_version` | `str`, `^[0-9A-Za-z.-]+$`, ≤ 32 | — | Content version, bumped on every change. | Maintainers | Phase 1 |
| `source` | `str`, 1 to 500 | — | Where the list comes from, or `proposed`. | Maintainers | Phase 1 |
| `licence` | `str`, 1 to 500 | — | Licence of the file's content. | Maintainers | Phase 1 |
| `entries` | list of entries | — | The metrics; codes unique across active and retired entries. | Maintainers | Phase 1 |

Each entry (`ImpactMetricReferenceEntry`) has the `ImpactMetric` fields `code`, `labels`,
`description`, `category`, `value_kind`, `unit`, `currency`, `sendai`, `desinventar`,
`aggregation`, `status` and `retirement` with the same rules, plus:

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `source` | `str`, 1 to 500 | — | Citation for the entry's definition and mappings, or `proposed`. | Maintainers | Phase 1 |
| `notes` | `str`, 1 to 2000, or null | — | Caveats for reviewers. | Maintainers | Phase 1 |

`labels` and `description` may be written as a bare `{language: text}` mapping in YAML.

## JSONB storage

The `impact_metrics` table stores the structured value objects as JSONB columns:
`labels` and `description` (`LocalizedText`), `sendai` (`SendaiIndicator`) and
`desinventar` (`DesInventarField`, both null when not mapped), and `retirement`
(`RetirementReason`, null unless retired). Each column holds exactly the JSON form of
its value object (`model_dump(mode="json")`), with the same fields and meanings as the
tables above. The mapper validates the column back into the value object on every
read, so a malformed value fails loudly and never reaches the domain. Nothing but the
repository writes these columns (`src/yakhnama/modules/impacts/infrastructure/orm.py`).

## Open questions

- **Sendai indicator mapping list.** Which UNDRR Sendai global indicators (A-1, A-2, B-1,
  C-2, D-1, ...) the initial metrics map to, and whether sub-indicator suffixes in the
  UNDRR technical guidance (possibly upper-case, for example crop and livestock splits
  of C-2) fit the proposed code pattern `^[A-G]-[0-9]{1,2}[a-z]?$`. Default: map only
  with a cited source, otherwise null.
- **DesInventar field names.** Exact DesInventar effect field names and spelling.
  Default: null unless sourced.
- **Monetary handling.** Nominal amounts in an ISO 4217 currency, default `PKR`, with the
  price year on the claim, no conversion. The `add-impact-metric` skill says currency
  needs an ADR first. Default: as proposed, pending that ADR.
- **Aggregation defaults.** `sum` for counts and money, `max` for measurements.
- **Negative values.** Rejected for every kind; would any metric need a signed value
  (for example a lake level change)?
- **Category list.** The seven proposed categories and their alignment above.
- **Retired metrics frozen.** Proposed that a retired metric cannot be relabelled.
