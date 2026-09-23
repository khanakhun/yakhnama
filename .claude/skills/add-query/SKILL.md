---
name: add-query
description: Add a read use case - a Get.../List... query model, a DTO, a query service port, a Specification for filters, an optimised SQL implementation with keyset cursor pagination, a fake, a unit test and a PostGIS integration test.
---

# add-query

## When to use

- Any read an API endpoint, export or another module needs.
- Never read through a repository for display purposes; repositories load aggregates for
  commands, query services serve readers (CQRS-lite, `AGENTS.md` §2.2).

## Preconditions

- The module and the row model(s) the query reads exist (`add-entity`). The example
  reads `HazardTypeRow` (`hazard_types`: `code`, `parent_code`, `status`,
  `retired_reason`).
- `yakhnama.shared_kernel.pagination` provides `PageRequest` (`limit` 1..200 via
  `MAX_PAGE_SIZE`, optional opaque `cursor` ≤ 512 chars), `Page[ItemT]` (`items`,
  `next_cursor`) and `encode_cursor` / `decode_cursor` for keyset values.
- `yakhnama.shared_kernel.specification` provides `Specification[CandidateT]` with
  `is_satisfied_by`, `and_`, `or_`, `not_`, and the combinator classes
  `AndSpecification` (`left`, `right`), `OrSpecification` (`left`, `right`),
  `NotSpecification` (`inner`). Check the real names before copying.
- `tests/integration/conftest.py` provides `db_session_factory:
  async_sessionmaker[AsyncSession]` bound to a fresh PostGIS database (Phase 1).

## Owning subagent

`application-engineer` (query model, DTO, Specifications, port, fake, unit tests) and
`persistence-engineer` (SQL implementation in `infrastructure/queries.py`, integration
tests).

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `src/yakhnama/modules/<m>/application/dto.py` | create/modify | Frozen read model |
| `src/yakhnama/modules/<m>/application/queries.py` | create/modify | `Get...`/`List...` models and Specifications |
| `src/yakhnama/modules/<m>/application/ports.py` | modify | `<Concept>QueryService` Protocol |
| `src/yakhnama/modules/<m>/infrastructure/queries.py` | create/modify | `SqlAlchemy<Concept>QueryService` and Specification compiler |
| `tests/fakes/<m>.py` | modify | `Fake<Concept>QueryService` |
| `tests/unit/modules/<m>/application/test_queries.py` | create/modify | Query model, Specification and paging tests |
| `tests/integration/modules/<m>/test_queries.py` | create/modify | SQL against PostGIS |
| `docs/data-dictionary/<m>.md` | modify | DTO fields if they differ from stored fields |

## Steps

1. Name the query `Get<Concept>` (one item) or `List<Concepts>` (a page), declared
   `Implements: Query`. Queries are frozen, `extra="forbid"`, and carry a `PageRequest` when they list.
2. Write the DTO (`Implements: DTO`): only the fields readers need, frozen, typed; no ORM objects.
3. Write one Specification per filter, over the DTO, so the fake and the SQL filter by the
   same rule. `List...to_specification()` combines the set filters with `and_` and returns
   `None` when none is set.
4. Add the `<Concept>QueryService` Protocol to `ports.py`. `get_...` raises the module's
   `...NotFoundError`; `list_...` returns `Page[DTO]`.
5. Implement the SQL service:
   - select only the DTO's columns (no `SELECT *`, no ORM entity loading);
   - compile the Specification to a `WHERE` clause with `match`; an unknown
     Specification raises `TypeError` (never silently ignored);
   - keyset pagination on a unique, indexed ordering (`ORDER BY code`, `WHERE code >
     :after`), fetch `limit + 1` rows to know whether a next page exists; never `OFFSET`,
     never `COUNT(*)` per page;
   - make sure the ordering and filter columns are indexed (migration if not).
6. Write the fake with the same ordering and Specifications.
7. Write the unit and integration tests below. Run the checks.

## Templates

### `src/yakhnama/modules/hazards/application/dto.py`
```python
"""Read models returned by the hazards query services.

Patterns: DTO.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.domain.entities import HazardTypeStatus
from yakhnama.modules.hazards.domain.value_objects import HazardCode


class HazardTypeDTO(BaseModel):
    """One hazard type as shown to readers.

    Implements: DTO.

    Attributes:
        code: Stable hazard code.
        parent_code: Code of the broader hazard type, if any.
        status: ``active`` or ``retired``.
        retired_reason: Why the type was retired, if it was.
    """

    model_config = ConfigDict(frozen=True)

    code: HazardCode
    parent_code: HazardCode | None
    status: HazardTypeStatus
    retired_reason: str | None
```

