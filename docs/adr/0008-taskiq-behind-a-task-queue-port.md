# 0008. Taskiq behind a `TaskQueue` port for background jobs

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Several kinds of work must not run inside an HTTP request: dataset exports for researchers,
bulk imports from external sources, media processing (EXIF stripping, thumbnails, hashing)
and the outbox relay ([0007](0007-transactional-outbox-for-domain-events.md)). These jobs
can be long, can fail and must be retried, and they use the same async stack (SQLAlchemy,
object storage) as the API.

How are background jobs scheduled and executed, and how tightly is the code bound to the
chosen tool?

## Decision drivers

- Async-native execution, reusing the same async adapters as the API.
- Retries and failure visibility for jobs that touch the system of record.
- Replaceability: the queue technology must not leak into application code.
- Testability: application code enqueues jobs through a port that has an in-memory Fake.
- Small team: simple to run in development (the Phase 0 `docker-compose.yml` provides Redis).

## Considered options

1. Taskiq behind a `TaskQueue` port, Redis as broker in development (chosen)
2. Celery
3. arq
4. Dramatiq
5. FastAPI `BackgroundTasks` only

## Decision outcome

Chosen option: **a `TaskQueue` port with Taskiq as the first adapter**, using Redis as the
broker in development, because Taskiq runs `async def` tasks natively and the port keeps it
swappable.

- Application code depends only on a `TaskQueue` `Protocol` (enqueue a named job with a
  Pydantic payload). No module imports Taskiq.
- The Taskiq adapter and worker entry point live in `platform/`; the binding happens in the
  composition root (`platform/container.py`).
- Job payloads are Pydantic models carrying ids, not entity snapshots; the job reloads state
  inside its own unit of work, so a stale payload cannot overwrite newer data.
- Jobs are idempotent, because brokers deliver at least once.
- The production broker, result backend, scheduling of periodic jobs (such as the outbox
  relay) and the Taskiq packages to add are chosen in the phase that introduces the first
  job; none are in `pyproject.toml` yet.

### Consequences

- Good, because workers share the async repositories, storage adapters and settings with the
  API, with no sync/async bridge.
- Good, because tests use a Fake `TaskQueue` and run jobs in-process, without Redis.
- Good, because replacing Taskiq (for example with Celery) touches one adapter and the worker
  entry point, not application code.
- Bad, because Taskiq is younger and has a smaller community than Celery, so fewer answers
  exist for operational problems, and some features (monitoring UIs, scheduling) come from
  separate packages of varying maturity.
- Bad, because a port that must fit any queue exposes only the common subset (enqueue,
  delay); features unique to one tool, such as workflow chaining, are not used.
- Bad, because Redis is one more stateful service; if it is used without persistence,
  enqueued jobs can be lost on restart, which the outbox relay must tolerate by re-reading
  pending rows.

## Pros and cons of the options

### Taskiq behind a port

- Good, because it is async-native and typed, with pluggable brokers.
- Bad, because of its smaller ecosystem.

### Celery

- Good, because it is the most mature Python task queue with wide operational knowledge.
- Bad, because its worker model is synchronous; running our async adapters inside it needs
  an event-loop bridge in every task.

### arq

- Good, because it is asyncio-native and small.
- Bad, because it is tied to Redis and has a smaller feature set and contributor base.

### Dramatiq

- Good, because it is reliable, simple and well regarded.
- Bad, because its core worker model is synchronous, with the same bridging cost as Celery.

### FastAPI `BackgroundTasks` only

- Good, because it needs no extra infrastructure.
- Bad, because tasks run in the API process after the response and are lost if the process
  restarts; there are no retries, no separate scaling and no visibility, which is
  unacceptable for exports, imports and the outbox relay.

## More information

- `AGENTS.md` §3 (Adapter, Decorator for retry and idempotency).
- `docker-compose.yml`, Phase 0 task T8 (Redis service for development).
