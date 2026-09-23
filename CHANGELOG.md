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
