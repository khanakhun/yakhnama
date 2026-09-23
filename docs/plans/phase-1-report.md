# Phase 1 report — Shared kernel and reference data

## Summary

The backend now has its framework-free shared kernel (errors, UUIDv7 ids, clock, value objects,
Specification, domain events, unit of work, cursors), a platform layer with an async SQLAlchemy
unit of work that writes domain events to a transactional outbox, an outbox relay, telemetry that
never exports personal data, a composition root and `/health/ready`. Alembic manages the schema
with four migrations. Three bounded contexts exist end to end: `geography` (hierarchy, places,
multilingual names with trigram search proven in Latin and Perso-Arabic script), `hazards`
(IRDR-aligned taxonomy and per-hazard attribute schemas) and `impacts` (metric registry).
Versioned YAML reference data loads idempotently through `poetry run poe seed`. 1,612 tests run
in the gate at 99.92 % branch coverage, with 100 % on every domain and application layer.

## Delivered

| Task | Subagent | Files | Tests added | Status |
|------|----------|-------|-------------|--------|
| T1 Dependencies, module skeletons, layer contracts, per-layer coverage gate | lead | `pyproject.toml`, module packages | n/a | done |
| T2 Shared kernel + ADR 0013 | architect (Opus) | `src/yakhnama/shared_kernel/`, `docs/adr/0013` | 215 | done |
| T3 Platform: db, uow, outbox, telemetry, container, health ready | architect (Opus) | `src/yakhnama/platform/`, `main.py` | 225 unit + 17 integration | done |
| T4 Alembic and migration 0001 | persistence-engineer (Opus) | `alembic.ini`, `migrations/` | 6 integration | done |
| T5 geography domain | domain-modeler (Opus) | `modules/geography/domain/` | 245 | done |
| T6 hazards domain | domain-modeler (Opus) | `modules/hazards/domain/` | 210 | done |
| T7 impacts domain | domain-modeler (Opus) | `modules/impacts/domain/` | 138 | done |
| T8 Reference data | domain-modeler (Opus) | `data/reference/` | 77 | done |
| T9 Application layers, seed handler, fakes | application-engineer (Opus) | `modules/*/application/`, `seed/application.py`, `tests/fakes/` | 675 | done |
| T10 Persistence, migrations 0002–0004 | persistence-engineer (Opus) | `modules/*/infrastructure/`, `migrations/versions/` | 133 integration | done |
| T11 Seed CLI, container bindings, structural tests | architect (Opus) | `seed/cli.py`, `platform/container.py` | 40 + 2 integration | done |
| T12 Fakes and factories | test-engineer (Opus) | `tests/fakes/`, `tests/factories/` | 82 | done |
| T13 Docs consolidation | docs-writer (Sonnet) | `docs/`, `CHANGELOG.md`, `README.md` | n/a | done |
| T14 Reviews | reviewers (Opus) | none | n/a | see Quality gate |
| T15 Gate, commits, report | lead | 15 commits, this file | n/a | done |

## Quality gate

- `poetry run poe check`: **PASS**
  ```
  ruff format --check / ruff check      All checks passed
  mypy --strict                          Success: no issues found in 253 source files
  lint-imports                           Contracts: 8 kept, 0 broken
  pytest (unit, api, architecture,
          integration on real PostGIS)   1612 passed, coverage 99.92 % (branch on)
  cov-layers                             domain 100 %, application 100 %
  diff-cover vs main                     99 % on changed lines
  gitleaks git                           no leaks found
  pip-audit --strict                     No known vulnerabilities found
  ```
- Coverage: overall 99.92 %, domain 100 %, application 100 %, diff 99 %. Two uncovered lines are
  the non-unique `IntegrityError` re-raise branches in two repositories, unreachable with the
  current schemas.
- `poetry run poe up && poe migrate && poe seed`: migrations 0001–0004 apply; first seed creates
  10 hazard types, 10 impact metrics, 15 places; every subsequent run reports all unchanged with
  no new outbox rows. `alembic check` reports no drift at head.
- CI: the `migrations` and `test-integration` jobs are now active by their file gates; `cov`
  includes the integration tier (Docker on the runner).
