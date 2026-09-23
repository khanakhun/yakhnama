# 0007. Transactional outbox for domain events

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Many state changes have side effects: a verified report must update the event's best
figures, write an audit entry, notify moderators, invalidate cached exports and perhaps
start media processing. These side effects must happen **if and only if** the change
committed. A notification about a verification that was rolled back, or a committed
verification with no audit entry, both damage trust in the record.

How are side effects of domain changes triggered reliably, without coupling modules to each
other?

## Decision drivers

- Correctness: no side effect for an uncommitted change; no committed change without its
  side effects.
- Auditability: every change to verified data leaves an audit record.
- Module decoupling: modules react to each other's events without importing internals
  ([0001](0001-modular-monolith-with-hexagonal-layers.md)).
- Resilience: a failing subscriber (for example an email provider) must not roll back or
  block the user's write.
- Small team: no message broker to operate just for this.

## Considered options

1. Domain events on aggregates + transactional outbox + relay to Observer subscribers
   (chosen)
2. In-process synchronous handlers called during the request
3. Publishing to a message broker inside the transaction
4. Event sourcing

## Decision outcome

Chosen option: **transactional outbox**, because it makes "commit the change" and "record
that its side effects are due" one atomic database write.

- Aggregates record domain events (past-tense names such as `ReportSubmitted`, defined per
  module in `domain/events.py` on the `shared_kernel` base) as part of their state change.
- The unit of work collects those events and inserts them into an `outbox` table **in the
  same transaction** as the aggregate changes.
- After commit, a relay reads pending outbox rows and dispatches them to registered
  subscribers (Observer pattern), marking rows as delivered. The relay runs as a background
  job ([0008](0008-taskiq-behind-a-task-queue-port.md)); its code lives in
  `platform/outbox/`.
- The audit log is written by a subscriber from outbox events, so every event that changes
  data produces an audit record.
- Delivery is **at least once**. Every subscriber must be idempotent, keyed by the event id
  (a UUIDv7, [0006](0006-uuidv7-identifiers.md)).
- Retry limits, back-off, ordering guarantees per aggregate, dead-letter handling and
  outbox retention are specified in the phase that builds `platform/outbox/`.

### Consequences

- Good, because a side effect can never be triggered by a rolled-back change, and a
  committed change can never lose its side effects.
- Good, because a slow or failing subscriber does not affect the user's request.
- Good, because modules subscribe to events instead of calling each other, keeping
  dependencies one-directional.
- Good, because the outbox table itself is a queryable record of what happened, useful for
  debugging and audits.
- Bad, because side effects are eventually consistent: read models such as best figures may
  lag the write by the relay interval, which the API and UI must tolerate.
- Bad, because at-least-once delivery means duplicates are normal; a non-idempotent
  subscriber is a bug that may only appear under failure.
- Bad, because the audit log depends on the relay running; if the relay is down, audit
  records are delayed (not lost). Monitoring of outbox lag is required.
- Bad, because the outbox table grows continuously and needs a retention or archival job.
- Bad, because audit written asynchronously is not visible in the same response; if a later
  requirement demands synchronous audit for some actions, that needs a separate decision.

## Pros and cons of the options

### Transactional outbox with Observer subscribers

- Good, because atomicity comes from the database we already have.
- Bad, because it adds a relay process, idempotency discipline and table maintenance.

### In-process synchronous handlers

- Good, because it is simple and immediately consistent.
- Bad, because a handler that runs before commit may act on a change that is then rolled
  back; one that runs after commit is lost if the process crashes in between.
- Bad, because a slow external call (email, storage) blocks or fails the user's request.

### Broker publish inside the transaction

- Good, because consumers get a real message stream.
- Bad, because the database commit and broker publish are two systems without a shared
  transaction (the dual-write problem): either can succeed while the other fails.
- Bad, because it adds a broker as critical infrastructure for writes. A broker can still be
  added later as a subscriber target fed by the relay.

### Event sourcing

- Good, because the event log is the source of truth and history is complete.
- Bad, because it makes corrections, schema evolution, removal of personal data and
  research SQL much harder, as discussed in
  [0001](0001-modular-monolith-with-hexagonal-layers.md).

## More information

- `AGENTS.md` §3 (Domain Events + Transactional Outbox + Observer), §7 (Outbox).
- Chris Richardson, "Pattern: Transactional outbox", microservices.io.
