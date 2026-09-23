# Phase 2 report — Identity, authentication and API foundations

## Summary

Clients can now authenticate with bearer JWTs validated against an OIDC provider's JWKS, and the
backend mirrors users on first sight into an `identity` module with organisations, memberships,
roles and composable Policy objects that every write handler consults. The HTTP foundations are in
place for every later endpoint: RFC 9457 Problem Details everywhere, cursor pagination,
`Idempotency-Key` replay and conflict semantics, `ETag`/`If-Match`, rate limiting behind a port with
in-memory and Redis adapters, security headers, CORS and trusted hosts from settings, body and NUL
guards, request ids, a personal-data-free request log and a production settings guard. The first
public routers serve hazard types, impact metrics and places (JSON and GeoJSON) anonymously, plus
`/me`, organisations, memberships and admin role routes. Scalar serves the API reference and the
OpenAPI snapshot of 20 operations is committed with its contract test. A Keycloak development
realm with two demo users completes the local flow. 2,571 tests run in the gate at 99.89 %.

## Delivered

| Task | Subagent | Files | Tests added | Status |
|------|----------|-------|-------------|--------|
| T1 Dependencies, identity skeleton, contracts, `openapi-snapshot` and `contract` tasks | lead | `pyproject.toml`, skeleton | n/a | done |
| T2 ADRs 0014–0017 | architect (Opus) | `docs/adr/` | n/a | done |
| T3 identity domain | domain-modeler (Opus) | `modules/identity/domain/` | 305 | done |
| T4 platform auth, idempotency, ETags, rate limiting, http, error handlers, Scalar, snapshot tool | architect (Opus) | `platform/`, `main.py` | 806 unit + 31 integration | done |
| T5 identity application; reference modules on identity policies | application-engineer (Opus) | `modules/identity/application/`, `modules/*/application/authorisation.py`, `seed/` | 493 (with API) | done |
| T6 identity persistence, migrations 0005–0006 | persistence-engineer (Opus) | `modules/identity/infrastructure/`, `migrations/` | 47 integration | done |
| T7 API routers, snapshot, contract test | api-engineer (Opus) | `modules/*/api/`, `tests/api/`, `tests/contract/` | 110 API + 2 contract | done |
| T8 Keycloak dev realm, auth guide | integration-engineer (Sonnet) | `docker/keycloak/`, `docs/architecture/auth.md` | manual token flow | done |
| T9 Test infrastructure | folded into T4 (`tests/fakes/auth.py`) and T7 (`tests/fakes/api.py`) | | | done |
| T10 Docs | docs-writer (Sonnet) | `docs/architecture/api.md`, open questions Q50–Q76 | n/a | done |
| T11 Reviews | security-reviewer (Opus), standards via gate | none | n/a | see below |
| T12 Gate, commits, report | lead | 4 commits, this file | n/a | done |

## Quality gate

- `poetry run poe check`: **PASS**
  ```
  ruff format --check / ruff check      All checks passed
  mypy --strict                          Success: no issues found in 361 source files
  lint-imports                           Contracts: 8 kept, 0 broken
  pytest (all tiers, real PostGIS
          and Redis via testcontainers)  2571 passed, coverage 99.89 % (branch on)
  cov-layers                             domain 100 %, application 100 %
  contract                               2 passed (snapshot matches the app)
  diff-cover vs main                     99 % on changed lines
  gitleaks git                           no leaks found
  pip-audit --strict                     No known vulnerabilities found
  ```
- Coverage: overall 99.89 %, domain 100 %, application 100 %, api 100 %, diff 99 %.
- CI: the `contract` job is now active by its file gate.
- Manual flow (from the T8 run): `demo-citizen` and `demo-moderator` obtain RS256 tokens from the
  local realm with `aud=yakhnama-api` and `realm_access.roles`; the JWKS `kid` matches.
- Reviews: security cycle 1 (mandatory) CHANGES REQUIRED with 10 items (5 medium: member
  addition without consent, real names mirrored from tokens, lone-surrogate 500, Redis limiter
  hang, JWKS refetch storm; 5 low) → all fixed; cycle 2 verdict: **APPROVE** (all ten items re-verified with crafted requests; one non-blocking note: LRM, RLM, ALM and ZWJ remain allowed because Urdu text needs them).
  Standards review was not run as a separate cycle this phase (cost); the gate, the structural
  tests and the catalog check enforce the mechanical rules, and the nits are logged as TD-20.

## ADRs added

0014 Scalar API reference (accepted; CDN in development, no docs in production); 0015 JWT
validation with PyJWT and a cached JWKS (accepted); 0016 idempotency keys in PostgreSQL
(accepted); 0017 rate limiting behind a port (accepted). 0015 and 0017 were amended before commit
with the review fixes. `AuthenticationError` and `PreconditionRequiredError` were added to the
kernel hierarchy (proposed in `AGENTS.md` §2.3).

## Deviations from the plan (and why)

1. One hook-verified commit carries the bulk of Phase 2 (a135d52) instead of one commit per task:
   the pieces are interdependent and the pre-commit hook checks the whole tree; the repository's
   guard forbids bypassing hooks, which is correct.
2. Adding organisation members is platform-admin only, not org-admin, until an invitation and
   acceptance flow exists (security finding).
3. Display names are never taken from tokens; users set them via `PATCH /me`.
4. Replayed idempotent creates return the stored 201, not 200 as the plan wrote.
5. Scalar loads from a CDN: `scalar-fastapi` ships no bundle; docs are disabled in production.
6. `platform/auth/dependencies.py` imports the container, so api layers read the principal from
   request state through small Protocols instead (Q70; consolidate in Phase 3).
7. T9 merged into T4 and T7.

## Open questions (blocking first)

None blocking. New this phase: Q50–Q76 in `docs/open-questions.md` (identity Q-I1–Q-I9,
organisation creation and member listing rights, member consent, self-suspension guard,
idempotent replay status, member ETag, coordinate rounding of reference places, Scalar
self-hosting, `purge_expired` scheduling, the dependencies import).

## Risks and technical debt (each with an issue reference)

| Ref | Item |
|-----|------|
| TD-20 | Standards nits not reviewed this phase: abbreviations (`ref`, `char`, `reference_dir`), single-letter test loops, duplicated `require_valid_credentials` per api module. |
| TD-21 | `purge_expired` for idempotency rows is not scheduled; stored bodies persist beyond 72 h until the Phase 3 Taskiq job. |
| TD-22 | Rate-limit client IP relies on uvicorn `--proxy-headers`; no deployment config yet. |
| TD-23 | Realm roles are not re-synced after first sight (Q-I1). |
| TD-24 | JWKS response size is checked after the body is read; stream-cap in Phase 3. |
| TD-25 | Scalar bundle unpinned and unsigned (development only). |
| TD-26 | Lead-owned skills still lag the real names (carried from TD-11). |

## Proposed next phase (brief)

Phase 3, core recording, as specified: `provenance`, `reports` with revisions and the triage
chain, `media` with the storage port, S3/MinIO adapter, presigned uploads, EXIF handling,
scanner port and SHA-256 deduplication, `events` with relations and report linking, `impacts`
claims, infrastructure assets, damage records and the best-figure policy, `verification` state
machine, `audit` via an outbox subscriber, public read APIs for verified events with GeoJSON and
filters, Taskiq behind the `TaskQueue` port (outbox relay trigger, idempotency purge). Security
review carries the Phase 3 list from this phase's reviewer.