### `src/yakhnama/modules/hazards/application/queries.py`
```python
"""Read-side queries and filter specifications for the hazards module.

Patterns: Query, Specification.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.application.dto import HazardTypeDTO
from yakhnama.modules.hazards.domain.entities import HazardTypeStatus
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import Specification


class HazardTypeStatusSpecification(Specification[HazardTypeDTO]):
    """Matches hazard types with a given status.

    Implements: Specification.

    Attributes:
        status: The status to match.
    """

    def __init__(self, status: HazardTypeStatus) -> None:
        """Create the specification.

        Args:
            status: The status to match.
        """
        self.status = status

    def is_satisfied_by(self, candidate: HazardTypeDTO) -> bool:
        """Return ``True`` if ``candidate`` has the wanted status."""
        return candidate.status is self.status


class HazardTypeParentSpecification(Specification[HazardTypeDTO]):
    """Matches the direct children of a hazard type.

    Implements: Specification.

    Attributes:
        parent: The parent whose children match.
    """

    def __init__(self, parent: HazardTypeRef) -> None:
        """Create the specification.

        Args:
            parent: The parent whose children match.
        """
        self.parent = parent

    def is_satisfied_by(self, candidate: HazardTypeDTO) -> bool:
        """Return ``True`` if ``candidate`` is a direct child of ``parent``."""
        return candidate.parent_code == self.parent.code


class GetHazardType(BaseModel):
    """Ask for one hazard type by code.

    Implements: Query.

    Attributes:
        ref: The hazard type to read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef


class ListHazardTypes(BaseModel):
    """Ask for one page of hazard types, ordered by code.

    Implements: Query.

    Attributes:
        status: Only return types with this status, if set.
        parent: Only return direct children of this type, if set.
        page: Cursor and page size (at most 200).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: HazardTypeStatus | None = None
    parent: HazardTypeRef | None = None
    page: PageRequest = PageRequest()

    def to_specification(self) -> Specification[HazardTypeDTO] | None:
        """Combine the set filters into one specification.

        Returns:
            The conjunction of every filter that is set, or ``None`` if no filter
            is set.
        """
        specifications: list[Specification[HazardTypeDTO]] = []
        if self.status is not None:
            specifications.append(HazardTypeStatusSpecification(self.status))
        if self.parent is not None:
            specifications.append(HazardTypeParentSpecification(self.parent))
        if not specifications:
            return None
        combined = specifications[0]
        for specification in specifications[1:]:
            combined = combined.and_(specification)
        return combined
```

### `src/yakhnama/modules/hazards/application/ports.py` (query port added)
```python
"""Ports the hazards application layer depends on.

Patterns: Repository (port side), Unit of Work, Query Service.
"""

from typing import Protocol

from yakhnama.modules.hazards.application.dto import HazardTypeDTO
from yakhnama.modules.hazards.application.queries import (
    GetHazardType,
    ListHazardTypes,
)
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.pagination import Page
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


class HazardTypeQueryService(Protocol):
    """Read port for hazard types.

    Implements: Query Service.
    """

    async def get_hazard_type(self, query: GetHazardType) -> HazardTypeDTO:
        """Return the hazard type named by ``query``.

        Raises:
            HazardTypeNotFoundError: If no hazard type matches ``query.ref``.
        """
        ...

    async def list_hazard_types(self, query: ListHazardTypes) -> Page[HazardTypeDTO]:
        """Return one page of hazard types matching ``query``, ordered by code."""
        ...
```

