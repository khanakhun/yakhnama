# 0017. Rate limiting behind a port with in-memory and Redis adapters

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

The public API, and `/health/ready` in particular, must not let one client exhaust the
database or starve others (Phase 1 security review). Several API processes run behind a
load balancer in production, so per-process counters are not enough there, but development
and tests must work without Redis. The Phase 2 plan fixes a port, an in-memory adapter and a
Redis adapter, per-IP and per-principal keys, and 429 Problem Details with `Retry-After`.

Which algorithm does each adapter use, what is the key, what happens when Redis is down, and
how is readiness protected?

## Decision drivers

- Fairness per caller; authenticated callers identified by principal, anonymous ones by IP.
- Privacy: no raw IP address stored or logged (`AGENTS.md` §5).
- Availability during a disaster, when the record matters most.
- One round trip to Redis per request; bounded memory in process.
- Testability with a frozen clock and without Redis in unit tests.

## Considered options

1. In-memory token bucket plus a Redis fixed window in one Lua script (chosen)
2. Sliding-window log in Redis (sorted sets)
3. A library such as `slowapi` or `limits`

## Decision outcome

Chosen option: **1**, because each adapter is the simplest correct algorithm for its setting
and both fit behind one small port.

- **Port.** `RateLimiter.check(key, limit_per_minute) -> RateLimitDecision(is_allowed,
  limit, remaining, retry_after_seconds)`.
- **In memory.** A token bucket per key: capacity `limit`, refilled continuously at
  `limit / 60` per second, time from the injected `Clock`, at most 10 000 buckets with
  least-recently-used eviction. One process only: development and tests.
- **Redis.** A fixed one-minute window: one Lua script does `INCR`, sets `PEXPIRE` on the
  first request of the window (and heals a key that lost its expiry) and returns the count
  and the milliseconds left, atomically. Keys are prefixed `yakhnama:ratelimit:`.
- **Keys.** `principal:<Principal.scope_key()>` for a valid token, else
  `ip:<first 128 bits of SHA-256 of the client IP>`. The IP is the ASGI peer address;
  behind a proxy uvicorn must run with `--proxy-headers` and a trusted `--forwarded-allow-ips`.
- **Limits.** `rate_limit_anonymous_per_minute` (default 60) and
  `rate_limit_authenticated_per_minute` (default 300), operational defaults to tune per
  deployment; `rate_limit_enabled` (default true; required in production) and
  `rate_limit_backend` (`memory` or `redis`; `redis` required in production).
- **Responses.** Over the limit: 429 `rate-limited` with `Retry-After`; every counted
  response carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`.
- **Redis down.** The request is allowed and `rate_limiter_unavailable` is logged with the
  error type ("fail open").
- **Readiness.** `/health/*` is not rate-limited, because orchestrators probe from a few
  addresses and a throttled probe takes a healthy process out of service. Instead
  `/health/ready` caches its result for one second per process (`ReadinessCache`), so the
  database sees at most one probe query per second per process however often it is called.

### Consequences

- Good, because production limits are shared across processes with one round trip.
- Good, because Redis and the limiter store never hold a raw IP address.
- Good, because an outage of Redis does not take the API down with it.
- Bad, because a fixed window lets up to twice the limit through around a window boundary.
- Bad, because failing open means no rate limiting while Redis is down; the warning log is
  the signal to alert on.
- Bad, because hashed IPv4 addresses are brute-forceable; the hash only keeps casual
  inspection of Redis from showing addresses, and keys expire after a minute.
- Bad, because clients behind one carrier-grade NAT share one anonymous bucket, common on
  mobile networks in the region; authenticated callers are unaffected.

## Pros and cons of the options

### Option 1, token bucket and fixed window

- Good, because both are simple, O(1) per key and easy to test.
- Bad, because the two adapters differ slightly at the edges (burst behaviour).

### Option 2, sliding-window log

- Good, because it is exact.
- Bad, because it stores one entry per request and costs more per call.

### Option 3, a library

- Good, because it is ready-made.
- Bad, because it adds a dependency with its own configuration and key model, and would
  still need wrapping to read our principal and to answer with Problem Details.

## More information

- `platform/ratelimit/`; the middleware runs after principal resolution and before the body
  is read (`platform/http.py` module docstring lists the order).
- ADR 0015 (principal and `scope_key`), ADR 0016 (idempotency).
