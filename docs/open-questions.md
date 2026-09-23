# Open questions

This log holds domain and product decisions that are not yet settled, so that nobody has
to invent a fact to keep working (`AGENTS.md` §5: "Never invent domain facts"). When a
change depends on something unsettled, add an entry here instead of guessing, and reference
it from the code or documentation that depends on it.

Each entry has:

- **Question** — the decision that needs to be made.
- **Why it matters** — what depends on it, or breaks without it.
- **Proposed default** — what we build against until the maintainer decides, if a default
  is safe to assume; marked non-blocking unless stated otherwise.
- **Blocking** — whether work is stopped until this is answered (`yes`) or can proceed on
  the proposed default (`no`).
- **Status** — `open`, `answered` (with the answer and date), or `superseded`.

---

## Q1 — Administrative boundary data source

**Question:** Which administrative boundary dataset do we use for Place records (country →
province/region → district → tehsil → union council → village)?

**Why it matters:** Every `Place` in the system links to a boundary and hierarchy; changing
source later means re-linking every place.

**Proposed default:** OCHA COD-AB (Common Operational Datasets, administrative boundaries)
for Pakistan, via HDX (Humanitarian Data Exchange).

**Blocking:** no

**Status:** open

---

## Q2 — Final hazard taxonomy labels and local-language names

**Question:** What are the final hazard type labels (IRDR-aligned) and their names in
Urdu, Shina, Burushaski, Balti, Wakhi and Khowar?

**Why it matters:** `Hazard type` codes are retired, never reused or deleted once
published, so the taxonomy needs to be right before it ships broadly.

**Proposed default:** None; hazard types are added one at a time via the `add-hazard-type`
skill as they are confirmed, English label only until local names are supplied.

**Blocking:** no

**Status:** open

---

## Q3 — Public coordinate rounding precision

**Question:** How many decimal places of latitude/longitude are shown on public endpoints
and exports, to protect reporter locations?

**Why it matters:** Too much precision can reveal a reporter's home or exact vantage point;
too little makes the dataset less useful for hazard mapping.

**Proposed default:** 2 decimal places (~1.1 km at the equator). Configurable via
`Settings.public_coordinate_decimals`.

**Blocking:** no

**Status:** open

---

## Q4 — Data licence

**Question:** What licence covers the published dataset (as opposed to the code)?

**Why it matters:** Determines who can reuse the data and how they must attribute it;
affects the licence sidecar shipped with every export.

**Proposed default:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), over
alternatives such as ODbL.

**Blocking:** no

**Status:** <!-- maintainer decision pending --> open, pending final maintainer confirmation

---

## Q5 — Production identity provider and hosting target

**Question:** Does production run on Keycloak (as used locally via `poe up`) or Zitadel,
and where is it hosted?

**Why it matters:** `platform/auth` is built against OIDC/JWKS either way, but operational
details (token lifetime defaults, admin bootstrap, hosting cost) differ.

**Proposed default:** Keycloak, matching local development; hosting target undecided.

**Blocking:** no

**Status:** open

---

## Q6 — Code licence final confirmation

**Question:** Is Apache-2.0 the final code licence?

**Why it matters:** `LICENSE` and `pyproject.toml` already declare Apache-2.0; this entry
tracks that it is a recommendation pending explicit maintainer sign-off, not yet a closed
decision.

**Proposed default:** Apache-2.0, recommended over MIT for its explicit patent grant and
NOTICE-file handling, both of which suit an infrastructure project that expects
institutional contributors.

**Blocking:** no

**Status:** <!-- maintainer decision pending --> open, pending final maintainer confirmation

---

## Q7 — Authoritative source for the glacier and glacial-lake counts

**Question:** What is the authoritative, citable source (for example the ICIMOD glacier
inventory or a GLIMS extract, with year) for the "more than 13,000 glaciers and thousands
of glacial lakes" figures used in the project's mission statement?

**Why it matters:** These figures currently come from the maintainer's project brief, not a
cited inventory; an uncited count in the public README affects the credibility of the
project's public record.

**Proposed default:** Cite the ICIMOD glacier inventory once the specific edition and year
are confirmed.

**Blocking:** no

**Status:** open

---

## Q8 — Licensing of third-party data mixed into exports

**Question:** How is licensing handled for exports that combine Yakhnama's own CC BY 4.0
data with ingested third-party data under different terms (for example OpenStreetMap data
under ODbL, or boundary datasets with their own terms)?

**Why it matters:** Yakhnama cannot relicense third-party content under CC BY 4.0; without a
rule, some export combinations may be legally impossible to publish under a single stated
licence. This blocks ADR 0010 moving from `proposed` to `accepted` (see ADR 0010,
Consequences).

**Proposed default:** Keep the per-source licence attached to each record or layer, and list
every distinct licence present in the export's metadata sidecar, rather than collapsing them
into one licence statement.

**Blocking:** yes, for accepting ADR 0010

**Status:** open

---

## Q9 — Contributor sign-off and `NOTICE` copyright line

**Question:** Do contributions require a Developer Certificate of Origin (DCO) sign-off or a
contributor licence agreement, and what copyright-holder line goes in the `NOTICE` file?

**Why it matters:** ADR 0010 lists this as one of the items the maintainer must decide before
the licensing ADR can be accepted.

**Proposed default:** DCO sign-off (`Signed-off-by:` trailer) on every commit, and a `NOTICE`
copyright line reading "Yakhnama contributors".

**Blocking:** yes, for accepting ADR 0010

**Status:** open

---

## Q10 — Anonymous or assisted citizen reporting under OIDC-only authentication

**Question:** Given ADR 0005 (OIDC-only authentication), how does an anonymous member of the
public, or someone assisted by another person, submit a report without an account?

**Why it matters:** OIDC-only authentication (ADR 0005) implies every actor has an identity
provider account; community reporting from people without one, or who need assistance
submitting, is a use case the current auth model does not yet cover.

**Proposed default:** Require an account to submit a report for now; revisit anonymous or
assisted submission when the reports module is built.

**Blocking:** no

**Status:** open

---

## Q11 — Pattern-catalog additions proposed in ADR 0011 (Settings, API Schema)

**Question:** Does the maintainer approve adding the Settings and API Schema entries to the
pattern catalog, as proposed in ADR 0011?

**Why it matters:** `AGENTS.md` §3 (the pattern catalog) is protected and changes only with
maintainer approval; ADR 0011 proposes additions to it.

**Proposed default:** None; the additions stay proposed until the maintainer approves the
catalog change.

**Blocking:** no

**Status:** open

## Q12 — Bounding boxes crossing the antimeridian

- **Question:** Bounding boxes crossing the antimeridian?
- **Why it matters:** `BoundingBox` rejects boxes where the minimum longitude exceeds the maximum. No Gilgit-Baltistan use case needs antimeridian boxes, but global datasets might.
- **Proposed default:** Reject; revisit if an ingestion source ships such extents.
- **Blocking:** no
- **Status:** open (raised by the shared-kernel review, Phase 1)

## Q13 — Definition of the `season` date precision

- **Question:** Definition of the `season` date precision?
- **Why it matters:** `DateWithPrecision.truncate()` floors `season` to meteorological quarters starting December, March, June and September. Local seasonal vocabulary in the valleys may differ.
- **Proposed default:** Meteorological quarters (DJF, MAM, JJA, SON).
- **Blocking:** no
- **Status:** open (raised by the shared-kernel review, Phase 1)

## Q14 — Time zone used when flooring dates

- **Question:** Time zone used when flooring dates?
- **Why it matters:** Day, month and year floors are computed in UTC; Asia/Karachi (UTC+5) would shift boundaries by five hours for events near midnight.
- **Proposed default:** UTC in storage; clients render local time (Asia/Karachi).
- **Blocking:** no
- **Status:** open (raised by the shared-kernel review, Phase 1)

## Q15 — Signing of pagination cursors

- **Question:** Signing of pagination cursors?
- **Why it matters:** Cursors are opaque but unsigned; a client can craft a well-formed token. A cursor only positions a query that authorisation has already scoped, and it carries no secrets.
- **Proposed default:** Unsigned; add an HMAC only if a future cursor must carry data that authorisation does not re-check.
- **Blocking:** no
- **Status:** open (raised by the shared-kernel review, Phase 1)

---

The entries below were raised while building the `geography`, `hazards` and `impacts`
modules in Phase 1 (tasks T5–T9) and the platform pieces underneath them (task T3). Each
one is also noted where it was raised, in `docs/data-dictionary/geography.md`,
`docs/data-dictionary/hazards.md`, `docs/data-dictionary/impacts.md`, `data/reference/README.md`
or the module's `application/` code.

## Q16 — Should `division` be a level of the administrative hierarchy?

- **Question:** The glossary lists country → province/region → district → tehsil → union council → village. Gilgit-Baltistan groups its districts into divisions; should `division` sit between `province_or_region` and `district`?
- **Why it matters:** `AdminLevel` ordering is used to validate every `Place.parent_id`; adding or removing a level later re-validates every place.
- **Proposed default:** Keep `division` between `province_or_region` and `district`.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G1)

## Q17 — Place code scheme

- **Question:** What scheme do place codes follow?
- **Why it matters:** Codes are unique and never reused once published, so the convention needs to be settled before places are seeded from a real boundary source.
- **Proposed default:** A lower-case dotted path from the country, for example `pk.gb.hunza`, used only as a convention (the domain does not enforce dots). Codes never change after publication, even if a place is re-parented.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G2)

## Q18 — Which scripts do place names need?

- **Question:** Which ISO 15924 scripts do place names need?
- **Why it matters:** `PlaceName.script` is validated against a fixed list.
- **Proposed default:** `Latn`, `Arab`, and `Tibt` for the occasional Tibetan-script Balti name. Others are added only when a sourced name needs them.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G3)

## Q19 — Must a place's parent be exactly one level above it?

- **Question:** Must `Place.parent_id` point at a place exactly one `AdminLevel` above the child, or may levels be skipped?
- **Why it matters:** Tehsils and union councils are not recorded everywhere in the source data, so a strict "exactly one level up" rule would make some real places unrepresentable.
- **Proposed default:** No. The parent must be strictly higher, and gaps are allowed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G4)

## Q20 — How many preferred names may a place have?

- **Question:** How many preferred `PlaceName` entries may a place have per language, and what is the display fallback when there is none?
- **Why it matters:** Determines the invariant `PlaceNames` enforces and what a client sees when no preferred name is recorded.
- **Proposed default:** At most one preferred name per language, possibly none. Display falls back to the first-recorded name in the language, then to the caller's fallback languages.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G5)

## Q21 — Should a placeholder `bbox` in reference data become the place geometry?

- **Question:** Should the `entries[].bbox` field in a place reference file ever populate `Place.geometry`?
- **Why it matters:** A placeholder rectangle is not a boundary; using it as geometry would misrepresent the place's real footprint.
- **Proposed default:** No. `bbox` stays in the reference file only; `geometry` waits for the boundary source (Q1).
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G6)

## Q22 — How do textual name citations become provenance `Source` records?

- **Question:** How does the free-text `source` citation on each reference-data place name become a linked `PlaceName.source_id`?
- **Why it matters:** The provenance module does not exist until Phase 3, so citations are currently text only.
- **Proposed default:** Keep citations in the YAML as text; link them to `PlaceName.source_id` when the provenance module arrives in Phase 3.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G7)

## Q23 — Must every non-country place have a parent inside the same data set?

- **Question:** Must a place reference file be self-contained, so that every non-country entry's `parent_code` resolves within the same file?
- **Why it matters:** A regional fixture that omits the country entry could not be loaded if parents must resolve locally.
- **Proposed default:** Yes. Reference files include the country entry.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/geography.md`, Q-G8)

## Q24 — IRDR placement of each local hazard type

- **Question:** Where does each hazard type sit in the IRDR 2014 peril classification? Candidates to check against the publication: `glof` → climatological / Glacial lake outburst; `flash_flood` → hydrological / Flood / Flash flood; `landslide` and `debris_flow` → hydrological / Landslide (or geophysical / Mass movement (dry) for dry rockfalls); `avalanche` → hydrological / Landslide / Avalanche; `cloudburst` → meteorological / Convective storm (peril name to confirm); `glacier_surge` → no obvious IRDR peril.
- **Why it matters:** A wrong placement mis-files every event in IRDR- or EM-DAT-aligned exports, and hazard type codes cannot be reused once published.
- **Proposed default:** The candidates above, each marked `proposed` in the reference file; `glacier_surge` placed under climatological with the placement flagged.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-1)

## Q25 — Definition of "cloudburst"

- **Question:** Is `cloudburst` a hazard type of its own or a peril of extreme rainfall, and is there an intensity or duration threshold (for example from PMD) that defines it?
- **Why it matters:** Without a definition, reporters and moderators will classify the same rainfall event differently.
- **Proposed default:** Own code `cloudburst` without a threshold; intensity recorded as reported.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-2)

## Q26 — Attribute fields and enumerations for every hazard attribute schema

- **Question:** Which fields and enumeration values should each hazard attribute schema carry (GLOF mechanism list, landslide movement type and material after Varnes/Hungr, trigger lists)? Is there an ICIMOD or NDMA reporting format to align to?
- **Why it matters:** These fields become the stored and exported structure of every event from Phase 3 onward.
- **Proposed default:** The minimal proposed fields recorded in `docs/data-dictionary/hazards.md`.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-3)

## Q27 — Avalanche size scale

- **Question:** Should `AvalancheAttributes.size_class` follow the EAWS / Canadian 1–5 destructive size scale, and are half classes (for example 2.5) needed?
- **Why it matters:** Whole classes cannot store a half-class observation later without a type change.
- **Proposed default:** Whole classes 1–5.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-4)

## Q28 — GLIMS glacier id pattern

- **Question:** Is `^G\d{6}E\d{5}[NS]$` the exact GLIMS glacier id format, and does it hold for every glacier in the region?
- **Why it matters:** A too-strict pattern rejects valid ids; a too-loose one accepts typos.
- **Proposed default:** The pattern above, widened only with a cited source.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-5)

## Q29 — Compound SI units in the shared kernel (resolved)

- **Question:** Should the shared kernel register `cubic_metre_per_second` and `metre_per_second` so discharge and rainfall intensity become a single `Measurement`, instead of the stand-in `MeasuredRate` (numerator and denominator)?
- **Why it matters:** Changing the representation later changes the stored JSON of `peak_discharge` and `peak_intensity`.
- **Resolution:** Compound units added to the kernel on 2026-09-23. The kernel now registers `cubic_metre_per_second` and `metre_per_second`; `MeasuredRate` was removed and `peak_discharge` / `peak_intensity` are single `Measurement` values in those units.
- **Blocking:** no
- **Status:** answered (Phase 1, 2026-09-23) (raised in `docs/data-dictionary/hazards.md`, QH-6)

## Q30 — Reactivation and retired parents in the hazard taxonomy

- **Question:** May a retired hazard type be reactivated, and may new types be created under a retired parent?
- **Why it matters:** Decides whether retirement is reversible and how retired branches of the taxonomy behave.
- **Proposed default:** Reactivation allowed with a reason and an event; new children under a retired parent rejected; existing children of a retired parent keep their status.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-7)

## Q31 — Provenance and confidence of hazard attribute numbers

- **Question:** Do hazard attribute numbers (peak discharge, volume, areas) need their own source and `Confidence`, or do they inherit them from the report, event or claim that carries them?
- **Why it matters:** `AGENTS.md` requires every uncertain number to carry a confidence level and a source.
- **Proposed default:** Inherit from the carrying record; revisit when events are modelled in Phase 3.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/hazards.md`, QH-8)

