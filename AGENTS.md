# AGENTS.md — Yakhnama backend contributor contract

This file is the condensed, binding version of the project standards. Every human and AI
contributor reads it before touching the repository. Where tooling and this file disagree,
tooling is a bug to fix, never a reason to weaken a rule. The full kickoff specification
lives with the maintainer; §2–§5 here change only with maintainer approval.

## 1. Mission

Yakhnama ("chronicle of the ice") is an open platform documenting natural hazards and
disasters in high-mountain regions, starting in Gilgit-Baltistan, Pakistan. This repository
is the Python backend and system of record: it collects community reports, lets moderators
verify them into canonical events, tracks impacts as sourced claims, and serves a
research-grade open dataset. A wrong figure, a lost report or an untraceable number damages
the only record the region has, so correctness, provenance and auditability beat speed.

## 2. Architecture

**Style:** modular monolith. Each module under `src/yakhnama/modules/<name>/` is a bounded
context with hexagonal layers. Modules talk only through `public.py` (Facade). No
microservices, no mediator library, no Result monads.

```
src/yakhnama/
  main.py            app factory + composition root (routes, exception handlers, wiring)
  shared_kernel/     framework-free building blocks: errors, value objects, specification,
                     domain events, unit-of-work protocol, ids, clock, pagination
  platform/          cross-cutting infrastructure: settings, db, uow, outbox, auth, storage,
                     logging, telemetry, container.py (the only other composition root)
  modules/<m>/
    domain/          entities.py value_objects.py events.py policies.py errors.py factories.py
    application/     commands.py queries.py handlers.py ports.py dto.py
    infrastructure/  orm.py repositories.py mappers.py adapters/
    api/             router.py schemas.py dependencies.py
    public.py        the ONLY import surface for other modules
tests/  unit/ integration/ api/ contract/ architecture/ factories/ fakes/
```

### 2.1 Layer rules (enforced by `import-linter`)

| Layer | May import | Must never import |
|-------|-----------|-------------------|
| `domain` | stdlib, `pydantic`, `geojson-pydantic`, `yakhnama.shared_kernel` | FastAPI, SQLAlchemy, httpx, Shapely, boto, anything in `platform` or `infrastructure` |
| `application` | own `domain`, `shared_kernel`, other modules' `public.py` | any `infrastructure`, any framework |
| `infrastructure` | `application`, `domain`, `platform`, third-party libraries | other modules' internals |
| `api` | `application` (commands, queries, DTOs), `platform` dependencies | `infrastructure` directly |
| cross-module | `yakhnama.modules.b.public` only | `yakhnama.modules.b.<anything else>` |

Ports are `typing.Protocol` classes in `application/ports.py`. Only `main.py` and
`platform/container.py` bind ports to adapters.

### 2.2 Reads and writes (CQRS-lite)

Writes: command (Pydantic model) → handler → unit of work → repositories → entities → domain
events. Reads: query service behind a port, optimised SQL in infrastructure, returns DTOs.
Handlers are plain callables wired in the composition root.

### 2.3 Errors

All domain errors derive from `shared_kernel.errors.YakhnamaError` (`NotFoundError`,
`ConflictError`, `ValidationError`, `PermissionDeniedError`, `InvariantViolationError`,
`InvalidTransitionError`, plus `PreconditionFailedError` for `If-Match` mismatches, proposed in
ADR 0012 pending maintainer approval). The API maps them to RFC 9457 Problem Details in exactly
one place: the exception handlers registered in `main.py`.

## 3. Pattern catalog

Every class declares `Implements: <Pattern>` in its docstring. Use the pattern mapped to the
concern; propose any other pattern in an ADR first. Never add a pattern without a concern.

