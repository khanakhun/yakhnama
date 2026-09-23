# 0001. Modular monolith with hexagonal layers

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Yakhnama is the system of record for hazard and disaster data in Gilgit-Baltistan. It has
several distinct concerns (reports, verification, events, impact claims, places, hazards,
sources, identity, ingestion, exchange) that change at different speeds and must not leak
into each other: a verification rule must not be bypassable by an import path, and a change
to an export format must not touch how impact claims are stored. The team is small (one
maintainer plus AI agents working in parallel on disjoint paths), and every change must be
reviewable and gated by tooling.

How should the backend be structured so that boundaries are real, testable without I/O and
enforced mechanically, without paying the operational cost of a distributed system?

## Decision drivers

- Correctness and auditability: domain rules live in one place, free of framework code, and
  are unit-testable with fakes (`AGENTS.md` §4.2).
- Small team: one deployable, one database, one CI pipeline, no network hops between
  modules to debug.
- Parallel agent work: modules and layers must have disjoint write paths and a clear
  import surface so concurrent changes do not collide.
- Boundaries enforced by tooling, not goodwill (`import-linter`, structural tests).
- Ability to extract a module later if a real scaling need appears, without rewriting it.
- Plain, readable Python for contributors from research and humanitarian organisations.

## Considered options

1. Modular monolith with hexagonal layers per bounded context (chosen)
2. Microservices, one service per bounded context
3. Layered monolith without module boundaries (global `models/`, `services/`, `routes/`)
4. Django-style apps
5. Full CQRS with a mediator library and event sourcing

## Decision outcome

Chosen option: **modular monolith with hexagonal layers**, because it gives enforced
boundaries and I/O-free domain tests at the operational cost of a single application.

- Each bounded context is a package under `src/yakhnama/modules/<name>/` with the layers
  `domain/`, `application/`, `infrastructure/` and `api/`, plus `public.py`, the Facade that
  is the only import surface for other modules.
- Ports are `typing.Protocol` classes in `application/ports.py`. Adapters live in
  `infrastructure/`. Only the composition roots, `main.py` and `platform/container.py`, bind
  ports to adapters.
- Framework-free building blocks (errors, value objects, specification, domain events,
  unit-of-work protocol, ids, clock, pagination) live in `shared_kernel/`; cross-cutting
  infrastructure lives in `platform/`.
- Reads and writes are separated as CQRS-lite: commands go through plain handler callables,
  a unit of work, repositories and entities; reads go through query services that return
  DTOs from optimised SQL. There is no mediator or bus library; handlers are wired in the
  composition root.
- Errors are raised as exceptions derived from `shared_kernel.errors.YakhnamaError` and
  mapped to HTTP in one place (see [0003](0003-fastapi-with-app-factory.md)). No Result
  monads.
- The layer and module rules in `AGENTS.md` §2.1 are enforced by `import-linter`
  (`poetry run poe arch`) and by structural tests in `tests/architecture/`.

### Consequences

- Good, because domain and application code can be tested with in-memory fakes and no
  database, which keeps the ≥ 95 % coverage target in those layers cheap to meet honestly.
- Good, because a module's internals can be refactored freely as long as `public.py` holds,
  and `lint-imports` fails the build if another module reaches past it.
- Good, because agents can work on different modules, or different layers of one module,
  without write conflicts.
- Good, because one process, one database and one transaction make the outbox
  ([0007](0007-transactional-outbox-for-domain-events.md)) and audit guarantees simple.
- Good, because a module with a stable Facade and its own ports can be extracted into a
  service later if a measured need arises.
- Bad, because the layering adds files and mapping code (ORM rows to entities, entities to
  DTOs, DTOs to API schemas) that a small CRUD app would not need.
- Bad, because boundaries between modules are logical, not physical: one database means a
  careless query in infrastructure could still join across another module's tables unless
  reviews and conventions catch it; `import-linter` checks imports, not SQL.
- Bad, because the whole application scales and deploys as one unit; a hot path (for
  example public dataset reads) cannot be scaled independently without scaling everything.
- Bad, because contributors used to Django or flat FastAPI projects face a learning curve.

## Pros and cons of the options

### Modular monolith with hexagonal layers

- Good, because boundaries are explicit and machine-checked.
- Good, because it runs as one process with one database, which a small team can operate.
- Neutral, because it requires discipline in reviews for what tooling cannot check (SQL
  across module tables).
- Bad, because of extra mapping and wiring code.

### Microservices

- Good, because services deploy and scale independently and boundaries are physical.
- Bad, because cross-context consistency (a report verified into an event with impact
  claims) becomes a distributed transaction or saga problem, which directly threatens the
  correctness and audit guarantees.
- Bad, because a small team would spend its effort on networking, deployment, tracing and
  versioned internal APIs instead of the domain.
- Bad, because the domain boundaries are not yet proven; splitting early fixes wrong
  boundaries in network contracts.

### Layered monolith without module boundaries

- Good, because it is the least code and the most familiar structure.
- Bad, because nothing stops any code from importing any other code, so verification or
  provenance rules erode over time.
- Bad, because parallel agents would constantly edit the same global files.

### Django-style apps

- Good, because the conventions are widely known and the admin is free.
- Bad, because Django apps couple domain models to the ORM (active record), which makes
  I/O-free domain tests and frozen, invariant-checked entities hard.
- Bad, because it conflicts with the chosen async FastAPI and SQLAlchemy stack
  ([0003](0003-fastapi-with-app-factory.md), [0004](0004-async-sqlalchemy-and-alembic.md)).

### Full CQRS with a mediator and event sourcing

- Good, because event sourcing gives a complete history by construction, which suits an
  audit-heavy domain.
- Bad, because a mediator library adds indirection without solving a concern we have;
  plain handler callables wired in the composition root are enough.
- Bad, because event sourcing makes schema evolution, corrections, GDPR-style erasure of
  personal data and ad-hoc research queries much harder, and needs projections for every
  read. The history requirement is met instead by append-only claims, report revisions,
  the outbox and the audit log ([0007](0007-transactional-outbox-for-domain-events.md)).

## More information

- `AGENTS.md` §2 (architecture), §2.1 (layer rules), §2.2 (CQRS-lite), §3 (pattern catalog).
- `[tool.importlinter]` in `pyproject.toml`. Phase 0 contains the `shared_kernel` and
  `platform` contracts; per-module layer contracts are added with each module by the
  `new-module` skill.
- Revisit if a module shows a measured, independent scaling or deployment need.
