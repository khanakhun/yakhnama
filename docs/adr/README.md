# Architecture Decision Records

This directory records the architectural decisions of the Yakhnama backend: what was
decided, why, which alternatives were rejected, and what the decision costs. An ADR is
written whenever a choice is hard to reverse, crosses module boundaries, adds a pattern not
in the catalog (`AGENTS.md` §3), or changes a rule in `AGENTS.md` §2–§5.

ADRs use the MADR format. The template and the step-by-step recipe are in the `write-adr`
skill, `.claude/skills/write-adr/SKILL.md`. The `architect` subagent owns this directory.

## Numbering

- Files are named `NNNN-<kebab-slug>.md`, with a four-digit, zero-padded number.
- Numbers are assigned sequentially and never reused, even if an ADR is rejected or
  withdrawn.
- The slug is a short kebab-case form of the title in the first line (`# NNNN. Title`).

## Status rules

| Status | Meaning |
|--------|---------|
| `proposed` | Written and under discussion. Not binding until accepted. |
| `accepted` | Binding. Code and documentation follow it. |
| `deprecated` | No longer applies, and nothing replaces it. |
| `superseded by [NNNN](NNNN-slug.md)` | Replaced by a later ADR, which links back to it. |

- Accepted ADRs are not rewritten. To change a decision, write a new ADR and mark the old
  one `superseded by` the new one. Only typo fixes, broken links and status lines may be
  edited in an accepted ADR.
- Decisions that touch `AGENTS.md` §2–§5 need the maintainer among the deciders.
- Every status change is reflected in the index below in the same commit.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001] | Modular monolith with hexagonal layers | accepted |
| [0002] | PostgreSQL with PostGIS as the system of record | accepted |
| [0003] | FastAPI with an app factory and a contract-first HTTP API | accepted |
| [0004] | Async SQLAlchemy 2.0 and Alembic for persistence and migrations | accepted |
| [0005] | OIDC-only authentication and policy-based authorisation | accepted |
| [0006] | UUIDv7 for every identifier | accepted |
| [0007] | Transactional outbox for domain events | accepted |
| [0008] | Taskiq behind a `TaskQueue` port for background jobs | accepted |
| [0009] | Object storage for media and rasters | accepted |
| [0010] | Licensing of code and data | proposed |
| [0011] | Catalog entries for settings and API schemas | proposed |
| [0012] | Catalog entries for commands, queries, DTOs and domain errors | proposed |
| [0013] | In-house RFC 9562 UUIDv7 generator | proposed |
| [0014] | Scalar API reference at /api/v1/docs | accepted |
| [0015] | JWT validation with PyJWT and a cached JWKS client | accepted |
| [0016] | Idempotency keys stored in PostgreSQL with a 72-hour TTL | accepted |
| [0017] | Rate limiting behind a port with in-memory and Redis adapters | accepted |

[0001]: 0001-modular-monolith-with-hexagonal-layers.md
[0002]: 0002-postgresql-with-postgis.md
[0003]: 0003-fastapi-with-app-factory.md
[0004]: 0004-async-sqlalchemy-and-alembic.md
[0005]: 0005-oidc-only-authentication.md
[0006]: 0006-uuidv7-identifiers.md
[0007]: 0007-transactional-outbox-for-domain-events.md
[0008]: 0008-taskiq-behind-a-task-queue-port.md
[0009]: 0009-object-storage-for-media-and-rasters.md
[0010]: 0010-licensing.md
[0011]: 0011-catalog-entries-for-settings-and-api-schemas.md
[0012]: 0012-catalog-entries-for-commands-queries-dtos-and-errors.md
[0013]: 0013-in-house-uuidv7-generator.md
[0014]: 0014-scalar-api-reference.md
[0015]: 0015-jwt-validation-with-pyjwt-and-a-cached-jwks.md
[0016]: 0016-idempotency-keys-in-postgresql.md
[0017]: 0017-rate-limiting-behind-a-port.md