| Concern | Pattern | Where |
|---------|---------|-------|
| Persisting aggregates | Repository (Protocol port, SQLAlchemy adapter) | `application/ports.py`, `infrastructure/repositories.py` |
| Transaction boundaries | Unit of Work | `shared_kernel/uow.py` protocol, `platform/uow.py` |
| Write use cases | Command Handler | `application/handlers.py` |
| Read use cases | Query Service | `application/queries.py` + infrastructure impl |
| Wiring | Composition Root + Dependency Injection (`Depends`) | `main.py`, `platform/container.py` |
| Cross-module access | Facade | `modules/<m>/public.py` |
| Immutable concepts | Value Object (frozen Pydantic model) | `domain/value_objects.py` |
| Identity-bearing concepts | Entity / Aggregate Root (frozen model; methods return new instances) | `domain/entities.py` |
| Hazard-specific attributes | Strategy + Registry (discriminated unions) | `modules/hazards/domain` |
| Verification lifecycle | State (explicit transition table) | `modules/verification/domain` |
| Report triage checks | Chain of Responsibility | `modules/reports/application` |
| Creating events from reports | Factory | `modules/events/domain/factories.py` |
| Search filters | Specification (`and_`/`or_`/`not_`, compiled to SQL in infrastructure) | `shared_kernel/specification.py` |
| Side effects after commit | Domain Events + Transactional Outbox + Observer | `shared_kernel/events.py`, `platform/outbox/` |
| External systems | Adapter + Anti-Corruption Layer (mappers) | `infrastructure/adapters/` |
| Ingestion pipelines | Template Method | `modules/ingestion/application` |
| Import/export formats | Strategy + Registry (one Importer/Exporter per format) | `modules/exchange/` |
| Authorisation rules | Policy (composable, Specification-style) | `modules/identity/domain/policies.py` |
| Retry, timing, idempotency | Decorator | `platform/decorators.py` |
| Test doubles for ports | Fake (in-memory), never mocks of our own ports | `tests/fakes/` |
| Test data | Factory (`polyfactory`) | `tests/factories/` |
| Configuration (proposed in ADR 0011, pending maintainer approval) | Settings (`pydantic-settings` `BaseSettings`) | `platform/settings.py` |
| HTTP request/response bodies (proposed in ADR 0011, pending maintainer approval) | API Schema (Pydantic model, frozen where possible) | `modules/*/api/schemas.py`, `platform/health.py` |
| Write requests (proposed in ADR 0012, pending maintainer approval) | Command (imperative Pydantic model) | `application/commands.py` |
| Read requests and results (proposed in ADR 0012, pending maintainer approval) | Query, DTO (Pydantic models) | `application/queries.py`, `application/dto.py` |
| Domain failures (proposed in ADR 0012, pending maintainer approval) | Domain Error (exception rooted at `YakhnamaError`) | `shared_kernel/errors.py`, `domain/errors.py` |

## 4. Standards

| Standard | Enforcement |
|----------|-------------|
| Poetry is the only package manager | PEP 621 `[project]` table, Poetry 2.x. Dependencies change only via `poetry add` / `poetry remove`. No `requirements.txt`, no pip, no uv. |
| Pydantic `BaseModel` for all data | Schemas, commands, queries, DTOs, value objects, entities, settings. No bare `dict` crosses a layer. |
| Types everywhere | `mypy --strict` + `pydantic.mypy`, zero errors. Ruff `ANN` on. `Any` only at external boundaries with a comment saying why. |
| PEP 8 | `ruff format` + `ruff check`, line length 88, config in `pyproject.toml`. |
| Docstrings and comments | Google style on every module, class and public function (Ruff `D`). Comments explain **why**. No commented-out code (Ruff `ERA`). |
| Naming | §6 below. Ruff `N` on. Abbreviations only from the glossary. |
| Modularity | Layer and module contracts in `[tool.importlinter]`, run by `lint-imports`. |
| Tests | Every public function has a unit test. Coverage ≥ 90 % overall, ≥ 95 % in `domain` and `application`, `diff-cover` ≥ 90 % on changed lines, branch coverage on. |
| Patterns | Declared in every class docstring, chosen from §3. |

**Hard rules.** Python ≥ 3.13 (CI on 3.13 and 3.14). No `print` (`T20`); use the structured
logger. No naive datetimes (`DTZ`); everything is timezone-aware and stored in UTC. No bare
`except`, no swallowed errors. `# type: ignore` and `# noqa` need a code and a reason:
`# type: ignore[attr-defined]  # reason: geoalchemy2 stubs lack X`. `TODO` needs an issue:
`# TODO(#42): ...`. No secrets anywhere; configuration comes from the environment through
`pydantic-settings`. **Never weaken a check to make it pass.** If you are blocked, stop and
report.

### 4.1 Commands

All commands run through Poetry and Poe so they behave the same on every OS and in CI.

