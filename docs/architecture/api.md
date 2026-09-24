# API conventions

## Purpose

This page documents the `/api/v1` conventions every route follows, as implemented in
Phase 2 (`docs/plans/phase-2.md`, task T7): authentication, error format, pagination,
idempotency, concurrency, content negotiation, rate limiting and the OpenAPI workflow. It
is the companion to `docs/architecture/auth.md` (the token flow and the realm) and to
`docs/architecture/README.md` (the module map and the write/read flow). Where this page and
the code disagree, the code and its tests are binding; report the mismatch.

## Base path and versioning

Every route lives under `/api/v1`, mounted once by `main.py`'s `create_app`. There is no
route outside this prefix except `/health/live` and `/health/ready`
(`src/yakhnama/platform/health.py`), which are unversioned and unauthenticated on purpose
(an orchestrator probes them before it knows anything about the API).

## Authentication

Bearer JWTs, validated once per request by `PrincipalResolutionMiddleware`
(`src/yakhnama/platform/auth/resolution.py`) against the OIDC provider's JWKS; see
`docs/architecture/auth.md` for the token flow and the validator's checks.

- A request with no `Authorization` header is **anonymous**.
- A request with a malformed header, several `Authorization` headers, or a token that
  fails validation is **not** anonymous: the rejection is recorded once and every route it
  reaches — public or not — answers `401 authentication-failed` with
  `WWW-Authenticate: Bearer realm="yakhnama"`, so a client never mistakes a rejected token
  for a successful anonymous call.
- Routes that require a caller use the `current_actor`/`current_principal` family of
  dependencies (`modules/identity/api/dependencies.py`, `platform/auth/dependencies.py`)
  and declare the `bearerAuth` security scheme in OpenAPI.
- Routes that stay open to anonymous callers use `optional_actor` or, in the three
  reference-data modules, the `require_valid_credentials` dependency
  (`modules/{geography,hazards,impacts}/api/dependencies.py`), which does not require a
  token but still enforces the "rejected token is not anonymous" rule above. These routes
  declare no security scheme, so OpenAPI lists them as public.

## Anonymous reads of reference data

Hazard types, impact metrics and places are public reference data (`AGENTS.md` §7's
glossary; Q10 in `docs/open-questions.md` keeps writes behind an account while keeping
verified data reads anonymous). `GET /hazard-types`, `/hazard-types/{code}`,
`/impact-metrics`, `/impact-metrics/{code}`, `/places` and `/places/{place_id}` never
require a token. `GET /organizations/{id}` is also anonymous
(`organization_read_policy`, **proposed**, see `docs/open-questions.md` Q59); listing an
organisation's members is not, because the list shows display names (`member_list_policy`,
same entry).

## Errors: RFC 9457 Problem Details

Every error response is `application/problem+json`, built by
`platform/problem_details.py` and rendered in exactly one place per `AGENTS.md` §2.3: the
exception handlers registered in `main.py`, or directly by a platform middleware that runs
outside FastAPI's own exception handling (`platform/http.py`, `platform/idempotency/`,
`platform/ratelimit/`). Every problem carries:

- `type`: `https://yakhnama.org/problems/<slug>`, one of the closed list below.
- `title`: the HTTP status phrase.
- `status`: the HTTP status, repeated for clients that lose the header.
- `detail`: a client-safe explanation, when there is one safe to show.
- `instance`: the request id of this occurrence (also the `X-Request-ID` response header
  and every log line of the request) — quote it to support or in a bug report.
- `errors`: field errors (`loc`, `msg`, `type`), for validation problems only; the
  rejected input value is never echoed back.

