---
name: new-module
description: Scaffold a new bounded context under src/yakhnama/modules/<m>/ with hexagonal layers, a public.py facade, import-linter contracts, mirrored tests, fakes and a data-dictionary stub.
---

# new-module

## When to use

- A phase plan introduces a bounded context that does not exist yet under
  `src/yakhnama/modules/` (for example `hazards`, `places`, `reports`).
- Not for adding a concept to an existing module: use `add-entity`, `add-command` or
  `add-query` instead.

## Preconditions

- The module and its responsibility are named in the approved `docs/plans/phase-N.md`.
  If the boundary is contested, write an ADR first (`write-adr`).
- `src/yakhnama/shared_kernel/` exists and exports, at least:
  `errors.YakhnamaError` and its subclasses (`NotFoundError`, `ConflictError`,
  `ValidationError`, `PermissionDeniedError`, `InvariantViolationError`,
  `InvalidTransitionError`), `uow.UnitOfWork` (async context manager with `commit`,
  `rollback` and `record_event`), `events.DomainEvent` (frozen, with
  `occurred_at: AwareDatetime`). **Check the real names before copying the templates**;
  where they differ, the shared kernel wins and this skill must be corrected.
- `src/yakhnama/main.py` and `src/yakhnama/platform/container.py` exist, because the
  `protected` contract below names them as allowed importers and import-linter fails on
  names missing from the graph.
- Nobody else is editing `pyproject.toml` `[tool.importlinter]` (single writer).
- Every class declares a pattern from `AGENTS.md` §3 (checked by
  `tests/architecture/test_structure.py`): `Value Object` also for enums, `Command` for
  command models, `Query` / `DTO` for read models, `Domain Error` for exceptions,
  `API Schema` for HTTP bodies (ADR 0011, ADR 0012).

## Owning subagent

`architect` scaffolds the package, the facade and the import-linter contracts. The layer
owners then fill it: `domain-modeler` (domain, data dictionary),
`application-engineer` (application, `public.py`, fakes), `persistence-engineer`
(infrastructure), `api-engineer` (api).

## Files

Replace `hazards` with the module name `<m>` throughout. Every directory under `tests/`
gets an `__init__.py` with a one-line docstring, so test modules with the same basename
in different modules do not collide.

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `src/yakhnama/modules/<m>/__init__.py` | create | Module docstring: purpose, `Patterns:` |
| `src/yakhnama/modules/<m>/domain/__init__.py` | create | Layer docstring and import rule |
| `src/yakhnama/modules/<m>/domain/value_objects.py` | create | First value object |
| `src/yakhnama/modules/<m>/domain/entities.py` | create | First entity |
| `src/yakhnama/modules/<m>/domain/errors.py` | create | Errors rooted at `YakhnamaError` |
| `src/yakhnama/modules/<m>/application/__init__.py` | create | Layer docstring and import rule |
| `src/yakhnama/modules/<m>/application/commands.py` | create | First command |
| `src/yakhnama/modules/<m>/application/ports.py` | create | Repository, UoW and UoW-factory Protocols |
| `src/yakhnama/modules/<m>/application/handlers.py` | create | First command handler |
| `src/yakhnama/modules/<m>/infrastructure/__init__.py` | create | Layer docstring; adapters come with `add-entity` |
| `src/yakhnama/modules/<m>/api/__init__.py` | create | Layer docstring |
| `src/yakhnama/modules/<m>/api/router.py` | create | Empty `APIRouter` under `/api/v1` |
| `src/yakhnama/modules/<m>/public.py` | create | Facade: the only cross-module import surface |
| `pyproject.toml` `[tool.importlinter]` | modify | Four contracts for the module (below) |
| `tests/unit/modules/<m>/domain/test_value_objects.py` | create | Value object tests (hypothesis) |
| `tests/unit/modules/<m>/application/test_handlers.py` | create | Handler tests with fakes |
| `tests/unit/modules/<m>/__init__.py`, `.../domain/__init__.py`, `.../application/__init__.py` | create | Test packages |
| `tests/integration/modules/<m>/__init__.py`, `tests/api/modules/<m>/__init__.py` | create | Empty test packages, filled by later skills |
| `tests/fakes/<m>.py` | create | In-memory fakes of every port in `ports.py` |
| `docs/data-dictionary/<m>.md` | create | Data dictionary stub |

## Steps

1. Confirm the preconditions. Read `AGENTS.md` §2–§3 and the phase plan section that
   introduces the module.