| Command | What it does |
|---------|--------------|
| `poetry install` | Install every group into the project virtualenv |
| `poetry run poe format` | `ruff format` then `ruff check --fix` |
| `poetry run poe lint` | Format check and lint, no changes |
| `poetry run poe typecheck` | `mypy --strict` over `src`, `tests`, `.claude/hooks` |
| `poetry run poe arch` | `lint-imports` layer and module contracts |
| `poetry run poe test-unit` | Unit tests with coverage |
| `poetry run poe test-api` | API and architecture tests |
| `poetry run poe test-integration` | Tests against real tools and services (git, ruff, mypy now; PostGIS and MinIO from Phase 1) |
| `poetry run poe cov` | All non-integration tests with the coverage gate |
| `poetry run poe diff-cover` | Coverage on changed lines against `main` |
| `poetry run poe security` | `gitleaks` and `pip-audit` |
| `poetry run poe migrate` | `alembic upgrade head` (from Phase 1) |
| `poetry run poe openapi-snapshot` | Regenerate `tests/contract/openapi.json` (task added in Phase 2) |
| `poetry run poe up` / `down` | Start or stop PostGIS, MinIO, Keycloak, Redis |
| `poetry run poe check` | Everything CI runs, in order. This is the gate. |

### 4.2 Testing rules

- `tests/unit/` mirrors `src/` and does no I/O. Domain and application code is tested with
  fakes from `tests/fakes/`. Use `hypothesis` for value objects, Specification combinators,
  the verification state machine and importers.
- `tests/integration/` runs every repository and adapter against real PostGIS and MinIO.
  Never mock SQLAlchemy or the database.
- `tests/api/` uses `httpx.AsyncClient` against the app factory with fakes wired in, and
  covers authentication, authorisation, errors, pagination and idempotency.
- Migrations: `upgrade head` then `downgrade base` on an empty database, plus `alembic check`.
- Architecture: `lint-imports` plus structural tests (every module has `public.py`, every
  repository implements a port, every class declares its pattern).
- Names: `test_<unit>_<scenario>_<expected_outcome>`, Arrange / Act / Assert separated by
  blank lines. Time comes from an injected `Clock`, ids from an injected `IdGenerator`, and
  there is no network access.

## 5. Never do

- Never commit to `main`, force-push, or rewrite pushed history.
- Never lower a coverage threshold, add an ignore, skip a test or loosen a type to pass.
- Never edit `poetry.lock` by hand, `.env*`, `LICENSE`, `.github/CODEOWNERS`, or a migration
  already committed. `AGENTS.md` and everything under `.claude/` (settings, hooks, agents,
  skills) are edited only by the lead session. The hook in
  `.claude/hooks/guard_protected_paths.py` and the deny list in `.claude/settings.json` block
  these for the file tools; shell writes are a residual risk backstopped by pre-commit, CI and
  CODEOWNERS.
- Never hard-delete verified data, overwrite an `ImpactClaim`, or edit a submitted `Report`
  (corrections are new revisions; retraction is a status change with a reason).
- Never store passwords, casualty names in public fields, or personal data in logs.
- Never let a `dict` cross a layer boundary, or a naive `datetime` exist anywhere.
- Never invent domain facts (hazard definitions, metric semantics, boundaries, local names,
  thresholds). Add them to `docs/open-questions.md` with a proposed default instead.
- Never mock our own ports; write a Fake. Never mock the database.
- Never create a `utils` or `helpers` module. Everything has a named home.
- Never add a pattern without naming the concern it solves.
- Never make live network calls in this run (no satellite, climate or OIDC servers in tests).
- Never claim "done" without the full gate passing.

## 6. Naming

- Modules and packages `snake_case`; layers singular (`domain`), collections plural
  (`handlers.py`).
- Classes `PascalCase` nouns with the pattern suffix: `...Repository`, `...Handler`,
  `...QueryService`, `...Policy`, `...Specification`, `...Adapter`, `...Exporter`,
  `...Importer`, `...Pipeline`, `...Factory`.
- Commands are imperative (`SubmitReport`), domain events past tense (`ReportSubmitted`),
  queries `Get...` / `List...`. Functions are verbs. Booleans start with `is_`, `has_`,
  `can_`. Constants `UPPER_SNAKE_CASE`. Private members start with `_`.
- No single-letter names except `i`/`j` in comprehensions. No abbreviations outside §7.
- Docstrings: module → purpose plus `Patterns: ...`; class → summary, `Implements: <Pattern>`,
  `Attributes:`; public function → `Args:`, `Returns:`, `Raises:`.

## 7. Glossary