| Slug | Status | Meaning |
|------|--------|---------|
| `bad-request` | 400 | The request is malformed (generic). |
| `invalid-host` | 400 | The `Host` header is not a trusted host. |
| `invalid-idempotency-key` | 400 | `Idempotency-Key` is not a UUID. |
| `authentication-failed` | 401 | No valid bearer token where one is required. |
| `permission-denied` | 403 | The principal may not perform the action. |
| `not-found` | 404 | The resource or route does not exist. |
| `method-not-allowed` | 405 | The route exists but not for this method. |
| `conflict` | 409 | The request conflicts with the current state. |
| `invariant-violation` | 409 | The change would break an invariant. |
| `invalid-transition` | 409 | The state machine forbids the transition. |
| `idempotency-key-reused` | 409 | The key was used with a different request. |
| `idempotency-key-in-use` | 409 | A request with the same key is still running. |
| `precondition-failed` | 412 | `If-Match` does not match the current version. |
| `payload-too-large` | 413 | The body exceeds `max_request_body_bytes`. |
| `validation-error` | 422 | The data is invalid; see `errors`. |
| `nul-character` | 422 | A string input contains the NUL character. |
| `precondition-required` | 428 | A conditional request came without `If-Match`. |
| `rate-limited` | 429 | Too many requests; see `Retry-After`. |
| `internal-error` | 500 | An unexpected server error. |
| `service-unavailable` | 503 | A dependency (the identity provider) is down. |
| `http-error` | any | Any other HTTP error raised by the framework. |

The slug list is closed (`platform/problem_details.py`, `PROBLEM_TYPES`); a new slug is
added there, not invented at a call site. `YakhnamaError` subclasses map to a slug by MRO
lookup (`main.py`, `ERROR_STATUSES`), so a module's own error subclass maps without being
listed, as long as it derives from a mapped kernel error (`AGENTS.md` §2.3).

## Pagination

Every list route uses opaque, unsigned cursor (keyset) pagination
(`shared_kernel/pagination.py`; the signing question is open, `docs/open-questions.md`
Q15):

- Request: `?limit=<1..200>&cursor=<opaque token>`. `limit` defaults to 50
  (`DEFAULT_PAGE_LIMIT`) and cannot exceed 200 (`MAX_PAGE_LIMIT`); a value outside that
  range is `422 validation-error`.
- Response body: `{"items": [...], "next_cursor": "<token>" | null}`.
- Response header: when there is a next page, the route also sets
  `Link: <path?...&cursor=...>; rel="next"` (for example `hazard-types`, `impact-metrics`,
  `places`, organisation members), so a client can follow the link without reconstructing
  the query itself.
- A cursor is only ever accepted in the exact canonical form `encode_cursor` produced for
  it; a hand-crafted but well-formed token is accepted (cursors are not signed), but it
  only positions a query that authorisation has already scoped — it carries no secret and
  no data the caller could not already see.

## `Idempotency-Key` (creating `POST`s)

Documented fully in ADR 0016 and `platform/idempotency/middleware.py`'s module docstring;
summary for API consumers:

- Send `Idempotency-Key: <uuid>` on a `POST` under `/api/v1` you might need to retry
  (currently `POST /organizations` and `POST /organizations/{id}/members`). The header is
  optional; without it, a retried `POST` simply runs again.
- It only applies to an **authenticated** request; an anonymous `POST` reaches its route
  and gets `401` regardless of the header, because idempotency is scoped per principal
  (`Principal.scope_key()`).
- A key that is not a single UUID is `400 invalid-idempotency-key`.
- The winning request runs the route. Its **original** status code and body are stored
  when the response is 2xx (in particular, `POST /organizations`'s `201` is stored and
  replayed as `201`, not downgraded to `200` on replay — `docs/open-questions.md` Q64) and
  kept for `idempotency_ttl_hours` (1–168, default 72). Anything else (an error) releases
  the key, so the client can simply retry.
- A replay of the same key with the same request (method, path, query string and body,
  hashed together) returns the stored response with `Idempotent-Replayed: true` added.
- The same key with a **different** request is `409 idempotency-key-reused`.
- The same key while the original request is **still running** is
  `409 idempotency-key-in-use` with `Retry-After: 1`.