### `src/yakhnama/modules/hazards/infrastructure/queries.py`
```python
"""SQL implementations of the hazards query service ports.

Patterns: Query Service (adapter side), Specification (SQL compilation).
"""

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.hazards.application.dto import HazardTypeDTO
from yakhnama.modules.hazards.application.queries import (
    GetHazardType,
    HazardTypeParentSpecification,
    HazardTypeStatusSpecification,
    ListHazardTypes,
)
from yakhnama.modules.hazards.domain.entities import HazardTypeStatus
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
from yakhnama.shared_kernel.pagination import Page, decode_cursor, encode_cursor
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
)

_COLUMNS = (
    HazardTypeRow.code,
    HazardTypeRow.parent_code,
    HazardTypeRow.status,
    HazardTypeRow.retired_reason,
)


def compile_specification(
    specification: Specification[HazardTypeDTO],
) -> ColumnElement[bool]:
    """Translate a hazard type specification into a SQL ``WHERE`` clause.

    Args:
        specification: A specification built by ``ListHazardTypes``.

    Returns:
        A boolean SQL expression over ``hazard_types``.

    Raises:
        TypeError: If the specification has no SQL translation yet.
    """
    match specification:
        case HazardTypeStatusSpecification():
            return HazardTypeRow.status == specification.status.value
        case HazardTypeParentSpecification():
            return HazardTypeRow.parent_code == specification.parent.code
        case AndSpecification():
            return and_(
                compile_specification(specification.left),
                compile_specification(specification.right),
            )
        case OrSpecification():
            return or_(
                compile_specification(specification.left),
                compile_specification(specification.right),
            )
        case NotSpecification():
            return not_(compile_specification(specification.inner))
        case _:
            message = f"no SQL translation for {type(specification).__name__}"
            raise TypeError(message)


class SqlAlchemyHazardTypeQueryService:
    """PostGIS-backed implementation of ``HazardTypeQueryService``.

    Implements: Query Service (port ``HazardTypeQueryService``).

    Selects only the columns the DTO needs and pages by keyset on ``code`` so
    every page costs one index range scan, however deep the cursor is.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens a read-only session per query.
        """
        self._session_factory = session_factory

    async def get_hazard_type(self, query: GetHazardType) -> HazardTypeDTO:
        """Return the hazard type named by ``query``.

        Args:
            query: The validated query.

        Returns:
            The hazard type.

        Raises:
            HazardTypeNotFoundError: If no hazard type matches ``query.ref``.
        """
        statement = select(*_COLUMNS).where(HazardTypeRow.code == query.ref.code)
        async with self._session_factory() as session:
            row = (await session.execute(statement)).one_or_none()
        if row is None:
            raise HazardTypeNotFoundError(query.ref)
        return _to_dto(row.code, row.parent_code, row.status, row.retired_reason)

    async def list_hazard_types(self, query: ListHazardTypes) -> Page[HazardTypeDTO]:
        """Return one page of hazard types matching ``query``, ordered by code.

        Args:
            query: The validated query.

        Returns:
            Up to ``query.page.limit`` items and the cursor of the next page.
        """
        statement = select(*_COLUMNS).order_by(HazardTypeRow.code)
        specification = query.to_specification()
        if specification is not None:
            statement = statement.where(compile_specification(specification))
        if query.page.cursor is not None:
            (after_code,) = decode_cursor(query.page.cursor)
            statement = statement.where(HazardTypeRow.code > after_code)
        # Fetch one extra row to learn whether a next page exists without COUNT(*).
        statement = statement.limit(query.page.limit + 1)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        items = tuple(
            _to_dto(row.code, row.parent_code, row.status, row.retired_reason)
            for row in rows[: query.page.limit]
        )
        has_more = len(rows) > query.page.limit
        next_cursor = encode_cursor((items[-1].code,)) if has_more else None
        return Page(items=items, next_cursor=next_cursor)


def _to_dto(
    code: str,
    parent_code: str | None,
    status: str,
    retired_reason: str | None,
) -> HazardTypeDTO:
    """Build the DTO from selected column values."""
    return HazardTypeDTO(
        code=code,
        parent_code=parent_code,
        status=HazardTypeStatus(status),
        retired_reason=retired_reason,
    )
```

