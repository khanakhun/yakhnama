# Data dictionary — events

The `events` module owns the canonical `Event` record of one real-world hazard
occurrence, created by moderators from reports, its links to those reports, the places
it concerns, and the relations between events. Conventions (column meanings, UTC, SI
units, UUIDv7) are in [README.md](README.md).

**Provenance legend.** `proposed` means the rule is a default chosen by the domain
modeller, not a confirmed domain fact; each is listed under "Open questions" below.

Code: `src/yakhnama/modules/events/domain/`.

## Event (`Event`, aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | UUID (v7) | — | Identifier of the event. | generated (`IdGenerator`) | Phase 3 |
| `hazard_type.code` | str (hazard code) | — | Hazard type the event is classified as (`hazards` taxonomy). | moderator decision | Phase 3 |
| `title` | safe text, 3–200, single line | — | Short name of the event. | moderator | Phase 3 |
| `summary` | safe text, 1–2000, line breaks allowed, or null | — | Moderator's summary. | moderator | Phase 3 |
| `period.started_at` | `DateWithPrecision` | — | When the event started, UTC, with its precision. | derived from reports, then moderator | Phase 3 |
| `period.ended_at` | `DateWithPrecision` or null | — | When it ended; null when the end is unknown or the event was a single moment. The end's precision period must not finish before the truncated start (proposed rule, see below). | derived from reports, then moderator | Phase 3 |
| `geometry` | GeoJSON `Point`, `Polygon` or `MultiPolygon`, WGS84 (EPSG:4326), 2-D, ≤ 1 000 000 positions, or null | degree | Where the event happened: a located point or a mapped extent. | moderator | Phase 3 |
| `centroid` | `Coordinates` or null | degree | Representative point. Equals `geometry.centroid()` when a geometry is set; otherwise the mean of the linked reports' **public (rounded)** points. | computed | Phase 3 |
| `affected_places[].place_code` | str (place code) | — | A gazetteer place the event concerns; existence checked through the `geography` facade. | moderator | Phase 3 |
| `affected_places[].kind` | enum `origin \| impacted \| reference` | — | Where it started, where it did harm, or a named reference point used to locate it. No duplicate (code, kind) pairs. | proposed | Phase 3 |
| `attributes` | one schema of `HazardAttributesUnion` (from `yakhnama.modules.hazards.public`), discriminated by `hazard_type`; JSONB in storage; or null | SI units per field | Hazard-specific attributes. The `hazard_type` discriminator must equal `hazard_type.code` (`AttributesMismatchError` from `set_attributes` and the factory; a validation error on construction). A stored mapping reloads into its concrete schema class. Field meanings in [hazards.md](hazards.md). | moderator, validated against the hazards attribute schemas | Phase 3 |
| `source_ids` | tuple of UUID (v7), ≥ 1, no duplicates | — | Every `Source` the event cites. Never shrinks: unlinking a report keeps its source. | derived from reports | Phase 3 |
| `report_links[]` | `ReportLink` | — | The reports currently linked; one link per report. | moderator | Phase 3 |
| `unlinked_reports[]` | `ReportUnlink` | — | Every link removed so far, with who, when, why; append-only. | moderator | Phase 3 |
| `status` | enum `draft \| published \| retracted \| merged` | — | Editorial status (proposed set). Not the verification state, which lives in `verification` and is mirrored on the read model. | moderator | Phase 3 |
| `status_reason` | safe text, 1–1000, or null | — | Why the event was retracted or merged; set exactly then. | moderator | Phase 3 |
| `merged_into` | UUID (v7) or null | — | The surviving event; set exactly when `status` is `merged`; never the event's own id. | moderator | Phase 3 |
| `created_by` | UUID (v7) | — | Moderator who created the event. | computed | Phase 3 |
| `version` | int ≥ 1 | — | Starts at 1, +1 per change. | computed | Phase 3 |
| `created_at`, `updated_at` | datetime (UTC) | — | Creation and last change; exact instants from the clock, `updated_at ≥ created_at`. | computed (`Clock`) | Phase 3 |

### Lifecycle rules