- Only `Content-Type`, `Location` and `ETag` are stored and replayed from the original
  response headers; nothing else, `Set-Cookie` above all, is ever replayed.

## `ETag` and `If-Match` (optimistic concurrency)

Documented in `platform/etag.py`; summary:

- Every route that returns one aggregate sets a strong `ETag: "<id>:<version>"`
  (`make_etag`), for example `hazard-types/{code}`, `impact-metrics/{code}`,
  `places/{place_id}`, `/me`, `/organizations/{id}`, and each organisation member.
- Routes that change one aggregate based on a version the client already has require
  `If-Match`: `PATCH /me` and `PATCH /organizations/{id}` answer
  `428 precondition-required` without it and `412 precondition-failed` when the tag names
  a stale version or the wrong resource.
- The organisation member and administrator routes (`PATCH`/`DELETE` on
  `.../members/{user_id}`, and every `/users/{user_id}/...` route) accept `If-Match` and
  check it when sent, but do not yet require it (`docs/open-questions.md` Q66).
- `If-Match` uses strong comparison only: a weak tag (`W/"..."`) never matches, and `*`
  matches any current representation.

## Content negotiation: GeoJSON for places

`GET /places` and `GET /places/{place_id}` (`modules/geography/api/router.py`) return
plain JSON by default and negotiate GeoJSON (RFC 7946) on either `?format=geojson` or
`Accept: application/geo+json`; `?format=` wins when both are present, so a link can pin
the representation. Every response carries `Vary: Accept`. A place's GeoJSON geometry is
its centroid `Point`, or `null` when the place has none — never a placeholder `bbox`
(`docs/open-questions.md` Q21). The list route returns a `FeatureCollection`; the detail
route a single `Feature`. The GeoJSON form of the list route carries its next page only in
the `Link` header, since a `FeatureCollection` has no room for `next_cursor`.

## Rate limiting

`platform/ratelimit/` (ADR 0017), applied by `RateLimitMiddleware` after principal
resolution and before the request body is read (`platform/http.py`'s middleware-order
docstring). Every counted response carries `X-RateLimit-Limit` and
`X-RateLimit-Remaining`; a request over the limit is `429 rate-limited` with
`Retry-After`. The limit is `rate_limit_authenticated_per_minute` (default 300) for a
valid bearer token, else `rate_limit_anonymous_per_minute` (default 60) keyed on a hashed
client IP. `/health/*` is exempt (protected instead by a short readiness-result cache);
every other route under `/api/v1` is counted, including anonymous reference-data reads.

## Security headers and request id

Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`X-Frame-Options: DENY`, a `Content-Security-Policy` (`default-src 'none'; ...` on every
route except the Scalar page, which gets a page-specific policy, ADR 0014) and, in
production, `Strict-Transport-Security`. Every response also carries the request id on
`X-Request-ID` (or whatever `request_id_header` is configured to), generated or accepted
from a well-formed inbound header by `RequestIdMiddleware`, and bound to every log line and
every Problem Details `instance` for that request. See `platform/http.py`'s module
docstring for the exact middleware order (request id, logging, security headers, trusted
host, CORS, then authentication and rate limiting, then the body guard and idempotency).

## Route table

