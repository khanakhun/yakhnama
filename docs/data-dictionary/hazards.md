# Data dictionary — hazards

The `hazards` module owns the hazard taxonomy (IRDR-aligned hazard types with stable
codes), the hazard-specific attribute schemas that events of each type may carry, and
references to glaciers and glacial lakes. Conventions (column meanings, UTC, SI units,
UUIDv7) are in [README.md](README.md).

**Provenance legend.** `cited` means the value follows a named source. `proposed` means
no source has been confirmed yet: the value is a minimal default chosen so a wrong guess
is cheap to change before Phase 3 stores real events, and an open question at the end of
this page tracks it. **No attribute field below is cited yet** (phase plan question Q2).

Code: `src/yakhnama/modules/hazards/domain/`.

## Hazard type (`HazardType`, aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | UUID (v7) | — | Surrogate identifier of the hazard type. | generated (`IdGenerator`) | Phase 1 |
| `code` | str, `^[a-z][a-z0-9_]{1,63}$` | — | Stable machine code, for example `glof`. Immutable; never deleted and never reused, even after retirement. | reference data (`data/reference/hazard_types.yaml`) | Phase 1 |
| `parent_code` | str (hazard code) or null | — | Code of the broader hazard type; null for a root. Must exist in the taxonomy; parent links never form a cycle; never the type's own code. | reference data | Phase 1 |
| `labels` | `LocalizedText` | — | Display labels per language code (BCP 47 subset). English at least; other languages only with a cited source (open question Q2 in `docs/open-questions.md`). | reference data | Phase 1 |
| `description` | `LocalizedText` or null | — | Optional longer explanation per language. | reference data | Phase 1 |
| `alignment.family` | enum `geophysical \| hydrological \| meteorological \| climatological \| extraterrestrial \| biological` | — | IRDR family the type belongs to. | cited: the six families of the IRDR Peril Classification (IRDR DATA Publication No. 1, 2014); the placement of each type is proposed | Phase 1 |
| `alignment.main_event` | str, 1–64 | — | IRDR main event, written as in the publication (for example `Flood`). | proposed per type (see open questions) | Phase 1 |
| `alignment.peril` | str, 1–64, or null | — | IRDR peril under the main event; null when the type maps to the main event as a whole. | proposed per type | Phase 1 |
| `attributes_schema` | str (registry code) or null | — | Code of the attribute schema events of this type carry (see below); null for abstract parents. Must be registered in the attribute registry when the type is created. | reference data | Phase 1 |
| `status` | enum `active \| retired` | — | Whether new events may still be classified with this type. Retired types stay resolvable so historical events keep their meaning. | moderator/maintainer decision | Phase 1 |
| `retirement.text` | str, 1–500 | — | Why the type was retired. Set exactly when `status` is `retired`. | moderator/maintainer decision | Phase 1 |
| `retirement.replaced_by` | str (hazard code) or null | — | Code to use instead; never the retired code itself, and must exist in the taxonomy. | moderator/maintainer decision | Phase 1 |
| `version` | int ≥ 1 | — | Starts at 1 and grows by one with every change (retire, reactivate, relabel, reparent). | computed | Phase 1 |
| `created_at` | datetime (UTC) | — | When the type was created. Exact instant from the injected clock, so no `DatePrecision` is stored. | computed (`Clock`) | Phase 1 |
| `updated_at` | datetime (UTC) | — | When the type last changed; never before `created_at`. Exact instant. | computed (`Clock`) | Phase 1 |

### Lifecycle rules

| operation | allowed from | effect | event |
|-----------|--------------|--------|-------|
| create | — | new active type at version 1; code must never have been used, parent must exist and be active (proposed rule), schema must be registered | `hazards.hazard_type_created` |
| retire | active | status `retired` with a reason and optional replacement | `hazards.hazard_type_retired` |
| reactivate | retired | status `active`, retirement cleared; needs a reason (proposed rule) | `hazards.hazard_type_reactivated` |
| relabel | active or retired | labels replaced; identical labels are a no-op without an event | `hazards.hazard_type_relabelled` |
| reparent | active | parent replaced; same parent is a no-op; cycles and unknown parents are rejected by `HazardTaxonomy.with_hazard_type` | `hazards.hazard_type_reparented` |

There is no delete operation.

## Hazard taxonomy (`HazardTaxonomy`)

An immutable set of every hazard type ever defined, active or retired, sorted by code.
Construction rejects duplicate codes, duplicate ids, parents or replacements that do not
exist and parent cycles (`InvalidTaxonomyError`). It answers `roots()`, `children(code)`,
`ancestors(code)` (nearest first) and `resolve(ref)`, which also returns retired types.