2. Create the package tree and every `__init__.py` with the docstrings below. Layers are
   singular (`domain`), collection files plural (`handlers.py`).
3. Write `domain/value_objects.py`, `domain/errors.py`, `domain/entities.py` from the
   templates, renaming the example concept. Keep the example code only if the phase plan
   really introduces it.
4. Write `application/commands.py`, `application/ports.py`, `application/handlers.py`.
   The scaffold handler is the canonical minimal example; the first real command must
   follow `add-command` (Policy check, Clock, domain event).
5. Write `api/router.py` with an empty router. Do not register it in `main.py` until the
   first endpoint exists (`add-api-endpoint`).
6. Write `public.py`. Export only value objects, commands, DTOs, errors and port types that
   another module needs today. Never export ORM rows, repositories, adapters or routers.
7. Add the four import-linter contracts to `pyproject.toml` (architect only), replacing
   `hazards`.
8. Write `tests/fakes/<m>.py` and the two unit test files.
9. Write `docs/data-dictionary/<m>.md` from the stub.
10. Run the checks. Prove the contracts bite: temporarily add
    `from yakhnama.modules.<m>.infrastructure import orm` to `api/router.py`, run
    `poetry run poe arch`, see it fail, then remove the line.

## Templates

### `src/yakhnama/modules/hazards/__init__.py`
```python
"""Hazards bounded context: the hazard taxonomy and hazard-specific attributes.

Other modules import this context only through ``yakhnama.modules.hazards.public``.

Patterns: Facade (via ``public.py``).
"""
```

### `src/yakhnama/modules/hazards/domain/__init__.py`
```python
"""Domain layer of the hazards context: entities, value objects, events and errors.

Imports only the standard library, ``pydantic``, ``geojson_pydantic`` and
``yakhnama.shared_kernel``.
"""
```

### `src/yakhnama/modules/hazards/application/__init__.py`
```python
"""Application layer of the hazards context: commands, queries, handlers and ports.

Imports only the hazards domain, ``yakhnama.shared_kernel`` and other modules'
``public`` facades.
"""
```

### `src/yakhnama/modules/hazards/infrastructure/__init__.py`
```python
"""Infrastructure layer of the hazards context: ORM rows, repositories and mappers.

Implements the ports declared in ``yakhnama.modules.hazards.application.ports``.
"""
```

### `src/yakhnama/modules/hazards/api/__init__.py`
```python
"""HTTP layer of the hazards context: routers, request and response schemas.

Imports application commands, queries and DTOs, never infrastructure.
"""
```

### `src/yakhnama/modules/hazards/domain/value_objects.py` (canonical)
```python
"""Value objects for the hazards bounded context.

Patterns: Value Object.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

HazardCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


class HazardTypeRef(BaseModel):
    """Immutable reference to a hazard type in the taxonomy.

    Implements: Value Object.

    Attributes:
        code: Stable machine code such as ``"glof"``. Never reused once retired.
    """

    model_config = ConfigDict(frozen=True)

    code: HazardCode
```

### `src/yakhnama/modules/hazards/domain/errors.py`
```python
"""Errors raised by the hazards domain and application layers.

Patterns: Domain Error (rooted at ``YakhnamaError``).
"""

from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.errors import InvalidTransitionError, NotFoundError


class HazardTypeNotFoundError(NotFoundError):
    """No hazard type matches the given reference.

    Implements: Domain Error.

    Attributes:
        ref: The reference that did not resolve.
    """

    def __init__(self, ref: HazardTypeRef) -> None:
        """Create the error.

        Args:
            ref: The reference that did not resolve.
        """
        super().__init__(f"hazard type {ref.code!r} does not exist")
        self.ref = ref


class HazardTypeAlreadyRetiredError(InvalidTransitionError):
    """A retired hazard type cannot be retired again.

    Implements: Domain Error.

    Attributes:
        ref: The hazard type that is already retired.
    """

    def __init__(self, ref: HazardTypeRef) -> None:
        """Create the error.

        Args:
            ref: The hazard type that is already retired.
        """
        super().__init__(f"hazard type {ref.code!r} is already retired")
        self.ref = ref
```

