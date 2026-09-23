# 0004. Async SQLAlchemy 2.0 and Alembic for persistence and migrations

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

The domain layer is framework-free and uses frozen Pydantic entities
([0001](0001-modular-monolith-with-hexagonal-layers.md)). Persistence must map those entities
to PostgreSQL/PostGIS tables ([0002](0002-postgresql-with-postgis.md)) behind repository
ports, support spatial types, run asynchronously under FastAPI, and evolve the schema
safely over years without losing data. Schema changes to a system of record are high-risk:
a bad migration can corrupt or drop verified data.

Which ORM and migration tool should the infrastructure layer use, and how are migrations
governed?

## Decision drivers

- Correctness: typed queries, explicit transactions, unit of work.
- Separation of domain models from persistence models (data mapper, not active record).
- Async I/O with PostgreSQL.
- First-class PostGIS column types and spatial functions.
- Safe, reviewable, reversible schema evolution with a single linear history.
- Integration tests against real PostGIS, never a mocked database (`AGENTS.md` §4.2).
- Small team: mature, well-documented tools.

## Considered options

1. SQLAlchemy 2.0 (typed, async) with asyncpg, GeoAlchemy2 and Alembic (chosen)
2. SQLModel
3. Tortoise ORM
4. Raw asyncpg with hand-written SQL
5. Django ORM

## Decision outcome

Chosen option: **SQLAlchemy 2.0 in its typed async style, with the asyncpg driver,
GeoAlchemy2 for spatial columns, and Alembic for migrations**, because it is the most mature
data-mapper toolkit in Python and keeps ORM classes out of the domain.

- ORM classes live only in `modules/<m>/infrastructure/orm.py`; mappers convert between rows
  and domain entities. Domain and application code never import SQLAlchemy (enforced by
  `import-linter`).
- The unit of work in `platform/uow.py` owns the `AsyncSession` and the transaction; commit
  happens only through it.
- Query services may use SQLAlchemy Core or hand-tuned SQL for read models.
- Alembic keeps a **single linear history** (one head). Branch merges of migrations are not
  allowed; a conflicting migration is rebased onto the new head.
- Only one author writes migrations at a time: the `persistence-engineer` via the
  `write-migration` skill. Committed migrations are never edited (enforced by the
  `guard_protected_paths.py` hook); fixes are new migrations.
- Every migration has a working `downgrade`. CI runs `upgrade head` then `downgrade base` on
  an empty database, and `alembic check` to prove models and migrations agree. These jobs
  start in Phase 1, when the Alembic environment is created.

### Consequences

- Good, because typed `Mapped[...]` models and `select()` work with `mypy --strict`.
- Good, because the data-mapper style lets domain entities stay frozen Pydantic models with
  their own invariants.
- Good, because GeoAlchemy2 gives spatial columns and functions without raw SQL strings
  everywhere.
- Good, because a linear, single-writer migration history is easy to review and reason
  about, and downgrade tests catch irreversible changes before they reach data.
- Bad, because maintaining separate ORM classes, mappers and entities duplicates field lists
  and adds code to keep in sync.
- Bad, because async SQLAlchemy forbids implicit lazy loading, so every relationship load
  must be explicit; mistakes show up as runtime errors, not type errors.
- Bad, because a single migration writer serialises schema work, which can slow parallel
  feature development.
- Bad, because "every migration is reversible" is sometimes impossible for data-destroying
  changes; such a case needs an explicit, reviewed decision, never a silent no-op
  downgrade. The exact procedure is set in Phase 1 with the `write-migration` skill.
- Bad, because GeoAlchemy2 typing stubs are incomplete, which may require narrowly scoped
  `# type: ignore[code]  # reason: ...` comments.

## Pros and cons of the options

### SQLAlchemy 2.0 async + asyncpg + GeoAlchemy2 + Alembic

- Good, because it is mature, typed, async and spatial-capable.
- Bad, because it is a large API surface with a learning curve.

### SQLModel

- Good, because one class serves as Pydantic model and table.
- Bad, because merging API/domain models with table definitions is exactly the coupling the
  hexagonal layers forbid.
- Bad, because it lags SQLAlchemy features and adds a layer of its own.

### Tortoise ORM

- Good, because it is async-first and simple.
- Bad, because it is active-record style, has weaker PostGIS support and a smaller
  community, and its migration tooling is less mature than Alembic.

### Raw asyncpg

- Good, because it is fastest and fully explicit.
- Bad, because every mapping, unit of work and migration tool would be our own code, which
  a small team cannot afford to write and test well.

### Django ORM

- Good, because it is mature, with integrated migrations.
- Bad, because it ties the project to Django ([0003](0003-fastapi-with-app-factory.md)) and
  to active-record models.

## More information

- `AGENTS.md` §3 (Repository, Unit of Work), §4.2 (migration tests), §5 (never edit a
  committed migration).
- `write-migration` skill in `.claude/skills/write-migration/SKILL.md`.
