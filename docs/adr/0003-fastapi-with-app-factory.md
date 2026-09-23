# 0003. FastAPI with an app factory and a contract-first HTTP API

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

The backend serves three kinds of clients: offline-capable mobile apps that submit reports
over unreliable mountain networks, a moderator web interface, and research consumers who
read the open dataset programmatically. All of them need a stable, documented HTTP API
with predictable errors, pagination that does not skip or duplicate rows while data is
being added, safe retries and protection against lost updates.

Which web framework and which HTTP conventions should the API use?

## Decision drivers

- Async I/O end to end (async SQLAlchemy, object storage, OIDC key fetches).
- Pydantic models everywhere (`AGENTS.md` §4) and a generated, reviewable OpenAPI document.
- Offline mobile clients: retries after timeouts must not create duplicates.
- Concurrent moderators: edits must not silently overwrite each other.
- Research consumers: stable versioned URLs, machine-readable errors, stable pagination.
- Testability: the whole app must be constructible in tests with fakes wired in.
- Small team: a widely used framework with good typing support.

## Considered options

1. FastAPI with a `create_app` factory (chosen)
2. Django REST Framework
3. Litestar
4. Flask

## Decision outcome

Chosen option: **FastAPI, built by a `create_app` factory in `src/yakhnama/main.py`**,
because it is async-native, Pydantic-native and produces OpenAPI from the same models the
application layer uses.

- `create_app` is the composition root for HTTP: it registers routers, exception handlers
  and dependency wiring. Tests build the app through the same factory with fakes.
- All resource routes live under `/api/v1`. Breaking changes require a new version prefix;
  additive changes do not. Health endpoints stay unversioned (`/health/live`).
- The generated OpenAPI document is committed as a snapshot (`tests/contract/openapi.json`,
  regenerated with `poetry run poe openapi-snapshot`) and treated as the API contract; any
  diff must be reviewed. The first snapshot is taken in Phase 2.
- Errors use RFC 9457 Problem Details (`application/problem+json`). Domain exceptions derived
  from `YakhnamaError` are mapped to status codes and problem types in exactly one place, the
  exception handlers registered in `main.py`.
- List endpoints use opaque cursor pagination, not offset pagination.
- Create endpoints that clients may retry accept an `Idempotency-Key` header; a repeated key
  with the same payload returns the original result.
- Mutable resources return an `ETag`; updates require `If-Match` and fail with
  `412 Precondition Failed` on a stale version.
- Exact semantics (idempotency key retention period, cursor encoding, problem type URIs) are
  specified in the phase that builds the first write endpoint and the `add-api-endpoint`
  skill.

### Consequences

- Good, because request and response schemas, validation and documentation come from one set
  of Pydantic models.
- Good, because the app factory makes API tests (`httpx.AsyncClient` against the factory)
  fast and free of global state.
- Good, because the OpenAPI snapshot turns accidental breaking changes into a failing,
  reviewable diff, which protects mobile and research clients.
- Good, because `Idempotency-Key` and client-generated ids
  ([0006](0006-uuidv7-identifiers.md)) together make offline resubmission safe.
- Good, because `ETag`/`If-Match` prevents lost updates between moderators.
- Bad, because FastAPI provides no admin, auth, or migrations out of the box; we build or
  integrate each (see [0004](0004-async-sqlalchemy-and-alembic.md),
  [0005](0005-oidc-only-authentication.md)).
- Bad, because idempotency storage and `ETag` handling are our own code and need their own
  tests and a retention policy.
- Bad, because cursor pagination does not support "jump to page N", which some research
  users may expect; bulk access is served by exports instead.
- Bad, because the `Idempotency-Key` header is still an IETF draft, so its semantics could
  shift; we document our own behaviour rather than rely on the draft alone.
- Bad, because FastAPI's release cadence is fast and 0.x versions can change behaviour; the
  version range in `pyproject.toml` is pinned to one minor series and upgrades are
  deliberate.

## Pros and cons of the options

### FastAPI

- Good, because it is async, Pydantic-native and generates OpenAPI.
- Good, because dependency injection via `Depends` fits the composition-root pattern.
- Bad, because the ecosystem for batteries (admin, auth) is thinner than Django's.

### Django REST Framework

- Good, because it is mature, with admin, auth and permissions included.
- Bad, because it is built around the Django ORM and synchronous request handling, which
  conflicts with the hexagonal layering ([0001](0001-modular-monolith-with-hexagonal-layers.md))
  and the async SQLAlchemy choice.
- Bad, because its serializers duplicate what Pydantic already gives us.

### Litestar

- Good, because it is async, typed and has strong built-in features (DTOs, OpenAPI).
- Bad, because its community and third-party ecosystem are smaller, which matters for a
  small team and for outside contributors.

### Flask

- Good, because it is simple and very widely known.
- Bad, because async support is secondary, and validation and OpenAPI need extensions,
  which means more glue to maintain.

## More information

- RFC 9457, Problem Details for HTTP APIs.
- IETF draft "The Idempotency-Key HTTP Header Field" (httpapi working group).
- RFC 9110, sections on `ETag`, `If-Match` and `412`.
- `AGENTS.md` §2.3 (errors) and §4.1 (`openapi-snapshot`).