## Value objects

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `HazardTypeRef.code` | str (hazard code) | — | Reference other records store to point at a hazard type. | — | Phase 1 |
| `GlacierRef.glims_id` | str, `^G\d{6}E\d{5}[NS]$` | — | GLIMS glacier id, for example `G074567E36234N`. | proposed pattern after the GLIMS id convention (longitude east ×1000, latitude ×1000, hemisphere); to confirm | Phase 1 |
| `GlacialLakeRef.inventory` | enum `icimod \| glims \| other` | — | Which inventory the lake id belongs to; `other` requires a source on the record using it. | proposed | Phase 1 |
| `GlacialLakeRef.inventory_id` | str, 1–64 | — | Lake id in that inventory, stored verbatim (whitespace stripped). | the named inventory | Phase 1 |

## Attribute schemas (Strategy + Registry)

Each schema is a frozen model with a `hazard_type` discriminator equal to its registry
code. Every field is optional; enumerations default to `unknown`; unknown fields are
rejected; every quantity is a non-negative `Measurement` in the unit shown (a
measurement in another unit, or in a unit outside the kernel registry, is rejected).
Payloads are validated by `DEFAULT_REGISTRY.validate(code, payload)`; the union is
`HazardAttributesUnion`. Numbers here inherit the provenance and `Confidence` of the
report, event or claim that carries them (open question QH-8).

### `glof` — `GlofAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"glof"` | — | Discriminator. | — | Phase 1 |
| `source_lake` | `GlacialLakeRef` or null | — | The lake that drained. | proposed | Phase 1 |
| `source_glacier` | `GlacierRef` or null | — | The glacier damming or feeding the lake. | proposed | Phase 1 |
| `mechanism` | enum `moraine_dam_breach \| ice_dam_breach \| englacial_drainage \| overtopping \| unknown` | — | How the lake released its water. | proposed: common release mechanisms in GLOF literature; list to confirm (QH-3) | Phase 1 |
| `peak_discharge` | `Measurement` (`cubic_metre_per_second`) | m^3/s | Highest flow rate of the flood. | proposed | Phase 1 |
| `flood_volume` | `Measurement` | m^3 | Total water volume released. | proposed | Phase 1 |
| `lake_area_before` | `Measurement` | m^2 | Lake surface area before the outburst. | proposed | Phase 1 |
| `lake_area_after` | `Measurement` | m^2 | Lake surface area after the outburst; may be 0. | proposed | Phase 1 |

### `landslide` — `LandslideAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"landslide"` | — | Discriminator. | — | Phase 1 |
| `movement_type` | enum `fall \| topple \| slide \| flow \| spread \| complex \| unknown` | — | Type of movement. | proposed: movement types after the Varnes (1978) / Hungr et al. (2014) classification; subset to confirm (QH-3) | Phase 1 |
| `material` | enum `rock \| debris \| earth \| unknown` | — | Main material moved. | proposed, after Varnes (1978) material classes; to confirm | Phase 1 |
| `volume` | `Measurement` | m^3 | Displaced volume. | proposed | Phase 1 |
| `runout_length` | `Measurement` | m | Distance travelled by the moved mass. | proposed | Phase 1 |
| `trigger` | enum `rainfall \| earthquake \| snowmelt \| human \| unknown` | — | What set the landslide off. | proposed | Phase 1 |

### `debris_flow` — `DebrisFlowAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"debris_flow"` | — | Discriminator. | — | Phase 1 |
| `volume` | `Measurement` | m^3 | Deposited volume. | proposed | Phase 1 |
| `runout_length` | `Measurement` | m | Distance travelled by the flow. | proposed | Phase 1 |
| `trigger` | enum `rainfall \| glof \| snowmelt \| unknown` | — | What set the flow off. | proposed | Phase 1 |
| `channel_blocked` | bool or null | — | Whether the flow blocked a river or stream channel; null when unknown. | proposed | Phase 1 |

### `cloudburst` — `CloudburstAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"cloudburst"` | — | Discriminator. | — | Phase 1 |
| `rainfall_total` | `Measurement` | m | Accumulated rainfall depth over the episode (100 mm is stored as 0.1 m). | proposed | Phase 1 |
| `duration` | `Measurement` | s | Length of the episode. | proposed | Phase 1 |
| `peak_intensity` | `Measurement` (`metre_per_second`) | m/s | Highest rainfall rate, as depth of water per time (100 mm/h is stored as about 2.78e-5 m/s). Distinct from the mean rate `rainfall_total / duration`, so it is kept as its own field. | proposed; no intensity threshold is defined for "cloudburst" (QH-2) | Phase 1 |

