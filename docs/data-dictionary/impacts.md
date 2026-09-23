# Data dictionary: impacts

The `impacts` module owns the **impact metric registry**: the list of metrics an impact
claim can report (`deaths`, `houses_destroyed`, ...). A metric is a definition, not a
figure. Since Phase 3 it also owns **impact claims** (one value, one source, one
confidence level; append-only), **infrastructure assets**, **damage records**
(append-only) and the **best figure** read model (see
[`docs/architecture/best-figure.md`](../architecture/best-figure.md)). Claims store
`metric code + value`, long and narrow, so the metric decides what every stored value
means.

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

## `ImpactClaim` (aggregate root, Phase 3)

Append-only. The value is never changed in place: a claim is **retracted** (status plus
reason) or **corrected** (a new claim with `supersedes_id`, and the old claim retracted in
the same unit of work). Claims are never deleted.

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Identity of the claim. | Generated (`IdGenerator`) | Phase 3 |
| `event_id` | `UUID` (v7) | — | The canonical event the figure is about. Called `hazard_event_id` in domain events, because `event_id` there is the event occurrence's id. | Moderator | Phase 3 |
| `metric` | `ImpactMetricRef` | — | The metric, by stable code. The metric must be active when the claim is recorded. | Moderator | Phase 3 |
| `value` | `ClaimValue` | the metric's unit or currency | The claimed value. Its `kind` equals the metric's `value_kind`, and its unit or currency equals the metric's. | Source | Phase 3 |
| `confidence` | `low` \| `medium` \| `high` | — | How far the source's figure can be trusted. | Moderator | Phase 3 |
| `source_id` | `UUID` (v7) | — | The provenance `Source` the figure comes from. | Moderator | Phase 3 |
| `source_type` | `SourceTypeName` | — | The source's type (`citizen`, `organisation`, `government`, `news`, `satellite`, `research`, `dataset`), copied from provenance so ranking needs no lookup. | Provenance | Phase 3 |
| `claimed_at` | `DateWithPrecision` | UTC + precision | When the source made the claim, not when it was entered. | Source | Phase 3 |
| `recorded_by` | `UUID` (v7) | — | The account that entered the claim. Internal; not a public field. | Authenticated actor | Phase 3 |
| `scope` | `ClaimScope` | — | The part of the event the figure covers; the whole event when both fields are null. | Moderator | Phase 3 |
| `note` | safe text, 1 to 1000 characters, line breaks allowed, or null | — | Moderator's note. Never copied into events. Must not contain casualty names. | Moderator | Phase 3 |
| `status` | `active` \| `retracted` | — | Whether the claim still counts towards the best figure. | Moderator | Phase 3 |
| `retraction_reason` | safe text, 1 to 1000 characters, or null | — | Why it was retracted. Set exactly when `retracted`. Never copied into events. | Moderator | Phase 3 |
| `retracted_by` | `UUID` (v7) or null | — | Who retracted it. Set exactly when `retracted`. | Authenticated actor | Phase 3 |
| `supersedes_id` | `UUID` (v7) or null | — | The claim this one corrects; never the claim itself. | Computed (`correct`) | Phase 3 |
| `version` | `int` ≥ 1 | — | Optimistic-concurrency version; 1 on creation, +1 on retraction. | Computed | Phase 3 |
| `created_at` | `datetime` (UTC) | — | When the claim was entered. Precision `exact`. | `Clock` | Phase 3 |
| `updated_at` | `datetime` (UTC) | — | When it last changed; never before `created_at`. Precision `exact`. | `Clock` | Phase 3 |

For a monetary value, `price_year` must not be later than the year of `claimed_at`.

### `ClaimValue` (discriminated by `kind`)

| `kind` | fields | rule |
|--------|--------|------|
| `count` | `count: int` | ≥ 0. Unit is `count`. |
| `measurement` | `measurement: Measurement` (`value`, `unit`) | Finite, ≥ 0, unit in `KNOWN_UNITS` other than `count`, equal to the metric's unit. |
| `monetary` | `amount: Decimal`, `currency`, `price_year` | `amount` ≥ 0, at most 20 digits and 2 decimal places (proposed). `currency` is ISO 4217 and equals the metric's currency. `price_year` is 1900 to 2100 (proposed). Nominal and never converted. |