- Reviews: shared kernel standards (1 cycle, 9 items fixed); platform security (CHANGES REQUIRED
  → 3 items fixed: span exporter scrubbing, outbox `last_error` type-only, container start);
  final standards review of T9–T13 (CHANGES REQUIRED → 1 must-fix and 6 should-fix items fixed:
  impacts self-replacement now a domain error, loader no longer overwrites stored centroids, C
  collation for code paging, search superset test, dictionary rows, reader tests moved to the
  integration tier; nits recorded as TD-18); final security review of T10–T11 (CHANGES REQUIRED →
  2 low items fixed: empty-after-folding search text rejected, CLI subprocess environment
  filtered). No fourth cycle was run; the fixes were verified by re-running the full gate.

## ADRs added

0013 in-house UUIDv7 generator (proposed). ADR 0012 gained the `PreconditionFailedError`
proposal.

## Deviations from the plan (and why)

1. `poe cov` now runs every tier including integration tests on real PostGIS: database
   adapters cannot be covered any other way without mocking the database, which is forbidden.
   `test-integration` remains a standalone task; `check` no longer runs it twice.
2. Compound units (`cubic_metre_per_second`, `metre_per_second`) were added to the kernel
   mid-phase at the hazards domain's request, so rates are unit-checked `Measurement`s.
3. The Shina language code is `scl` (ISO 639-3), not `shi` as the plan said.
4. `poe migrate` and `poe seed` tasks were defined in T1 and became live in T4 and T11.
5. `is_unique_violation` lives in `platform/db.py` instead of being copied per repository.
6. `LoadReport`, `SkippedChange` and the placeholder `AdminOnlyPolicy` are duplicated per module
   to keep modules independent until identity (Phase 2) supplies real policies.
7. Commit order in history: the platform commit precedes the kernel commit because the pre-commit
   hook stashes changes outside a path-limited commit; every commit on the branch is
   hook-verified, but intermediate commits before `fb2f6cd` do not type-check standalone.
8. No public read endpoints yet (plan Q6 default kept); they arrive with the API foundations.

## Open questions (blocking first)

Recorded in `docs/open-questions.md` Q12–Q49; none blocks Phase 2. Highlights needing the
maintainer: boundary data source (Q1) before real geometry; Sendai numbering (Q38); IRDR
placement of local hazards (Q24); monetary metrics need an ADR before seeding (Q34, blocks only
that); ADR 0010–0013 acceptances.

## Risks and technical debt (each with an issue reference)

| Ref | Item |
|-----|------|
| TD-11 | Lead-owned skills `add-hazard-type`, `add-impact-metric`, `new-module`, `add-entity` lag the real names (`reference.py`, `DEFAULT_REGISTRY`, `AggregateChange`); update before Phase 3 adds entities. |
| TD-12 | Outbox relay: dead-letter, back-off, retention and a lease-based claim (attempts incremented before dispatch) are Phase 3 with Taskiq. |
| TD-13 | OTLP exporter package not installed; `otel_exporter=otlp` fails fast with instructions. |
| TD-14 | `pg_trgm` on Arabic script depends on a UTF-8 database locale; document for production. |
| TD-15 | Cursor `backward` direction is accepted but treated as forward. |
| TD-16 | `.env` on a developer machine must set `YAKHNAMA_DATABASE_URL` to the compose port when a local PostgreSQL occupies 5432; README quick start says so. |
| TD-17 | Cost: this phase used roughly 25 Opus agent runs; Phase 2 batches reviews per wave. |
| TD-18 | Review nits deferred: `ref`/`char`/`reference_dir` abbreviations, `_LoadRun` pattern label, single-letter loop variables in tests, discovery rule of the repository structural test, no CHECK constraints in 0002–0004, `application/specifications.py` and `authorisation.py` not yet listed in AGENTS.md §2, ORM row models labelled `Adapter`. |
| TD-19 | The seed's allow-list policy only ever contains the actor the seed generates; real authorisation arrives with identity in Phase 2. |

## Proposed next phase (brief)

Phase 2 as planned in `docs/plans/phase-2.md`: identity module, JWT/JWKS validation, idempotency,
ETags, rate limiting, security headers and CORS, production settings validator, first public
read endpoints, Scalar docs and the OpenAPI snapshot contract.
