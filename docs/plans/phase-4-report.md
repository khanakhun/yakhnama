# Phase 4 report — Data exchange and ingestion framework

## Summary

Researchers can now get data out and curators can get history and external datasets in. The
`exchange` module runs exports of verified events, impact claims and (for moderators) rounded
reports as asynchronous jobs in JSON, GeoJSON, CSV and GeoParquet, each with a metadata sidecar
carrying the proposed licence, the filters, the schema version, a citation and a checksum
measured from the bytes actually stored. Imports of the historical backfill (CSV or GeoJSON with
a documented column contract) run with `dry_run`, produce a row-level validation report, create
nothing when any row is blocking, and otherwise create events and claims in atomic batches under
a `dataset` lineage source; imported events are not public until verified. The `ingestion` module
holds the dataset catalog with required licences, versions, runs with lineage counts and
checksums, a Template Method pipeline whose validation collector never crashes on row data, a
narrow observation table keyed by time first with no foreign keys so it can become a TimescaleDB
hypertable without API change, a STAC-aligned raster catalog, and one reference adapter reading a
synthetic local CSV temperature fixture. The deferred security items from Phases 2 and 3 landed.
5,919 tests run in the gate at 99.73 % branch coverage with 100 % on every domain and
application layer; the Phase 4 gate flow runs over HTTP on real PostGIS and MinIO.

## Delivered

| Task | Subagent | Files | Tests added | Status |
|------|----------|-------|-------------|--------|
| T1 Plan, pyarrow, skeletons, contracts | lead | `pyproject.toml`, skeletons | n/a | done |
| T2a exchange domain; T2b ingestion domain | domain-modeler ×2 (Opus) | `modules/{exchange,ingestion}/domain/` | 376 + 366 | done |
| T3a exchange application (+ `CreateHistoricalEvent`); T3b ingestion application and pipeline | application-engineer ×2 (Opus) | `modules/*/application/`, facades, fakes | 632 + 366 | done |
| T4 Persistence, migrations 0015–0017 (+ GIN index on `events.source_ids`) | persistence-engineer (Opus) | `modules/*/infrastructure/`, `migrations/` | 111 integration | done |
| T5a Format adapters and S3 artifact store; T5b reference temperature adapter, fixture, catalog entry, `data-sources.md` | integration-engineer ×2 (Opus) | `modules/*/infrastructure/adapters/`, `data/` | 465 + 20; 505 | done |
| T6 API: 18 operations, snapshot | api-engineer (Opus) | `modules/*/api/`, `tests/api/`, `tests/contract/` | 90 | done |
| T7 Deferred security items (JWKS streaming cap, token `typ`, IPv6 /64 keys, storage key guard, citation checker, outbox payload structural test) | architect (Opus) | `platform/`, `modules/events/` | 60 | done |
| Wiring, seed of the dataset catalog, gate flow on real services | architect (Opus) | `platform/container.py`, `platform/wiring/`, `seed/` | 62 integration | done |
| T8 Docs | docs-writer (Sonnet) | `docs/architecture/{exchange,ingestion}.md`, Q180–Q211 | n/a | done |
| T9 Security review, gate, report | reviewer (Opus), lead | | | see below |

## Quality gate

- `poetry run poe check`: **PASS**
  ```
  ruff format --check / ruff check      All checks passed
  mypy --strict                          Success: no issues found in 899 source files
  lint-imports                           Contracts: 8 kept, 0 broken
  pytest (all tiers, real PostGIS,
          MinIO and Redis)               5919 passed, coverage 99.73 % (branch on)
  cov-layers                             domain 100 %, application 100 %
  contract                               2 passed
  diff-cover vs main                     99 % on changed lines
  gitleaks git                           no leaks found
  pip-audit --strict                     No known vulnerabilities found
  ```
- Gate flow (`tests/integration/wiring/test_exchange_ingestion_flow_postgis.py`): fixture ingestion
  → 144 observations in kelvin with one `missing` and one `suspect`, run recorded with the file's
  checksum; GeoJSON export of the verified event → download URL serving a `FeatureCollection` and
  a sidecar whose checksum equals the artifact; dry-run import reports exactly one issue on row 2
  (`hazard_type`, error) without quoting the value and creates nothing; the corrected real import
  creates one event with its claim and a `dataset` lineage source, not public until verified.