### `ClaimScope`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `place_code` | geography place code (`^[a-z0-9][a-z0-9_.-]{1,63}$`) or null | — | The place the figure is about, if narrower than the event. The pattern mirrors geography's `PlaceCode`. | Moderator | Phase 3 |
| `asset_id` | `UUID` (v7) or null | — | The infrastructure asset the figure is about. | Moderator | Phase 3 |

### `SourceRank` (proposed)

This ranking only breaks ties in the best-figure policy: government 7, research 6,
satellite 5, dataset 4, organisation 3, news 2, citizen 1 (higher wins). The ordering of
government, research, organisation, news and citizen comes from the Phase 3 plan.
Placing satellite and dataset between research and organisation is a further proposal.
The names mirror provenance's `SourceType`. The impacts domain does not import
provenance, and a unit test pins the list.

## `InfrastructureAsset` (aggregate root, Phase 3)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Identity of the asset. | Generated | Phase 3 |
| `kind` | `AssetKind` | — | `bridge`, `road_segment`, `water_channel`, `power_line`, `building`, `other` (proposed list). Never changes. | Moderator | Phase 3 |
| `name` | safe text, 1 to 200 characters, single line | — | Display name. Only the latest name is kept (`InfrastructureAssetRenamed`). | Moderator / source | Phase 3 |
| `osm_id` | `^(node\|way\|relation)/[1-9][0-9]{0,18}$` or null | — | The asset's OpenStreetMap element. | OSM | Phase 3 |
| `location` | `Coordinates` (WGS84) or null | degrees | A representative point. | Source / moderator | Phase 3 |
| `place_code` | geography place code or null | — | The place the asset lies in. | Moderator | Phase 3 |
| `source_id` | `UUID` (v7) | — | The provenance source describing the asset. | Moderator | Phase 3 |
| `version`, `created_at`, `updated_at` | as for claims | — | Concurrency version and UTC change times. | Computed / `Clock` | Phase 3 |

`relocate` replaces `location`, `place_code` and `osm_id` together, so clearing a wrong
value is explicit.

## `DamageRecord` (aggregate root, Phase 3)

Append-only. The only change is one retraction with a reason. A different level is a new
record.

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Identity of the record. | Generated | Phase 3 |
| `event_id` | `UUID` (v7) | — | The event that caused the damage (`hazard_event_id` in events). | Moderator | Phase 3 |
| `asset_id` | `UUID` (v7) | — | The damaged asset. | Moderator | Phase 3 |
| `level` | `DamageLevel` | — | `damaged` (still standing, needs repair), `destroyed` (unusable, remains in place), `washed_away` (carried off; nothing usable remains). Proposed scale. | Source | Phase 3 |
| `confidence` | `low` \| `medium` \| `high` | — | How far the source can be trusted. | Moderator | Phase 3 |
| `source_id` | `UUID` (v7) | — | The provenance source of the record. | Moderator | Phase 3 |
| `recorded_at` | `DateWithPrecision` | UTC + precision | When the source recorded the damage, not when it was entered. | Source | Phase 3 |
| `recorded_by` | `UUID` (v7) | — | The account that entered the record. Internal. | Authenticated actor | Phase 3 |
| `note` | safe text, 1 to 1000 characters, line breaks allowed, or null | — | Moderator's note; never in events. | Moderator | Phase 3 |
| `status`, `retraction_reason`, `retracted_by` | as for claims | — | Retraction state. | Moderator | Phase 3 |
| `version`, `created_at`, `updated_at` | as for claims | — | Concurrency version and UTC change times. | Computed / `Clock` | Phase 3 |

## `BestFigure` (read model, Phase 3)

The best figure is derived and never stored as a fact. It is computed by `BestFigurePolicy`
under the rules in [`best-figure.md`](../architecture/best-figure.md).

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `metric` | `ImpactMetricRef` | — | The metric. | Input | Phase 3 |
| `value` | `ClaimValue` or null | the metric's | The best value; null when no claim is active. | Computed | Phase 3 |
| `basis` | `sum` \| `max` \| `latest` \| `none` | — | The aggregation applied. | Computed | Phase 3 |
| `contributing_claim_ids` | list of `UUID` | — | The claims behind the value, in recording order. | Computed | Phase 3 |
| `confidence` | `Confidence` or null | — | The lowest confidence among the contributing claims. | Computed | Phase 3 |
| `computed_at` | `datetime` (UTC) | — | When it was computed. | `Clock` | Phase 3 |