### `tests/fakes/hazards.py` — class to append (merge the imports)
```python
"""In-memory fake of the hazards query service (append to ``tests/fakes/hazards.py``).

Patterns: Fake.
"""

from collections.abc import Iterable

from yakhnama.modules.hazards.application.dto import HazardTypeDTO
from yakhnama.modules.hazards.application.queries import GetHazardType, ListHazardTypes
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.shared_kernel.pagination import Page, decode_cursor, encode_cursor


class FakeHazardTypeQueryService:
    """In-memory ``HazardTypeQueryService`` using the same specifications as SQL.

    Implements: Fake.

    Attributes:
        rows: The hazard types the fake serves, keyed by code.
    """

    def __init__(self, rows: Iterable[HazardTypeDTO] = ()) -> None:
        """Create the fake.

        Args:
            rows: Hazard types that exist before the test acts.
        """
        self.rows: dict[str, HazardTypeDTO] = {row.code: row for row in rows}

    async def get_hazard_type(self, query: GetHazardType) -> HazardTypeDTO:
        """Return the hazard type or raise ``HazardTypeNotFoundError``."""
        row = self.rows.get(query.ref.code)
        if row is None:
            raise HazardTypeNotFoundError(query.ref)
        return row

    async def list_hazard_types(self, query: ListHazardTypes) -> Page[HazardTypeDTO]:
        """Filter, order by code and page exactly like the SQL implementation."""
        specification = query.to_specification()
        after = decode_cursor(query.page.cursor)[0] if query.page.cursor else ""
        matching = [
            row
            for code, row in sorted(self.rows.items())
            if code > after
            and (specification is None or specification.is_satisfied_by(row))
        ]
        items = tuple(matching[: query.page.limit])
        has_more = len(matching) > query.page.limit
        next_cursor = encode_cursor((items[-1].code,)) if has_more else None
        return Page(items=items, next_cursor=next_cursor)
```

The template above was checked as a standalone module; when you append it, move its
imports to the top of `tests/fakes/hazards.py` and drop the module docstring. Tests then
import it from `tests.fakes.hazards`.

### `tests/unit/modules/hazards/application/test_queries.py`
```python
"""Unit tests for the hazards queries and specifications."""

import pydantic
import pytest

from tests.fakes.hazards import FakeHazardTypeQueryService
from yakhnama.modules.hazards.application.dto import HazardTypeDTO
from yakhnama.modules.hazards.application.queries import (
    GetHazardType,
    ListHazardTypes,
)
from yakhnama.modules.hazards.domain.entities import HazardTypeStatus
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.shared_kernel.pagination import PageRequest

# Synthetic codes: tests must not assert a real taxonomy.
PARENT = HazardTypeDTO(
    code="example_a",
    parent_code=None,
    status=HazardTypeStatus.ACTIVE,
    retired_reason=None,
)
CHILD_ACTIVE = HazardTypeDTO(
    code="example_b",
    parent_code="example_a",
    status=HazardTypeStatus.ACTIVE,
    retired_reason=None,
)
CHILD_RETIRED = HazardTypeDTO(
    code="example_c",
    parent_code="example_a",
    status=HazardTypeStatus.RETIRED,
    retired_reason="merged into example_b",
)


def test_list_hazard_types_with_limit_above_max_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        ListHazardTypes(page=PageRequest(limit=201))


def test_list_hazard_types_without_filters_returns_no_specification() -> None:
    query = ListHazardTypes()

    specification = query.to_specification()

    assert specification is None


def test_list_hazard_types_with_status_and_parent_matches_only_both() -> None:
    query = ListHazardTypes(
        status=HazardTypeStatus.ACTIVE,
        parent=HazardTypeRef(code="example_a"),
    )

    specification = query.to_specification()

    assert specification is not None
    assert [
        row.code
        for row in (PARENT, CHILD_ACTIVE, CHILD_RETIRED)
        if specification.is_satisfied_by(row)
    ] == ["example_b"]


async def test_fake_query_service_pages_by_code_returns_cursor_until_last_page() -> (
    None
):
    service = FakeHazardTypeQueryService([CHILD_RETIRED, PARENT, CHILD_ACTIVE])

    first = await service.list_hazard_types(
        ListHazardTypes(page=PageRequest(limit=2)),
    )
    second = await service.list_hazard_types(
        ListHazardTypes(page=PageRequest(limit=2, cursor=first.next_cursor)),
    )

    assert [row.code for row in first.items] == ["example_a", "example_b"]
    assert [row.code for row in second.items] == ["example_c"]
    assert second.next_cursor is None


async def test_fake_query_service_get_when_missing_raises_not_found() -> None:
    service = FakeHazardTypeQueryService()

    with pytest.raises(HazardTypeNotFoundError):
        await service.get_hazard_type(
            GetHazardType(ref=HazardTypeRef(code="example_b")),
        )
```

