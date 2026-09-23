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
