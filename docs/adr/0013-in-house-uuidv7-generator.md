# 0013. In-house RFC 9562 UUIDv7 generator

- Date: 2026-09-23
- Status: proposed
- Deciders: lead agent, maintainer

## Context and problem statement

[ADR 0006](0006-uuidv7-identifiers.md) makes UUIDv7 the identifier for every entity and
puts generation behind an `IdGenerator` port in `shared_kernel`, but defers the choice
between a third-party library and an in-house implementation to Phase 1. Python 3.14
ships `uuid.uuid7()`; Python 3.13, our minimum and one of the two CI interpreters, does
not. The first entities land in Phase 1, so the production adapter is needed now. Which
UUIDv7 implementation backs the `IdGenerator` port?

## Decision drivers

- Correctness of every identifier in the system of record: RFC 9562 layout, version and
  variant bits, and ordering that never regresses (`AGENTS.md` §1).
- One code path on Python 3.13 and 3.14, so CI on both interpreters tests the same
  generator (`AGENTS.md` §4).
- No dependency without architectural weight: each new package adds audit surface
  (`poe security`) and the kernel may import only the standard library, `pydantic` and
  `geojson-pydantic` (`AGENTS.md` §2.1).
- Deterministic tests through injected time and randomness (`AGENTS.md` §4.2).
- Identifiers are published, so the random part must come from a CSPRNG (ADR 0006).

## Considered options

1. In-house generator in `shared_kernel/ids.py` (proposed)
2. A small third-party package (for example `uuid6` or `uuid-utils`)
3. Python 3.14's `uuid.uuid7()` with a fallback on 3.13

## Decision outcome

Proposed option: **1, an in-house generator**, because it is about fifty lines of
integer arithmetic, runs identically on both interpreters, adds no dependency, and
accepts the injected `Clock` and random source the tests need.

`Uuid7Generator` follows RFC 9562 §5.7 and §6.2 "Method 1" with a 42-bit counter: the
top 12 counter bits fill `rand_a`, the low 30 fill the top of `rand_b`, and 32 bits of
`rand_b` are fresh random bits from `secrets.token_bytes` for every identifier. The
counter is reseeded from random bits each new millisecond with its top bit cleared
(the RFC's rollover guard). Within one millisecond, and when the wall clock steps
backwards, the last timestamp is kept and the counter incremented, so identifiers from
one generator are strictly increasing. If the counter ever overflows, the timestamp is
advanced one millisecond ahead of the clock (allowed by §6.2) instead of blocking, so a
frozen test clock cannot hang the generator. CPython 3.14's `uuid.uuid7()` uses the same
42-bit counter and 32-bit random tail, so switching later keeps the ordering properties.

`is_uuid7`, `extract_timestamp` and the `EntityId` Pydantic type validate client-
supplied identifiers as ADR 0006 requires. Tests cover the RFC appendix A.6 timestamp,
version and variant bits, timestamp round-trips, strict ordering of 10,000 identifiers
in one frozen millisecond, a clock moving backwards, counter rollover and bad inputs.

**What is needed to move this ADR to `accepted`:**

1. The maintainer's confirmation of the in-house choice (Phase 1 plan, question Q1).

### Consequences

- Good, because 3.13 and 3.14 share one tested implementation and no package is added.
- Good, because time and randomness are injected, so tests are deterministic.
- Good, because the generator is thread-safe and never blocks, even on a frozen clock.
- Bad, because we own correctness of a standard algorithm; mitigated by property tests
  and an RFC test vector, and by the port, which lets us replace it in one place.
- Bad, because ordering is guaranteed per generator instance only; identifiers from
  different processes in the same millisecond are unique but not mutually ordered (true
  of every UUIDv7 implementation).
- Bad, because the counter can run ahead of the wall clock under extreme load or a
  regressed clock, so the embedded time may be slightly later than the true creation
  time; ADR 0006 already forbids treating it as a business timestamp.

## Pros and cons of the options

### In-house generator

- Good, because it has no dependency and full control over injection and testing.
- Bad, because the project maintains it until Python 3.13 support ends.

### Third-party package

- Good, because someone else maintains the algorithm.
- Bad, because it is a new runtime dependency in the kernel for a tiny function, with
  its own release cadence, audit findings and, for compiled packages, wheels per
  platform; most do not accept an injected clock.

### Standard library on 3.14 with a 3.13 fallback

- Good, because 3.14 users get the reference implementation.
- Bad, because the two CI interpreters would exercise different code, and the fallback
  would still have to be written, so it combines the costs of options 1 and 2.

## More information

- This ADR resolves the library-or-in-house choice that
  [ADR 0006](0006-uuidv7-identifiers.md) deferred to Phase 1. ADR 0006 stays accepted and
  unchanged; once this ADR is accepted, a note in 0006 pointing here may be added by the
  maintainer or lead.
- RFC 9562, §5.7 (UUID version 7), §6.2 (monotonicity and counters), appendix A.6.
- Implementation: `src/yakhnama/shared_kernel/ids.py`; tests:
  `tests/unit/shared_kernel/test_ids.py`.
- Revisit when Python 3.13 support is dropped: `uuid.uuid7()` can then replace the
  adapter behind the same port.
- Phase 1 plan: `docs/plans/phase-1.md` §2 and T2.
