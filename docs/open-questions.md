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