### `src/yakhnama/modules/hazards/domain/entities.py`
```python
"""Entities of the hazards bounded context.

Patterns: Entity.
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints

from yakhnama.modules.hazards.domain.errors import HazardTypeAlreadyRetiredError
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef

RetirementReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class HazardTypeStatus(StrEnum):
    """Lifecycle status of a hazard type code.

    Implements: Value Object.
    """

    ACTIVE = "active"
    RETIRED = "retired"


class HazardType(BaseModel):
    """A node in the hazard taxonomy, identified by its stable code.

    Implements: Entity.

    Attributes:
        ref: Identity of the hazard type.
        parent: The broader hazard type, or ``None`` for a root node.
        status: Whether new events may still be classified with this type.
        retired_reason: Why the type was retired; ``None`` while active.
    """

    model_config = ConfigDict(frozen=True)

    ref: HazardTypeRef
    parent: HazardTypeRef | None = None
    status: HazardTypeStatus = HazardTypeStatus.ACTIVE
    retired_reason: RetirementReason | None = None

    @property
    def is_retired(self) -> bool:
        """Return ``True`` once the type no longer accepts new classifications."""
        return self.status is HazardTypeStatus.RETIRED

    def retire(self, *, reason: str) -> Self:
        """Return a retired copy of this hazard type.

        Args:
            reason: Why the type is retired, for example ``"merged into flood"``.

        Returns:
            A new instance with ``status`` set to ``retired``.

        Raises:
            HazardTypeAlreadyRetiredError: If the type is already retired.
        """
        if self.is_retired:
            raise HazardTypeAlreadyRetiredError(self.ref)
        # model_validate, not model_copy: model_copy skips validation, and the
        # reason must pass the same constraints as on construction.
        return self.model_validate(
            self.model_dump()
            | {"status": HazardTypeStatus.RETIRED, "retired_reason": reason},
        )
```

### `src/yakhnama/modules/hazards/application/commands.py`
```python
"""Commands accepted by the hazards write side.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.domain.entities import RetirementReason
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef


class RetireHazardType(BaseModel):
    """Ask to retire a hazard type so it can no longer classify new events.

    Implements: Command.

    Attributes:
        ref: The hazard type to retire.
        reason: Why it is retired; kept for the audit trail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef
    reason: RetirementReason
```

### `src/yakhnama/modules/hazards/application/ports.py` (canonical, plus the UoW ports)
```python
"""Ports the hazards application layer depends on.

Patterns: Repository (port side), Unit of Work.
"""

from typing import Protocol

from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.uow import UnitOfWork


class HazardTypeRepository(Protocol):
    """Persistence port for hazard types.

    Implements: Repository.
    """

    async def get(self, ref: HazardTypeRef) -> HazardType | None:
        """Return the hazard type for ``ref``, or ``None`` if it does not exist."""
        ...

    async def save(self, hazard_type: HazardType) -> None:
        """Stage ``hazard_type`` for persistence in the current unit of work."""
        ...


class HazardsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the hazards repositories.

    Implements: Unit of Work.
    """

    @property
    def hazard_types(self) -> HazardTypeRepository:
        """Return the hazard type repository bound to this transaction."""
        ...


class HazardsUnitOfWorkFactory(Protocol):
    """Opens a fresh hazards unit of work per use case.

    Implements: Unit of Work (factory port).
    """

    def __call__(self) -> HazardsUnitOfWork:
        """Return a new, not yet entered, unit of work."""
        ...
```

### `src/yakhnama/modules/hazards/application/handlers.py` (canonical)
```python
"""Write-side use cases for the hazards module.

Patterns: Command Handler, Unit of Work.
"""

from yakhnama.modules.hazards.application.commands import RetireHazardType
from yakhnama.modules.hazards.application.ports import HazardsUnitOfWorkFactory
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError


class RetireHazardTypeHandler:
    """Retire a hazard type so no new events can be classified with it.

    Implements: Command Handler.

    Retired types stay resolvable so historical events keep their meaning.
    """

    def __init__(self, uow_factory: HazardsUnitOfWorkFactory) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a unit of work scoped to the hazards module.
        """
        self._uow_factory = uow_factory

    async def __call__(self, command: RetireHazardType) -> None:
        """Retire the hazard type referenced by ``command``.

        Args:
            command: The validated retire command.

        Raises:
            HazardTypeNotFoundError: If no hazard type matches ``command.ref``.
        """
        async with self._uow_factory() as uow:
            hazard_type = await uow.hazard_types.get(command.ref)
            if hazard_type is None:
                raise HazardTypeNotFoundError(command.ref)
            await uow.hazard_types.save(hazard_type.retire(reason=command.reason))
            await uow.commit()
```