## Q32 — Sendai indicator mapping and code shape for impact metrics

- **Question:** Which UNDRR Sendai global indicators (A-1, A-2, B-1, C-2, D-1, ...) do the initial impact metrics map to, and does the proposed code pattern `^[A-G]-[0-9]{1,2}[a-z]?$` fit every sub-indicator in the UNDRR technical guidance, including suffixes that may be upper-case (for example crop and livestock splits of C-2)?
- **Why it matters:** A wrong or too-strict pattern either mis-maps a metric to Sendai reporting or rejects a valid sub-indicator code.
- **Proposed default:** Map a metric to a Sendai indicator only with a cited source; otherwise leave `sendai` null. Keep the current pattern until a sourced suffix needs it widened.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, from the T7 report)

## Q33 — DesInventar field names for impact metrics

- **Question:** What are the exact DesInventar effect field names and spelling that the initial impact metrics should map to in `ImpactMetric.desinventar`?
- **Why it matters:** DesInventar-aligned exports depend on exact field names; a guessed name is worse than none.
- **Proposed default:** Null unless sourced.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, from the T7 report)

## Q34 — Monetary impact metrics

- **Question:** Should monetary impact metrics record nominal amounts in an ISO 4217 currency (default `PKR`), with the price year on the claim and no inflation or exchange-rate conversion?
- **Why it matters:** Currency and conversion handling cannot change in place once claims exist against a metric; `add-impact-metric` already requires an ADR before any monetary metric ships.
- **Proposed default:** Yes, as described. Keep the pattern; write and get the ADR accepted before any monetary metric is seeded.
- **Blocking:** yes, for seeding a monetary impact metric (not blocking for Phase 1's non-monetary metrics)
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, from the T7 report)

## Q35 — Aggregation defaults for impact metrics

- **Question:** Should impact metric aggregation default to `sum` for counts and monetary metrics, `max` for measurements, and `latest` only when chosen explicitly per metric? Is de-duplicating overlapping claims about the same people or assets left entirely to the Phase 3 best-figure policy?
- **Why it matters:** `aggregation` is immutable once a metric exists; the wrong default retires and replaces metrics unnecessarily.
- **Proposed default:** As stated; de-duplication of overlapping claims is the best-figure policy's job (Phase 3), not the aggregation rule's.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, from the T7 report)

## Q36 — Negative values, the seven metric categories, and frozen retired metrics

- **Question:** Three smaller impact-metric defaults, tracked together: (1) negative values are rejected for every metric kind — does any metric need a signed value, for example a lake-level change? (2) are the seven proposed categories (`human`, `economic`, `infrastructure`, `housing`, `agriculture`, `environment`, `services`) and their Sendai/DesInventar alignment correct? (3) should a retired metric be frozen, so it can no longer be relabelled?
- **Why it matters:** All three are structural rules on `ImpactMetric` that are costly to change once claims exist.
- **Proposed default:** Yes to all three: reject negative values, keep the seven categories, freeze retired metrics against relabelling.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, from the T7 report)

## Q37 — Shina ISO 639-3 code (resolved)

- **Question:** Is the ISO 639-3 code for Shina `shi` (as listed in the Phase 1 plan, Q5) or `scl`?
- **Resolution:** `shi` is Tachelhit, not Shina. The correct code is `scl`. Fixed across the repository (`data/reference/languages.yaml` and elsewhere) on 2026-09-23.
- **Why it matters:** A wrong language code silently mislabels every Shina name and label.
- **Blocking:** no
- **Status:** resolved (2026-09-23) (raised from the T8 report; recorded as QD-1 in that report)

## Q38 — Sendai numbering used for the initial impact metric seed

- **Question:** The initial reference metrics use A-2 for deaths, A-3 for missing, and B-2 for injured or ill, following the numbering in UN document A/71/644. Is this the numbering the project should standardise on?
- **Why it matters:** This conflicts, on its face, with the "never guess a mapping" rule (Q32/AGENTS.md §5): the numbering is sourced to a named UN document, but the mapping of each Yakhnama metric to that number is still a contributor's judgement call, not a confirmed alignment.
- **Proposed default:** Keep the numbering as proposed, flagged non-blocking, until the maintainer confirms it or a more specific technical guidance document supersedes it.
- **Blocking:** no
- **Status:** open (raised from the T8 report; recorded as QD-2 in that report)

## Q39 — IRDR main event for glacier surge

- **Question:** The `glacier_surge` hazard type currently carries the placeholder IRDR main event "Glacial lake outburst", which is wrong (a glacier surge does not require a glacial lake). Should `HazardType.alignment.main_event` become optional instead of carrying an incorrect placeholder?
- **Why it matters:** A wrong main event mis-files every glacier-surge event in IRDR-aligned exports; an optional field is preferable to a false claim.
- **Proposed default:** Make `alignment.main_event` optional and leave it null for `glacier_surge` until a correct placement is found.
- **Blocking:** no
- **Status:** open (raised from the T8 report; recorded as QD-3 in that report)

## Q40 — Counting unit for road damage

- **Question:** Should the impact metric for road damage count road sections or metres of road affected?
- **Why it matters:** The unit determines how comparable Yakhnama's road-damage figures are to other datasets, and changing it later means retiring and replacing the metric.
- **Proposed default:** Road sections. Revisit before Phase 3 stores real claims against the metric.
- **Blocking:** no
- **Status:** open (raised from the T8 report; recorded as QD-4 in that report)

## Q41 — Duplicated `LoadReport` and `AdminOnlyPolicy` per module

- **Question:** Should `LoadReport` and `AdminOnlyPolicy` (currently declared once per module's `application/authorisation.py` and `dto.py`, in `geography`, `hazards` and `impacts`) stay duplicated, or move to a shared location before identity lands?
- **Why it matters:** Duplication is a maintenance cost, but there is no identity module yet to own a shared policy type, and moving it now risks creating a shared-kernel dependency that later needs to change shape once real authorisation exists.
- **Proposed default:** Keep the duplication until the identity module (Phase 2) provides a real `AdminOnlyPolicy` and a shared load-report DTO shape to replace all three copies.
- **Blocking:** no
- **Status:** open (raised from the T9 report)

## Q42 — `BoundaryLoader` port deferred

- **Question:** The geography application layer's `BoundaryLoader` port (for importing a real administrative boundary dataset) is not yet implemented. When should it be built?
- **Why it matters:** It depends on the boundary source decision (Q1); building it earlier risks a wrong shape.
- **Proposed default:** Defer `BoundaryLoader` until Q1 (boundary source) is decided.
- **Blocking:** no
- **Status:** open (raised from the T9 report)

## Q43 — What the reference-data loaders apply in place versus skip

- **Question:** The three modules' `LoadReference*` handlers currently apply only labels, retirement status, names and centroids in place when re-running against changed reference data, and report every other kind of change (for example a hazard type's `alignment`, an impact metric's `category`, or a place's `level`) as skipped for a human to apply. Is this the right scope for an idempotent, unattended loader?
- **Why it matters:** Silently applying a structural change (for example changing a metric's immutable `category`) could corrupt existing claims; but under-applying means a maintainer must remember to check the skipped list after every reference-data change.
- **Proposed default:** Keep the current scope (labels, retirement, names, centroids applied in place; everything else skipped and reported) until Phase 3 needs a wider in-place update.
- **Blocking:** no
- **Status:** open (raised from the T9 report)
- **Update (2026-09-23, standards review):** a stored centroid that differs from the file's is no longer overwritten; it is reported as skipped (`centroid differs; it is not changed in place`). Only a missing centroid is filled in.

## Q44 — Seed is not atomic across modules

- **Question:** `SeedReferenceDataHandler` loads hazard types, then impact metrics, then places, each in its own unit of work, so the whole seed run is not one atomic transaction. Is this acceptable?
- **Why it matters:** A failure partway through leaves some modules loaded and others not; a fully atomic seed across three modules would need a distributed transaction or a saga, which is disproportionate for reference data.
- **Proposed default:** Accept the non-atomic seed: every individual load is idempotent (upsert by code/version, never deletes), so re-running the seed after a partial failure completes the work without duplicating anything.
- **Blocking:** no
- **Status:** open (raised from the T9 report)

## Q45 — Display-name fallback language

- **Question:** When a place, hazard type or impact metric has no preferred name or label in the caller's requested language, should the display fallback always be `en`?
- **Why it matters:** Determines what a client sees by default when a local-language label is missing, which is common until Q2/Q5 are resolved.
- **Proposed default:** Fall back to `en`.
- **Blocking:** no
- **Status:** open (raised from the T9 report)

## Q46 — `opentelemetry-exporter-otlp` not installed

- **Question:** Should the OTLP span exporter package be added now, or only once a collector exists to receive spans?
- **Why it matters:** `platform/telemetry.py` fails fast with an instruction message if `YAKHNAMA_OTEL_EXPORTER=otlp` is set without the package installed, rather than silently dropping spans; adding the dependency before there is a collector to point it at is premature.
- **Proposed default:** Add `opentelemetry-exporter-otlp` and wire it in `platform/telemetry.py` when a collector is available to receive spans.
- **Blocking:** no
- **Status:** open (raised from the T3 report)

## Q47 — Outbox relay operational defaults

- **Question:** The outbox relay defaults to `max_attempts=5` and a maximum batch size of 1000. Are these the right operational defaults, and what should dead-lettering, back-off between retries, and message retention look like?
- **Why it matters:** These are operational, not domain, decisions, and they affect how quickly a stuck subscriber is noticed versus how much load a retry storm places on the database.
- **Proposed default:** Keep `max_attempts=5` and batch size 1000 as the Phase 1 defaults; design dead-letter handling, back-off and retention in Phase 3, alongside the task queue that triggers `relay_once`.
- **Blocking:** no
- **Status:** open (raised from the T3 report)

## Q48 — Production should reject the development `database_url` default

- **Question:** `Settings.database_url` currently defaults to the development DSN (matching `docker-compose.yml`) in every environment, including `environment="production"`. Should production be required to set `YAKHNAMA_DATABASE_URL` explicitly, with the development default rejected?
- **Why it matters:** A production deployment that forgets to set `YAKHNAMA_DATABASE_URL` would otherwise silently try to reach a local development database instead of failing loudly.
- **Proposed default:** Add a validator in Phase 2 that rejects `DEVELOPMENT_DATABASE_URL` when `environment="production"`.
- **Blocking:** no
- **Status:** open (raised from the T3 report)

## Q49 — Test coordinate envelope is not a sourced boundary

- **Question:** Test fixtures and factories use a coordinate envelope of 72.0–77.9° E, 34.5–37.1° N to generate in-region test data. Is this envelope ever mistaken for, or should it ever be promoted to, a sourced boundary for Gilgit-Baltistan?
- **Why it matters:** A rectangle used only to keep generated test coordinates inside the general region is not a boundary dataset and must not be confused with one when the real boundary source (Q1) is chosen.
- **Proposed default:** None needed; this entry exists only to record explicitly that the envelope is test data, not a sourced boundary, so nobody promotes it by mistake.
- **Blocking:** no
- **Status:** open (raised from the T12 report)

---

The entries below were raised while building the `identity` module and the platform HTTP
foundations in Phase 2 (tasks T3–T9). Q50–Q57 are copied from
`docs/data-dictionary/identity.md` (Q-I1–Q-I8), which remains the source of truth for them;
Q58–Q71 come from the T5, T7 and T4 reports; Q72–Q76 are the Phase 2 plan's own open
questions (`docs/plans/phase-2.md` §7), recorded here in the standard format.

## Q50 — Re-synchronising realm roles on every sign-in

- **Question:** Realm roles are copied from the token only at first sight (Phase 2 plan Q2). A role removed at the provider later, such as a demoted admin, stays in Yakhnama. Should roles be re-synchronised on every sign-in?
- **Why it matters:** A user demoted at the identity provider keeps the role inside Yakhnama until an admin also demotes them there, which is a real privilege-drift risk.
- **Proposed default:** Keep first-sight mirroring for Phase 2. Demote in both places. Before production, record which roles came from the provider, so they can be re-synchronised without touching roles granted in Yakhnama.
- **Blocking:** no (security-relevant)
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I1)

## Q51 — Role implication order

- **Question:** Is the `Role` implication order right (`admin` implies `moderator`; `org_admin` implies `org_member`; nothing else, in particular `admin` does not imply `trusted_reporter`)?
- **Why it matters:** Every policy in `modules/identity/domain/policies.py` composes over this order; a wrong implication either over- or under-grants access.
- **Proposed default:** The order as implemented; see `docs/data-dictionary/identity.md`, "Role" table.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I2)

## Q52 — Meaning of the realm-level `org_member` and `org_admin` roles

- **Question:** What do the realm-level `org_member` and `org_admin` roles mean, given per-organisation memberships?
- **Why it matters:** If they were read as a general "trust this person with any organisation" signal instead of markers, they would silently widen every `CanManageOrganization` check.
- **Proposed default:** Markers only. Per-organisation rights come only from `Membership` records.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I3)

## Q53 — Organisation types

- **Question:** Which organisation types exist?
- **Why it matters:** `OrganizationType` is stored on every organisation; the list is easy to extend but a wrong initial member is harder to withdraw once organisations exist.
- **Proposed default:** `government`, `ngo`, `research`, `media`, `community`, `other`.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I4; same list also recorded as the Phase 2 plan's Q3, see Q74 below)