| operation | allowed from | effect | event |
|-----------|--------------|--------|-------|
| create (`EventFactory.from_reports`) | — | draft at version 1 (see "Factory derivations") | `events.event_created` |
| `link_report` | draft, published | adds a link; cites the report's source if new; a report links once | `events.report_linked_to_event` |
| `unlink_report` (reason) | draft, published | removes the link, appends a `ReportUnlink`; sources kept | `events.report_unlinked_from_event` |
| `add_affected_place` | draft, published | adds a place; an existing one is a no-op | `events.affected_place_added` |
| `set_geometry` | draft, published | sets, replaces or removes; centroid follows a new geometry, kept on removal; equal geometry is a no-op | `events.event_geometry_changed` |
| `set_period` | draft, published | replaces the period; equal is a no-op | `events.event_period_changed` |
| `set_attributes` | draft, published | sets, replaces or clears; must match the hazard type; equal is a no-op | `events.event_attributes_changed` |
| `publish` | draft | status `published`; verification requirements are checked by the application | `events.event_published` |
| `retract` (reason) | draft, published | status `retracted` with a reason | `events.event_retracted` |
| `merge_into` (target, reason) | draft, published | status `merged`, points at the target | `events.event_merged` |

A retracted or merged event rejects every operation (`EventImmutableError`). There is no
delete operation.

### Period rule (proposed)

`EventPeriod` is valid when `latest_instant_of(ended_at) ≥ truncate(started_at)`, where
`truncate` floors an instant to the start of its precision period and
`latest_instant_of` returns the last microsecond of that period (the instant itself for
`exact`). This accepts everything that "truncated end ≥ truncated start" accepts, plus
ends whose coarser period still contains later instants: a start of 2024-07-15 (day)
with an end of 2024-07 (month) is valid. For search, an event spans
`[truncate(started_at), latest_instant_of(ended_at or started_at)]`, so an unknown end
limits the event to its start period.

### Centroid approximation (proposed)

For polygons and multipolygons, `EventGeometry.centroid()` is the arithmetic mean, in
degrees, of every exterior-ring vertex (closing vertex counted once, holes ignored),
clamped to the bounding box of those vertices. It is not the area centroid and can fall
outside a concave shape; it is computed without Shapely so the domain stays
framework-free. Not valid across the antimeridian, like the kernel's `BoundingBox`.

### Factory derivations (`EventFactory.from_reports`, proposed)

| derived field | rule |
|---------------|------|
| `period.started_at` | earliest `observed_at` value, at the coarsest precision among the reports |
| `period.ended_at` | latest `observed_at` value at the same precision; null if every report has the same instant |
| `centroid` | mean of the reports' public points; `geometry` stays null |
| `report_links` | every report, role `primary`, linked by the creator at creation time |
| `source_ids` | each report's source once, in report order |
| `attributes` | optional input of the moderator; must match the hazard type, otherwise `AttributesMismatchError` |

1 to 100 distinct reports per call (100 is a technical cap on the `EventCreated`
payload, not a domain fact); further reports are linked afterwards.

## Value objects

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `ReportLink.report_id` | UUID (v7) | — | The linked report. | moderator | Phase 3 |
| `ReportLink.linked_by` | UUID (v7) | — | Who linked it. | computed | Phase 3 |
| `ReportLink.linked_at` | datetime (UTC) | — | When; exact instant. | computed (`Clock`) | Phase 3 |
| `ReportLink.role` | enum `primary \| supporting \| contradicting` | — | A direct observation the event rests on, one that adds detail, or one that contradicts the record. | proposed | Phase 3 |
| `ReportUnlink.*` | `report_id`, `role`, `unlinked_by`, `unlinked_at` (UTC), `reason` (safe text 1–1000) | — | Record of a removed link. | moderator | Phase 3 |
| `ReportForEvent` | `id`, `observed_at` (`DateWithPrecision`), `coordinates` (public, rounded), `source_id` | degree | What the domain needs from a report; mapped by the application from the `reports` facade. Never the reporter's exact GPS position. | application | Phase 3 |
| `EventRelation.from_event_id`, `to_event_id` | UUID (v7), different | — | The two related events. | moderator | Phase 3 |
| `EventRelation.kind` | enum `triggered_by \| part_of \| same_as` | — | `from` was caused by `to`; `from` is a component of `to`; both describe the same occurrence (symmetric). | proposed | Phase 3 |
| `EventRelation.note` | safe text, 1–500, or null | — | Why the events are related. | moderator | Phase 3 |
| `EventRelation.related_by`, `related_at` | UUID (v7); datetime (UTC) | — | Who recorded the relation, and when. | computed | Phase 3 |

## Event graph (`EventGraph`)

An immutable set of relations. Rules: no relation twice (`same_as` compared without
direction) and no cycle of `part_of` relations. `triggered_by` cycles are not rejected
(open question). `same_as_group(id)` returns the connected component over `same_as`.
`relate` adds a relation and emits `events.events_related` on `from_event_id`.

## Search candidate (`EventSearchCandidate`) and specifications

The flat read-model view search filters are evaluated against: `id`, `centroid`,
`hazard_code`, `place_codes`, `status`, `period`, `verification_state` (the mirrored
`VerificationState` value, for example `verified`, or null without a case).