### `tests/integration/modules/hazards/test_queries.py`
```python
"""Integration tests for the hazards query services against real PostGIS."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.hazards.application.queries import (
    GetHazardType,
    ListHazardTypes,
)
from yakhnama.modules.hazards.domain.entities import HazardTypeStatus
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.domain.value_objects import HazardTypeRef
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
from yakhnama.modules.hazards.infrastructure.queries import (
    SqlAlchemyHazardTypeQueryService,
)
from yakhnama.shared_kernel.pagination import PageRequest

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_session_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Insert a synthetic taxonomy and return the session factory."""
    async with db_session_factory() as session, session.begin():
        session.add(HazardTypeRow(code="example_a", parent_code=None, status="active"))
        await session.flush()
        session.add_all(
            [
                HazardTypeRow(
                    code="example_b",
                    parent_code="example_a",
                    status="active",
                ),
                HazardTypeRow(
                    code="example_c",
                    parent_code="example_a",
                    status="retired",
                    retired_reason="merged into example_b",
                ),
            ],
        )
    return db_session_factory


async def test_list_hazard_types_pages_by_code_returns_cursor_until_last_page(
    seeded_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyHazardTypeQueryService(seeded_session_factory)

    first = await service.list_hazard_types(
        ListHazardTypes(page=PageRequest(limit=2)),
    )
    second = await service.list_hazard_types(
        ListHazardTypes(page=PageRequest(limit=2, cursor=first.next_cursor)),
    )

    assert [row.code for row in first.items] == ["example_a", "example_b"]
    assert [row.code for row in second.items] == ["example_c"]
    assert second.next_cursor is None


async def test_list_hazard_types_with_status_filter_returns_only_matching(
    seeded_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyHazardTypeQueryService(seeded_session_factory)

    page = await service.list_hazard_types(
        ListHazardTypes(status=HazardTypeStatus.RETIRED),
    )

    assert [row.code for row in page.items] == ["example_c"]


async def test_get_hazard_type_when_missing_raises_not_found(
    seeded_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyHazardTypeQueryService(seeded_session_factory)

    with pytest.raises(HazardTypeNotFoundError):
        await service.get_hazard_type(
            GetHazardType(ref=HazardTypeRef(code="missing")),
        )
```

## Required tests

- Unit: `test_list_hazard_types_with_limit_above_max_raises_validation_error`,
  `test_list_hazard_types_without_filters_returns_no_specification`,
  `test_list_hazard_types_with_status_and_parent_matches_only_both`,
  `test_fake_query_service_pages_by_code_returns_cursor_until_last_page`,
  `test_fake_query_service_get_when_missing_raises_not_found`.
- Integration (marker `integration`):
  `test_list_hazard_types_pages_by_code_returns_cursor_until_last_page`,
  `test_list_hazard_types_with_status_filter_returns_only_matching`,
  `test_get_hazard_type_when_missing_raises_not_found`.
- A test for every Specification class and for the `TypeError` branch of the compiler if
  the module adds combinators of its own.

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/hazards/application -q
poetry run poe test-unit
poetry run poe up
poetry run poe test-integration     # PostGIS/MinIO fixtures from Phase 1
poetry run poe check
```

## Definition of Done

- [ ] Query named `Get...`/`List...`, frozen, bounded; DTO frozen and minimal.
- [ ] Filters are Specifications shared by the fake and the SQL compiler.
- [ ] Keyset cursor pagination, `limit` ≤ 200, no `OFFSET`, no per-page `COUNT(*)`.
- [ ] Only needed columns selected; ordering and filter columns indexed.
- [ ] Unit tests with the fake and integration tests with real PostGIS pass.
- [ ] `standards-reviewer` approved (`security-reviewer` if the query serves public or
      personal data).
- [ ] Conventional Commit, for example `feat(hazards): list hazard types`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Non-unique ordering.** Keyset pagination on a non-unique column skips or repeats
  rows. Order by a unique key, or by `(sort_column, id)` and encode both in the cursor.
- **Trusting the cursor.** It is client input: `decode_cursor` must reject malformed
  tokens with a `ValidationError`, and the value is only ever a bound parameter.
- **Public reads leaking unverified data.** Anonymous list queries filter to verified
  records in SQL, not in the router.
- **Loading entities for reads.** Selecting ORM entities and mapping them wastes memory
  and couples the read model to the write model.
- **Unmarked integration tests never run.** `poe test-integration` selects
  `-m integration`; every module under `tests/integration/` sets
  `pytestmark = pytest.mark.integration`.
- **Geometry in listings.** Return geometry as GeoJSON built in SQL
  (`ST_AsGeoJSON`) or via the mapper; never let `WKBElement` reach a DTO.
