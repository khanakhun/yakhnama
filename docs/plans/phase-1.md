# Phase 1 plan — Shared kernel and reference data

Status: **awaiting maintainer approval**
Branch: `phase/1-shared-kernel` (from `main` at the Phase 0 merge, PR #1)
Lead: Fable 5.1 (orchestration, integration glue). Implementers on Opus 5.5, `docs-writer` on Sonnet.

## 1. Goal

Build the framework-free building blocks every module depends on, the database and
transaction infrastructure around them, and the three reference-data modules (`geography`,
`hazards`, `impacts`) with versioned YAML seed data and an idempotent loader. At the end of
Phase 1 the database schema exists under Alembic, the outbox skeleton is in place, the
per-layer coverage gate is enforced, and Phase 2 can add identity and the public API on top.
No public endpoints beyond `/health/live` and `/health/ready` are added in this phase.

## 2. Carried-over decisions and assumptions

- Phase 0 approved by the merge of PR #1. ADR 0010 (licence), 0011 and 0012 (catalog rows,
  `PreconditionFailedError`) remain **proposed**; Phase 1 builds against them as written and
  flags any change in the report. Nothing in Phase 1 depends on the licence choice.
- UUIDv7 (ADR 0006 deferred the library choice): proposed default is an **in-house RFC 9562
  generator** behind the `IdGenerator` port, tested with hypothesis, with no new dependency;
  Python 3.14's `uuid.uuid7` is not used so both interpreters share one code path. Recorded as
  ADR 0013 (proposed) by the architect.
- Domain facts (hazard definitions, attribute fields, metric semantics, administrative names,
  local-language labels) are **never invented**: every seed entry is marked with its source or
  `status: proposed` and listed in `docs/open-questions.md`.
- Scalar API docs land in Phase 2 (recorded in the Phase 0 report).

## 3. Tasks

