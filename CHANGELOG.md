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

### Phase 2

- `identity` domain: `User` (mirrored from the OIDC `sub`/`iss` at first sight),
  `Organization`, `Membership`, the `Role` enum, the `Actor` value object, and the
  composable Specification-style policies every module authorises through
  (`IsAdmin`, `IsModerator`, `IsMemberOf`, `IsOrgAdminOf`, `IsSelf`,
  `CanManageReferenceData`, `CanModerate`, `CanManageOrganization`,
  `CanReadVerifiedData`).
- `identity` application and persistence: commands (`EnsureUserFromPrincipal`,
  `CreateOrganization`, `AddMember`, `ChangeMemberRole`, `RemoveMember`, `GrantRole`,
  `RevokeRole`, `SuspendUser`, `ReinstateUser`, `RenameSelf`, `RenameOrganization`),
  queries (`GetMe`, `GetOrganization`, `ListOrganizationMembers`), a `public.py` facade,
  ORM, repositories and migration `0005_identity`; the placeholder `AdminOnlyPolicy`
  duplicated in `geography`, `hazards` and `impacts` is replaced by identity policies
  through the facade.
- Platform authentication (`platform/auth/`): an in-house `HttpJwksClient` with a TTL
  cache and rate-limited refetch on key rotation, a PyJWT-based `TokenValidator` (`RS256`/
  `ES256` only, exact `iss`/`aud` checks, clock-skew leeway from the injected `Clock`),
  the `Principal` DTO and `PrincipalResolutionMiddleware` resolving the bearer token once
  per request (ADR 0015).
- Platform idempotency (`platform/idempotency/`): a PostgreSQL-backed `IdempotencyStore`
  (reservation-first, migration `0006_idempotency_keys`) and `IdempotencyMiddleware`
  replaying the stored response of a repeated `Idempotency-Key` on an authenticated
  creating `POST`, with a 72-hour default retention (ADR 0016).
- Platform concurrency (`platform/etag.py`): strong `ETag`s from an aggregate's `version`
  and `If-Match` checks (`PreconditionFailedError` 412, `PreconditionRequiredError` 428).
- Platform rate limiting (`platform/ratelimit/`): a `RateLimiter` port with an in-memory
  token-bucket adapter and a Redis fixed-window adapter, per-principal and per-hashed-IP
  keys, `429` Problem Details with `Retry-After` (ADR 0017).
- Platform HTTP hardening (`platform/http.py`): pure-ASGI request id, request logging (no
  personal data), security headers and CSP, `TrustedHostMiddleware`, CORS options and
  request body guard (size limit and NUL-character rejection) middlewares; extended
  exception handlers in `main.py` for `RequestValidationError`, 404/405 and a catch-all
  500 that adds its own request id and security headers; a production settings validator
  (`platform/settings.py`) that collects every unsafe-for-production setting into one
  error.
- Self-hosted Scalar API reference at `/api/v1/docs`, gated by `docs_enabled`, replacing
  Swagger UI and ReDoc, with a page-specific Content-Security-Policy computed from its own
  rendered HTML (ADR 0014).
- First public read endpoints: `GET /hazard-types`, `/hazard-types/{code}`,
  `/impact-metrics`, `/impact-metrics/{code}`, `/places` (with GeoJSON content
  negotiation), `/places/{place_id}`; the identity endpoints `GET`/`PATCH /me`,
  `POST`/`GET`/`PATCH /organizations[/{id}]`, the organisation member endpoints, the
  administrator-only user-role and suspension endpoints, and the `/moderation` router
  scaffold (`GET /moderation/ping`).
- The committed OpenAPI snapshot (`tests/contract/openapi.json`) and its contract test,
  plus the `openapi-snapshot` and `contract` Poe tasks.
- Local Keycloak development realm (`docker/keycloak/yakhnama-realm.json`): the
  `yakhnama-api` and `yakhnama-dev-cli` clients, the platform roles, and two demo users;
  `docs/architecture/auth.md` documents the token flow, the realm and the backend's
  validation rules.
- ADRs 0014 (Scalar API reference), 0015 (JWT validation with PyJWT and a cached JWKS),
  0016 (idempotency keys in PostgreSQL) and 0017 (rate limiting behind a port).
- `docs/architecture/api.md`: the `/api/v1` conventions (authentication, Problem Details,
  pagination, idempotency, `ETag`/`If-Match`, content negotiation, rate limiting, the
  route table and the OpenAPI workflow).

### Phase 3

- `provenance` domain, application, persistence and API: `Source` (citizen, organisation,
  government, news, satellite, research, dataset), immutable once any fact cites it, a
  `Licence` value object, and `GET`/`POST /sources[/{id}]`.
- `audit` domain, application and persistence: the append-only `AuditEntry` (ids, codes and
  SHA-256 digests only, never free text), written after commit by an outbox subscriber
  keyed by `event_id`; a `BEFORE UPDATE OR DELETE` database trigger backs the domain's
  immutability with a database-level guarantee, and `recorded_at` is a database-default
  column, both added in migration `0009_audit`.