Auth column: **anon** = no token needed; **auth** = `current_actor`/`current_principal`
required (`401` without a valid token); **auth (policy)** = authenticated and further
checked by an identity policy (`docs/data-dictionary/identity.md`, "Policies").

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/health/live` | anon | Always 200; checks nothing. |
| `GET` | `/health/ready` | anon | `SELECT 1` against the database, cached 1 s; 503 on failure. |
| `GET` | `/api/v1/hazard-types` | anon | Cursor pagination. |
| `GET` | `/api/v1/hazard-types/{code}` | anon | `ETag`. |
| `GET` | `/api/v1/impact-metrics` | anon | Cursor pagination. |
| `GET` | `/api/v1/impact-metrics/{code}` | anon | `ETag`. |
| `GET` | `/api/v1/places` | anon | Cursor pagination; GeoJSON negotiation. |
| `GET` | `/api/v1/places/{place_id}` | anon | `ETag`; GeoJSON negotiation. |
| `GET` | `/api/v1/me` | auth | Mirrors the user on first sight; `ETag`. |
| `PATCH` | `/api/v1/me` | auth | Required `If-Match`; `IsSelf` implicitly (only the caller). |
| `POST` | `/api/v1/organizations` | auth | `Idempotency-Key` optional; `201`, `Location`, `ETag`. Authorisation: any authenticated user (Q58). |
| `GET` | `/api/v1/organizations/{organization_id}` | anon | `organization_read_policy` (**proposed** public read, Q59); `ETag`. |
| `PATCH` | `/api/v1/organizations/{organization_id}` | auth (policy) | `CanManageOrganization`; required `If-Match`. |
| `GET` | `/api/v1/organizations/{organization_id}/members` | auth (policy) | `member_list_policy` (members and platform admins, Q59); cursor pagination. |
| `POST` | `/api/v1/organizations/{organization_id}/members` | auth (policy) | `CanManageOrganization`; `Idempotency-Key` optional; `201`, `ETag`. |
| `PATCH` | `/api/v1/organizations/{organization_id}/members/{user_id}` | auth (policy) | `CanManageOrganization`; optional `If-Match` (Q66). |
| `DELETE` | `/api/v1/organizations/{organization_id}/members/{user_id}` | auth (policy) | `CanManageOrganization`; optional `If-Match` (Q66); `204`. |
| `POST` | `/api/v1/users/{user_id}/roles` | auth (policy) | `IsAdmin`; optional `If-Match` (Q66). |
| `DELETE` | `/api/v1/users/{user_id}/roles/{role}` | auth (policy) | `IsAdmin`; `citizen` cannot be revoked; optional `If-Match`. |
| `POST` | `/api/v1/users/{user_id}/suspension` | auth (policy) | `IsAdmin`; optional `If-Match`. |
| `DELETE` | `/api/v1/users/{user_id}/suspension` | auth (policy) | `IsAdmin`; optional `If-Match`. |
| `GET` | `/api/v1/moderation/ping` | auth (policy) | `CanModerate`; scaffold for the moderation router (Phase 3 fills it in). |
| `GET` | `/api/v1/docs` | anon | Only when `docs_enabled`; excluded from the OpenAPI document itself. |
| `GET` | `/api/v1/openapi.json` | anon | Only when `docs_enabled`. |

## Phase 3 additions

Phase 3 (`docs/plans/phase-3.md`) added the `reports`, `media`, `events`,
`verification`, `impacts` (claims, assets, damage) and `provenance` routes below.
The conventions above (Problem Details, pagination, `Idempotency-Key`,
`ETag`/`If-Match`, rate limiting, security headers) apply unchanged; this section
adds what is specific to recording data. See
[`recording.md`](recording.md) for the end-to-end flow and the reporter privacy
rules these routes enforce.

### GeoJSON for reports and events

`GET /reports` and `GET /events` negotiate GeoJSON the same way as `GET /places`
(`?format=geojson` or `Accept: application/geo+json`, `?format=` winning,
`Vary: Accept` always). `GET /events/{event_id}` also negotiates; its GeoJSON
geometry is the event's own `geometry` when set, else its `centroid` as a `Point`,
else `null`. `GET /reports` places each report at its **rounded** public point;
the exact position never appears in a GeoJSON response, list or otherwise. The
list forms carry their next page only in the `Link` header (a `FeatureCollection`
has no room for `next_cursor`).

### Report submission replay

`POST /reports` is idempotent twice over. First, on the client's own
`client_report_id` (a UUIDv7 sent in the body): a retried submission with the
same id returns the **stored report again**, `201` with the identical body,
because the handler has no way to tell a retry from the very first call apart
from that id. Second, on `Idempotency-Key` through the platform middleware
(optional, same semantics as every other creating `POST`, see above). Both
mechanisms may be used together; they answer different failure modes (an offline
client resubmitting later versus a client retrying the same HTTP request).

### Media visibility

`GET /media/{asset_id}` is anonymous. An anonymous caller, or any authenticated
caller who is neither the uploader nor a moderator, sees a **published** asset
only, and only its EXIF-stripped **public copy** — never the private original,
never its EXIF facts, never an unpublished or rejected asset (reported as
missing, the same "not found, not forbidden" rule reports and events use). The
uploader and moderators additionally see the private original's presigned
download link. Moderation of media happens at
`POST /moderation/media/{asset_id}/decision`, `CanModerate` required.

### Route table (Phase 3)

Auth column as above. `M` marks a route under `/api/v1/moderation`, requiring
`CanModerate` first, unless noted otherwise.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/api/v1/reports` | auth | `Idempotency-Key` optional; idempotent on `client_report_id`; `201`, `Location`, `ETag`. |
| `GET` | `/api/v1/reports` | auth | Moderators see every report; others only their own; cursor pagination; GeoJSON negotiation; always rounded. |
| `GET` | `/api/v1/reports/{report_id}` | auth | Exact view for the reporter and moderators; rounded, no accuracy, for organisation members; `ETag`. |
| `POST` | `/api/v1/reports/{report_id}/revisions` | auth | Reporter only (`IsSelf`); required `If-Match`; `201`, `Location`, `ETag`. |
| `POST` | `/api/v1/reports/{report_id}/withdrawal` | auth | Reporter only; required `If-Match`; `ETag`. |
| `POST` | `/api/v1/media` | auth | Presigned upload grant before the report exists; `Idempotency-Key` optional; `201`, `Location`. |
| `POST` | `/api/v1/reports/{report_id}/media` | auth | Presigned upload grant for the caller's own report; same as above. |
| `POST` | `/api/v1/media/{asset_id}/complete` | auth | Uploader only; safe to repeat; `ETag`. |
| `GET` | `/api/v1/media/{asset_id}` | anon | See "Media visibility" above; `ETag`. |
| `POST` | `/api/v1/moderation/media/{asset_id}/decision` | auth (policy, M) | `CanModerate`; approves/rejects, sets sensitivity, may publish the public copy; `ETag`. |
| `GET` | `/api/v1/events` | anon | Only `published` and `verified` events, unless the caller can moderate; cursor pagination; GeoJSON negotiation. |
| `GET` | `/api/v1/events/{event_id}` | anon | Same visibility rule; `ETag`; GeoJSON negotiation. |
| `GET` | `/api/v1/events/{event_id}/timeline` | anon | Same visibility rule; observations, period, verification transitions and claims in timeline order. |
| `GET` | `/api/v1/events/{event_id}/impacts` | anon | Follows the event's own visibility; best figure per metric plus every claim, active and retracted; no moderator names, no retraction reasons or notes. |
| `GET` | `/api/v1/infrastructure-assets/{asset_id}` | anon | Public reference data; `ETag`. |
| `POST` | `/api/v1/moderation/events` | auth (policy, M) | Creates a draft event from report ids (`EventFactory.from_reports`); `Idempotency-Key` optional; `201`, `Location`, `ETag`. |
| `PATCH` | `/api/v1/moderation/events/{event_id}` | auth (policy, M) | Geometry, period and/or attributes; one command per member sent; optional `If-Match` (narrows, does not close, a race — open question); `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/reports` | auth (policy, M) | Links a report with a role; optional `If-Match`; `ETag`. |
| `DELETE` | `/api/v1/moderation/events/{event_id}/reports/{report_id}` | auth (policy, M) | Unlinks with a reason (JSON body, RFC 9110 §9.3.5); optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/places` | auth (policy, M) | Adds an affected place with a kind; optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/relations` | auth (policy, M) | Relates this event to another (`triggered_by`/`part_of`/`same_as`); optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/publication` | auth (policy, M) | Sets `status=published`; does not itself require `verified` (open question); optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/retraction` | auth (policy, M) | Sets `status=retracted` with a reason; final; optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/merge` | auth (policy, M) | Sets `status=merged`, points at the survivor; final; optional `If-Match`; `ETag`. |
| `GET` | `/api/v1/moderation/verification-cases` | auth (policy, M) | `CanModerate`; filters on state, target kind, assignee; cursor pagination. |
| `GET` | `/api/v1/moderation/verification-cases/{case_id}` | auth (policy, M) | With history and `ETag`. |
| `POST` | `/api/v1/moderation/verification/{case_id}/transitions` | auth (policy, M) | Moves the case; the API always sends `is_human=True`; `verified` reachable only this way; a reason is required for every target except `submitted`; optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/verification/{case_id}/assignment` | auth (policy, M) | Sets the reviewer; not allowed on `rejected`/`retracted` cases; optional `If-Match`; `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/impact-claims` | auth (policy, M) | Records a claim; `Idempotency-Key` optional; `201`. |
| `POST` | `/api/v1/moderation/impact-claims/{claim_id}/retraction` | auth (policy, M) | `204`; no read route for one claim outside `/events/{id}/impacts` (open question). |
| `POST` | `/api/v1/moderation/impact-claims/{claim_id}/correction` | auth (policy, M) | New claim, old one retracted, same unit of work; `Idempotency-Key` optional; `201`. |
| `POST` | `/api/v1/moderation/infrastructure-assets` | auth (policy, M) | Registers an asset; `Idempotency-Key` optional; `201`, `Location`, `ETag`. |
| `POST` | `/api/v1/moderation/events/{event_id}/damage-records` | auth (policy, M) | Records damage to an asset; `Idempotency-Key` optional; `201`; no read route for one damage record (open question). |
| `GET` | `/api/v1/sources` | anon | Cursor pagination; optional `source_type` filter. |
| `GET` | `/api/v1/sources/{source_id}` | anon | `ETag`. |
| `POST` | `/api/v1/moderation/sources` | auth (policy, M) | Registers a `government`/`news`/`satellite`/`research`/`dataset` source (citizen and organisation sources are registered by the platform itself at report submission); `Idempotency-Key` optional; `201`, `Location`, `ETag`. |