## Q54 — Removing or demoting the only admin of an organisation

- **Question:** May the only admin of an organisation be removed or demoted?
- **Why it matters:** Without a last-admin rule, an organisation could be left with no admin able to manage it, other than a platform administrator.
- **Proposed default:** No. Another admin must be appointed first; this also applies to a platform admin acting on the organisation. An organisation may start with no admin.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I5)

## Q55 — Plain `http` issuers

- **Question:** May an OIDC issuer use plain `http`?
- **Why it matters:** A token endpoint reachable over plain `http` could be intercepted or spoofed on the path.
- **Proposed default:** Only for loopback hosts, for the development realm; the production settings validator requires `https` for the configured issuer.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I6)

## Q56 — What suspension does

- **Question:** What does suspending a user do to their roles, memberships and `last_seen_at`? Do members of a suspended organisation keep their membership rights?
- **Why it matters:** Fixes what every policy sees for a suspended user or a member of a suspended organisation.
- **Proposed default:** A suspended user keeps their roles and memberships but gets no `Actor`, so every request needing one is refused; their record and memberships cannot change until reinstated; `last_seen_at` is still recorded. Suspending an organisation blocks renaming it and adding members. Memberships of inactive (suspended or retired) organisations are left out of the actor, so a member of a suspended organisation is treated as a non-member until it is reinstated.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I7)

## Q57 — Whether a contact channel is ever needed

- **Question:** Is a contact channel (email or phone) ever needed, for example for moderation follow-up?
- **Why it matters:** The identity module deliberately stores no personal data beyond an optional display name (`AGENTS.md` §5); adding a contact field later is a privacy-relevant schema change.
- **Proposed default:** No. None is stored. Any future need goes through the maintainer and a privacy review.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/identity.md`, Q-I8)

## Q58 — Who may create an organisation

- **Question:** Who may call `POST /api/v1/organizations`?
- **Why it matters:** `CreateOrganizationHandler` needs an authorisation rule, and none was specified by the Phase 2 plan beyond "an account is required to write" (Q10).
- **Proposed default:** Any authenticated, active user (any holder of `citizen`, which every active user holds). The creator becomes the organisation's first admin.
- **Blocking:** no
- **Status:** open (raised from the T5 report)

## Q59 — Who may list members and read organisation details

- **Question:** Who may call `GET /api/v1/organizations/{id}` and `GET /api/v1/organizations/{id}/members`?
- **Why it matters:** The member list shows display names, which is the one piece of personal data the module stores; the organisation detail does not.
- **Proposed default:** Organisation detail is public (`organization_read_policy`, anonymous callers included). The member list is restricted to the organisation's own members and platform administrators (`member_list_policy`).
- **Blocking:** no
- **Status:** open (raised from the T5 report)

## Q60 — May a member leave an organisation on their own

- **Question:** May a member remove their own membership, without an organisation admin or a platform admin acting on their behalf?
- **Why it matters:** `RemoveMemberHandler` currently authorises only through `CanManageOrganization`; there is no self-service path yet.
- **Proposed default:** Not in Phase 2. Add an `IsSelf`-based self-removal path later; `IsSelf(user_id)` already exists as a policy building block (`docs/data-dictionary/identity.md`, "Policies").
- **Blocking:** no
- **Status:** open (raised from the T5 report)

## Q61 — May an admin suspend themselves or revoke the last admin role

- **Question:** May an administrator suspend their own account, or revoke the platform `admin` role from the only remaining admin?
- **Why it matters:** Either could leave the platform with no administrator able to reverse the action.
- **Proposed default:** No guard exists yet in Phase 2's `SuspendUserHandler` or `RevokeRoleHandler`. Decide and implement a guard before production.
- **Blocking:** no
- **Status:** open (raised from the T5 report)

## Q62 — Invalid display-name claims are dropped to `None`

- **Question:** When a token's name claim fails `User`'s `display_name` validation (for example it is empty after stripping, or contains a control character), should mirroring the user fail, or should the invalid value be dropped and the field left unset?
- **Why it matters:** Failing the request would turn a cosmetic, untrusted provider claim into an outage for the affected user.
- **Proposed default:** Drop the invalid claim to `None` and continue mirroring; the user can still set a display name themselves.
- **Blocking:** no
- **Status:** open (raised from the T5 report)

## Q63 — Members of suspended organisations are treated as non-members

- **Question:** Should a `Membership` of a suspended organisation still count in the member's `Actor.memberships`, for policies such as `IsMemberOf`?
- **Why it matters:** Counting it would let a member of a suspended organisation keep acting on its behalf while it is suspended.
- **Proposed default:** No; the actor's memberships include only memberships of active organisations (also recorded as part of Q56/Q-I7).
- **Blocking:** no
- **Status:** open (raised from the T5 report)

## Q64 — Replayed idempotent creates return 201, not 200

- **Question:** When `IdempotencyMiddleware` replays a stored response for a repeated `POST` (for example `POST /organizations`), should the replayed status stay `201 Created`, or become `200 OK` because nothing was created this time?
- **Why it matters:** `IETF draft-ietf-httpapi-idempotency-key-header` and ADR 0016 do not fix this; a client polling on status code could behave differently either way.
- **Proposed default:** Keep `201`: the middleware replays the stored response byte for byte, including its original status code, so the response describes what happened the first time, with `Idempotent-Replayed: true` marking the replay.
- **Blocking:** no
- **Status:** open (raised from the T7 report)

## Q65 — Member `ETag` shape

- **Question:** `MemberSummary`'s `ETag` is `make_etag(member.version, member.user_id)` (the *user's* id, not the membership's). Should it instead be keyed on a stable membership id, so that removing and re-adding the same user does not silently reuse the tag shape of a different membership row?
- **Why it matters:** `Membership.id` exists (`docs/data-dictionary/identity.md`) but is not yet exposed on `MemberSummary` or used in its tag.
- **Proposed default:** Keep the current `(user_id, version)` tag in Phase 2; expose a membership id on `MemberSummary` and switch the tag to it in a later phase, as a documented, non-breaking addition.
- **Blocking:** no
- **Status:** open (raised from the T7 report)

## Q66 — `If-Match` is optional on the member and administrator routes

- **Question:** `PATCH`/`DELETE` on organisation members (`.../members/{user_id}`) and on user roles and suspension (`/users/{user_id}/...`) accept `If-Match` but do not require it, unlike `PATCH /me` and `PATCH /organizations/{id}`, which return 428 without it. Should they also require it?
- **Why it matters:** Without a required `If-Match`, two concurrent role or suspension changes can race silently instead of the second one getting 412.
- **Proposed default:** Keep `If-Match` optional on these routes in Phase 2 (moderation and admin actions are comparatively rare and usually sequential); revisit before any of them sees high-concurrency use.
- **Blocking:** no
- **Status:** open (raised from the T7 report)

## Q67 — No coordinate rounding applied yet on the public places endpoints

- **Question:** `GET /api/v1/places` and `GET /api/v1/places/{id}` return each place's stored centroid at full precision; `Settings.public_coordinate_decimals` (Q3) exists but is not yet applied to this response.
- **Why it matters:** Q3 proposes rounding public coordinates to protect reporter locations; reference-data places (administrative centroids, not reporter locations) may not need the same protection, but the setting currently has no effect anywhere.
- **Proposed default:** None for reference-data centroids specifically; apply `public_coordinate_decimals` when reporter- or event-linked coordinates are served, from Phase 3, and have the security reviewer confirm whether administrative centroids need it too.
- **Blocking:** no
- **Status:** open (raised from the T7 report)

## Q68 — Self-hosting the Scalar bundle with an integrity hash

- **Question:** Should the Scalar API reference bundle be self-hosted (a pinned version of the npm package `@scalar/api-reference`, served as a static asset or from `yakhnama.org`) with a Subresource Integrity hash, instead of loading the latest, unpinned bundle from the jsDelivr CDN?
- **Why it matters:** ADR 0014 accepted the CDN only as a stopgap: by default a developer's browser loads unpinned, third-party JavaScript, which the CDN could serve any current version of.
- **Proposed default:** Keep the CDN for development only, as ADR 0014 already records; non-blocking because production has `docs_enabled = false` and serves no docs page at all.
- **Blocking:** no
- **Status:** open (raised in ADR 0014, "More information"; recorded here per the docs-writer brief)

## Q69 — What schedules `purge_expired` for idempotency rows

- **Question:** `IdempotencyStore.purge_expired(now)` deletes lapsed `idempotency_keys` rows (ADR 0016) but nothing calls it yet; what process runs it, and how often?
- **Why it matters:** Stored response bodies can carry personal data for up to 72 hours (`idempotency_ttl_hours`); without a scheduled purge, expired rows are simply never deleted, which both wastes storage and keeps stale data past its documented retention.
- **Proposed default:** A Taskiq job in Phase 3, once the task-queue port (ADR 0008) exists; run at least daily.
- **Blocking:** no
- **Status:** open (raised from the T4 report)

## Q70 — `platform/auth/dependencies.py` importing the container

- **Question:** `platform/auth/dependencies.py` imports `platform/container.py` to build its FastAPI dependencies, which is why every module's `api/dependencies.py` reads the container through its own module-local Protocol instead of importing `platform.auth.dependencies` directly (see the `geography`/`identity` dependency-module docstrings). Should the principal be read from request state entirely inside `platform` instead, so no `api` layer needs this workaround?
- **Why it matters:** The workaround is documented and does not break the layer contract (`lint-imports` passes), but it is friction every module's `api` layer repeats, and it signals that `platform/auth/dependencies.py` sits slightly wrong relative to the container.
- **Proposed default:** Read the principal from request state directly inside `platform` (as `platform/auth/resolution.py`'s `get_principal_resolution` already does) and move the `current_actor`-equivalent helpers there in Phase 3, removing the need for each module to route around the container import.
- **Blocking:** no
- **Status:** open (raised from the T4 report)

## Q71 — Keycloak's custom user-profile override

- **Question:** The development realm's user-profile component (`docker/keycloak/yakhnama-realm.json`) is customised to drop the default `firstName`/`lastName` requirements and make `email` optional (Keycloak does not allow removing the `email` attribute entirely). Should this override be kept as the realm evolves?
- **Why it matters:** The override exists only so demo users need no personal data, matching the backend's rule of storing none (`AGENTS.md` §5); if the realm is later extended or replaced, this constraint has to travel with it.
- **Proposed default:** Keep it, and carry the same constraint (no required name or email) into whatever production identity provider is chosen (Q5).
- **Blocking:** no
- **Status:** open (raised from the T4 report; see also `docs/architecture/auth.md`, "The `yakhnama` realm")

## Q72 — Audience value for the API (`aud` claim)

- **Question:** Is `yakhnama-api` the right `aud` value for access tokens the backend accepts?
- **Why it matters:** `Settings.oidc_audience` defaults to it and the development realm's audience mapper adds it; changing it later requires updating every deployed client and the realm configuration together.
- **Proposed default:** `yakhnama-api`, from settings.
- **Blocking:** no
- **Status:** open (raised in the Phase 2 plan, §7 Q1)

## Q73 — Are `moderator` and `admin` granted through OIDC realm roles or only through the identity module?

- **Question:** Should `moderator` and `admin` ever be granted purely through a Keycloak realm role, or only ever by an admin acting inside the identity module?
- **Why it matters:** Determines how much trust the identity provider's role assignment carries for the platform's most sensitive roles, and interacts with Q50 (re-synchronisation).
- **Proposed default:** Realm roles are mirrored on the token at first sight; roles beyond what the realm grants are added only by an admin inside Yakhnama, and module-granted roles are never downgraded by a later token.
- **Blocking:** no
- **Status:** open (raised in the Phase 2 plan, §7 Q2; see also Q50)

## Q74 — Organisation types (Phase 2 plan copy)

- **Question:** Same question as Q53/Q-I4: are `government`, `ngo`, `research`, `media`, `community` (plus `other`, added during implementation) the right organisation types?
- **Why it matters:** Recorded here because the Phase 2 plan lists it as its own open question, §7 Q3; see Q53 for the full entry, which is the one to update if this is answered.
- **Proposed default:** See Q53.
- **Blocking:** no
- **Status:** open (raised in the Phase 2 plan, §7 Q3; duplicate of Q53/Q-I4)

## Q75 — Storing `display_name` from the token

- **Question:** Should the backend store `display_name` from the token at all?
- **Why it matters:** It is the one piece of personal data the identity module stores (`docs/data-dictionary/identity.md`, "Personal data"); the Phase 2 plan raised it as an explicit yes/no before implementation, distinct from Q57/Q-I8 (whether a contact channel is needed).
- **Proposed default:** Yes, optional, editable by the user; never email or phone.
- **Blocking:** no
- **Status:** open (raised in the Phase 2 plan, §7 Q4)

## Q76 — Idempotency scope for anonymous callers

- **Question:** What idempotency scope applies to an anonymous caller's `POST`?
- **Why it matters:** `IdempotencyMiddleware` scopes reservations to `Principal.scope_key()`, which only an authenticated caller has.
- **Proposed default:** None: anonymous `POST`s are rejected with 401 before idempotency would matter, since every creating route requires an account (Q10).
- **Blocking:** no
- **Status:** open (raised in the Phase 2 plan, §7 Q5)

---

The entries below were raised while building `provenance`, `audit`, `reports`, `media`,
`events`, `verification` and the `impacts` extension (claims, assets, damage) in Phase 3.
Q77–Q101 are copied from the seven modules' own data-dictionary pages, which remain the
source of truth for them; Q102–Q173 come from the Phase 3 implementation reports
(domain, application, persistence, adapter and API tasks) and from
`docs/architecture/best-figure.md`'s own open-questions table.

## Q77 — How are source licences modelled?

- **Question:** Should `Source.licence` be one of an SPDX-shaped id or free custom text, with SPDX ids checked only for shape (not against the real SPDX list, and no `MIT OR Apache-2.0`-style expressions)?
- **Why it matters:** Changing the model later means migrating every stored `licence` value.
- **Proposed default:** As stated; shape check only, no list, no expressions.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/provenance.md`, Q-P1)

## Q78 — Source URL rules