| specification | satisfied when |
|---------------|----------------|
| `EventBboxSpecification(bbox)` | the centroid lies inside the box, edges included; no centroid never matches (proposed: centroid, not geometry) |
| `EventHazardTypeSpecification(code)` | `hazard_code == code` exactly; narrower types not included |
| `EventPlaceSpecification(place_code)` | `place_code` is among the place codes, any kind; child places not included |
| `EventStatusSpecification(status)` | `status` equals |
| `EventPeriodOverlapsSpecification(start, end)` | the event's span meets the closed window; either bound may be null; bounds must be timezone-aware and `start ≤ end` |
| `VerifiedEventSpecification()` | `verification_state == "verified"` |

## Domain events

Every event has `aggregate_type = "event"` and `actor_id`. Payloads carry ids, codes,
statuses and geometry summaries only; reason, note, title and summary texts never enter
the outbox.

| `event_type` | payload beyond the base fields |
|--------------|--------------------------------|
| `events.event_created` | `hazard_code`, `report_ids`, `source_ids` |
| `events.report_linked_to_event` | `report_id`, `role` |
| `events.report_unlinked_from_event` | `report_id` |
| `events.affected_place_added` | `place_code`, `kind` |
| `events.event_geometry_changed` | `geometry_type` or null, `centroid` or null |
| `events.event_period_changed` | `started_at`, `ended_at` |
| `events.event_attributes_changed` | `hazard_code`, `has_attributes` |
| `events.event_published` | — |
| `events.event_retracted` | — |
| `events.event_merged` | `target_event_id` |
| `events.events_related` | `to_event_id`, `kind` (`aggregate_id` is the relation's start) |

## Persistence

`events`, `event_relations` and `event_report_links` (migration `0012_events`)
add mapper-derived and projection detail beyond the domain model:

- **`period_earliest_at` / `period_latest_at`.** Two extra, `NOT NULL` columns
  derived by the mapper from `period.started_at` and `period.ended_at` (`ended_at`
  falling back to `started_at` when unset), checked `period_earliest_at <=
  period_latest_at`. They exist purely to make search fast: `EventPeriodOverlapsSpecification`
  compiles to a range comparison against these two columns, and
  `ix_events_period_earliest_at_id` serves the keyset listing, instead of
  recomputing the domain's precision-aware period rule in SQL on every query.
  `geometry` and `centroid` each get their own explicit GiST index, and
  `affected_places` a GIN index for place search.
- **`event_report_links`.** A narrow table, keyed by `(event_id, report_id)`, that
  projects only the *current* report links (not `unlinked_reports`, which stays
  inside the `events` row's JSONB, append-only) for fast lookup "which events
  link this report". `ON DELETE CASCADE` on `event_id` (unlike every other
  foreign key into `events`, which is `RESTRICT`), because the projection is
  disposable and is rebuilt from the aggregate, not a source of truth itself.
- **Key-derived relation ids.** `event_relations.id` and every other id in these
  three tables are the platform's UUIDv7s, generated the same way as every other
  aggregate id (not a composite or hash-derived key); "key-derived" here refers
  to the **uniqueness** key, `UNIQUE (from_event_id, to_event_id, kind)`, which
  is what actually enforces the domain's "no relation twice" rule at the
  database level, alongside a check that the two ends differ.

`merged_into` references `events.id` (`ON DELETE RESTRICT`; events are never
deleted). `hazard_code` and `status` are indexed for filtering.

## Open questions

- **Event status set.** `draft | published | retracted | merged`, with `retracted` and
  `merged` final. Default: as proposed.
- **Centroid approximation.** Vertex mean of exterior rings. Default: as proposed;
  infrastructure may store PostGIS `ST_PointOnSurface` beside it if needed.
- **Period rule.** The precision-aware order check above differs from the plan's
  wording ("truncated end ≥ truncated start"), which would reject a start of
  2024-07-15 (day) with an end of 2024-07 (month). Default: precision-aware.
- **Unknown end in search.** An event without `ended_at` is treated as lasting only for
  its start period. Default: as proposed.
- **Link roles.** `primary | supporting | contradicting`. Default: as proposed.
- **Affected-place kinds.** `origin | impacted | reference`. Default: as proposed.
- **Bounding-box search on centroid.** Extents that only overlap the box do not match.
  Default: centroid.
- **`triggered_by` cycles.** Not rejected (only `part_of` cycles are). Default: allowed.
- **Factory precision.** The combined period takes the coarsest report precision.
  Default: as proposed.
- **Publishing requires verification?** The aggregate checks only `draft`; whether
  `publish` needs a `verified` case is left to the application. Default: public reads
  are verified-only (phase plan), publication itself not gated.
