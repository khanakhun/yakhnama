# 0011. Catalog entries for settings and API schemas

- Date: 2026-09-23
- Status: proposed
- Deciders: lead agent, maintainer (required: this changes `AGENTS.md` §3)

## Context and problem statement

`AGENTS.md` §3 requires every class to declare `Implements: <Pattern>` with a pattern
from the catalog, and `tests/architecture/test_structure.py` now checks the declared
name against the catalog. Two kinds of class already exist in the platform and will
exist in every module, but no catalog row covers them:

- `platform/settings.py` `Settings`, a pydantic-settings `BaseSettings` subclass that
  reads `YAKHNAMA_*` environment variables. It was declared as
  `Implements: Settings (pydantic-settings)`.
- `platform/health.py` `LivenessResponse`, the body of an HTTP response. It was
  declared as `Implements: DTO (API response schema)`. Every endpoint added from
  Phase 2 (`modules/*/api/schemas.py`) will add more request and response bodies.

Neither declaration names a catalog pattern, and forcing them onto the nearest row
would mislabel them. Which catalog patterns should these classes declare?

## Decision drivers

- Every class must declare a catalog pattern, and the check must be mechanical.
- Pattern names must describe what the class is, so readers are not misled.
- HTTP bodies are the public contract (OpenAPI snapshot) and must stay separate from
  application DTOs, so the API can change without changing use cases and vice versa.
- Configuration is an environment boundary, not a domain concept.
- `AGENTS.md` §3 is maintainer-protected; changes need an ADR and maintainer approval.

## Considered options

1. Add two catalog rows: **Settings** and **API Schema** (proposed).
2. Map them to existing rows: settings as Value Object, API bodies as DTO.
3. Exempt these classes from the `Implements:` rule.

## Decision outcome

Proposed option: **1, add two catalog rows** to `AGENTS.md` §3:

| Concern | Pattern | Where |
|---------|---------|-------|
| Configuration | Settings (pydantic-settings `BaseSettings`) | `platform/settings.py` |
| HTTP request/response bodies | API Schema (Pydantic model, frozen where possible) | `modules/*/api/schemas.py`, `platform/health.py` |

- **Settings.** One `BaseSettings` subclass per process, loaded only from the
  environment (and a local `.env`), cached behind `get_settings()`, with
  `extra="forbid"` so typos fail loudly. It lives only in `platform/settings.py`;
  modules receive the values they need through the composition root.
- **API Schema.** A Pydantic model that describes an HTTP request or response body.
  It is frozen where possible, uses `extra="forbid"` on requests, and lives only in the
  `api` layer. Routers convert between API schemas and application commands, queries
  and DTOs; an API schema never crosses into `application` or `domain`.

Until this ADR is accepted, the two names are listed in the catalog marked as
proposed, and `CATALOG_PATTERNS` in `tests/architecture/test_structure.py` includes
them so the structural test passes. If the maintainer rejects the ADR, those entries
are removed and the two classes are relabelled in the same change.

**What is needed to move this ADR to `accepted`:** the maintainer's written approval
of the two rows above, after which the "proposed" marker is removed from `AGENTS.md` §3
and the index in [README.md](README.md) is updated.

### Consequences

- Good, because every class can declare an accurate catalog pattern and the
  structural test can check names mechanically, with no exemptions.
- Good, because naming API Schema separately from DTO keeps the HTTP contract and the
  application's read models apart, as the layer rules in `AGENTS.md` §2.1 intend.
- Good, because a single Settings class in one place keeps configuration out of the
  domain and makes the "no secrets in code" rule easy to review.
- Bad, because the catalog grows by two rows that describe framework mechanisms rather
  than design patterns in the classic sense.
- Bad, because routers must map between API schemas and DTOs even when the shapes are
  identical, which adds some boilerplate per endpoint.

## Pros and cons of the options

### Add Settings and API Schema rows

- Good, because the names say exactly what the classes are.
- Bad, because it needs a maintainer-approved change to a protected section.

### Reuse Value Object and DTO

- Good, because the catalog stays unchanged.
- Bad, because `Settings` is not a domain concept, so calling it a Value Object
  misleads readers about where it belongs.
- Bad, because the glossary defines DTO as the output of query services; calling HTTP
  bodies DTOs blurs the boundary between the `api` and `application` layers and
  invites routers to return application DTOs directly.

### Exempt these classes

- Good, because nothing needs to change in the catalog.
- Bad, because exemptions weaken a mechanical check, which `AGENTS.md` forbids, and
  every future exemption would need its own justification.

## More information

- `AGENTS.md` §3 (pattern catalog), §4 (standards), §7 (glossary entry for DTO).
- pydantic-settings documentation, docs.pydantic.dev/latest/concepts/pydantic_settings.
