# 0015. JWT validation with PyJWT and a cached JWKS client

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

ADR 0005 fixes OIDC-only authentication with locally validated JWT access tokens and leaves
the library, the claim mapping and the key handling to the phase that implements identity.
Phase 2 adds `PyJWT[crypto]`. Keycloak signs access tokens with RS256 by default, rotates
keys by publishing a new `kid`, and puts realm roles in `realm_access.roles`. Tests must
never reach a real identity provider (`AGENTS.md` §5).

How are bearer tokens validated, how are the provider's keys fetched and cached, and what
does the rest of the code see of a caller?

## Decision drivers

- Security: no algorithm confusion (`none`, `HS256` keyed with a public key), exact issuer and
  audience checks, bounded work on hostile input, no token text in errors or logs.
- Availability: no call to the provider per request; key rotation without a restart; a
  flood of made-up `kid` values must not turn the API into a load generator.
- Testability: time from the injected `Clock`, keys from a fake, no network.
- Provider independence: only `platform/auth` knows claim names (ADR 0005).

## Considered options

1. PyJWT for signature and claim checks, our own `HttpJwksClient` over `httpx` with a TTL
   cache, time claims checked against the injected clock (chosen)
2. PyJWT's built-in `PyJWKClient`
3. `authlib` or `python-jose`

## Decision outcome

Chosen option: **1**, because PyJWT is small and maintained, and owning the JWKS client is
the only way to use the async `httpx` client, the injected `Clock` and a rate-limited
refetch.

- **Algorithms.** `oidc_allowed_algorithms` is a list of `Literal["RS256", "ES256"]`, so
  `none` and `HS*` cannot even be configured. The token header's `alg` must be in the list
  *before* any key is looked up, and must equal the algorithm bound to the JWK (PyJWT
  refuses a mismatch when given a `PyJWK`). Symmetric (`oct`) and encryption (`use: enc`)
  keys are dropped when the JWKS is parsed.
- **Claims.** PyJWT checks the signature, `iss` (exact string equality with `oidc_issuer`),
  `aud` (must contain `oidc_audience`, default `yakhnama-api`) and the presence of `exp`,
  `iat`, `iss`, `aud` and `sub`. `exp`, `nbf` (when present) and `iat` are checked by
  `TokenValidator` against the injected `Clock` with `oidc_leeway_seconds` (0–60, default
  10) of tolerance.
- **Bounds.** Tokens longer than 8192 characters are refused unparsed; `sub` ≤ 255, role
  names ≤ 100, at most 100 roles, display name ≤ 200.
- **Errors.** Every rejection raises the same `AuthenticationError` (new in the shared
  kernel, HTTP 401 with `WWW-Authenticate: Bearer realm="yakhnama"`) with a fixed message.
  Only the reason (a PyJWT error type or a fixed slug) is logged, as `bearer_token_rejected`.
  A provider that cannot be reached raises `IdentityProviderUnavailableError` (HTTP 503),
  because the token is not at fault.
- **JWKS.** `HttpJwksClient` fetches `oidc_jwks_url`, or discovers it once from
  `<issuer>/.well-known/openid-configuration`, whose `issuer` must equal the configured one
  and whose `jwks_uri` must be `https` (or loopback `http`). Keys are cached for
  `jwks_cache_ttl_seconds` (60–86400, default 600). An unknown `kid` triggers one refetch;
  no fetch is attempted within 30 seconds of the last attempt, successful or not, which is
  also the backoff after a failure. A failed forced refetch keeps the fresh cache; when the
  refresh of an expired cache fails or is skipped by the backoff, the expired keys are
  served for one more TTL (`jwks_stale_served`, warning), then the provider counts as
  unavailable. Fetches are serialised by a lock, time out after
  `oidc_http_timeout_seconds`, never follow redirects and refuse documents above 256 KiB.
- **Principal.** The only view of a caller is the `Principal` DTO: `subject`, `issuer`,
  `realm_roles` (read from the one claim path `oidc_roles_claim`, default
  `realm_access.roles`; no other claim grants a role), `display_name`
  (`preferred_username` only, never `name`, the legal full name; optional personal data,
  never logged),
  `token_id` (`jti`) and `expires_at`. `Principal.scope_key()` is a SHA-256 of issuer and
  subject, used by rate limiting and idempotency.
- **Once per request.** `PrincipalResolutionMiddleware` validates the token once and keeps
  the outcome in the request state; `current_principal` and `optional_principal` read it. A
  request that sends credentials which are rejected gets 401 on every route, public or not.
- **No issuer configured.** `oidc_issuer = None` (the default) disables authentication:
  every presented token is rejected and protected routes answer 401.

### Consequences

- Good, because algorithm confusion, issuer spoofing and audience reuse are closed by
  construction and tested one by one.
- Good, because tests sign tokens with an in-memory key and use a fake JWKS client; no key
  material is committed and no network is used.
- Good, because key rotation needs no restart, and refetch storms are bounded.
- Bad, because we maintain a small JWKS client instead of using PyJWT's.
- Bad, because the exact issuer comparison makes the issuer depend on how Keycloak is
  reached (host and port); a mismatch rejects every token (documented in
  `docs/architecture/auth.md`).
- Bad, because Keycloak does not put `yakhnama-api` in `aud` by default; the realm needs an
  audience mapper (T8).
- Bad, because tokens stay valid until `exp` (ADR 0005); nothing here revokes them.

## Pros and cons of the options

### Option 1, PyJWT and our own JWKS client

- Good, because every behaviour above is explicit and testable with a frozen clock.
- Bad, because the client is code we own.

### Option 2, PyJWT's `PyJWKClient`

- Good, because it is ready-made.
- Bad, because it is synchronous (it would block the event loop or need a thread), uses
  `urllib` rather than `httpx`, reads the wall clock and cannot rate-limit refetches the way
  we need.

### Option 3, `authlib` or `python-jose`

- Good, because `authlib` covers all of OIDC.
- Bad, because it is a much larger dependency for one feature; `python-jose` is poorly
  maintained.

## More information

- The default `environment` stays `development`, but `create_app` logs
  `environment_defaulted` (warning) when `YAKHNAMA_ENVIRONMENT` was not set. The
  alternative, a required environment variable, was rejected for now because it would break
  every local start and test that relies on the default; it can replace the warning later.
- `AGENTS.md` §2.3 lists the kernel errors; this ADR adds `AuthenticationError` (401), and
  ADR 0016 adds `PreconditionRequiredError` (428). The list in `AGENTS.md` is edited only by
  the lead, with the maintainer's approval (see ADR 0012).
- RFC 7519 (JWT), RFC 7517 (JWK), RFC 8725 (JWT best current practices), RFC 6750
  (bearer tokens); OpenID Connect Discovery 1.0 §4.3.