## Phase 4 additions

Phase 4 (`docs/plans/phase-4.md`) added the `exchange` (exports, imports, the
historical backfill contract) and `ingestion` (the dataset catalog, observations,
raster assets) routes below. The conventions above apply unchanged; every creating
`POST` in `exchange` answers `202 Accepted` (the job is queued; a worker runs it) and
accepts `Idempotency-Key`. See [`exchange.md`](exchange.md) and
[`ingestion.md`](ingestion.md) for the full flow each route belongs to.

### Route table (Phase 4)

Auth column as above; `M` marks a route under `/api/v1/moderation`. `ingestion`'s
reads are anonymous (`docs/open-questions.md`, run reports and observations public by
default); its writes live under `/api/v1/admin` and require `IsAdmin`.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/api/v1/exports` | auth (policy) | `export_policy`: any authenticated user for `events`/`claims`, moderators only for `reports`; `202`; `Location`, `ETag`. |
| `GET` | `/api/v1/exports` | auth | Every job for a moderator, else the caller's own; cursor pagination. |
| `GET` | `/api/v1/exports/{job_id}` | auth (policy) | Owner or moderator, else `404`; `ETag`; fresh `download_url` and inline sidecar once completed. |
| `DELETE` | `/api/v1/exports/{job_id}` | auth (policy) | Owner or moderator; `204`; `409` once started or final. |
| `POST` | `/api/v1/moderation/imports/uploads` | auth (policy, M) | `import_policy`; presigned upload grant; `201`. |
| `POST` | `/api/v1/moderation/imports` | auth (policy, M) | `import_policy`; `dry_run` required; `202`; `Location`, `ETag`. |
| `GET` | `/api/v1/moderation/imports/{job_id}` | auth (policy, M) | `import_policy`; `ETag`; row-level report once produced. |
| `GET` | `/api/v1/datasets` | anon | Cursor pagination; optional status filter. |
| `GET` | `/api/v1/datasets/{code}` | anon | `ETag`. |
| `GET` | `/api/v1/datasets/{code}/runs` | anon | Newest first. |
| `GET` | `/api/v1/ingestion-runs/{run_id}` | anon | Full report. |
| `GET` | `/api/v1/observations` | anon | Half-open `[from, to)` window; cursor pagination. |
| `GET` | `/api/v1/raster-assets` | anon | GeoJSON/STAC negotiation; `Vary: Accept`. |
| `POST` | `/api/v1/admin/datasets` | auth (policy) | `IsAdmin`; licence required; `201`, `Location`, `ETag`. |
| `POST` | `/api/v1/admin/datasets/{code}/versions` | auth (policy) | `IsAdmin`; `201`. |
| `POST` | `/api/v1/admin/datasets/{code}/status` | auth (policy) | `IsAdmin`; `If-Match` optional. |
| `POST` | `/api/v1/admin/datasets/{code}/runs` | auth (policy) | `IsAdmin`; `202`; runs `ingestion.run`. |
| `POST` | `/api/v1/admin/raster-assets` | auth (policy) | `IsAdmin`; `201`. |

## OpenAPI snapshot and contract tests

`tests/contract/openapi.json` is the committed HTTP contract (`tests/contract/README.md`).
After a route changes:

1. Regenerate it: `poetry run poe openapi-snapshot` (`python -m
   yakhnama.platform.openapi_snapshot`, a `[tool.poe.tasks]` entry).
2. Review the diff like any other change: a removal or a narrowed type is a breaking
   change and needs an ADR before it ships.
3. Verify the committed snapshot still matches the app: `poetry run poe contract`
   (`pytest tests/contract`), part of the `poe check` gate (`pyproject.toml`,
   `[tool.poe.tasks.check]`).

## Interactive documentation

When `docs_enabled` is true (the development default; **must** be `false` in production,
`platform/settings.py`'s production guard), `GET /api/v1/docs` serves a Scalar API
reference page built from `GET /api/v1/openapi.json` (ADR 0014). Swagger UI and ReDoc are
never served (`docs_url=None`, `redoc_url=None` in `create_app`). The Scalar bundle itself
is loaded from a CDN by default (`docs_scalar_js_url`); see `docs/open-questions.md` Q68
for self-hosting it with an integrity hash.

## Further reading

- `docs/architecture/README.md` — the module map, layers and the write/read flow.
- `docs/architecture/auth.md` — the token flow, the development realm and the backend's
  validation rules.
- `docs/adr/0014-scalar-api-reference.md`, `0015-jwt-validation-with-pyjwt-and-a-cached-jwks.md`,
  `0016-idempotency-keys-in-postgresql.md`, `0017-rate-limiting-behind-a-port.md`.
- `docs/data-dictionary/identity.md` — the `identity` module's fields, roles and policies.
- `docs/architecture/recording.md` — the Phase 3 recording flow, reporter privacy rules,
  task schedules and outbox semantics behind the routes in "Phase 3 additions".
- `docs/architecture/exchange.md`, `docs/architecture/ingestion.md` — the Phase 4
  export/import and dataset-catalog flows behind the routes in "Phase 4 additions".
- `docs/open-questions.md` — Q58–Q70 record the authorisation and API defaults this page
  documents that are not yet maintainer-confirmed; Q77 onward record the Phase 3 ones;
  Q180 onward the Phase 4 ones.