- `reports` domain, application, persistence and API: `Report` (reporter, `observed_at` with
  precision, private exact GPS position, description, hazard-type guess), revisions
  (`ReportRevised`/`ReportSuperseded`), withdrawal, and the triage Chain of Responsibility
  (EXIF plausibility, duplicate suspicion, a PII scrub, a spam check) that only suggests;
  `POST /reports` idempotent on the client's own `client_report_id` as well as on
  `Idempotency-Key`; `GET /reports[/{id}]` with GeoJSON negotiation and rounded public
  positions (`shared_kernel/privacy.py`'s `PublicCoordinatePolicy`).
- `media` domain, application, persistence, adapters and API: `MediaAsset` (private
  original, EXIF-stripped public copy, SHA-256 deduplication per owner, malware-scanner
  verdict, moderation status and sensitivity flag); an `aiobotocore` S3/MinIO storage
  adapter (presigned `PUT`/`GET`), a Pillow-based EXIF reader and metadata stripper, a
  `filetype` magic-byte MIME sniffer, and the `MalwareScanner` port with a `NoOpScanner`
  (development) and a `ClamAvScanner` (unverified against a real daemon, see
  `docs/open-questions.md` Q116); presigned-upload and moderation routes under `/media` and
  `/moderation/media`.
- `events` domain, application, persistence and API: `Event` (hazard-specific attributes
  validated against the `hazards` registry, geometry or point, affected places, report
  links, status), `EventRelation` (`triggered_by`/`part_of`/`same_as`), and
  `EventFactory.from_reports` deriving an event's period and centroid from linked reports'
  rounded public points; `GET /events[/{id}][/timeline]` (anonymous, `published` and
  `verified` only) and the `/moderation/events` write routes.
- `verification` domain, application, persistence and API: `VerificationCase` per report,
  event or claim with the `AGENTS.md` §6.3 transition table (a reason required except for
  `submitted`; only a human reaches `verified`), and the `/moderation/verification-cases`
  and `/moderation/verification/{case_id}/...` routes.
- `impacts` extension (domain, application, persistence and API): the append-only
  `ImpactClaim` (correction supersedes and retracts in one unit of work),
  `InfrastructureAsset`, `DamageRecord`, and the documented `BestFigurePolicy` (`sum`,
  `max` or `latest` per metric, source-rank tie-breaking, minimum contributing
  confidence — `docs/architecture/best-figure.md`); `GET /events/{id}/impacts`,
  `GET /infrastructure-assets/{id}` and the `/moderation` write routes for claims, assets
  and damage.
- Platform task queue (`platform/tasks/`): the `TaskQueue` port (ADR 0008) bound to a
  Taskiq adapter with a Redis broker in production and an in-memory fake in tests;
  `TaskHandlerRegistry` binding task names to handlers; `reports.run_triage` and
  `media.scan` as one-off tasks, `outbox.relay_once`, `outbox.purge_published` and
  `idempotency.purge_expired` as periodic tasks scheduled via Taskiq `schedule` labels; the
  `poe worker` and `poe scheduler` tasks.
- Platform outbox: lease-based claiming (`SELECT ... FOR UPDATE SKIP LOCKED` plus
  `leased_until`), dead-lettering after `max_attempts`, and `purge_published` retention,
  extending the Phase 1 transactional outbox (`platform/outbox/relay.py`).
- Shared kernel: the `SafeText` free-text type (surrogates, control characters and
  bidirectional overrides refused), the `public_coordinate_decimals` rounding helper
  (`shared_kernel/privacy.py`), and the `TaskQueue` port.
- Migrations `0007_outbox_lease_and_dead_letter` through `0014_impact_claims`: the outbox
  lease/dead-letter columns and, one per module, `sources`, `audit_entries` (with its
  append-only trigger), `reports`, `media_assets`, `events`/`event_relations`/
  `event_report_links`, `verification_cases`, and `infrastructure_assets`/`impact_claims`/
  `damage_records`.
- `docs/architecture/recording.md`: the end-to-end recording flow, reporter privacy rules,
  the gate-flow sequence diagram, task schedules and the outbox's lease/dead-letter/
  retention semantics.
- `docs/architecture/api.md`: the Phase 3 route table, GeoJSON rules for reports and
  events, the report-submission replay rule, and media visibility rules.
- `docs/architecture/media.md`: extended with the Phase 3 route pointers.
- Seven new data-dictionary pages: `provenance.md`, `audit.md`, `reports.md`, `media.md`,
  `events.md`, `verification.md`, and the `impacts` page's claims/assets/damage extension,
  each with a "Persistence" section for the database-level detail beyond the domain model.
- `docs/open-questions.md`: Q77–Q173, covering every per-module proposed default from the
  seven Phase 3 data dictionaries and `docs/architecture/best-figure.md`, plus the
  cross-cutting defaults and gaps recorded by the Phase 3 implementation reports.