- **Question:** Are the URL rules right: `http` allowed as well as `https`, printable ASCII only (IDNs punycoded), 2048 characters?
- **Why it matters:** Too strict rejects valid citations; too loose admits unsafe schemes.
- **Proposed default:** Yes to all three.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/provenance.md`, Q-P2)

## Q79 — Keeping a citizen source anonymous

- **Question:** Should the domain refuse URLs to social-media profiles, or citations that look like names or phone numbers, for a `citizen` source?
- **Why it matters:** A citizen source must not identify the reporter; the domain cannot tell a person's name from a place name, so it currently only documents the rule for moderators.
- **Proposed default:** A documented moderator rule only; the reports triage PII scrub may later suggest redactions for citations too.
- **Blocking:** no (privacy-relevant)
- **Status:** open (raised in `docs/data-dictionary/provenance.md`, Q-P3)

## Q80 — Custom licence text length

- **Question:** Is 500 characters enough for a custom licence, or should long terms be stored as a document with a URL?
- **Why it matters:** Determines whether a source with unusual reuse terms can be fully recorded.
- **Proposed default:** 500 characters, with the full terms' URL in `url` where one exists.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/provenance.md`, Q-P4)

## Q81 — Correcting `source_type` before a source is referenced

- **Question:** May `Source.source_type` be corrected while the source is still unreferenced?
- **Why it matters:** `source_type` feeds the best-figure source ranking, so a wrong type recorded then corrected changes ranking silently unless a new source is required instead.
- **Proposed default:** No; a wrong type means registering a new source.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/provenance.md`, Q-P5)

## Q82 — Recording when a source was first referenced

- **Question:** Should a source record `referenced_at`, separately from the `provenance.source_referenced` event and the audit log?
- **Why it matters:** Determines whether "when did this become immutable" needs its own field or is reconstructed from history.
- **Proposed default:** The event and the audit log are enough; no extra field.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/provenance.md`, Q-P6)

## Q83 — Audit log retention

- **Question:** How long are audit entries kept, and may they ever be deleted, for example after a legal request about a user?
- **Why it matters:** The append-only guarantee (Q-A) and any future erasure request are in tension unless the retention rule is explicit.
- **Proposed default:** Kept indefinitely; entries hold only ids and digests, so a user's erasure removes the user record and the entries keep only an id that no longer resolves.
- **Blocking:** no (privacy-relevant)
- **Status:** open (raised in `docs/data-dictionary/audit.md`, Q-A1)

## Q84 — Who may read the audit log

- **Question:** Who may read the audit log?
- **Why it matters:** The current implementation has no dedicated read API or route for `audit_entries` at all.
- **Proposed default:** Admins only, never public; to be decided when a read route is built.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/audit.md`, Q-A2)

## Q85 — Actor kinds in the audit log

- **Question:** Is every actor either a user with an id or the system without one? Are there service accounts or organisations acting as actors?
- **Why it matters:** `AuditEntry.actor_kind` is derived from whether `actor_id` is set; a third kind would need a new derivation rule.
- **Proposed default:** Only `user` and `system`.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/audit.md`, Q-A3)

## Q86 — Audit field lengths versus event-type lengths

- **Question:** `action` and `target_type` allow 64 characters, but a domain event's `event_type` and `aggregate_type` allow 100. An event beyond 64 cannot be audited today. Should the limits match?
- **Why it matters:** A future module with a longer `event_type` would silently fail to audit rather than truncate.
- **Proposed default:** Keep 64 in audit and keep event types short; the outbox subscriber fails loudly on a longer one rather than truncating it.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/audit.md`, Q-A4)

## Q87 — `recorded_at` outside the domain model

- **Question:** Should the entry also record when it was written (`recorded_at`), separate from `occurred_at`?
- **Why it matters:** Distinguishes "when the change happened" from "when the relay eventually delivered it".
- **Proposed default:** Not in the domain; persistence adds a database-default column (implemented, migration `0009_audit`; see `docs/data-dictionary/audit.md`, "Persistence").
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/audit.md`, Q-A5)

## Q88 — `TRUNCATE` is not covered by the append-only trigger

- **Question:** The append-only trigger on `audit_entries` rejects `UPDATE`/`DELETE` but not `TRUNCATE`, a separate table-level privilege. Should a dedicated database role with only `INSERT` and `SELECT` be created for it?
- **Why it matters:** Any role granted `TRUNCATE` on the table could erase the whole audit log without the trigger stopping it.
- **Proposed default:** Yes; not yet done.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/audit.md`, Q-A6; from the migration `0009_audit` docstring)

## Q89 — Duplicate-report suspicion window

- **Question:** Distance and time window for duplicate suspicion.
- **Why it matters:** Too tight misses real duplicates; too loose floods moderators with false positives.
- **Proposed default:** 2 km and 24 hours.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R1)

## Q90 — EXIF plausibility tolerances

- **Question:** EXIF plausibility tolerances for triage.
- **Why it matters:** Same trade-off as Q89, for a different signal.
- **Proposed default:** Capture time within 7 days of `observed_at`, position within 5 km of the observation point.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R2)

## Q91 — Which PII patterns triage looks for

- **Question:** Which personal-data patterns does triage look for? CNIC numbers without dashes and landlines with a leading `0` alone are deliberately not matched, to avoid flagging ordinary numbers.
- **Why it matters:** Under-matching misses real personal data; over-matching flags harmless text.
- **Proposed default:** Mobile numbers with `0`, `+92` or `0092`; landlines with `+92` or `0092`; emails; CNIC `NNNNN-NNNNNNN-N` only.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R3)

## Q92 — Spam signal thresholds

- **Question:** What description length, link count and repeated-character run should the spam rule flag?
- **Why it matters:** Sets how aggressively genuine short or link-heavy reports are flagged.
- **Proposed default:** Shorter than 10 characters, more than 3 links, or one character repeated 10+ times.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R4)

## Q93 — Comparing dates of coarse precision in triage rules

- **Question:** How do triage rules compare times known only to the month, season or year?
- **Why it matters:** A naive comparison could flag or clear a pair of reports based on an arbitrary floor.
- **Proposed default:** They do not: comparisons need `exact`, `hour` or `day` precision on both sides; the rule skips the comparison otherwise.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R5)

## Q94 — Confidence per triage rule

- **Question:** What confidence does each triage rule attach to its flags?
- **Why it matters:** Feeds how much weight a moderator gives each flag.
- **Proposed default:** EXIF medium, duplicate low, personal data medium, spam low.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R6)

## Q95 — Maximum media assets per report

- **Question:** How many media assets may one report revision carry?
- **Why it matters:** Bounds both storage cost and the size of the `ReportSubmitted`/`ReportRevised` payloads.
- **Proposed default:** 20.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R7)

## Q96 — Who generates a revision's id

- **Question:** Who generates the id of a report revision, given an offline client cannot know it in advance?
- **Why it matters:** Revision 1 uses the client's own id for idempotency; a later revision needs a different rule.
- **Proposed default:** The platform's `IdGenerator`; a retried revision relies on `Idempotency-Key` instead.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R8)

## Q97 — Which reports may be withdrawn

- **Question:** May any revision be withdrawn, or only a draft or the current revision?
- **Why it matters:** Determines whether a superseded revision can be withdrawn on its own.
- **Proposed default:** Drafts and current revisions only; the reporter withdraws the latest revision; withdrawn reports stay on record.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R9)

## Q98 — `observed_at` after the submission time

- **Question:** May `observed_at` lie after the submission time (a wrong client clock)?
- **Why it matters:** Rejecting it would refuse a legitimate report from a device with a bad clock.
- **Proposed default:** Not rejected by the domain; the value is the reporter's own statement.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R10)

## Q99 — Does a new triage run replace the previous result

- **Question:** Does re-running triage replace the report's stored `TriageResult`?
- **Why it matters:** Determines whether moderators see only the latest signal or an accumulating one.
- **Proposed default:** Yes; the aggregate keeps only the latest result, and every run stays in the outbox and audit log through `ReportTriaged`.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R11)

## Q100 — Hazard and place code formats mirrored into the reports domain

- **Question:** `hazard_guess.hazard_code` and `place_hint` mirror the `hazards` and `geography` code formats because a domain layer may import only the kernel. Should the code formats move into `shared_kernel`?
- **Why it matters:** Two independent copies of a format can drift; a shared definition cannot.
- **Proposed default:** Keep the mirrors, guarded by unit tests that compare them with the originals; the application checks existence through the facades.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R12)

## Q101 — Does a revision keep the original's reporter, organisation and source

- **Question:** Does every revision keep the original report's reporter, organisation and source?
- **Why it matters:** A revision is meant to be a correction by the same reporter, not a new observation from a new source.
- **Proposed default:** Yes.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/reports.md`, Q-R13)

## Q102 — Report hazard and place codes are not checked for existence

- **Question:** `Report.hazard_guess.hazard_code` and `place_hint` are validated only for shape (Q100), not checked for existence against the `hazards` and `geography` modules at submission time.
- **Why it matters:** A reporter could submit a well-formed but non-existent code, which a moderator would only discover later.
- **Proposed default:** Check existence in the application layer through the `hazards`/`geography` facades before Phase 3 is considered complete for production use; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application report)

## Q103 — Media attached after report submission

- **Question:** A reporter may request an upload for an already-submitted report (`POST /reports/{id}/media`), so `media_ids` can grow after `ReportSubmitted` fired with a smaller `media_count`.
- **Why it matters:** A subscriber or export that reads `media_count` from the submission event alone would undercount; the current media count must be read from the report itself.
- **Proposed default:** Accept as designed; document that `media_count` on the event is a snapshot, not the final count.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application report)

## Q104 — Triage after a broker failure

- **Question:** If the broker never delivers `reports.run_triage` (a dropped message, a broker outage during enqueue), nothing currently re-triggers it.
- **Why it matters:** An untriaged report gets no EXIF, duplicate, PII or spam signal for moderators, silently.
- **Proposed default:** A periodic task that re-enqueues triage for reports with no `TriageResult` past an age threshold; not yet built.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application/adapter reports)

## Q105 — Allowed media types

- **Question:** Which media MIME types may be uploaded?
- **Why it matters:** Too narrow blocks legitimate evidence (for example HEIC, the iPhone default, is not included); too broad widens the attack surface.
- **Proposed default:** `image/jpeg`, `image/png`, `image/webp`, `video/mp4`, `application/pdf`.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M1)

## Q106 — Largest accepted upload

- **Question:** What is the largest accepted upload?
- **Why it matters:** Bounds storage cost and in-memory read size (each full read is capped at this size).
- **Proposed default:** 50 MiB.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M2)

## Q107 — Does an infected scan quarantine automatically

- **Question:** Does an infected malware-scan verdict quarantine the asset automatically?
- **Why it matters:** Determines whether a moderator must act before an infected file's public copy (if any) is withdrawn.
- **Proposed default:** Yes, with a fixed reason, and any public copy is withdrawn immediately.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M3)

## Q108 — Sensitivity flag values

- **Question:** Which sensitivity values exist for media?
- **Why it matters:** Determines what a moderator can flag and what stays private as a result (Q109).
- **Proposed default:** `none`, `injured_or_deceased`, `identifiable_people`, `other`; set by a moderator only.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M4)

## Q109 — May sensitive imagery be published

- **Question:** May imagery flagged `injured_or_deceased` or `identifiable_people` ever be published?
- **Why it matters:** Directly affects the safety and dignity of people shown in reported imagery.
- **Proposed default:** No; such imagery keeps an approved asset private until the maintainer decides how, if ever, it is shown (blurred, behind a warning, never).
- **Blocking:** no (safety-relevant)
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M5)

## Q110 — Object key layout

- **Question:** What object key layout do media originals and public copies use?
- **Why it matters:** Keys must never leak a file name or be guessable from one asset to another.
- **Proposed default:** `media/original/<id>` and `media/public/<id>`, never from file names.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M6)

## Q111 — Deduplication scope

- **Question:** Is deduplication scoped to the same SHA-256 **and** the same owner, or across owners too?
- **Why it matters:** Cross-owner deduplication could let one user detect that another already uploaded a specific file.
- **Proposed default:** Same SHA-256 and same owner only.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M7)

## Q112 — Can a moderator's approval lift a quarantine

- **Question:** Can a moderator's approval lift a quarantine? Can a clean rescan?
- **Why it matters:** Determines the recovery path for a wrongly-quarantined asset.
- **Proposed default:** A moderator's approval can (unless the scan says infected); a clean rescan alone cannot.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M8)

## Q113 — What the development scanner should report

- **Question:** The `NoOpScanner` cannot vouch for a file; should it report `unavailable` (nothing publishable in development) or `clean` (indistinguishable from a real verdict)?
- **Why it matters:** Affects whether a full local publication walkthrough is possible without a real scanner, and whether the domain can ever tell a no-op verdict from a real one.
- **Proposed default:** `unavailable` by default; a developer may build it to return `clean` for a walkthrough; production refuses to start with the `noop` scanner.
- **Blocking:** yes, for the T5 scanner adapter (resolved by the adapter's default; confirmation pending)
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M9, and `docs/architecture/media.md`, Q-M9)

## Q114 — `UploadFailed` event, not in the original plan

- **Question:** The Phase 3 plan's event list does not mention `media.upload_failed`; is it needed?
- **Why it matters:** Without it, a `failed` upload has no event and no audit trail.
- **Proposed default:** Keep it.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/media.md`, Q-M10)

## Q115 — EXIF capture time zone

- **Question:** Which time zone does a zone-less EXIF `DateTimeOriginal` use, and should an unknown zone lower its precision below `exact`?
- **Why it matters:** Pakistan Standard Time (UTC+5) is likelier for local phones than UTC; a wrong assumption shifts every EXIF-derived time by up to 5 hours.
- **Proposed default:** Assume UTC, keep `exact` precision.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/media.md`, Q-M11)

## Q116 — ClamAV adapter unverified against a real daemon

- **Question:** `ClamAvScanner` is unit tested against a fake stream and a loopback stand-in, but not yet verified against a real `clamd`.
- **Why it matters:** A protocol mismatch (framing, size limits) would only surface once real malware scanning is relied on in production.
- **Proposed default:** Verify against `clamav/clamav` before production; not yet done.
- **Blocking:** yes, before production
- **Status:** open (raised in `docs/architecture/media.md`, Q-M12)

## Q117 — PDF and MP4 metadata not stripped

- **Question:** Must PDF and MP4 metadata (document information, `udta` box) be stripped before publication?
- **Why it matters:** Either format can carry an author name or a position in its metadata, which the current public copy does not remove.
- **Proposed default:** Not stripped now, documented as a gap; candidates are `pikepdf` for PDF and remuxing without `udta` for MP4.
- **Blocking:** yes, before public media launch
- **Status:** open (raised in `docs/architecture/media.md`, Q-M13)