### `flash_flood` — `FlashFloodAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"flash_flood"` | — | Discriminator. | — | Phase 1 |
| `peak_discharge` | `Measurement` (`cubic_metre_per_second`) | m^3/s | Highest flow rate. | proposed | Phase 1 |
| `trigger` | enum `cloudburst \| glof \| snowmelt \| dam_failure \| unknown` | — | What caused the flood. | proposed | Phase 1 |

### `avalanche` — `AvalancheAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"avalanche"` | — | Discriminator. | — | Phase 1 |
| `avalanche_type` | enum `slab \| loose_snow \| wet \| ice \| unknown` | — | Kind of avalanche. Named `avalanche_type`, not `type`, to avoid clashing with the builtin and the discriminator. | proposed | Phase 1 |
| `size_class` | int 1–5 or null | — | Destructive size class. | proposed: the international avalanche size scale 1–5 (EAWS / Canadian Avalanche Association), whole classes only (QH-4) | Phase 1 |
| `trigger` | enum `natural \| human \| unknown` | — | Whether the release was natural or human-triggered. | proposed | Phase 1 |

### `glacier_surge` — `GlacierSurgeAttributes`

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `hazard_type` | `"glacier_surge"` | — | Discriminator. | — | Phase 1 |
| `glacier` | `GlacierRef` or null | — | The surging glacier. | proposed | Phase 1 |
| `advance_distance` | `Measurement` | m | How far the terminus advanced. | proposed | Phase 1 |
| `surge_start` | `DateWithPrecision` or null | — | When the surge began (UTC instant plus precision, often `month` or `season`). | proposed | Phase 1 |
| `surge_end` | `DateWithPrecision` or null | — | When the surge ended; never before `surge_start`. | proposed | Phase 1 |
| `river_blocked` | bool or null | — | Whether the advancing ice blocked a river; null when unknown. | proposed | Phase 1 |

## Domain events

All carry `event_id`, `occurred_at` (UTC), `aggregate_id`, `aggregate_type = "hazard_type"`
and `code`.

| event_type | extra fields | meaning |
|------------|--------------|---------|
| `hazards.hazard_type_created` | `parent_code`, `attributes_schema` | A type was added to the taxonomy. |
| `hazards.hazard_type_retired` | `reason`, `replaced_by` | A type stopped accepting new classifications; its code stays reserved. |
| `hazards.hazard_type_reactivated` | `reason` | A retired type was made active again. |
| `hazards.hazard_type_relabelled` | `labels` | The display labels changed. |
| `hazards.hazard_type_reparented` | `previous_parent_code`, `parent_code` | The type moved in the tree. |

## Reference file (`HazardTypeReferenceFile`)

Validates `data/reference/hazard_types.yaml` (task T8).

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `schema_version` | int, only `1` | — | Version of the file structure. | — | Phase 1 |
| `data_version` | str, 1–32 | — | Version of the content, bumped on every change. | maintainers | Phase 1 |
| `source` | str, 1–500 | — | Where the taxonomy as a whole comes from. | maintainers | Phase 1 |
| `licence` | str, 1–100 | — | Licence of the file content. | maintainers (open question Q4) | Phase 1 |
| `entries[].code` | hazard code | — | Unique within the file. | — | Phase 1 |
| `entries[].parent` | hazard code or null | — | Must be another entry's code; no cycles. | — | Phase 1 |
| `entries[].labels` | map language code → str | — | `en` required. | — | Phase 1 |
| `entries[].description` | map language code → str, or null | — | Optional. | — | Phase 1 |
| `entries[].alignment` | `IrdrAlignment` | — | As above. | — | Phase 1 |
| `entries[].attributes_schema` | registry code or null | — | As above. | — | Phase 1 |
| `entries[].status` | `active \| retired` | — | Defaults to `active`. | — | Phase 1 |
| `entries[].retirement` | `RetirementReason` or null | — | Required exactly when retired; `replaced_by` must be another entry's code. | — | Phase 1 |
| `entries[].source` | str, 1–500 | — | Citation for the entry, or `proposed`. | — | Phase 1 |
| `entries[].notes` | str, 1–2000, or null | — | Remarks for reviewers, for example the open question. | — | Phase 1 |

## JSONB storage