- Reviews: security cycle 1 (mandatory) CHANGES REQUIRED with 2 items (medium: export download
  links did not re-check the reader's current rights, so a demoted moderator kept access; low:
  three backfill parser messages echoed cell values) → both fixed (export jobs record a
  `visibility` set at request time, migration 0018; links are issued only if the reader may export
  that dataset now and, for moderation-visibility jobs, can moderate now; issue messages are built
  from fixed sentences per error type with a hypothesis test that no cell marker leaks). The
  reviewer's "before production" list is carried into the proposed next steps. No second cycle
  was run; the fixes were verified by the full gate and the reviewer's exact reproduction
  scenarios as API tests.

## ADRs added

None new this phase (0018 safe text was Phase 3). ADRs 0010–0013 and 0018 remain proposed and
need the maintainer's acceptance.

## Deviations from the plan (and why)

1. Three read routes were not built for lack of read queries: `GET /moderation/imports` (list),
   `GET /raster-assets/{id}`, `GET /datasets/{code}/versions` (versions are in the dataset detail,
   capped at 20). Run detail is `GET /ingestion-runs/{run_id}`.
2. Imports accept an uploaded artifact only (no inline CSV); the upload grant is a presigned PUT,
   which cannot cap size, so the size and checksum are enforced when the file is read.
3. Claims exported in the geo formats carry a null geometry rather than being refused.
4. `expected_version` on events and verification commands stays open (`If-Match` is checked in
   the router before the unit of work).
5. Account-creation limits (Q176) stay open for the maintainer.
6. The lead-owned skills were not regenerated this phase (TD-36 carried; the agents' corrections
   are recorded in the phase reports and in `docs/open-questions.md`).

## Open questions (blocking first)

Before production or public launch (not for the next step): ClamAV client unverified (Q116),
PDF/MP4 metadata (Q117), presigned PUT size cap (Q-exchange), GeoJSON import parsed whole with only
the declared-size cap. Everything else is Q180–Q211 with proposed defaults in use.

## Risks and technical debt (each with an issue reference)

| Ref | Item |
|-----|------|
| TD-40 | Jobs and runs stuck in `queued`/`running` after a broker or worker failure need a sweeper. |
| TD-41 | Export reads each event's detail after listing summaries; add an export-oriented read. |
| TD-42 | Outbox payload offenders in `PENDING_REMOVAL` (Q179) to be dropped module by module. |
| TD-43 | Skills regeneration from the real code (carried). |
| TD-44 | No TimescaleDB image in tests; hypertable readiness is checked structurally only. |

## Readiness for the analytics/ML repository

The analytics and ML repository consumes this backend through its APIs and exports. What is ready:

- **Stable identifiers.** Every record carries a UUIDv7 id (ADR 0006, 0013); codes for hazard
  types, impact metrics, places and datasets are stable and never reused.
- **Units and time.** Every timestamp is UTC with a `DatePrecision`; measurements are SI with
  explicit units (`Measurement`); observations store kelvin, metres, cubic metres per second.
- **Provenance.** Every event, claim, report, media asset, observation and imported row links to a
  `Source` or a dataset version; ingestion runs record input checksums and counts; imports create
  a `dataset` lineage source; audit entries hold digests.
- **Verified data only, by default.** Anonymous reads and public exports include only published
  and verified events with best figures derived by the documented policy
  (`docs/architecture/best-figure.md`); claims are append-only with corrections chained.
- **Bulk access.** `POST /exports` yields JSON, GeoJSON, CSV or GeoParquet artifacts with a
  sidecar (licence status, filters, schema version `1.0`, citation, checksum); GeoParquet uses WKB
  geometry with GeoParquet 1.1 metadata readable by pyarrow and geopandas.
- **Time series.** `GET /observations` pages by `(observed_at, site_ref)` with half-open windows;
  the table is designed for a TimescaleDB hypertable later without API change.
- **Rasters.** `GET /raster-assets` returns STAC 1.1-shaped items with object-store hrefs; the
  backend never stores raster bytes in PostgreSQL.
- **Contract.** `tests/contract/openapi.json` is the committed API contract; breaking changes
  require `/api/v2`.

What the analytics repository should not expect yet: monetary metrics (no ADR), live external
sources (only the fixture adapter exists), report-level exports for non-moderators, and the three
read routes listed under deviations.

## Proposed next steps (beyond this run)

Maintainer decisions on ADRs 0010–0013 and 0018 and on Q176; a Phase 5 for the production
hardening list from the security reviews (ClamAV verification, media metadata for video and PDF,
presigned POST uploads, sweepers, outbox field removals), the skills regeneration, and the first
live source adapter under a confirmed licence.