## Q118 — Placeholder digest for an oversize upload

- **Question:** `StoredObject.sha256` cannot express "not computed" for an object that exceeded the size cap; should the field become optional instead of using a placeholder?
- **Why it matters:** A placeholder digest of 64 zeros could be mistaken for a real hash by a careless caller.
- **Proposed default:** Keep the placeholder for now; make the field optional in the DTO in a later phase.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/media.md`, Q-M14)

## Q119 — Path-style versus virtual-hosted S3 addressing

- **Question:** Should production use path-style or virtual-hosted S3 addressing?
- **Why it matters:** Path-style works with both MinIO and AWS; virtual-hosted is AWS's newer recommendation but is not MinIO-compatible without extra configuration.
- **Proposed default:** Path-style.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/media.md`, Q-M15)

## Q120 — Public host for presigned links behind a private network

- **Question:** Presigned URLs currently carry the endpoint the API itself uses; behind a private network, external clients would need a different, public host.
- **Why it matters:** A presigned link generated with the private endpoint is unusable from outside that network.
- **Proposed default:** One endpoint for now; add a separate public presign endpoint when deploying behind a private network.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/media.md`, Q-M16)

## Q121 — Presigned URL lifetime

- **Question:** How long should a presigned upload or download URL live?
- **Why it matters:** Too short breaks slow uploads; too long widens the window a leaked link stays usable.
- **Proposed default:** 15 minutes (`storage_presign_ttl_seconds`, default 900, range 60–3600).
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/media.md`, Q-M17)

## Q122 — Quality-95 re-encoding for the public copy

- **Question:** Is quality-95 JPEG/WebP re-encoding, and dropping the ICC colour profile, acceptable for the public copy?
- **Why it matters:** Affects the visual fidelity of public imagery; the original keeps full fidelity regardless.
- **Proposed default:** Yes.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/media.md`, Q-M18)

## Q123 — Event status set

- **Question:** Is `draft | published | retracted | merged` the right event status set, with `retracted` and `merged` final?
- **Why it matters:** Changing the set later means migrating every stored event and every exported status.
- **Proposed default:** As proposed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q124 — Event centroid approximation for polygons

- **Question:** Is the vertex mean of a polygon's exterior ring (holes ignored, clamped to the bounding box) an acceptable centroid approximation, given it is not the area centroid and can fall outside a concave shape?
- **Why it matters:** The domain computes it without Shapely to stay framework-free; a wrong approximation misplaces an event on the map.
- **Proposed default:** Keep it; infrastructure may store PostGIS `ST_PointOnSurface` beside it if a more accurate point is needed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q125 — Event period validity rule

- **Question:** Is the precision-aware order check (`latest_instant_of(ended_at) >= truncate(started_at)`) the right period-validity rule, rather than the plan's simpler "truncated end >= truncated start" (which would reject a day-precision start with a later month-precision end)?
- **Why it matters:** The two rules disagree on real cases (for example a start known to the day, an end known only to the month it falls in).
- **Proposed default:** The precision-aware rule, as implemented.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q126 — Unknown event end in search

- **Question:** Should an event with no `ended_at` be treated, for search, as lasting only for its start period?
- **Why it matters:** Affects whether an ongoing, still-unresolved event matches a period-overlap search for later dates.
- **Proposed default:** Yes, as proposed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q127 — Report-link roles

- **Question:** Is `primary | supporting | contradicting` the right set of report-link roles?
- **Why it matters:** Feeds how a timeline or export explains why a report is linked to an event.
- **Proposed default:** As proposed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q128 — Affected-place kinds

- **Question:** Is `origin | impacted | reference` the right set of affected-place kinds?
- **Why it matters:** Feeds place-based search and how an event's geography is explained.
- **Proposed default:** As proposed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q129 — Bounding-box event search matches only the centroid

- **Question:** Should a bounding-box event search match on the centroid only, or also on any overlap with a polygon/multipolygon geometry?
- **Why it matters:** A large event whose polygon overlaps a search box but whose centroid falls outside it is currently missed.
- **Proposed default:** Centroid only, as implemented.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q130 — `triggered_by` cycles are not rejected

- **Question:** Should a cycle of `triggered_by` relations between events be rejected, the way a `part_of` cycle already is?
- **Why it matters:** An unrejected cycle ("A triggered B triggered A") could confuse any causal-chain export or visualisation.
- **Proposed default:** Allowed, as implemented; revisit if a real cycle is ever recorded by mistake.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q131 — Event factory combined-period precision

- **Question:** Should `EventFactory.from_reports` take the **coarsest** precision among the linked reports for the derived period, rather than, for example, the finest?
- **Why it matters:** Determines how precise a freshly created event's period claims to be when its source reports disagree in precision.
- **Proposed default:** Coarsest, as implemented.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q132 — Does publishing an event require verification first

- **Question:** Should `publish` on the `Event` aggregate itself require a `verified` `VerificationCase`, or is that left entirely to the application and to what public reads show?
- **Why it matters:** As implemented, the aggregate only checks the event is `draft`; a `published` but not yet `verified` event exists, and is simply hidden from public reads.
- **Proposed default:** Public reads stay verified-only (as implemented); `publish` itself stays ungated at the aggregate level.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/events.md`, "Open questions")

## Q133 — Verification case initial states

- **Question:** Should reports and claims open their `VerificationCase` in `submitted`, and events in `draft`?
- **Why it matters:** Sets the very first state a moderator sees for each kind of target.
- **Proposed default:** As proposed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/verification.md`, "Open questions")

## Q134 — Verification terminal states

- **Question:** Are `rejected` and `retracted` correctly terminal, with a correction always taking the form of a new report revision or a new claim, never a further transition?
- **Why it matters:** Confirms there is no "un-reject" or "un-retract" path in the state machine.
- **Proposed default:** Terminal, as implemented; a correction is a new report revision or a new claim.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/verification.md`, "Open questions")

## Q135 — Who may dispute a verified case

- **Question:** Who may move a `verified` case to `disputed` — any moderator, the original reviewer only, or an external party acting through a moderator? May an automated check dispute one?
- **Why it matters:** Disputing a verified record is a significant action against already-published data.
- **Proposed default:** Any actor with a reason; authorisation is the application's job, not the state machine's.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/verification.md`, "Open questions")

## Q136 — Automated rejection

- **Question:** May an automated check move a case to `rejected` or `needs_information`, given only `verified` explicitly requires a human?
- **Why it matters:** Reads literally, the transition table allows it; whether it should be allowed in practice is an application-level policy decision.
- **Proposed default:** Allowed by the table's literal reading; the application may restrict it further.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/verification.md`, "Open questions")

## Q137 — Assignment on closed verification cases

- **Question:** Should `rejected` and `retracted` cases be unassignable, as implemented?
- **Why it matters:** Assigning a reviewer to a closed case could imply work remains to be done on it.
- **Proposed default:** Unassignable, as implemented.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/verification.md`, "Open questions")

## Q138 — Infrastructure asset kinds and damage levels

- **Question:** Are `bridge | road_segment | water_channel | power_line | building | other` the right asset kinds, and `damaged | destroyed | washed_away` the right damage levels?
- **Why it matters:** Both lists are used to classify every recorded asset and damage claim from Phase 3 onward.
- **Proposed default:** As listed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, "Open questions")

## Q139 — Monetary claim precision and price-year bounds

- **Question:** Are two decimal places, price years 1900–2100, and "`price_year` not after the claim's year" the right rules for a monetary `ClaimValue`?
- **Why it matters:** These bound what a monetary impact claim (once a monetary metric is seeded, see Q34) can express.
- **Proposed default:** As listed.
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md`, "Open questions")

## Q140 — Source ranking for `satellite` and `dataset`

- **Question:** Where do `satellite` and `dataset` sources rank in the best-figure tie-break, given the Phase 3 plan only orders government, research, organisation, news and citizen?
- **Why it matters:** Decides which of two equally-recent, equal-value claims from these source types wins a tie.
- **Proposed default:** Between research and organisation (government > research > satellite > dataset > organisation > news > citizen).
- **Blocking:** no
- **Status:** open (raised in `docs/data-dictionary/impacts.md` and `docs/architecture/best-figure.md`, "Open questions")

## Q141 — `sum` deduplication and nested scopes

- **Question:** Is "one claim per `(source_id, scope)`, newest kept" the right deduplication for `sum`, and how should nested scopes (a district claim and a village claim inside it) be handled?
- **Why it matters:** Double counting inflates casualty and damage figures; nested scopes are not currently detected automatically.
- **Proposed default:** As implemented; nested scopes are the moderator's job (retract the overlapping claim).
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/best-figure.md`, "Open questions")

## Q142 — Flooring `claimed_at` before comparing

- **Question:** Should `claimed_at` be floored to the start of its precision period before two claims are compared for recency?
- **Why it matters:** Decides which claim counts as "newest" when precisions differ (day versus month, for example).
- **Proposed default:** Yes, floor to the start of the precision period, in UTC.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/best-figure.md`, "Open questions")

## Q143 — Minimum confidence as the combined figure's confidence

- **Question:** Is the minimum confidence among contributing claims the right combined confidence for a best figure?
- **Why it matters:** Sets the confidence shown alongside every public figure.
- **Proposed default:** Yes, the minimum.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/best-figure.md`, "Open questions")

## Q144 — Monetary sums across different price years

- **Question:** Is a nominal sum across different price years, labelled with the latest contributing price year, an acceptable presentation?
- **Why it matters:** A nominal (unconverted) sum across years is only nominally meaningful, not real-terms comparable.
- **Proposed default:** Nominal sum, latest price year label, no conversion.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/best-figure.md`, "Open questions")

## Q145 — May a retired metric's claims be corrected

