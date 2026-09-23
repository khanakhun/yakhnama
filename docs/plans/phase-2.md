# Phase 2 plan — Identity, authentication and API foundations

Status: **approved in advance** (maintainer's standing approval of 2026-09-23: "work autonomously
to achieve the end goal"; "lets move to phase 2")
Branch: `phase/2-identity-api` (from `main` after the Phase 1 merge, or stacked on
`phase/1-shared-kernel` if the maintainer has not merged yet; the report states which)
Lead: Fable 5.1. Implementers and reviewers on Opus 5.5, `docs-writer` on Sonnet.

## 1. Goal

Make the backend usable by clients: bearer-JWT authentication validated against an OIDC
provider's JWKS, an `identity` module with users mirrored from tokens, organisations,
memberships and Policy-based authorisation, the API foundations every later endpoint relies on
(RFC 9457 everywhere, cursor pagination, `Idempotency-Key`, `ETag`/`If-Match`, rate limiting
behind a port, CORS and security headers, request logging without personal data), the first
public read endpoints for reference data, Scalar API docs, and the first committed OpenAPI
snapshot with its contract test. Reports, events and media remain Phase 3.

## 2. Inputs carried from Phase 1

- Security reviewer's "Phase 2 must add" list: `RequestValidationError` handler that never
  echoes input; 404/405 as Problem Details; catch-all 500 with correlation id; production
  settings validator (`database_url` not the dev default, `docs_enabled` false, JSON logs, no
  console exporter, no `*` or plain-http CORS origins; `environment` must be set explicitly
  outside tests); CORS from settings; security headers; `TrustedHostMiddleware`; body size
  limit; rate limiting that also covers `/health/ready` (or a 1–2 s readiness cache); NUL
  rejection in every string input.
- Maintainer decision: Scalar replaces Swagger UI and ReDoc at `/api/v1/docs`, self-hosted,
  gated by `docs_enabled` (ADR 0014).
- Open question Q10: anonymous or assisted citizen reporting under OIDC-only auth. Default kept:
  an account is required to write; verified data reads stay anonymous.
- Placeholder `AdminOnlyPolicy` in the three Phase 1 modules is replaced by identity policies
  exposed through `modules/identity/public.py`.

## 3. Tasks

| # | Task | Owner | Owns | Depends on | Acceptance criteria |
|---|------|-------|------|------------|---------------------|
| T1 | Dependencies (`PyJWT[crypto]`, `redis`, `scalar-fastapi`, `testcontainers[redis]`), `identity` module skeleton and import contracts, `openapi-snapshot` Poe task, `contract` test dir, CI contract job activation check | lead | `pyproject.toml`, `poetry.lock`, skeleton packages, `tests/contract/` | approval | `poe check` green before module code lands |
| T2 | ADRs: 0014 Scalar API reference; 0015 JWT validation with PyJWT and a JWKS cache; 0016 idempotency-key storage in PostgreSQL with 72 h TTL; 0017 rate limiting port with in-memory and Redis adapters | architect | `docs/adr/` | T1 | MADR, indexed |
| T3 | `identity` domain: `User` (id, `subject` = OIDC `sub`, `issuer`, `display_name` optional, `roles: frozenset[Role]`, `status`), `Organization` (id, slug, name, type, status, version), `Membership` (user, organisation, `OrganizationRole` `member|admin`), `Role` enum `citizen|trusted_reporter|org_member|org_admin|moderator|admin`, `Actor` value object (user id, roles, memberships) used by every policy, policies as composable Specification-style objects in `policies.py` (`IsAdmin`, `IsModerator`, `IsMemberOf(org)`, `IsOrgAdminOf(org)`, `CanManageReferenceData`, `AnyOf`/`AllOf`); no personal data beyond an optional display name; email and phone are NOT stored (open question if a contact channel is ever needed) | domain-modeler | `modules/identity/domain/**`, `docs/data-dictionary/identity.md`, tests | T1 | hypothesis on policy algebra; 100 % on domain |
| T4 | Platform auth and HTTP foundations: `platform/auth/` (JWKS client over `httpx` with TTL cache, key rotation on unknown `kid`, `TokenValidator` checking `iss`, `aud`, `exp`, `nbf`, `alg` allow-list `RS256`/`ES256`, leeway from settings; `Principal` DTO; FastAPI dependencies `current_principal`, `optional_principal`), `platform/idempotency/` (store port + PostgreSQL adapter + middleware: same key + same body hash → replay stored response; same key + different body → 409; scope per principal; 72 h TTL; only creating `POST`s), `platform/etag.py` (weak/strong ETag from `version`, `If-Match` check raising `PreconditionFailedError` → 412), `platform/ratelimit/` (port, in-memory token bucket, Redis adapter, per-IP and per-principal keys, configurable limits, `429` Problem Details with `Retry-After`), `platform/http.py` (security headers, CORS from settings, `TrustedHostMiddleware`, body size limit, NUL rejection, request logging middleware: method, route template, status, duration, request id; no IP, query or headers), settings additions and the production validator, extended error handlers | architect | `platform/**`, `main.py`, `.env.example`, tests incl. integration with a Redis testcontainer and a fake JWKS server via `httpx.MockTransport` | T1, T2 | every check from the Phase 1 security list is covered by a test; no network in tests |
| T5 | `identity` application: commands `EnsureUserFromPrincipal` (idempotent mirror on first request), `CreateOrganization`, `AddMember`, `ChangeMemberRole`, `RemoveMember`, `GrantRole` (admin only); queries `GetMe`, `GetOrganization`, `ListOrganizationMembers`; ports; `public.py` exporting `Actor`, policies, `IdentityPolicy` protocol; replace the three placeholders in geography/hazards/impacts by identity policies through the facade; fakes | application-engineer | `modules/identity/application/**`, `public.py`, the three modules' `application/authorisation.py`, `tests/fakes/identity.py`, tests | T3 | 100 % on application; policy denied paths tested for every handler |
| T6 | Persistence: identity ORM, repositories, query services, migration `0005_identity`; idempotency keys table migration `0006_idempotency_keys` (persistence-engineer writes the migration; architect the adapter) | persistence-engineer | `modules/identity/infrastructure/**`, `migrations/versions/0005_*`, `0006_*`, integration tests | T4, T5 | `alembic check` clean; integration tests on PostGIS |
| T7 | API: routers `GET /api/v1/hazard-types`, `/hazard-types/{code}`, `/impact-metrics`, `/impact-metrics/{code}`, `/places?q=&language=&level=&cursor=&limit=` (+ `Accept: application/geo+json` / `?format=geojson` FeatureCollection), `/places/{id}`; `GET /me` (ensures the user mirror), `POST /organizations` (Idempotency-Key, 201, ETag), `GET /organizations/{id}` (ETag), `PATCH /organizations/{id}` (If-Match), `POST /organizations/{id}/members`, `DELETE /organizations/{id}/members/{user_id}`; `/api/v1/moderation` router prefix scaffold with a moderator-only `GET /moderation/ping`; Scalar mounted at `/api/v1/docs`; `tests/contract/openapi.json` + contract test; API tests with fakes and locally signed JWTs | api-engineer | `modules/*/api/**`, `main.py` route lines, `tests/api/**`, `tests/contract/**` | T4, T5, T6 | every route: auth, authz, errors, pagination, idempotency tests; snapshot committed and diff reviewed |
| T8 | Keycloak development realm (`docker/keycloak/yakhnama-realm.json` imported on start: realm, client `yakhnama-api`, roles, two demo users with dev-only passwords), compose update, README auth section; OIDC settings defaults pointing at the local realm | integration-engineer | `docker/keycloak/**`, `docker-compose.yml`, `docs/architecture/auth.md` | T4 | `poe up` imports the realm; a manual token flow documented; no secrets |
| T9 | Test infrastructure: RSA test key pair generated at session start (never committed), `issue_test_token()` helper, JWKS fake, identity factories and fakes consolidation, coverage audit | test-engineer | `tests/fakes/**`, `tests/factories/**`, `tests/conftest.py` | T3, T4 | no key material in the repo; gitleaks clean |
| T10 | Docs: data dictionary identity, `docs/architecture/auth.md` (token flow, JWKS rotation, roles, policies), open questions, CHANGELOG, README | docs-writer | `docs/**`, root Markdown | T7 | `mkdocs build --strict` |
| T11 | Reviews: `security-reviewer` on T4, T5, T6, T7, T8, T9 (mandatory this phase); `standards-reviewer` on all | reviewers | none | each task | APPROVE within ≤ 3 cycles |
| T12 | Gate, commits, report | lead | commits, `docs/plans/phase-2-report.md` | all | §5 |

## 4. Parallelism

Serial spine: T1 → T2. Wave A after T1: T3 (identity domain) ∥ T4 (platform foundations) ∥ T8
(Keycloak realm, needs only the settings names agreed in T1). Wave B: T5 (after T3) ∥ T9 (after
T3, T4). Wave C: T6 (after T4, T5). Wave D: T7 (after T4, T5, T6). T10 after T7. Reviews as tasks
land; security review of T4 before T7 starts.

## 5. Gate

`poe check` green (now including `contract`); CI `contract` job active; `poe up && poe migrate &&
poe seed` still green; a manual flow: obtain a token from the dev realm, call `GET /me`,
create an organisation with an `Idempotency-Key`, replay it, get `200` with the same body,
change the body, get `409`; `PATCH` with a stale `If-Match` returns `412`; anonymous
`GET /hazard-types` returns 200 and anonymous `POST /organizations` returns 401 as
Problem Details; security reviewer APPROVE.

## 6. Risks

| Risk | Mitigation |
|------|------------|
| JWKS fetch in tests | never; tests sign with a local key and serve JWKS through `httpx.MockTransport` |
| Idempotency replay storing response bodies with personal data | store only for creating POSTs, scoped per principal, body hash + response body; purge after 72 h; security review |
| Rate-limit state in memory per process | port + Redis adapter; in-memory only for dev and tests |
| Snapshot churn | `openapi-snapshot` task regenerates; the diff is reviewed in the PR |
| Keycloak realm JSON drift across versions | pinned image digest; realm export checked into `docker/keycloak/` |

## 7. Open questions

| # | Question | Proposed default | Blocking |
|---|----------|------------------|----------|
| 1 | Audience value for the API (`aud` claim): `yakhnama-api`? | `yakhnama-api`, from settings | no |
| 2 | Are `moderator` and `admin` granted through OIDC realm roles or only through the identity module? | Realm roles are mirrored on token; module-granted roles override upward only by an admin | no |
| 3 | Organisation types (government, NGO, research, media, community) | that list, proposed | no |
| 4 | Store `display_name` from the token? | Yes, optional, editable by the user; never email or phone | no |
| 5 | Idempotency scope for anonymous callers | none: anonymous POSTs are rejected anyway | no |