| # | Task | Owner | Owns (writes to) | Depends on | Acceptance criteria |
|---|------|-------|------------------|------------|---------------------|
| T1 | Dependencies and tooling: `poetry add` `pyyaml`, `types-pyyaml`, `types-shapely`, `testcontainers[postgres]`, `testcontainers[minio]`, `opentelemetry-instrumentation-sqlalchemy`; Poe tasks `migrate`, `seed`, `cov-layers` (95 % on `domain` and `application` via `coverage report --include`), `check` extended with `cov-layers`; `import-linter` contracts for the three modules and their layers; `.env.example` database settings | lead | `pyproject.toml`, `poetry.lock`, `.env.example` | approval | `poetry check --lock`; `poe check` still green before any module code lands |
| T2 | `shared_kernel`: `errors.py` (hierarchy incl. proposed `PreconditionFailedError`), `ids.py` (`IdGenerator` port, in-house UUIDv7, `EntityId` types), `clock.py` (`Clock` port, `SystemClock`), `value_objects.py` (`Coordinates` WGS84 with bounds, `BoundingBox`, `Measurement` with SI unit registry, `DateWithPrecision`, `LanguageCode` BCP 47 subset, `LocalizedText`, `Confidence`), `specification.py` (base plus `and_`/`or_`/`not_`), `events.py` (`DomainEvent` base, occurred-at from `Clock`), `uow.py` (protocol with `record_event`), `pagination.py` (opaque cursor encode/decode, `Page[T]`, `limit` ≤ 200); ADR 0013 (UUIDv7 in-house) | architect | `src/yakhnama/shared_kernel/**`, `tests/unit/shared_kernel/**`, `docs/adr/0013-*.md` | T1 | hypothesis property tests for every value object, the Specification laws (associativity, De Morgan) and the UUIDv7 monotonicity/timestamp; `shared_kernel` imports only stdlib, pydantic, geojson-pydantic; coverage ≥ 95 % |
| T3 | `platform`: `db.py` (async engine, session factory, naming conventions, PostGIS-aware metadata), `uow.py` (SQLAlchemy Unit of Work implementing the protocol, event collection), `outbox/` (table model, writer in the same transaction, relay skeleton with an in-process subscriber registry, no broker), `telemetry.py` (OpenTelemetry tracer provider, FastAPI and SQLAlchemy instrumentation, console/OTLP exporter from settings), `container.py` (composition root skeleton binding `Clock`, `IdGenerator`, session factory), `settings.py` extension (`database_url`, `otel_*`, `seed_*`), `/health/ready` (database round-trip; object storage check deferred to Phase 3 when the storage port exists, stated in the docstring) | architect | `src/yakhnama/platform/**`, `src/yakhnama/main.py` (wiring only), `tests/unit/platform/**`, `tests/integration/platform/**`, `tests/api/test_health.py` | T2 | UoW commit/rollback/event-collection tested against real PostGIS; outbox writer tested in the same transaction as an aggregate write; `/health/ready` 200 with DB up and 503 with DB down (integration) |
| T4 | Alembic: `alembic.ini`, `migrations/env.py` (async, PostGIS types, naming conventions, `compare_type`), migration `0001_extensions_and_outbox` (`postgis`, `pg_trgm`, `unaccent`, outbox table), migration test harness (`upgrade head` → `downgrade base` → `upgrade head` → `alembic check`) on testcontainers | persistence-engineer | `alembic.ini`, `migrations/**`, `tests/integration/migrations/**` | T3 | `poe migrate` works against compose PostGIS; CI `migrations` job turns on (its file gate) and passes; migrations excluded from Ruff formatting only in `versions/` as configured |
| T5 | `geography` domain: `AdminLevel` (country → province/region → district → tehsil → union council → village), `Place` aggregate (id, level, parent, geometry as `geojson-pydantic` Point/Polygon/MultiPolygon, source reference), `PlaceName` (name, `LanguageCode`, script, `is_preferred`, transliteration flag), `PlaceNames` invariants (one preferred per language), errors, factories, data dictionary | domain-modeler | `src/yakhnama/modules/geography/domain/**`, `tests/unit/modules/geography/domain/**`, `docs/data-dictionary/geography.md` | T2 | every entity frozen with methods returning new instances; hypothesis tests on name invariants and level ordering; open questions for boundary source and script codes |
| T6 | `hazards` domain: `HazardType` aggregate (code, parent, IRDR family/main event/peril alignment, labels `LocalizedText`, status active/retired with reason, retired codes never reused), taxonomy tree operations, `HazardAttributes` discriminated union registry (Strategy + Registry) with **proposed** attribute schemas for GLOF, landslide, debris flow, cloudburst/extreme rainfall, flash flood, avalanche, glacier surge, each field sourced or marked proposed; `GlacierRef` (GLIMS id) and `GlacialLakeRef` (ICIMOD inventory id) value objects; data dictionary | domain-modeler | `src/yakhnama/modules/hazards/domain/**`, `tests/unit/modules/hazards/domain/**`, `docs/data-dictionary/hazards.md` | T2 | registry rejects unknown attribute types and validates JSON payloads; retire/reactivate rules tested; every schema field documented with unit and source |
| T7 | `impacts` domain (registry only): `ImpactMetric` (code, SI unit, category, Sendai indicator, DesInventar mapping, status), unit registry aligned with `Measurement`, errors; data dictionary | domain-modeler | `src/yakhnama/modules/impacts/domain/**`, `tests/unit/modules/impacts/domain/**`, `docs/data-dictionary/impacts.md` | T2 | metric codes stable; units validated against the shared unit registry |
| T8 | Reference data: `data/reference/hazard_types.yaml`, `impact_metrics.yaml`, `languages.yaml`, `admin_hierarchy_gb.yaml` (a small **fixture**: Gilgit-Baltistan region and its districts, geometry omitted or placeholder bounding boxes, `status: fixture`), each file versioned (`schema_version`, `data_version`, `source`, `licence`); YAML schema models in each module's domain; loader parsers | domain-modeler | `data/reference/**`, `src/yakhnama/modules/*/domain/reference.py`, `tests/unit/data/**` | T5–T7 | YAML round-trips through the Pydantic models; every entry carries a source or `proposed`; no local-language label without a source (English only otherwise, recorded as open question) |
| T9 | Application layers: `geography` (ports `PlaceRepository`, `PlaceQueryService`, `BoundaryLoader`; queries `SearchPlaces(q, language, level, limit, cursor)`, `GetPlace`; command `LoadReferencePlaces`), `hazards` (ports; queries `ListHazardTypes`, `GetHazardType`; commands `LoadReferenceHazardTypes`, `RetireHazardType`), `impacts` (ports; `ListImpactMetrics`, `LoadReferenceImpactMetrics`); `SeedReferenceData` orchestrating handler (idempotent: upsert by code/version, never deletes, reports counts); `public.py` facades; fakes | application-engineer | `src/yakhnama/modules/{geography,hazards,impacts}/application/**`, `public.py`, `tests/unit/modules/*/application/**`, `tests/fakes/**` | T5–T8 | handlers tested only with fakes; idempotency proven by running the seed twice and asserting no change; policies deny by default (an `is_admin` placeholder policy until identity arrives in Phase 2, stated in docstrings) |
| T10 | Persistence: ORM models, mappers, repositories and query services for the three modules; `pg_trgm` + `unaccent` place search with per-language ranking; GiST index on place geometry; migrations `0002_geography`, `0003_hazards`, `0004_impacts` (one at a time, linear); integration tests on testcontainers including search relevance cases in Latin and Perso-Arabic script | persistence-engineer | `src/yakhnama/modules/*/infrastructure/{orm,repositories,mappers,queries}.py`, `migrations/versions/**`, `tests/integration/modules/**` | T4, T9 | every repository implements its port (structural test); upgrade/downgrade/check green; search returns `Hunza` for `hunza`, `Hunzā`, and the Urdu spelling when seeded |
| T11 | Seed CLI and wiring: `python -m yakhnama.seed` (Composition Root use), `poe seed` task, `container.py` bindings for the three modules, `main.py` lifespan (engine start/stop) | lead (glue) + architect | `src/yakhnama/seed/__init__.py`, `__main__.py`, `platform/container.py`, `main.py` | T9, T10 | `poe up && poe migrate && poe seed` succeeds twice with identical row counts |
| T12 | Test infrastructure: `tests/factories/` (polyfactory for every entity and value object, UTC datetimes), `tests/fakes/` consolidation, `tests/conftest.py` PostGIS and MinIO session fixtures via testcontainers with reuse, per-layer coverage audit and gap filling | test-engineer | `tests/factories/**`, `tests/fakes/**`, `tests/conftest.py`, `tests/integration/conftest.py` | T2, T9 | `poe cov-layers` ≥ 95 % on `domain` and `application`; a named list of any public function still untested |
| T13 | Docs: data-dictionary consistency, `docs/architecture/README.md` module map update, `docs/open-questions.md` entries from T5–T8, CHANGELOG | docs-writer | `docs/**` (not ADRs), `CHANGELOG.md` | T8 | `mkdocs build --strict` green |
| T14 | Reviews: `standards-reviewer` on every task; `security-reviewer` on T3 (database settings, outbox, telemetry export), T4 (migrations), T10 (SQL construction, search input handling) | reviewers | none | each task | APPROVE or fixes within ≤ 3 cycles |
| T15 | Integration and gate: `poe check` (now incl. `cov-layers`, `migrations` via CI), compose + migrate + seed run, commits per accepted task in the maintainer's name, phase report | lead | commits, `docs/plans/phase-1-report.md` | all | §6 |