### `src/yakhnama/modules/hazards/api/router.py`
```python
"""HTTP routes of the hazards context, mounted under ``/api/v1``.

Patterns: none. Routes are added with the ``add-api-endpoint`` skill.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["hazards"])
```

### `src/yakhnama/modules/hazards/public.py`
```python
"""Public facade of the hazards context: the only import surface for other modules.

Patterns: Facade.

Export only what another bounded context genuinely needs: value objects, commands,
DTOs, domain errors and port types. Never export ORM rows, repositories or routers.
"""

from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.domain.value_objects import HazardCode, HazardTypeRef

__all__ = ["HazardCode", "HazardTypeNotFoundError", "HazardTypeRef"]
```

### `pyproject.toml` — append under `[tool.importlinter]`

These four contracts were run against a scratch copy of the tree with `lint-imports`
(import-linter 2.15): all kept, and a deliberate `api -> infrastructure` import and a
foreign `other -> hazards.domain` import were both reported as broken.

```toml
[[tool.importlinter.contracts]]
name = "hazards: other contexts import only yakhnama.modules.hazards.public"
type = "protected"
protected_modules = [
  "yakhnama.modules.hazards.domain",
  "yakhnama.modules.hazards.application",
  "yakhnama.modules.hazards.infrastructure",
  "yakhnama.modules.hazards.api",
]
allowed_importers = [
  "yakhnama.modules.hazards",
  "yakhnama.main",
  "yakhnama.platform.container",
]
broken_contract_guidance = "Import yakhnama.modules.hazards.public instead."

[[tool.importlinter.contracts]]
name = "hazards: api | infrastructure -> application -> domain"
type = "layers"
containers = ["yakhnama.modules.hazards"]
layers = [
  "api | infrastructure",
  "application",
  "domain",
]

[[tool.importlinter.contracts]]
name = "hazards: domain is framework-free"
type = "forbidden"
source_modules = ["yakhnama.modules.hazards.domain"]
forbidden_modules = [
  "yakhnama.platform",
  "yakhnama.main",
  "fastapi",
  "starlette",
  "sqlalchemy",
  "geoalchemy2",
  "alembic",
  "asyncpg",
  "httpx",
  "shapely",
  "boto3",
  "botocore",
  "structlog",
]

[[tool.importlinter.contracts]]
name = "hazards: application imports no framework or platform code"
type = "forbidden"
source_modules = ["yakhnama.modules.hazards.application"]
forbidden_modules = [
  "yakhnama.platform",
  "yakhnama.main",
  "fastapi",
  "starlette",
  "sqlalchemy",
  "geoalchemy2",
  "alembic",
  "asyncpg",
  "httpx",
  "shapely",
  "boto3",
  "botocore",
  "structlog",
]
```

How they encode §2.1:

- `protected`: only the module itself and the two composition roots may import its
  layers directly, so every other module must go through `public.py`. `public.py` is not
  protected, which is exactly what makes it the facade.
- `layers`: `api` and `infrastructure` sit side by side (`|` makes them independent, so
  api never imports infrastructure and vice versa); both may import `application` and
  `domain`; `application` may import `domain`; nothing imports upwards. The contract also
  checks **indirect** imports: an api module that imports `yakhnama.platform.container`,
  which imports this module's infrastructure, breaks it.
- The two `forbidden` contracts keep domain and application free of frameworks and
  platform code. The lists match the shared-kernel contract in `pyproject.toml`
  (including `boto3` and `botocore`). Keep the lists in sync with the `shared_kernel` contract already in
  `pyproject.toml`; add a package to all of them when it becomes a dependency.

