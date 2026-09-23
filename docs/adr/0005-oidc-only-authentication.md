# 0005. OIDC-only authentication and policy-based authorisation

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Yakhnama has citizens submitting reports, moderators verifying them, organisation staff,
researchers and administrators. Some reports concern sensitive situations (casualties,
damaged homes), and the moderation trail must show who did what. Storing credentials
ourselves would make the backend a high-value target and put password security, reset
flows and multi-factor authentication on a small team.

How do users authenticate, and how does the backend decide what they may do?

## Decision drivers

- Security: never store passwords (`AGENTS.md` §5); minimise what an attacker gains from a
  database leak.
- Auditability: every action is attributable to a stable subject identifier.
- Offline mobile clients: tokens obtained online must be refreshable by standard flows,
  without custom protocols.
- Provider independence: the identity provider may change; only one adapter may know it.
- Least privilege: nothing is allowed unless a policy says so.
- Tests make no network calls (`AGENTS.md` §5).

## Considered options

1. OpenID Connect only; the backend validates JWT access tokens (chosen)
2. Built-in username and password accounts
3. Server-side session cookies
4. API keys only

## Decision outcome

Chosen option: **OIDC only**, because it delegates credential handling to a dedicated
identity provider and leaves the backend a stateless token validator.

- The backend never stores or receives passwords. Clients obtain tokens from the identity
  provider with standard OIDC flows.
- Every request's bearer JWT is validated against the provider's JWKS: signature, `iss`,
  `aud`, `exp` and `nbf`. The JWKS is cached with a TTL and refreshed on an unknown `kid`
  to follow key rotation, with a bounded refresh rate. Allowed algorithms are an explicit
  allow-list.
- Keycloak runs in `docker-compose.yml` (Phase 0 task T8) for development. The **production provider
  (Keycloak or Zitadel) is pending a maintainer decision**; this ADR does not choose it.
- The OIDC adapter in `platform/auth` is the only code that knows the provider: it maps
  provider-specific claims (roles, groups, organisation) into our own principal value
  object. Nothing else reads raw token claims.
- Authorisation is expressed as composable Policy objects in
  `modules/identity/domain/policies.py` (Specification-style `and_`/`or_`/`not_`),
  evaluated in the application layer. **Deny by default**: an action with no matching
  policy is refused with `PermissionDeniedError`.
- Tests use a Fake token verifier and locally generated keys, never a live provider.
- The JWT library, claim mapping, role names and token lifetimes are chosen in the phase
  that implements identity; this ADR fixes only the approach.

### Consequences

- Good, because a database leak exposes no credentials, and MFA, password resets and
  brute-force protection are the provider's job.
- Good, because tokens are validated locally from cached keys, with no per-request call to
  the provider.
- Good, because swapping providers means rewriting one adapter and its claim mapping.
- Good, because deny-by-default policies in the domain are unit-testable and reviewable in
  one place.
- Bad, because an identity provider is one more service to run, secure, back up and
  upgrade, and it is a single point of failure for new logins.
- Bad, because stateless JWTs cannot be revoked instantly; a compromised or demoted account
  keeps access until the token expires, so token lifetimes must be short and sensitive
  actions may need extra checks.
- Bad, because offline mobile clients depend on refresh tokens whose lifetime and storage
  are a mobile security concern outside this repository.
- Bad, because citizens without smartphones or email may find account creation a barrier;
  whether anonymous or assisted reporting is allowed is an open product question, not
  decided here.
- Bad, because until the production provider is chosen, provider-specific details (claim
  names, organisation modelling) cannot be finalised.

## Pros and cons of the options

### OIDC only

- Good, because it is a standard with mature open-source providers and client libraries.
- Bad, because it adds infrastructure and a dependency on the provider's availability.

### Built-in username and password

- Good, because it has no external dependency.
- Bad, because we would store password hashes and own resets, MFA and lockout, which the
  project rules forbid and a small team cannot secure well.

### Session cookies

- Good, because sessions are revocable immediately on the server.
- Bad, because they fit browsers, not offline mobile apps or research scripts, and bring
  CSRF handling and server-side session storage.

### API keys only

- Good, because they are simple for research scripts.
- Bad, because keys identify integrations, not people, so moderation actions would not be
  attributable to individuals, and rotation and scoping become our own system. Machine
  access, if needed, can use OIDC client credentials instead.

## More information

- OpenID Connect Core 1.0; RFC 7517 (JWK); RFC 7519 (JWT).
- `AGENTS.md` §3 (Policy pattern), §5 (no passwords, no personal data in logs).
- Production provider choice: tracked in `docs/open-questions.md`.