- **Question:** May a claim under a retired `ImpactMetric` be corrected, given a retired metric accepts no new claims?
- **Why it matters:** Without correction, an error recorded against a metric before it was retired could never be fixed.
- **Proposed default:** No; a retired metric accepts no new claims, corrections included.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/best-figure.md`, "Open questions")

## Q146 — Should source rank ever override recency or size

- **Question:** Should `SourceRank` ever override a newer claim (`latest`) or a larger value (`max`), rather than only breaking ties?
- **Why it matters:** Changing this would let a government claim silently override a more recent or larger claim from another source type.
- **Proposed default:** No; rank only breaks ties.
- **Blocking:** no
- **Status:** open (raised in `docs/architecture/best-figure.md`, "Open questions")

## Q147 — Orphaned sources and duplicate original media objects

- **Question:** Nothing currently cleans up a `Source` registered but never cited by any fact, or an original media object left in storage after its asset row would otherwise be considered abandoned (for example a `failed` upload).
- **Why it matters:** Both accumulate storage cost and unreferenced records with no automatic removal.
- **Proposed default:** A periodic clean-up task, in a later phase; not yet built.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 persistence/adapter reports)

## Q148 — System tasks act with no actor

- **Question:** Background tasks (`reports.run_triage`, `media.scan`) run with no `Actor` — their commands and events record `actor_id = null` ("system"), the same convention `audit` uses.
- **Why it matters:** Confirms this is deliberate and consistent, not an oversight, since a human reading an audit entry with no actor needs to know that is expected for these actions.
- **Proposed default:** Keep it; document `actor_id = null` as meaning "the system", consistently.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application report)

## Q149 — Cross-module application calls are not atomic

- **Question:** A use case that calls another module's facade (for example `events` calling `reports`, or `reports` calling `provenance`) does so as a separate call, not inside one distributed transaction; a failure partway through can leave one module changed and another not.
- **Why it matters:** A distributed transaction or a saga across modules is disproportionate for a modular monolith; the alternative is to make every cross-module call safe to repeat.
- **Proposed default:** Every cross-module call the application layer makes is idempotent, so a retried handler completes the work without duplicating it, mirroring the Phase 1 seed's own non-atomicity (Q44).
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application report)

## Q150 — "Published and verified" as the public-visibility gate

- **Question:** Is requiring **both** `status = published` **and** a `verified` `VerificationCase` the right combined gate for anonymous, public reads of an event?
- **Why it matters:** A stricter or looser gate changes what the public dataset shows versus what moderators can see in progress.
- **Proposed default:** Both required, as implemented (`EventRecordQueryService`); see also Q132.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application/API reports)

## Q151 — Verification rules for reporter resubmission and disputes

- **Question:** May a reporter themselves move their own report's case from `needs_information` back to `submitted`, and are disputes raised by moderators only, never directly by a member of the public?
- **Why it matters:** Determines whether a reporter has any direct role in their own report's verification lifecycle, versus only through resubmitting a revision.
- **Proposed default:** A reporter corrects by submitting a new revision (which does not itself move the verification case); disputes are raised by moderators only. Not yet enforced as a distinct authorisation rule beyond `CanModerate` on every verification route.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application report)

## Q152 — Merging an event does not move its claims or verification case

- **Question:** When an event is merged into another (`merge_into`), its impact claims, damage records and its own `VerificationCase` are not moved or re-pointed to the surviving event.
- **Why it matters:** A reader following only the surviving event after a merge would miss the merged event's claims unless they also follow `merged_into` links.
- **Proposed default:** Leave claims and the case on the merged event; a reader (or the API) needing the combined picture must follow the merge; consider moving them explicitly in a later phase.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain/application reports)

## Q153 — `EventFactory.from_reports` has no `summary` parameter

- **Question:** Event creation from reports takes `title` and `attributes` but not `summary`; a moderator must set it afterwards, if at all, through a route that does not yet exist for it alone.
- **Why it matters:** `summary` currently has no dedicated command to set it after creation.
- **Proposed default:** Add `summary` to the factory or a dedicated `SetEventSummary` command in a later phase; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain/API reports)

## Q154 — Impact claims have no verification case in Phase 3

- **Question:** `VerificationCase.target.kind` includes `claim`, and claims open in `submitted` (Q133), but no Phase 3 command actually opens a `VerificationCase` for a recorded `ImpactClaim`.
- **Why it matters:** The domain and data dictionary describe claim verification, but it is not yet wired up end to end.
- **Proposed default:** Wire `RecordImpactClaim` to open a case in a later phase; until then, claim verification is modelled but not exercised.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 application report)

## Q155 — No `expected_version` on events or verification commands

- **Question:** Unlike reports, the `events` and `verification` commands carry no `expected_version`, so their routes' `If-Match` check (comparing against a freshly re-read current tag before the command runs) only narrows, and does not close, a race with a concurrent change.
- **Why it matters:** Two concurrent moderator actions on the same event or case can still both succeed, even with `If-Match` sent.
- **Proposed default:** Add `expected_version` to these commands in a later phase, matching the pattern `reports` already uses; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 API reports; also noted in the `events` and `verification` router docstrings)

## Q156 — Actor dependency helpers copied per API package

- **Question:** Each Phase 3 module's `api/dependencies.py` defines its own `CurrentActor`/`ModeratorActor`/`OptionalActor` FastAPI dependency aliases, rather than sharing one definition (the same pattern already noted for `identity` in Q70).
- **Why it matters:** Seven near-identical copies is a maintenance cost, though it keeps each module's `api` layer free of a direct import of another module's dependencies.
- **Proposed default:** Keep the duplication for now, consistent with Q70's reasoning; revisit if `platform/auth` grows a shared helper all modules can use directly.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 API report)

## Q157 — Two `ModerationStatus` names in the OpenAPI document

- **Question:** The generated OpenAPI schema contains more than one component schema named `ModerationStatus` (from `media` and from other Phase 3 modules' own moderation-decision enums), which some client generators handle by suffixing or colliding.
- **Why it matters:** A generated client in a strict tool could produce two conflicting types or overwrite one with the other.
- **Proposed default:** Rename one enum to a more specific name (for example `MediaModerationStatus`) in a later phase; not yet done, and not blocking because FastAPI's own schema still validates correctly.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 API report)

## Q158 — `PATCH` on an event runs one command per changed member

- **Question:** `PATCH /moderation/events/{id}` runs `SetEventGeometry`, `SetEventPeriod` and `SetEventAttributes` as separate commands, in sequence, rather than one combined command; a failure part-way through leaves the members before it applied.
- **Why it matters:** The response always reflects the current event, but a client sending several members in one request may see a partially-applied change if one command fails.
- **Proposed default:** Accept as documented behaviour (the router docstring already states it); consider one combined command in a later phase if partial application proves confusing in practice.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 API report; also documented in `modules/events/api/router.py`'s `update_event` docstring)

## Q159 — No read routes for one impact claim or one damage record

- **Question:** There is no `GET` route for a single `ImpactClaim` or `DamageRecord` by id; both are only ever read as part of `GET /events/{id}/impacts`.
- **Why it matters:** A client that only has a claim or damage-record id (for example from an event, from a moderator's earlier response) has no direct way to re-fetch it.
- **Proposed default:** Add dedicated `GET` routes in a later phase if a real need appears; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 API report)

## Q160 — Infrastructure assets have no generic update

- **Question:** `InfrastructureAsset` supports `rename` and `relocate` as its only two changes; there is no generic "save every field" update.
- **Why it matters:** A field added later (for example a new descriptive attribute) would need its own dedicated command, following the same pattern, rather than reusing a generic one.
- **Proposed default:** Keep the two dedicated commands; add another dedicated command per new field, consistent with the append-only and explicit-change style used across Phase 3.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain/application reports)

## Q161 — SQL coordinate rounding depends on `extra_float_digits`

- **Question:** A PostgreSQL query that rounds or compares coordinates server-side depends on the session's `extra_float_digits` setting for consistent `float`-to-text conversion; this is now pinned in the connection setup, but the dependency itself is worth recording.
- **Why it matters:** A session with a different `extra_float_digits` value could round or format a coordinate differently than the application layer's `PublicCoordinatePolicy` does, producing a mismatch between what is stored, what SQL reports and what the API returns.
- **Proposed default:** Keep `extra_float_digits` pinned in the engine's connection setup, as implemented; prefer the application-layer `round_coordinates` over server-side rounding wherever a choice exists.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 persistence report)

## Q162 — No cross-module foreign keys

- **Question:** Every Phase 3 table's references to another module's records (reporter ids, organisation ids, source ids, place codes, hazard codes, asset ids from a different module) carry no database foreign key, consistent with the modular-monolith rule that a module's persistence never imports another module's ORM.
- **Why it matters:** The database cannot itself prevent a dangling reference (for example a report citing a source id that no longer exists, which should never happen because sources are never deleted, but the database does not enforce it).
- **Proposed default:** Accept as the intended consequence of module boundaries (`AGENTS.md` §2.1); rely on application-layer existence checks and the "referenced data is never deleted" rules instead.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 persistence report)

## Q163 — `find_nearby`-style queries cannot use the GiST index

- **Question:** A nearest-neighbour or "within distance" query over `reports.observation`, `events.centroid` or `infrastructure_assets.location` that does not use PostGIS's `<->`/`ST_DWithin` operators cannot use the GiST index built for each column, and falls back to a sequential scan.
- **Why it matters:** Any future proximity query (for example a real duplicate-suspicion query run in SQL rather than in the domain, or a "assets near this event" query) needs to be written against the indexable operators from the start.
- **Proposed default:** Document the indexable operators for each geometry column; write proximity queries in `infrastructure/queries.py` against them, not a manual haversine calculation in SQL.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 persistence report)

## Q164 — Cursor-parsing logic copied per module

- **Question:** Each Phase 3 module's query-service or router implements its own parsing of the opaque pagination cursor into the module's own sort key, rather than sharing one generic cursor codec beyond `shared_kernel/pagination.py`'s `encode_cursor`/`decode_cursor`.
- **Why it matters:** Seven near-identical "decode, then build this module's keyset predicate" blocks are a maintenance cost, similar to Q156.
- **Proposed default:** Keep the duplication; the shared part (`encode_cursor`/`decode_cursor`) already lives in `shared_kernel`, and the module-specific part (which columns the keyset walks) genuinely differs per module.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 persistence report)

## Q165 — Delayed (scheduled-for-later) tasks are unsupported

- **Question:** The `TaskQueue` port and its Taskiq adapter support enqueueing a task for immediate delivery and periodic tasks (via `platform/tasks/scheduled.py`), but not a one-off task scheduled for a specific future time.
- **Why it matters:** A future feature needing "run this once, in an hour" (for example a delayed re-triage) has no port method to call yet.
- **Proposed default:** Add delayed one-off scheduling to the `TaskQueue` port only once a real use case needs it; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 adapter report)

## Q166 — No back-off between outbox or task retries

- **Question:** Neither the outbox relay (Q47) nor a failed background task waits progressively longer between retries; a failed outbox row is retried on the very next relay run, and a failed task is not retried by the broker at all (the outbox is what drives eventually-consistent work instead).
- **Why it matters:** A systematically failing subscriber or handler (for example a downstream outage) is retried at full speed until `max_attempts` is reached, rather than backing off.
- **Proposed default:** Add exponential back-off to the outbox relay's retry timing in a later, dedicated operational pass; not yet done (see also Q47).
- **Blocking:** no
- **Status:** open (raised from the Phase 3 adapter report)

## Q167 — Redis stream cap may drop tasks under sustained backlog

- **Question:** The Taskiq Redis broker's stream has a maximum length; under a sustained backlog (workers down for longer than it takes to fill the stream), the oldest unconsumed tasks could be evicted before a worker ever sees them.
- **Why it matters:** A dropped `media.scan` or `reports.run_triage` task would silently leave an asset or report without its expected side effect, with only the outbox (for domain events, not tasks) as a separate safety net.
- **Proposed default:** Monitor stream length in production; document the cap and revisit if it needs raising or if a re-enqueue sweep (like Q104's) should also cover missed tasks generally.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 adapter report)

## Q168 — The "ids and non-personal fields only" outbox payload rule is not mechanically enforced

- **Question:** The Phase 2 outbox payload contract (ids and non-personal fields only, size cap) is followed by convention and code review in every Phase 3 domain event, but nothing automatically rejects a payload that breaks it (for example a future contributor accidentally adding a description field to an event).
- **Why it matters:** A silent violation would leak personal data into the outbox, and from there into the audit log's `payload_digest` computation (though not the audit entry itself) and to every subscriber.
- **Proposed default:** Keep relying on code review and the module docstrings' explicit "never carries X" statements for now; consider a schema-level check (for example a test that inspects every registered event's field names against a denylist) in a later phase.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain/security reports)

## Q169 — `LocalizedText` is not yet built on the shared `SafeText` type

- **Question:** `shared_kernel`'s `LocalizedText` (used by `hazards` and `impacts` labels since Phase 1) validates its own text rather than reusing the `SafeText` type Phase 3 introduced for `reports`, `provenance`, `media`, `events`, `verification` and `impacts` claims' free-text fields.
- **Why it matters:** Two independent text-validation implementations can drift (for example if a new forbidden character class is added to one but not the other).
- **Proposed default:** Migrate `LocalizedText` onto `SafeText` in a later phase, once the Phase 1 modules are touched again for another reason; not blocking on its own.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain report)

## Q170 — `identity` keeps its own copy of the safe-text rule

- **Question:** `identity`'s `display_name` validation (Phase 2) predates the shared kernel `SafeText` type and still carries its own copy of the same rule (no control characters, lone surrogates or bidirectional overrides) rather than importing `SafeText`.
- **Why it matters:** Same drift risk as Q169, for the one personal-data field `identity` stores.
- **Proposed default:** Migrate `identity.display_name` onto `SafeText` in a later phase; not blocking.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain report)

## Q171 — Cataloguing `PublicCoordinatePolicy` and `round_coordinates`

- **Question:** `shared_kernel/privacy.py`'s `PublicCoordinatePolicy` declares `Implements: Policy` (`AGENTS.md` §3's Policy row, currently scoped to "Authorisation rules"); is coordinate-rounding the right fit for that catalog entry, or does it need its own row?
- **Why it matters:** `AGENTS.md` §3 is protected and changes only with maintainer approval; using an existing pattern label for a different concern (privacy transformation rather than authorisation) is a judgement call worth flagging rather than silently stretching the catalog's meaning.
- **Proposed default:** Keep `Implements: Policy` for now (a composable, rule-based decision, which is the catalog's own general description of Policy); propose a dedicated catalog row only if the maintainer disagrees.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain report)

## Q172 — Task payload size limits

- **Question:** The `TaskQueue` port and its adapter document no explicit maximum payload size for an enqueued task, unlike the outbox's documented payload contract (Q168) and the HTTP body-size guard (`platform/http.py`).
- **Why it matters:** Every current Phase 3 task payload is tiny (one id), so this has not mattered yet, but nothing stops a future task from being given a large payload by mistake.
- **Proposed default:** Document and, if needed, enforce a payload size cap on `TaskQueue.enqueue` in a later phase; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 adapter report)

## Q173 — Additional Unicode characters allowed by `SafeText`

- **Question:** `SafeText` (Phase 3's shared free-text validator) currently allows U+200B (zero-width space), U+FEFF (byte-order mark / zero-width no-break space), and U+2028/U+2029 (line and paragraph separator) through, having rejected only control characters, lone surrogates and bidirectional overrides/embeddings/isolates.
- **Why it matters:** These characters are invisible or format-affecting rather than displayable text; U+2028/U+2029 in particular can break naive single-line rendering assumptions the same way a literal newline would, and a string of only zero-width characters could pass a "non-empty after stripping" check while displaying as nothing.
- **Proposed default:** Reject U+200B, U+FEFF, U+2028 and U+2029 too, tightening `SafeText` in a later, small change; not yet done, and not blocking because no known exploit depends on them today.
- **Blocking:** no
- **Status:** open (raised from the Phase 3 domain/security reports)

## Q174 — JWKS response streaming cap

- **Question:** JWKS response streaming cap?
- **Why it matters:** The JWKS client checks the response size only after reading the whole body; a hostile or misconfigured provider could send a very large document.
- **Proposed default:** Deferred to Phase 4: cap while streaming (`httpx` `aiter_bytes`) at the configured size.
- **Blocking:** no (explicitly deferred from Phase 2 with the lead's acceptance; the maintainer may pull it forward)
- **Status:** resolved (2026-09-23) in Phase 4 T7: `HttpJwksClient` streams discovery and JWKS bodies with `aiter_bytes()` and abandons the stream once the decoded body exceeds `max_document_bytes` (default 256 KiB); a declared `Content-Length` above the cap is refused before any byte is read.

## Q175 — Access-token `typ` check

- **Question:** Access-token `typ` check?
- **Why it matters:** Tokens whose `typ` header is not an access token (for example ID tokens) are accepted if otherwise valid.
- **Proposed default:** Deferred to Phase 4: reject `typ` values other than `JWT`/`at+jwt` when present.
- **Blocking:** no (explicitly deferred from Phase 2 with the lead's acceptance; the maintainer may pull it forward)
- **Status:** resolved (2026-09-23) in Phase 4 T7: when the JOSE header carries `typ`, `TokenValidator` accepts only `oidc_accepted_token_types` (default `JWT`, `at+jwt`), compared case-insensitively with an optional `application/` prefix (RFC 7515 §4.1.9); a token without `typ` is not refused for it; rejections are logged as `token_type_not_accepted`.

## Q176 — Account-creation limits

- **Question:** Account-creation limits?
- **Why it matters:** Any valid token from the provider creates a user on first sight; self-registration at the provider would let anyone create accounts at will.
- **Proposed default:** Deferred to Phase 4: rate-limit first-sight mirroring per provider subject range, or require an invitation for non-citizen roles.
- **Blocking:** no (explicitly deferred from Phase 2 with the lead's acceptance; the maintainer may pull it forward)
- **Status:** deferred to Phase 4

## Q177 — IPv6 rate-limit keys

- **Question:** IPv6 rate-limit keys?
- **Why it matters:** Anonymous rate limiting hashes the full client address, so an IPv6 caller can rotate through a /64 to evade limits.
- **Proposed default:** Deferred to Phase 4: key anonymous limits on the /64 prefix for IPv6 and the /32 address for IPv4.
- **Blocking:** no (explicitly deferred from Phase 2 with the lead's acceptance; the maintainer may pull it forward)
- **Status:** resolved (2026-09-23) in Phase 4 T7: anonymous rate-limit keys hash the /64 prefix of an IPv6 peer and the full address of an IPv4 peer (IPv4-mapped IPv6 counts as IPv4); `client_network` in `platform/ratelimit/middleware.py`.

## Q178 — Source citation lookup for public reads

- **Question:** How should the provenance read service learn whether a citizen or organisation source is cited by a published, verified event?
- **Why it matters:** Until a `SourceCitationChecker` adapter exists, such sources are hidden from anonymous and non-member readers even when the citing event is public, so public event pages cannot link to their citizen sources.
- **Proposed default:** No checker bound in Phase 3 (safe: nothing leaks). Phase 4 adds an events read `is_source_cited_by_public_event(source_id)` and wires it.
- **Blocking:** no
- **Status:** resolved (2026-09-23) in Phase 4 T7: the events read port `EventCitationQueryService.is_source_cited_by_public_event` (SQL `EXISTS` on `source_ids @>`, `status = 'published'` and the verification case `verified`) answers `SourceCitationChecker` through `platform/wiring/provenance.py`, bound in `build_recording_services`. Only the event's own `source_ids` count (linked reports' sources are already added there); a source cited only by an impact claim stays hidden.

## Q179 — Domain event fields that carry coordinates or free text

- **Question:** `tests/architecture/test_outbox_payloads.py` lists 22 event fields (place and event centroids and bounding boxes, place names, retirement and merge reasons, relabel texts, claim values, asset locations) under `PENDING_REMOVAL`. Should they be dropped from the events so subscribers re-read the aggregate?
- **Why it matters:** The outbox payload contract is ids and non-personal scalars only; today the only subscriber digests the payload, so nothing leaks, but a future subscriber could copy these fields.
- **Proposed default:** Drop them module by module in a follow-up; the test refuses new offenders and any listed entry that stops offending must be removed from the list.
- **Blocking:** no
- **Status:** open (Phase 4 T7)

---

The entries below were raised while building `exchange` and `ingestion` in Phase 4
(tasks T2–T6): exports, imports, the historical backfill contract, the dataset
catalog, the ingestion pipeline, observations and raster assets. Item-level entries
already recorded in the two modules' own data-dictionary pages (`docs/data-dictionary/exchange.md`,
`docs/data-dictionary/ingestion.md`, whose `Q-I*` entries remain their source of
truth) are not repeated here except where a cross-cutting decision needed its own
entry.

## Q180 — Who may export which dataset

- **Question:** Any authenticated user may export `events` and `claims`; only
  moderators may export `reports` (`export_policy`). Is this the right split?
- **Why it matters:** Reports carry a reporter's rounded position and are the closest
  thing to personal data the recording modules hold; a wrong default either over- or
  under-restricts a bulk-download path the read API does not otherwise offer.
- **Proposed default:** As implemented.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a)

## Q181 — An import with any blocking row error creates nothing

- **Question:** A dry run, an empty file, or a real import whose validation report has
  any `error`-severity issue writes **nothing at all**, not even the rows without a
  problem — is "all or nothing" the right rule, rather than writing the valid rows and
  reporting the rest as skipped?
- **Why it matters:** "All or nothing" makes a moderator's fix-and-retry loop simple
  (the report is authoritative and repeatable), but it also means one bad row in a
  10 000-row file blocks every good row in the same file until it is removed or fixed.
- **Proposed default:** Keep "all or nothing", as implemented and stated in the Phase
  4 plan (§1).
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a)

## Q182 — No sweeper for stuck or crashed export/import jobs, and no per-batch progress

- **Question:** A job left `running` by a crashed worker (one that started `_start`
  and then died before `_complete`/`_fail`) stays `running` forever; nothing reclaims
  it the way the outbox relay reclaims leased rows. A running import's `writes` field
  is also only ever visible once the job reaches a terminal status, not batch by batch
  while it runs.
- **Why it matters:** A stuck job is invisible to its owner (it looks "still running")
  and to a moderator deciding whether to trust an in-progress large import; without a
  sweeper, only a human noticing an old `running` row can free it up.
- **Proposed default:** Add a time-based sweeper (parallel to a future ingestion-run
  sweeper, Q195) and expose `writes`/row progress on a running job in a later phase;
  not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a)

## Q183 — A failed `enqueue` after commit leaves the job `queued`

- **Question:** `RequestExportHandler`/`RequestImportHandler` commit the job, then
  call `tasks.enqueue`; if the enqueue call itself fails, the job is left `queued`
  with no task ever delivered, exactly the same pattern `reports` already accepts
  (`docs/open-questions.md`, this entry's sibling in `reports`). Is a periodic sweep of
  old `queued` jobs the right robustness mechanism, given there isn't one yet?
- **Why it matters:** Without a sweep, a job stuck at `queued` because of a transient
  broker outage never runs unless someone requests it again.
- **Proposed default:** A `queued`-job sweep in a later, dedicated operational pass,
  consistent with the outbox relay's own retry model; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a)

## Q184 — `EventFactory.from_record` does not exist

- **Question:** `CreateHistoricalEventHandler` (the backfill's way of creating an
  event, with no linked reports) builds the `Event` directly with
  `Event.model_validate` in the application layer, because `EventFactory` currently
  only knows how to derive an event from reports (`EventFactory.from_reports`).
  Should a `EventFactory.from_record` classmethod give this construction path a home
  in the `events` domain layer instead?
- **Why it matters:** Two different places construct an `Event` from scratch today
  (the domain factory for reports, the application handler for historical records),
  which is a modest but real drift risk if the aggregate's construction invariants
  ever need to change in one place and not the other.
- **Proposed default:** Add `EventFactory.from_record` in a later phase, when the
  `events` domain is next touched; not yet done. This is the `events` module's open
  question, not `exchange`'s, even though `exchange` depends on the handler it
  concerns.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a; documented in
  `CreateHistoricalEventHandler`'s own docstring)

## Q185 — Export row visibility relies entirely on the events/impacts/reports facades

- **Question:** `ExportRowSource` streams rows through `EventRecordQueryService`,
  `EventImpactsQueryService` and `AuthorisedReportQueryService`, the same read ports
  the API itself uses, so `exchange` enforces no visibility rule of its own beyond
  `export_policy` (which dataset an actor may request at all). Is delegating every
  row-level visibility check to the owning module's own facade sufficient, or does an
  export need an additional, dedicated check?
- **Why it matters:** A future visibility rule added to one owning module's query
  service (for example a new report-visibility tier) automatically applies to exports
  too, which is the intended benefit, but also means `exchange` itself has nothing to
  audit for row-level correctness beyond trusting those three ports.
- **Proposed default:** Accept the delegation as the intended design (it is exactly
  what "the same visibility rules as the API" in the Phase 4 plan asks for); revisit
  only if a visibility rule specific to bulk export, rather than to reading one
  record, is ever needed.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a)

## Q186 — The export citation's author is a fixed "Yakhnama contributors"

- **Question:** Every export's metadata sidecar cites `CITATION_AUTHOR = "Yakhnama
  contributors"` as a fixed string. Is this the wording the maintainer wants for the
  dataset's citation, or does ADR 0010's eventual acceptance also settle a different
  author line (for example naming the project, not "contributors")?