### `tests/fakes/hazards.py`
```python
"""In-memory fakes for the hazards ports.

Patterns: Fake.
"""

from collections.abc import Iterable
from types import TracebackType
from typing import Self

from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.events import DomainEvent


class FakeHazardTypeRepository:
    """In-memory ``HazardTypeRepository`` that stages writes until commit.

    Implements: Fake.

    Attributes:
        committed_rows: Hazard types visible after the last commit, keyed by code.
    """

    def __init__(self, hazard_types: Iterable[HazardType] = ()) -> None:
        """Create the repository.

        Args:
            hazard_types: Hazard types that exist before the test acts.
        """
        self.committed_rows: dict[str, HazardType] = {
            hazard_type.ref.code: hazard_type for hazard_type in hazard_types
        }
        self._staged: dict[str, HazardType] = {}

    async def get(self, ref: HazardTypeRef) -> HazardType | None:
        """Return the staged or committed hazard type for ``ref``."""
        return self._staged.get(ref.code, self.committed_rows.get(ref.code))

    async def save(self, hazard_type: HazardType) -> None:
        """Stage ``hazard_type`` until the unit of work commits."""
        self._staged[hazard_type.ref.code] = hazard_type

    def flush(self) -> None:
        """Make staged writes visible, as a database commit would."""
        self.committed_rows.update(self._staged)
        self._staged.clear()

    def discard(self) -> None:
        """Drop staged writes, as a database rollback would."""
        self._staged.clear()


class FakeHazardsUnitOfWork:
    """In-memory ``HazardsUnitOfWork`` recording commits and events.

    Implements: Fake.

    Attributes:
        hazard_types: The fake repository bound to this unit of work.
        committed: ``True`` once ``commit`` has been called.
        events: Domain events published by committed transactions.
    """

    def __init__(self, hazard_types: FakeHazardTypeRepository) -> None:
        """Create the unit of work.

        Args:
            hazard_types: The repository shared across units of work in a test.
        """
        self.hazard_types = hazard_types
        self.committed = False
        self.events: list[DomainEvent] = []
        self._pending_events: list[DomainEvent] = []

    async def __aenter__(self) -> Self:
        """Enter the transaction."""
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the transaction, discarding anything not committed."""
        await self.rollback()

    async def commit(self) -> None:
        """Publish staged writes and recorded events."""
        self.hazard_types.flush()
        self.events.extend(self._pending_events)
        self._pending_events.clear()
        self.committed = True

    async def rollback(self) -> None:
        """Discard staged writes and recorded events."""
        self.hazard_types.discard()
        self._pending_events.clear()

    def record_event(self, event: DomainEvent) -> None:
        """Queue ``event`` for publication on commit."""
        self._pending_events.append(event)

    @property
    def collected_events(self) -> tuple[DomainEvent, ...]:
        """Events recorded in this unit of work and not yet discarded."""
        return tuple(self._pending_events)


class FakeHazardsUnitOfWorkFactory:
    """``HazardsUnitOfWorkFactory`` that always returns the same fake unit of work.

    Implements: Fake.

    Attributes:
        uow: The single fake unit of work the test can inspect.
    """

    def __init__(self, hazard_types: Iterable[HazardType] = ()) -> None:
        """Create the factory.

        Args:
            hazard_types: Hazard types that exist before the test acts.
        """
        self.uow = FakeHazardsUnitOfWork(FakeHazardTypeRepository(hazard_types))

    def __call__(self) -> FakeHazardsUnitOfWork:
        """Return the shared fake unit of work."""
        return self.uow

    @property
    def committed(self) -> bool:
        """Return ``True`` if any use case committed."""
        return self.uow.committed

    @property
    def hazard_types(self) -> FakeHazardTypeRepository:
        """Return the fake repository for assertions on committed state."""
        return self.uow.hazard_types

    @property
    def events(self) -> list[DomainEvent]:
        """Return the events published by committed transactions."""
        return self.uow.events
```

### `tests/unit/modules/hazards/domain/test_value_objects.py`
```python
"""Unit tests for the hazards value objects."""

import pydantic
import pytest
from hypothesis import given
from hypothesis import strategies as st

from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef

VALID_CODES = st.from_regex(r"^[a-z][a-z0-9_]{1,63}$", fullmatch=True)


@given(code=VALID_CODES)
def test_hazard_type_ref_with_valid_code_round_trips(code: str) -> None:
    ref = HazardTypeRef(code=code)

    restored = HazardTypeRef.model_validate(ref.model_dump())

    assert restored == ref


@pytest.mark.parametrize("code", ["", "G", "1glof", "glof-lake", "a" * 65])
def test_hazard_type_ref_with_invalid_code_raises_validation_error(code: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        HazardTypeRef(code=code)


def test_hazard_type_ref_assignment_raises_validation_error() -> None:
    ref = HazardTypeRef(code="glof")

    with pytest.raises(pydantic.ValidationError):
        ref.code = "landslide"  # type: ignore[misc]  # reason: asserting frozen
```