## 4. Parallelism

- **Serial spine:** T1 → T2 → T3 → T4. `shared_kernel`, `platform`, `pyproject.toml` and
  `migrations/` are single-writer.
- **Wave A (after T2, disjoint domain paths):** T5, T6, T7 in parallel (three domain-modeler
  agents), then T8.
- **Wave B (after T8 and T3):** T9 (application, fakes) and T12 (factories, fixtures) in parallel;
  T13 docs in parallel.
- **Wave C (after T4 and T9):** T10 persistence, migrations one at a time.
- T11 after T10; T14 continuously; T15 last.

## 5. Gate

- `poetry run poe check` green, now including `cov-layers` (≥ 95 % `domain` and `application`)
  and `test-integration` against real PostGIS via testcontainers.
- CI `migrations` job active and green; `test-integration` job green on 3.13 and 3.14.
- `poe up && poe migrate && poe seed` twice → identical counts, no errors.
- `/health/ready` returns 200 against the compose stack.
- Reviews approved; commits per accepted task; `docs/plans/phase-1-report.md` delivered.

## 6. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Domain facts for hazard attribute schemas and metric semantics are not available from a cited source | high | Every field marked `proposed` with rationale; open question per schema; schemas kept minimal (dates, magnitude class, trigger, volume where measurable) so wrong guesses are cheap to change before Phase 3 uses them |
| `unaccent` does nothing for Perso-Arabic script; trigram similarity across scripts is weak | medium | Search matches per language: Latin via `unaccent` + trigram, other scripts via trigram on the stored form; transliterations stored as extra `PlaceName` rows; documented limits |
| testcontainers on WSL2 startup time (PostGIS ~10 s per session) | medium | Session-scoped container, `reuse` enabled locally, one container for all integration tests |
| Async SQLAlchemy + GeoAlchemy2 + Alembic autogenerate quirks (geometry columns, indexes) | medium | Hand-reviewed migrations per the `write-migration` skill; `alembic check` in the loop |
| In-house UUIDv7 correctness | low | RFC 9562 test vectors plus hypothesis; version and variant bits asserted |
| Coverage gate per layer on tiny modules hides weak tests | medium | `test-engineer` audit lists untested public functions by name |

## 7. Open questions for the maintainer

| # | Question | Why it matters | Proposed default | Blocking |
|---|----------|----------------|------------------|----------|
| Q1 | Confirm the in-house UUIDv7 generator (no library) | Every id in the system | In-house, ADR 0013 | no |
| Q2 | Hazard attribute schema fields: do you have a reference (ICIMOD GLOF reports, NDMA formats) I should align to? | Avoids inventing science | Minimal proposed schemas, every field marked proposed | no |
| Q3 | Impact metric list: start from the Sendai Framework global indicators (A-1 deaths, A-2 missing, B-1 affected, C-x economic loss, D-x infrastructure) plus DesInventar basics? | Registry content | Yes, marked proposed with indicator codes | no |
| Q4 | Administrative hierarchy fixture: Gilgit-Baltistan's 3 divisions and 10 districts (current official count) with no geometry until the boundary source is chosen | Places are needed for tests and seeds | Fixture with `status: fixture` and no polygons | no |
| Q5 | Language and script codes: `en`, `ur` (Arabic script), `shi`/`bsk`/`bft`/`wbl`/`khw` (ISO 639-3) with script tags `Latn`/`Arab` | Stable keys for names | As listed; no local labels shipped without a source | no |
| Q6 | Should Phase 1 expose read endpoints for hazard types, metrics and places now (anonymous public reads need no auth), or wait for Phase 2's API foundations as the specification orders? | Earlier visible progress vs. contract churn | Wait for Phase 2 (idempotency, ETag and error mapping are built there) | no |

Reply "approved" (with any answers) and I will start with T1 and the serial spine.