- **Why it matters:** The citation line is copied into every exported file already in
  this phase; changing it later does not retroactively fix files already downloaded.
- **Proposed default:** Keep `"Yakhnama contributors"` until ADR 0010 settles the
  dataset's citation wording (also recorded in `docs/data-dictionary/exchange.md`
  under `MetadataSidecar.citation`); matches Q9's proposed `NOTICE` copyright line.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3a)

## Q187 — No lifecycle rule for completed export files or aborted uploads

- **Question:** A completed export's file and sidecar live in object storage forever
  once written (`GET /exports/{id}` just asks for a fresh presigned link each time);
  a `DELETE /exports/{job_id}` only cancels a **queued** job, never removes a
  completed one's files. Likewise, an import upload granted by `POST
  /moderation/imports/uploads` but never followed by a real `POST
  /moderation/imports` request leaves an orphaned object under `imports/` forever,
  and a multipart export upload aborted partway through (`ArtifactStore.open_sink`
  raising) is documented to leave no partial object, but nothing sweeps an
  storage-provider-side incomplete multipart upload itself.
- **Why it matters:** Without a retention or cleanup rule, object storage accumulates
  orphaned export files and abandoned upload grants indefinitely, which is both a
  storage cost and, for the rare case of a cancelled-after-completion export, a data
  a user might expect to have gone away.
- **Proposed default:** Define a retention policy and a sweep (or a storage
  lifecycle rule, for example an S3 bucket lifecycle configuration expiring
  incomplete multipart uploads and old `imports/` objects) in a later, dedicated
  operational pass; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q188 — A claim's `geometry` column/field is always null in every spatial export format

- **Question:** `ClaimExportRow`/`flat_layout.geometry_of` and `GeoJsonExporter` both
  give a claim `geometry: null` in every geometry-capable format, because a claim
  carries no location of its own (only its event does). Is `null` the right choice, or
  should a claim inherit its event's geometry/centroid when exported, so a claims
  GeoJSON file is directly mappable without a join back to the events file?
- **Why it matters:** A researcher who only downloads the `claims` dataset in GeoJSON
  or GeoParquet gets no map-ready geometry at all today; they must also download
  `events` and join on `event_id`.
- **Proposed default:** Keep `null` (a claim's location is genuinely its event's, not
  its own, and inheriting it would duplicate data across every claim of the same
  event); document the join requirement instead. Revisit if researcher feedback shows
  this is a real friction point.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q189 — Object storage bucket for exports and imports is not a dedicated setting

- **Question:** `ArtifactStore` writes and reads through the same storage
  configuration `media` already uses (bucket, endpoint, credentials from
  `platform/storage`), rather than a dedicated `exports`/`imports` bucket setting.
  Should exchange artifacts have their own bucket, separate from media, for example so
  a different retention or public-access policy can apply to each?
- **Why it matters:** Sharing one bucket is simpler operationally but means a bucket
  policy (public read, lifecycle rules) cannot differ between uploaded media and
  exchange artifacts without prefix-based rules instead of bucket-based ones.
- **Proposed default:** Share the one configured bucket, prefixed (`exports/`,
  `imports/`) as implemented; add a dedicated setting only if a real operational need
  (for example a different retention period) appears.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q190 — `IMPORT_MAX_BYTES` for a whole-file GeoJSON parse is not yet an enforced setting

- **Question:** `GeoJsonImporter` reads and parses an entire GeoJSON file in memory
  (a `FeatureCollection` cannot be tokenised row by row the way CSV can), bounded only
  by `IMPORT_UPLOAD_MAX_BYTES` (50 MiB) at the upload-grant stage and by
  `MeteredSource`'s cap at read time — there is no dedicated, documented
  `IMPORT_MAX_BYTES` applied specifically to the parse step itself.
- **Why it matters:** A large, validly-sized-but-pathological GeoJSON file (very many
  small features, or deeply nested geometry) could still cost significant memory and
  CPU during the single parse call, since the format's own structure forces reading
  the whole file before any row can be validated.
- **Proposed default:** Treat `IMPORT_UPLOAD_MAX_BYTES` (50 MiB, Q193) as the
  effective cap for now, since it already bounds the file the importer reads; add a
  dedicated, possibly lower, `IMPORT_MAX_BYTES` setting for whole-file-parse formats
  specifically if GeoJSON imports prove to need a tighter bound in practice.
- **Blocking:** no (security-relevant: an unbounded parse is a denial-of-service
  surface)
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q191 — The CSV formula guard has no ADR of its own

- **Question:** `CsvExporter.defuse_formula` (the leading-apostrophe OWASP mitigation
  for spreadsheet formula injection) is implemented and documented in code and in
  `docs/architecture/exchange.md`, but, unlike comparable security-relevant defaults
  in this project (for example ADR 0015 for JWT validation, ADR 0016 for idempotency),
  it has no dedicated ADR recording the threat model and the alternatives considered
  (for example quoting differently, or refusing such cells outright).
- **Why it matters:** A security-relevant mitigation without a recorded rationale is
  easy to accidentally weaken or remove in a later refactor, since nothing forces a
  reviewer to reconsider the threat model.
- **Proposed default:** Keep the mitigation as implemented; write a short ADR
  recording it in a later phase, following the `write-adr` skill; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q192 — The export column layout (CSV/GeoParquet) is a proposed default, not a confirmed schema

- **Question:** `flat_layout.py`'s per-dataset column names, order and typed-variant
  split (for example a claim's `count`/`measurement_value`/`amount` columns) are the
  domain modeller's/integration engineer's proposal, not a schema the maintainer or a
  downstream data consumer has confirmed.
- **Why it matters:** Every published `.csv`/`.parquet` file's column names become a
  de facto public contract the moment a researcher builds tooling against it; renaming
  a column later is a breaking change to every consumer, not just to Yakhnama's own
  tests.
- **Proposed default:** Ship the layout as documented in `docs/architecture/exchange.md`
  and `docs/data-dictionary/exchange.md`; bump `EXPORT_SCHEMA_VERSION` (currently
  `1.0`) and record the change in the sidecar and the changelog before any column is
  renamed or removed, once the maintainer or real consumers give feedback.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q193 — A presigned `PUT` cannot itself enforce the 50 MiB import upload cap

- **Question:** `POST /moderation/imports/uploads` grants a presigned `PUT` with
  `max_bytes: IMPORT_UPLOAD_MAX_BYTES` (50 MiB) in its response, but whether the
  underlying object-storage provider actually **refuses** an upload larger than that
  at the storage layer depends on the provider's presigned-URL implementation
  (S3-compatible providers vary); the size is otherwise only checked **after the
  fact**, when the import worker reads the file back through `MeteredSource`, which
  aborts once the declared `byte_size` is exceeded.
- **Why it matters:** If storage does not itself cap the `PUT`, a moderator's client
  (or a compromised one) could store a much larger file than declared before the
  import ever runs, consuming storage and bandwidth even though the import itself will
  correctly refuse to process it.
- **Proposed default:** Verify the configured storage adapter's presigned-`PUT`
  behaviour against the size constraint in a later security-focused pass, and document
  the confirmed behaviour; until then, treat the read-time check as the only
  guaranteed enforcement.
- **Blocking:** no (security-relevant)
- **Status:** open (raised from the Phase 4 adapter report, T5a)

## Q194 — `IMPORT_UPLOAD_MAX_BYTES` (50 MiB) is a proposed operational default

- **Question:** Is 50 MiB the right cap for an uploaded import file, given
  `IMPORT_MAX_ROWS` (10 000 rows) and the backfill contract's per-cell limits?
- **Why it matters:** Too low rejects a legitimate large historical backfill file with
  many claims per row; too high widens the exposure window of Q193 and Q190.
- **Proposed default:** Keep 50 MiB (`IMPORT_UPLOAD_MAX_BYTES`), about 5 KiB per row at
  the maximum row count, as implemented.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 API report, T6)

## Q195 — Missing read routes: import job listing, a single raster asset, dataset versions

- **Question:** Three read routes a client might reasonably expect do not exist yet:
  `GET /moderation/imports` (listing every import job, the way `GET /exports` lists
  export jobs), `GET /raster-assets/{id}` (one raster by id, rather than only through
  the list route's filters), and `GET /datasets/{code}/versions` (a dedicated,
  paginated list, rather than only the "most recent versions" inline on
  `GET /datasets/{code}`).
- **Why it matters:** A moderator has no way to review past imports except by id
  (which they must already have); a client that only has a raster asset's id (for
  example from a STAC search elsewhere) cannot re-fetch it directly; a dataset with
  many versions cannot page through all of them.
- **Proposed default:** Add the three routes in a later phase if a real need appears,
  following the pattern of the equivalent Phase 3 gaps (`docs/open-questions.md`
  Q159); not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 API report, T6)

## Q196 — Admin `If-Match` is optional, not required, on every `exchange`/`ingestion` write

- **Question:** Every admin/moderator write with a concurrency-sensitive target
  (`POST /admin/datasets/{code}/status`, and every organisation/user administrator
  route from Phase 2, Q66) accepts `If-Match` and checks it when sent, but does not
  require it.
- **Why it matters:** Same race as Q66: two concurrent administrative changes to the
  same dataset can both succeed even though `If-Match` exists, because neither request
  is forced to send it.
- **Proposed default:** Keep `If-Match` optional on these routes for the same reason
  Q66 gives (administrative actions are comparatively rare and usually sequential);
  revisit together with Q66 if either sees high-concurrency use.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 API report, T6; duplicate reasoning of Q66)

## Q197 — No sweeper for ingestion runs stuck `running` after a worker crash

- **Question:** Like Q182 for exchange jobs, an `IngestionRun` left `running` by a
  crashed worker (between `IngestionRun.start()` and the pipeline's
  `record_lineage`) has no time-based reclaim mechanism.
- **Why it matters:** A stuck run looks like an ingestion still in progress
  indefinitely, and a curator has no signal to retry it (a retry is always a new run,
  so the stuck one is simply abandoned, not resumed).
- **Proposed default:** Add a time-based sweeper marking a run `failed` after a
  configurable timeout, in a later, dedicated operational pass alongside Q182; not yet
  done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b)

## Q198 — Which duplicate observation wins is a pipeline-wide default, not per-source

- **Question:** `IngestionPipeline.deduplicate`'s default keeps the **first** record
  in source order within one run's batch, and `ObservationRepository.append_many`
  keeps whichever was **stored first** across runs; is "first wins" the right general
  default, given that some publishers document their own tie-breaking rule (for
  example "latest revision wins")?
- **Why it matters:** A source whose publisher means the opposite (last record should
  win, as a correction of an earlier one in the same batch) gets a silently wrong
  value unless its pipeline subclass overrides the hook, which is easy to forget.
- **Proposed default:** Keep "first wins" as the documented, overridable default
  (`IngestionPipeline.deduplicate`'s own docstring already says a subclass that knows
  better overrides it); require every new source's pipeline to state its rule
  explicitly in its own docstring, following the fixture temperature pipeline's
  example.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b; also `docs/data-dictionary/ingestion.md`)

## Q199 — A checksum mismatch fails the whole run before parsing

- **Question:** When the bytes `fetch` returns do not hash to the `DatasetVersion`'s
  pinned `input_checksum`, the pipeline records one error and skips straight to
  `record_lineage` — nothing is parsed, validated or stored, even if the mismatch is
  something innocuous like a trailing byte-order mark the publisher added between
  releases.
- **Why it matters:** This is the correct behaviour for lineage integrity (an
  observation must point at exact, pinned bytes), but it means a version recorded with
  a slightly wrong checksum can never ingest anything until a new version is recorded
  with the corrected checksum — there is no "re-pin and retry" path short of a new
  `DatasetVersion`.
- **Proposed default:** Keep failing the run outright, as implemented (a version is
  fixed once recorded and has no change methods, `docs/data-dictionary/ingestion.md`);
  a wrong checksum is fixed by recording a corrected version, never by editing the
  existing one.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b)

## Q200 — Read access to ingestion runs and observations is public by default

- **Question:** Every `GET` under `/api/v1/datasets`, `/ingestion-runs`,
  `/observations` and `/raster-assets` is anonymous, including a run's full report
  (issues, counts) and every observation's value. Should any of this be restricted, for
  example to authenticated users only, or should run reports hide their issue text
  from anonymous readers?
- **Why it matters:** Run reports and observation values are not personal data, and
  publishing the raw dataset is the whole point of the platform, but a run's issue
  messages could in principle quote something from a future, less-careful source
  adapter (today's issue-message cleaning already strips anything unsafe, `issue_message`
  in `application/pipeline.py`).
- **Proposed default:** Keep every read anonymous, as implemented (Phase 4 plan
  intent: open data); revisit only if a future source's issue text needs hiding.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b)

## Q201 — No dedicated "curator" role; catalog administration requires full `IsAdmin`

- **Question:** Every `/api/v1/admin/*` route in `ingestion` requires the platform
  `admin` role (`catalog_policy` = `IsAdmin`); there is no narrower role (for example
  `curator`) that could register datasets and request runs without also holding every
  other administrator capability (user suspension, role grants).
- **Why it matters:** Registering datasets and requesting ingestion runs is a
  different kind of trust than administering user accounts; requiring full `admin` for
  both means the platform cannot delegate dataset curation to someone who should not
  also manage users.
- **Proposed default:** Keep `IsAdmin` for Phase 4 (the identity module's role set is
  a Phase 2 decision, `docs/data-dictionary/identity.md`); consider a `curator` role in
  a later phase alongside a broader review of the role set, not as a Phase 4-only
  addition.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b)

## Q202 — `Dataset.is_fixture` exists only in the reference file, not on the persisted aggregate

- **Question:** `is_fixture` (marking synthetic test data such as
  `fixture.temperature_sample`, so it is never mistaken for a real publisher's
  dataset) is a field of the `DatasetReferenceEntry` in `data/reference/datasets.yaml`,
  but the domain `Dataset` aggregate and the `datasets` table carry no equivalent
  column — a client reading `GET /datasets/{code}` cannot itself tell a fixture
  dataset apart from a real one, except by its `code` starting with `fixture.` by
  convention.
- **Why it matters:** Nothing stops a future export or public listing from surfacing
  the synthetic fixture dataset indistinguishably from a real one, beyond the naming
  convention.
- **Proposed default:** Add `is_fixture` to the `Dataset` aggregate and the `datasets`
  table in a later phase, so it can be filtered on and shown in the API; until then,
  rely on the `fixture.` code prefix and this project's small number of registered
  datasets.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 domain report, T2b)

## Q203 — `QueryObservations`'s time window is half-open, unlike other date-range filters

- **Question:** `QueryObservations`'s `observed_from`/`observed_to` window is
  half-open (`[from, to)`), unlike `EventPeriodOverlapsSpecification`'s closed window
  or `ExportFilters.occurred_from`/`occurred_to`'s inclusive-both-ends range elsewhere
  in the codebase. Should every time-range filter in the system use the same
  convention?
- **Why it matters:** A client (or a future contributor) moving between an events
  search and an observations query has to remember two different edge-case rules for
  what "up to this instant" means.
- **Proposed default:** Keep `[from, to)` for `QueryObservations` specifically (it
  suits a dense, regularly-sampled time series better, avoiding a double-count at
  exact window boundaries between consecutive pages); document the difference
  prominently, as this page and `docs/architecture/ingestion.md` already do, rather
  than unifying the two conventions.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b)

## Q204 — `PERSIST_BATCH_SIZE` (1000 observations per `append_many` call) is a proposed default

- **Question:** Is 1000 rows the right batch size for `IngestionPipeline.persist`,
  given every batch still shares the run's one transaction (so batching here bounds
  one statement's size, not transaction scope, unlike the exchange import's
  per-batch transactions)?
- **Why it matters:** Too small adds round-trips for a large ingestion run; too large
  risks a very large single `INSERT` statement for a source with many observations per
  version.
- **Proposed default:** Keep 1000 (`PERSIST_BATCH_SIZE`), as implemented; revisit with
  real ingestion volumes once a non-fixture source exists.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 application report, T3b)

## Q205 — Celsius-to-Kelvin conversion rounding (half-even, 3 decimals)

- **Question:** `LocalCsvTemperatureAdapter`'s conversion (Kelvin = °C + 273.15,
  decimal arithmetic, rounded half-even to 3 decimals) is the fixture's own choice;
  should this rounding rule (precision and tie-breaking) be a documented, shared
  convention for every future temperature source, rather than decided per adapter?
- **Why it matters:** Two sources rounding the same nominal Celsius value differently
  would make otherwise-identical readings from different publishers look different in
  the stored series.
- **Proposed default:** Adopt half-even rounding to 3 decimals (1 mK) as the shared
  convention for every future `air_temperature` source, documented in
  `docs/data-dictionary/ingestion.md`'s variable registry when the first real
  temperature source is added; not yet formalised beyond the fixture.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5b)

## Q206 — The ingestion fixtures directory has no dedicated settings field yet

- **Question:** `reference_adapters(fixtures_dir, clock=...)` takes the fixtures
  directory as a constructor argument from the composition root, rather than reading
  it from a `Settings` field the way most other paths and directories in the platform
  are configured.
- **Why it matters:** Every other configurable path in the project goes through
  `pydantic-settings` (`AGENTS.md` §4's hard rules: "configuration comes from the
  environment through `pydantic-settings`"); a hard-coded composition-root path is a
  narrow, low-risk exception today because there is exactly one fixture source, but it
  would not scale to a second local-file source with a different directory.
- **Why it matters (cont.):** would not scale cleanly if a second local-file source
  needs its own directory.
- **Proposed default:** Add a `Settings.ingestion_fixtures_dir` field (defaulting to
  `data/fixtures/ingestion`) in a later phase, once a second local-file source exists
  to justify it; not yet done.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5b)

## Q207 — The 10 MiB local fixture file cap is a proposed operational default

- **Question:** `FixturePathResolver` refuses files over 10 MiB before reading them.
  Is 10 MiB the right ceiling for a local CSV source file, given the fixture itself is
  a few hundred rows?
- **Why it matters:** Too low would refuse a legitimate, larger real local-file source
  later; too high risks reading an unexpectedly large file into memory on a worker
  thread.
- **Proposed default:** Keep 10 MiB for now; revisit once a real (non-fixture)
  local-file source exists with its own realistic file size.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5b)

## Q208 — `RasterAsset.stac_id` uniqueness is scoped per dataset version, not globally

- **Question:** `(dataset_version_id, stac_id)` is the unique constraint
  (`uq_raster_assets_dataset_version_id`), so the same STAC item id can recur across
  different versions of the same dataset (and across different datasets entirely).
  Should `stac_id` instead be required to be globally unique, matching how STAC
  catalogs elsewhere typically expect an item id to be unique within a whole
  collection?
- **Why it matters:** A STAC client aggregating items across Yakhnama's whole raster
  catalog (rather than one dataset version at a time) could see two different items
  sharing an id if two different datasets, or two versions of the same dataset,
  happen to reuse a publisher's scene id.
- **Proposed default:** Keep per-version uniqueness, as implemented (a re-release of
  the same scene under the same STAC id across versions is a real, expected case for a
  raster catalog that tracks dataset versions); document the scope clearly wherever
  `stac_id` is surfaced, as `docs/data-dictionary/ingestion.md` and
  `docs/architecture/ingestion.md` already do.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 persistence report, T4)

## Q209 — `observations`'s hypertable readiness is checked by table structure only

- **Question:** The `observations` table is built so that
  `create_hypertable('observations', 'observed_at', migrate_data => true)` could run
  without changing a column, the primary key or any query (time-first primary key, no
  foreign keys) — but nothing in this phase actually runs TimescaleDB, creates a
  hypertable, or tests that the conversion behaves as expected on real data volumes.
- **Why it matters:** "Hypertable-ready" is a structural claim proven by table design
  review, not an operational claim proven by actually converting the table; a real
  conversion could still surface an unexpected TimescaleDB constraint or performance
  characteristic that table structure alone does not reveal.
- **Proposed default:** Treat the table as ready-by-design; verify the actual
  conversion (and its effect on the existing indexes and query plans) in a dedicated
  operational task before TimescaleDB is actually adopted in production, not assumed
  from this design alone.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 persistence report, T4)

## Q210 — `ix_events_source_ids_gin` was added without `CONCURRENTLY`

- **Question:** Migration `0015` builds the GIN index on `events.source_ids` inside
  the normal single migration transaction, not with `CREATE INDEX CONCURRENTLY`
  (which Alembic would need run outside a transaction). The migration's own docstring
  argues this is safe because `events` is still small at this stage.
- **Why it matters:** The same migration pattern, copied later once `events` has grown
  much larger, would briefly lock the table; this is worth flagging now so a future
  index addition on `events` chooses `CONCURRENTLY` deliberately rather than by habit
  from copying this migration.
- **Proposed default:** Accept the non-concurrent build for this migration
  specifically (the table is small); require `CONCURRENTLY` for any future index
  added to `events` once real data volume exists.
- **Blocking:** no
- **Status:** resolved (2026-09-23) in Phase 4 T4/T7 (Q-T7-b): the index exists and
  serves `is_source_cited_by_public_event` (see Q178); this entry records the
  build-method trade-off for future index additions, which stays open.

## Q211 — An unrecognised quality code maps to `suspect`, not to a rejected row

- **Question:** `LocalCsvTemperatureAdapter`'s quality mapping treats any code outside
  `good`/`suspect`/`missing`/`estimated` as `suspect`, with a warning, rather than
  rejecting the row outright. Should every future pipeline follow this same
  fail-open-with-a-warning default, or should an unrecognised code instead reject the
  row (fail closed) until a curator maps it explicitly?
- **Why it matters:** Fail-open keeps more of a source's data usable when its quality
  vocabulary has an undocumented or new code, at the cost of silently downgrading
  trust in a value that might actually have been `good`; fail-closed is safer but
  could reject a large share of a source's rows on day one if its documentation is
  incomplete.
- **Proposed default:** Keep fail-open-with-a-warning as the shared default for every
  future pipeline (mirroring `deduplicate`'s "first wins unless the source says
  otherwise" stance, Q198); a pipeline that knows its source's full vocabulary can
  reject unknown codes explicitly instead.
- **Blocking:** no
- **Status:** open (raised from the Phase 4 adapter report, T5b)