### `tests/unit/modules/hazards/application/test_handlers.py` (canonical)
```python
"""Unit tests for the hazards command handlers."""

import pytest

from tests.fakes.hazards import FakeHazardsUnitOfWorkFactory
from yakhnama.modules.hazards.application.commands import RetireHazardType
from yakhnama.modules.hazards.application.handlers import RetireHazardTypeHandler
from yakhnama.modules.hazards.domain.entities import HazardType, HazardTypeStatus
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef


async def test_retire_hazard_type_when_missing_raises_not_found() -> None:
    uow_factory = FakeHazardsUnitOfWorkFactory()
    handler = RetireHazardTypeHandler(uow_factory)
    command = RetireHazardType(ref=HazardTypeRef(code="glof"), reason="merged")

    with pytest.raises(HazardTypeNotFoundError):
        await handler(command)

    assert uow_factory.committed is False


async def test_retire_hazard_type_when_active_commits_retired_type() -> None:
    ref = HazardTypeRef(code="glof")
    uow_factory = FakeHazardsUnitOfWorkFactory([HazardType(ref=ref)])
    handler = RetireHazardTypeHandler(uow_factory)

    await handler(RetireHazardType(ref=ref, reason="merged"))

    stored = uow_factory.hazard_types.committed_rows["glof"]
    assert stored.status is HazardTypeStatus.RETIRED
    assert stored.retired_reason == "merged"
    assert uow_factory.committed is True
```

### Test package `__init__.py` (every directory under `tests/`)

Use the dotted package path, the form the existing test packages use:

```python
"""tests.unit.modules.hazards test package."""
```

### `docs/data-dictionary/hazards.md`

```markdown
# Data dictionary: hazards

Owner: domain-modeler. Every field, unit and meaning introduced by this module is
listed here in the same change that introduces it.

## Concepts

| Concept | Kind | Meaning | Source of truth |
|---------|------|---------|-----------------|
| Hazard type | Entity | A node in the hazard taxonomy (glossary: Hazard type) | `data/reference/hazard_types.yaml` |

## Fields

| Entity / table | Field | Type | Unit | Nullable | Meaning | Allowed values |
|----------------|-------|------|------|----------|---------|----------------|
| HazardType / `hazard_types` | `code` | string (≤ 64) | — | no | Stable machine code; retired, never reused | `^[a-z][a-z0-9_]{1,63}$` |

## Open questions

Link each uncertain definition to its entry in `docs/open-questions.md`.
```

## Required tests

- `test_hazard_type_ref_with_valid_code_round_trips` (hypothesis over the code pattern)
- `test_hazard_type_ref_with_invalid_code_raises_validation_error` (parametrised)
- `test_hazard_type_ref_assignment_raises_validation_error` (frozen)
- `test_retire_hazard_type_when_missing_raises_not_found` (canonical; asserts no commit)
- `test_retire_hazard_type_when_active_commits_retired_type` (asserts committed state)
- Structural tests in `tests/architecture/` (owned by architect) must pick the module up
  automatically: it has `public.py`, every class declares `Implements:`.

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/hazards -q
poetry run poe test-unit
poetry run poe test-api        # architecture tests see the new module
poetry run poe check           # the gate
```

## Definition of Done

- [ ] Package tree, all `__init__.py` docstrings, `public.py` present.
- [ ] Four import-linter contracts added; `poetry run poe arch` passes and was shown to
      fail on a deliberate violation.
- [ ] Every class declares `Implements: <Pattern>` from `AGENTS.md` §3.
- [ ] `tests/fakes/<m>.py` covers every Protocol in `ports.py`; no mocks.
- [ ] Unit tests exist for every public function in the scaffold; coverage ≥ 95 % in the
      module's `domain` and `application`.
- [ ] `docs/data-dictionary/<m>.md` exists.
- [ ] `standards-reviewer` approved.
- [ ] Conventional Commit, for example `feat(hazards): scaffold hazards module`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Indirect imports break the layer contract.** `api/dependencies.py` must never import
  `yakhnama.platform.container`; read bound services from `request.app.state` (see
  `add-api-endpoint`).
- **Missing modules in contracts.** import-linter errors with "not present in the graph"
  if `yakhnama.main` or `yakhnama.platform.container` do not exist yet. Create them first;
  never delete the allowed importer to get green.
- **Protocol attributes.** Declare repositories on the UoW Protocol as read-only
  `@property`; a plain attribute is invariant and a fake with a concrete repository type
  fails mypy.
- **`model_copy` skips validation.** Entity methods rebuild with `model_validate` so
  constraints run on the new values.
- **Do not export too much.** Every name in `public.py` is a promise to other modules.
- **No `utils.py` or `helpers.py`.** Everything has a named home.
- **Template names are examples.** `RetireHazardType` and `HazardTypeRef` exist only if
  the plan asks for them.
