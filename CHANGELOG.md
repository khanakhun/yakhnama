# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) via
[PEP 440](https://peps.python.org/pep-0440/). `commitizen` maintains this file from
Conventional Commits going forward (`cz bump` updates it on release); entries below Phase 0
were written by hand because there is no release yet.

## [Unreleased]

### Added

- Phase 0 foundation: Poetry 2.x project on Python ≥ 3.13 with the full Ruff, mypy strict,
  import-linter and pytest/coverage configuration.
- App factory (`yakhnama.main:create_app`) with structured logging and a `/health/live`
  liveness endpoint.
- Agent infrastructure: `AGENTS.md`, `CLAUDE.md`, subagent definitions and skills.
- Pre-commit hooks and the CI workflow (`ci.yml`).
- docker-compose services for local development: PostGIS, MinIO, Keycloak, Redis.
- Community documents: `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue and pull request templates, Dependabot configuration.
- Initial architecture decision records in `docs/adr/`.
- Documentation site scaffold (`mkdocs.yml`, `docs/`), including the architecture overview,
  data dictionary conventions and open questions log.

### Phase 1

- Shared kernel (`yakhnama.shared_kernel`): error hierarchy, `IdGenerator` port with an
  in-house RFC 9562 UUIDv7 generator (ADR 0013), `Clock` port, value objects
  (`Coordinates`, `BoundingBox`, `Measurement` with an SI unit registry,
  `DateWithPrecision`, `LanguageCode`, `LocalizedText`, `Confidence`), a `Specification`
  base with `and_`/`or_`/`not_`, `DomainEvent`, the `UnitOfWork` protocol, and opaque
  cursor pagination.
- Platform (`yakhnama.platform`): async SQLAlchemy engine and session factory,
  `SqlAlchemyUnitOfWork`, the transactional outbox (`OutboxWriter`, `OutboxRelay` with
  at-least-once delivery and `SELECT ... FOR UPDATE SKIP LOCKED` claiming), OpenTelemetry
  tracing with personal-data scrubbing on every span and exporter, `platform/container.py`
  as the infrastructure composition root, and a database-backed `/health/ready` probe.
- `geography` domain: `AdminLevel`, the `Place` aggregate and `PlaceName` value object,
  with invariants for name uniqueness and one preferred name per language.
- `hazards` domain: the `HazardType` taxonomy with an explicit lifecycle (create, retire,
  reactivate, relabel, reparent), the hazard attribute schema registry (Strategy +
  Registry) for GLOF, landslide, debris flow, cloudburst, flash flood, avalanche and
  glacier surge, and `GlacierRef` / `GlacialLakeRef` value objects.
- `impacts` domain: the `ImpactMetric` registry (count, measurement and monetary value
  kinds, categories, Sendai and DesInventar mapping fields, aggregation defaults).
- Application layers for `geography`, `hazards` and `impacts`: commands, queries,
  handlers, ports, DTOs and `public.py` facades, plus the idempotent
  `SeedReferenceDataHandler` orchestrating all three.
- Alembic async migration environment and migration `0001` (PostGIS/pg_trgm/unaccent
  extensions and the outbox table).
- Versioned reference data (`data/reference/hazard_types.yaml`, `impact_metrics.yaml`,
  `languages.yaml`, `admin_hierarchy_gb.yaml`) with schema and data versioning and a
  source-or-`proposed` provenance convention.
- Test infrastructure: `tests/factories/` (polyfactory), consolidated `tests/fakes/`, and
  shared PostGIS/MinIO testcontainer fixtures.