| Term | Meaning |
|------|---------|
| **Report** | One raw observation from one person or organisation. Never trusted by default, never edited after submission; corrections are revisions. |
| **Event** | The canonical record of one real-world hazard occurrence, created or merged by moderators through verification. |
| **Impact claim** | One metric value from one source with a confidence level. Append-only. |
| **Best figure** | The derived read model of an event's current impact value, computed by a documented aggregation policy. |
| **Source** | The provenance record (citizen, organisation, government, news, satellite, research, dataset) every fact links to. Immutable once referenced. |
| **Place** | A named location with WGS84 geometry in the administrative hierarchy (country → province/region → district → tehsil → union council → village). |
| **Place name** | One name for a place in one language and script (English, Urdu, Shina, Burushaski, Balti, Wakhi, Khowar, plus alternative spellings). |
| **Hazard type** | A node in the hazard taxonomy (IRDR-aligned), with a stable code that is retired, never reused or deleted. |
| **Impact metric** | A registry entry (code, unit, category) aligned to Sendai indicators and DesInventar. Stored long and narrow: metric + value. |
| **Verification case** | The state-machine record attached to a report, event or claim. Only a human moves it to `verified`. |
| **Date precision** | `exact \| hour \| day \| month \| season \| year`; stored beside every UTC timestamp. |
| **Measurement** | Value plus unit; SI units in storage. |
| **Confidence** | `low \| medium \| high` on every uncertain number, always with its source. |
| **Outbox** | The transactional table where domain events are written in the same transaction, then relayed to subscribers. |
| **GLOF** | Glacial lake outburst flood. |
| **GLIMS** | Global Land Ice Measurements from Space glacier inventory id. |
| **ICIMOD** | International Centre for Integrated Mountain Development. |
| **IRDR** | Integrated Research on Disaster Risk peril classification. |
| **STAC** | SpatioTemporal Asset Catalog, the raster metadata standard we align to. |
| **COG** | Cloud-Optimised GeoTIFF. |
| **OIDC / JWKS** | OpenID Connect and its JSON Web Key Set used to validate JWTs. |
| **UoW** | Unit of Work. |
| **DTO** | Data transfer object returned by query services. |
| **ADR** | Architecture Decision Record in `docs/adr/`, MADR format. |
| **HDX / OCHA COD** | Humanitarian Data Exchange; OCHA Common Operational Datasets (candidate boundary source). |
| **PMD** | Pakistan Meteorological Department. |

Allowed abbreviations in code: `id`, `uow`, `dto`, `adr`, `glof`, `glims`, `stac`, `cog`,
`oidc`, `jwks`, `jwt`, `utc`, `api`, `orm`, `sql`, `csv`, `json`, `url`, `uri`, `uuid`,
`http`, `mime`, `exif`, `gps`, `osm`, `crs`, `srid`, `bbox`, `db`, `ttl`, `cors`, `etag`.

## 8. How to add things

Each recipe is a skill in `.claude/skills/<name>/SKILL.md` with exact files, templates,
required tests and checks. Owning subagent in brackets.

| Want to add | Skill | Owner |
|-------------|-------|-------|
| A bounded context | `new-module` | architect scaffolds; layer owners fill |
| An entity or value object | `add-entity` | domain-modeler → persistence-engineer |
| A command or query | `add-command`, `add-query` | application-engineer |
| An endpoint | `add-api-endpoint` | api-engineer |
| A migration | `write-migration` | persistence-engineer only, one at a time |
| A hazard type | `add-hazard-type` | domain-modeler |
| An impact metric | `add-impact-metric` | domain-modeler |
| An external data source | `add-source-adapter` | integration-engineer |
| An import/export format | `add-exchange-format` | integration-engineer |
| An architecture decision | `write-adr` | architect |
| A phase report | `phase-report` | lead |

Typical order within a module: domain → application (with fakes) → persistence → API →
integration tests. `shared_kernel`, `platform`, `pyproject.toml` and `migrations/` have one
writer at a time.

## 9. Definition of Done (per task)

- [ ] Every class declares its pattern and the pattern matches §3.
- [ ] Every module, class and public function has a Google-style docstring; comments say why.
- [ ] `mypy --strict` is clean. Ruff is clean with no new unjustified ignores.
- [ ] `lint-imports` passes.
- [ ] Unit tests exist for every public function; integration tests for every adapter and
      repository.
- [ ] Coverage gates are met, including `diff-cover`.
- [ ] Any schema change has a reviewed, reversible migration.
- [ ] Any API change has its OpenAPI snapshot updated and is documented.
- [ ] The data dictionary in `docs/data-dictionary/` covers every new field, unit and meaning.
- [ ] `standards-reviewer` approved; `security-reviewer` approved where auth, personal data,
      media, uploads, the public API or dependencies were touched.
- [ ] A Conventional Commit exists on the phase branch (`feat(reports): ...`).
- [ ] `poetry run poe check` passes.