## Phase 3 domain events

Events carry ids and structured, non-personal fields only. Notes, reasons and names stay
on the aggregate.

| `event_type` | aggregate | extra fields | emitted when |
|--------------|-----------|--------------|--------------|
| `impacts.impact_claim_recorded` | `impact_claim` | `hazard_event_id`, `metric_code`, `value`, `confidence`, `source_id`, `source_type`, `claimed_at`, `scope`, `recorded_by` | A claim is recorded |
| `impacts.impact_claim_retracted` | `impact_claim` | `hazard_event_id`, `metric_code`, `retracted_by`, `superseded_by_id` (set when part of a correction) | A claim is retracted |
| `impacts.impact_claim_corrected` | `impact_claim` (the new claim) | `hazard_event_id`, `metric_code`, `supersedes_id`, `value`, `confidence`, `claimed_at`, `recorded_by` | A correcting claim is recorded |
| `impacts.infrastructure_asset_registered` | `infrastructure_asset` | `kind`, `osm_id`, `place_code`, `source_id` | An asset is registered |
| `impacts.infrastructure_asset_renamed` | `infrastructure_asset` | none | An asset's name changes |
| `impacts.infrastructure_asset_relocated` | `infrastructure_asset` | `location`, `place_code`, `osm_id` | An asset's location changes |
| `impacts.damage_recorded` | `damage_record` | `hazard_event_id`, `asset_id`, `level`, `confidence`, `source_id`, `recorded_at`, `recorded_by` | Damage is recorded |
| `impacts.damage_retracted` | `damage_record` | `hazard_event_id`, `asset_id`, `retracted_by` | A damage record is retracted |

## Persistence (Phase 3: claims, assets, damage)

`infrastructure_assets`, `impact_claims` and `damage_records` (migration
`0014_impact_claims`) add referential integrity the domain layer cannot express
(a domain module may not import another module's ORM, `AGENTS.md` §2.1), but
that the database can enforce within `impacts`' own tables:

- **`impact_claims.metric_code` references `impact_metrics.code`**
  (`ON DELETE RESTRICT`), not the metric's `id`: `code` is the stable public key
  claims are recorded against (`ImpactMetricRef`), so the foreign key follows the
  same key the domain treats as identity. A metric can never be deleted while any
  claim cites it, matching "a metric code is never reused" and "old claims still
  need a best figure" even for a retired metric.
- **`impact_claims.scope_asset_id` references `infrastructure_assets.id`**
  (`ON DELETE RESTRICT`, nullable — a claim's scope may be a place code instead, or
  the whole event). `damage_records.asset_id` references the same table and is
  required (`ON DELETE RESTRICT`).
- **`UNIQUE (supersedes_id)` on `impact_claims`** enforces "at most one
  correction replaces a claim" at the database level, the same rule
  `reports.supersedes_id` enforces for report revisions; `supersedes_id` also
  references `impact_claims.id` (`ON DELETE RESTRICT`).
- **`infrastructure_assets.osm_id` is unique** (nullable), and its `location`
  has an explicit GiST index.
- Event, source, place and user ids on all three tables carry no foreign key,
  because they belong to other modules. Indexes: `impact_claims` on
  `(event_id, metric_code)`, `(event_id, created_at, id)` and `source_id`;
  `damage_records` on `event_id` and `asset_id`.

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
- **Asset kinds and damage levels.** The six asset kinds and the three damage levels
  (`damaged`, `destroyed`, `washed_away`) and their meanings. Default: as listed.
- **Monetary precision and price years.** Two decimal places, price years 1900 to 2100,
  `price_year` not after the claim's year. Default: as listed.
- **Source ranking for `satellite` and `dataset`.** Default: between research and
  organisation. See `docs/architecture/best-figure.md` for the other best-figure
  questions.
