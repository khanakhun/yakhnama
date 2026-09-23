# 0016. Idempotency keys stored in PostgreSQL with a 72-hour TTL

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Clients in the region often have unreliable connections: a `POST` whose response is lost is
retried, and without protection each retry creates another organisation, report or claim.
The Phase 2 plan fixes the behaviour: an `Idempotency-Key` on creating `POST`s, the same key
with the same body replays the stored response, the same key with another body is 409,
scoped per principal, 72 hours of retention. Two copies of one request can also arrive at
the same time.

Where are keys and responses stored, and how are concurrent requests with one key handled?

## Decision drivers

- Correctness: a retried request must never run the route twice, including when two copies
  race (`AGENTS.md` §1: a duplicated record damages the only record).
- No new infrastructure for a durable store; PostgreSQL is already the system of record
  (ADR 0002).
- Privacy: stored responses may contain personal data; keep as little as possible, for as
  short as possible, visible only to the same principal.
- Standards: the IETF `Idempotency-Key` header draft.

## Considered options

1. A PostgreSQL table with a reservation row taken before the route runs (chosen)
2. A PostgreSQL table written only after the route succeeds, with the unique constraint
   deciding the winner afterwards
3. Redis with `SET NX` and a TTL

## Decision outcome

Chosen option: **1**, because it is the only option where the loser of a race never runs the
route, and it needs no new service.

- **Scope.** Only authenticated `POST` requests under `/api/v1` that carry the header are
  handled; others pass through (an anonymous `POST` reaches its route and gets 401). The key
  must be one UUID, else 400 `invalid-idempotency-key`. It is scoped to
  `Principal.scope_key()`, so two users never see each other's keys or responses.
- **Fingerprint.** SHA-256 over the method, path, query string and body (length-prefixed),
  so the same key on another endpoint is also "a different request".
- **Reservation.** `IdempotencyStore.reserve` runs `INSERT ... ON CONFLICT DO NOTHING
  RETURNING` in its own short transaction; the unique constraint on `(scope, key)` decides
  the one winner. A pending reservation lives for 5 minutes (`PENDING_LEASE`), so a crash
  blocks the key briefly, not for the whole TTL.
- **Outcomes.** The winner runs the route. A 2xx response of at most 256 KiB is stored
  (`complete`) and kept for `idempotency_ttl_hours` (1–168, default 72); anything else
  deletes the reservation (`release`), so the client may retry after a failure. A loser
  re-reads the record: same fingerprint and stored response → replay with
  `Idempotent-Replayed: true`; same fingerprint still running → 409
  `idempotency-key-in-use` with `Retry-After: 1`; different fingerprint → 409
  `idempotency-key-reused`. An expired record counts as absent and is replaced.
- **Stored data.** Status, body bytes, and only the `Content-Type`, `Location` and `ETag`
  headers (never `Set-Cookie`). `purge_expired(now)` deletes lapsed rows; it is scheduled by
  the task queue when that arrives (ADR 0008).
- **Table** `idempotency_keys` (ORM in `platform/idempotency/models.py`, migration
  `0006_idempotency_keys` by the persistence-engineer): `id uuid` primary key, `scope
  varchar(255)`, `key uuid`, `request_hash char(64)`, `method varchar(10)`, `path
  varchar(2048)`, `status_code smallint NULL`, `response_body bytea NULL`,
  `response_headers jsonb NULL`, `created_at` and `expires_at timestamptz`; unique
  `uq_idempotency_keys_scope_key (scope, key)`; index `ix_idempotency_keys_expires_at`;
  checks `ck_idempotency_keys_status_code_success` (NULL or 200–299) and
  `ck_idempotency_keys_response_complete` (status, body and headers all NULL or all set).

### Consequences

- Good, because a lost response can be retried safely, and a race never creates two
  resources.
- Good, because the store is transactional, backed up with the rest of the data and needs no
  new service.
- Bad, because every idempotent `POST` costs two or three extra short transactions.
- Bad, because the reservation is not in the route's own transaction: if the route commits
  and the process dies before `complete`, the key is released by lease expiry and a retry
  after 5 minutes runs the route again. Closing this needs the reservation inside the unit
  of work, which is left for a later ADR if it proves necessary.
- Bad, because stored response bodies can hold personal data for up to 72 hours; they are
  per principal and purged, but the purge job does not exist until the task queue does.

## Pros and cons of the options

### Option 1, reservation first

- Good, because exactly one request runs the route.
- Bad, because it needs a lease for crashed requests.

### Option 2, store after success

- Good, because it is one write per request.
- Bad, because both racing requests run the route and create two resources before the
  constraint rejects the second row: the duplicate this feature exists to prevent.

### Option 3, Redis

- Good, because `SET NX PX` is a natural reservation and expiry is automatic.
- Bad, because Redis is optional in development, not durable by default, and responses
  would live outside the backed-up system of record.

## More information

- The brief suggested a `get`/`put` port with "second insert loses, treat as replay"; the
  reservation variant keeps that re-read behaviour but prevents the double execution.
- IETF draft `draft-ietf-httpapi-idempotency-key-header`; ADR 0002, ADR 0008.
- `platform/etag.py` (same phase) adds `PreconditionRequiredError` (428) to the kernel for
  `If-Match`; see ADR 0015, More information, for the `AGENTS.md` §2.3 note.