The `hazard_types` table stores the structured value objects as JSONB columns: `labels`
and `description` (`LocalizedText`), `alignment` (`IrdrAlignment`) and `retirement`
(`RetirementReason`, null unless retired). Each column holds exactly the JSON form of
its value object (`model_dump(mode="json")`), with the same fields and meanings as the
tables above. The mapper validates the column back into the value object on every
read, so a malformed value fails loudly and never reaches the domain. Nothing but the
repository writes these columns (`src/yakhnama/modules/hazards/infrastructure/orm.py`).

## Open questions

These are written in the `docs/open-questions.md` format with provisional `QH-n` numbers;
`docs-writer` assigns the final numbers in task T13.

### QH-1 — IRDR placement of each local hazard type

**Question:** Where does each hazard type sit in the IRDR 2014 peril classification?
Candidates to check against the publication: `glof` → climatological / Glacial lake
outburst; `flash_flood` → hydrological / Flood / Flash flood; `landslide` and
`debris_flow` → hydrological / Landslide (or geophysical / Mass movement (dry) for dry
rockfalls); `avalanche` → hydrological / Landslide / Avalanche; `cloudburst` →
meteorological / Convective storm (peril name to confirm); `glacier_surge` → no obvious
IRDR peril.

**Why it matters:** A wrong placement mis-files every event in IRDR- or EM-DAT-aligned
exports, and codes cannot be reused once published.

**Proposed default:** The candidates above, each marked `proposed` in the reference file;
`glacier_surge` placed under climatological with the placement flagged.

**Blocking:** no

**Status:** open

### QH-2 — Definition of "cloudburst"

**Question:** Is `cloudburst` a hazard type of its own or a peril of extreme rainfall, and
is there an intensity or duration threshold (for example from PMD) that defines it?

**Why it matters:** Without a definition, reporters and moderators will classify the same
rainfall differently.

**Proposed default:** Own code `cloudburst` without a threshold; intensity recorded as
reported.

**Blocking:** no

**Status:** open

### QH-3 — Attribute fields and enumerations for every schema

**Question:** Which fields and enumeration values should each attribute schema carry (GLOF
mechanism list, landslide movement type and material after Varnes/Hungr, trigger lists)?
Is there an ICIMOD or NDMA reporting format to align to?

**Why it matters:** These fields become the stored and exported structure of every event.

**Proposed default:** The minimal proposed fields on this page.

**Blocking:** no

**Status:** open

### QH-4 — Avalanche size scale

**Question:** Should `size_class` follow the EAWS / Canadian 1–5 destructive size scale,
and are half classes (for example 2.5) needed?

**Why it matters:** Whole classes cannot store half-class observations later without a
type change.

**Proposed default:** Whole classes 1–5.

**Blocking:** no

**Status:** open

### QH-5 — GLIMS id pattern

**Question:** Is `^G\d{6}E\d{5}[NS]$` the exact GLIMS glacier id format, and does it hold
for every glacier in the region?

**Why it matters:** A too-strict pattern rejects valid ids; a too-loose one accepts typos.

**Proposed default:** The pattern above, widened only with a cited source.

**Blocking:** no

**Status:** open

### QH-6 — Compound SI units in the shared kernel

**Question:** Should the shared kernel register `cubic_metre_per_second` and
`metre_per_second` so discharge and rainfall intensity become a single `Measurement`?

**Why it matters:** `MeasuredRate` (numerator and denominator) is a stand-in; changing it
later changes the stored JSON of `peak_discharge` and `peak_intensity`.

**Proposed default:** Add both units to `SiUnit` before Phase 3 stores events, then replace
`MeasuredRate` in the schemas.

**Blocking:** no

**Status:** answered (Phase 1, 2026-09-23): the kernel registers `cubic_metre_per_second`
and `metre_per_second`; `MeasuredRate` is removed and `peak_discharge` / `peak_intensity`
are single `Measurement` values in those units.

### QH-7 — Reactivation and retired parents

**Question:** May a retired hazard type be reactivated, and may new types be created under
a retired parent?

**Why it matters:** Decides whether retirement is reversible and how retired branches
behave.

**Proposed default:** Reactivation allowed with a reason and an event; new children under
a retired parent rejected; existing children of a retired parent keep their status.

**Blocking:** no

**Status:** open

### QH-8 — Provenance and confidence of attribute numbers

**Question:** Do attribute numbers (peak discharge, volume, areas) need their own source
and `Confidence`, or do they inherit them from the report, event or claim that carries
them?

**Why it matters:** `AGENTS.md` requires every uncertain number to carry a confidence and
a source.

**Proposed default:** Inherit from the carrying record; revisit when events are modelled
in Phase 3.

**Blocking:** no

**Status:** open
