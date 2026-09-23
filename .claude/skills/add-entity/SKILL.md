---
name: add-entity
description: Add an entity or value object to an existing module, end to end, from the frozen Pydantic model through mapper, ORM row, repository port and adapter, migration, factory, unit and PostGIS integration tests.
---

# add-entity

## When to use

- A new identity-bearing concept (entity or aggregate root) or immutable concept (value
  object) is needed in an existing module.
- A value object alone needs only steps 1–3 and 9–10; skip persistence if nothing stores
  it on its own.

## Preconditions

- The module exists (`new-module`). Its `public.py` and import-linter contracts are in
  place.
- The concept, every field, its unit and its meaning are defined in the phase plan or
  the glossary. **Never invent a domain fact**: an uncertain definition goes to
  `docs/open-questions.md` with a proposed default before you write code.
- `yakhnama.platform.db.Base` (SQLAlchemy `DeclarativeBase` with the naming convention in
  `write-migration`) exists. Check its real name.
- For geometry: `shapely` has no bundled type information. `types-shapely` must be in the
  dev group (lead runs `poetry add --group dev types-shapely`); if it is missing,
  `mypy --strict` fails on the mapper. Stop and report instead of adding an ignore.
- `tests/integration/conftest.py` provides an async `db_session: AsyncSession` fixture
  against real PostGIS via testcontainers, rolled back after each test (test-engineer,
  from Phase 1).

## Owning subagent

`domain-modeler` writes the domain model, factory and unit tests and the data
dictionary; it then hands over to `persistence-engineer` for the ORM row, mapper,
repository adapter, migration and integration tests. `application-engineer` adds the
port methods and fake updates.

## Files

The example is a `Place` entity with WGS84 geometry and a `BoundingBox` value object in a
`places` module. **Example only**: the real places model is defined by its phase plan.

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `src/yakhnama/modules/<m>/domain/value_objects.py` | modify | Value objects (frozen) |
| `src/yakhnama/modules/<m>/domain/entities.py` | modify | Entity (frozen; methods return new instances) |
| `src/yakhnama/modules/<m>/application/ports.py` | modify | Repository port methods |
| `src/yakhnama/modules/<m>/infrastructure/orm.py` | create/modify | Typed `Mapped[]` row model |
| `src/yakhnama/modules/<m>/infrastructure/mappers.py` | create/modify | Entity ↔ row (Anti-Corruption Layer) |
| `src/yakhnama/modules/<m>/infrastructure/repositories.py` | create/modify | SQLAlchemy adapter of the port |
| `migrations/versions/NNNN_<slug>.py` | create | Via `write-migration` |
| `tests/factories/<m>.py` | create/modify | polyfactory factory |
| `tests/fakes/<m>.py` | modify | Fake repository for the new port |
| `tests/unit/modules/<m>/domain/test_value_objects.py` | modify | hypothesis tests |
| `tests/unit/modules/<m>/domain/test_entities.py` | modify | Entity behaviour tests |
| `tests/integration/modules/<m>/test_repositories.py` | create/modify | Real PostGIS round trip |
| `docs/data-dictionary/<m>.md` | modify | Every field, unit, meaning |

## Steps

1. **Domain (domain-modeler).** Write value objects and the entity. `model_config =
   ConfigDict(frozen=True)`; every string has `max_length`, every number has bounds;
   every datetime is `AwareDatetime`; geometry is a `geojson_pydantic` type with
   longitude first. Methods that "change" the entity return a new instance built with
   `model_validate` (not `model_copy`, which skips validation).
2. Errors for broken invariants derive from the shared-kernel error classes in
   `domain/errors.py`.
3. Unit tests: hypothesis for every value object; behaviour tests for every entity
   method; the factory in `tests/factories/<m>.py` builds valid instances with fixed UTC
   datetimes. Update `docs/data-dictionary/<m>.md`.
4. **Port (application-engineer).** Add `get`/`save` (and only what a use case needs) to
   the repository Protocol and to the UoW Protocol as a read-only property. Update the
   fake in `tests/fakes/<m>.py`.
5. **Row model (persistence-engineer).** SQLAlchemy 2.0 typed `Mapped[...]` with
   `mapped_column`. Timestamps `DateTime(timezone=True)`. Geometry
   `Geometry(geometry_type=..., srid=4326, spatial_index=False)` plus an explicit GiST
   `Index(..., postgresql_using="gist")` so autogenerate sees it under our name.
6. **Mapper.** Pure functions `<entity>_to_row` and `row_to_<entity>`. Shapely and WKB
   stay here; the domain only sees GeoJSON models.
7. **Repository adapter.** Class `SqlAlchemy<Entity>Repository` taking the UoW's
   `AsyncSession`; it never commits. Its docstring names the port it implements.
8. **Migration.** Follow `write-migration` exactly (persistence-engineer only).
9. **Integration tests** against real PostGIS: save → get round trip equality, SRID is
   4326, missing id returns `None`.
10. Run the checks.

## Templates

### `src/yakhnama/modules/places/domain/value_objects.py`
```python
"""Value objects for the places bounded context.

Patterns: Value Object.
"""

from enum import StrEnum
from typing import Annotated, Self

from geojson_pydantic import MultiPolygon, Point, Polygon
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The discriminator lets Pydantic pick the geometry class from the GeoJSON "type"
# member instead of trying each class in turn.
PlaceGeometry = Annotated[Point | Polygon | MultiPolygon, Field(discriminator="type")]

Longitude = Annotated[float, Field(ge=-180.0, le=180.0, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0, allow_inf_nan=False)]


class AdministrativeLevel(StrEnum):
    """Level of a place in the administrative hierarchy (glossary: Place).

    Implements: Value Object.
    """

    COUNTRY = "country"
    PROVINCE_OR_REGION = "province_or_region"
    DISTRICT = "district"
    TEHSIL = "tehsil"
    UNION_COUNCIL = "union_council"
    VILLAGE = "village"


class BoundingBox(BaseModel):
    """Axis-aligned WGS84 bounding box in degrees.

    Implements: Value Object.

    Boxes crossing the antimeridian are rejected; no Yakhnama region needs them.

    Attributes:
        west: Minimum longitude.
        south: Minimum latitude.
        east: Maximum longitude.
        north: Maximum latitude.
    """

    model_config = ConfigDict(frozen=True)

    west: Longitude
    south: Latitude
    east: Longitude
    north: Latitude

    @model_validator(mode="after")
    def _check_corner_order(self) -> Self:
        """Reject boxes whose minimum corner exceeds the maximum corner.

        Returns:
            The validated box.

        Raises:
            ValueError: If ``west > east`` or ``south > north``.
        """
        if self.west > self.east or self.south > self.north:
            message = "bounding box corners are out of order"
            raise ValueError(message)
        return self

    def contains(self, longitude: float, latitude: float) -> bool:
        """Return ``True`` if the point lies inside or on the edge of the box.

        Args:
            longitude: Longitude in degrees.
            latitude: Latitude in degrees.

        Returns:
            Whether the point is covered by the box.
        """
        return self.west <= longitude <= self.east and (
            self.south <= latitude <= self.north
        )
```

The levels mirror the glossary entry for **Place**
(country → province/region → district → tehsil → union council → village). Their codes
are an example; confirm them in the data dictionary before use.

### `src/yakhnama/modules/places/domain/entities.py`
```python
"""Entities of the places bounded context.

Patterns: Entity.
"""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

from yakhnama.modules.places.domain.value_objects import (
    AdministrativeLevel,
    PlaceGeometry,
)

PlaceDisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class Place(BaseModel):
    """A named location with WGS84 geometry in the administrative hierarchy.

    Implements: Entity.

    Names in local languages and scripts live in ``PlaceName`` records; this
    entity keeps only the English display name used in logs and admin screens.

    Attributes:
        id: Stable identity from the injected ``IdGenerator``.
        display_name: English display name.
        level: Administrative level.
        parent_id: The enclosing place, or ``None`` for a country.
        geometry: GeoJSON geometry in WGS84 (EPSG:4326), longitude first.
        created_at: When the place was first recorded, in UTC.
        revised_at: When the geometry or name last changed, in UTC.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    display_name: PlaceDisplayName
    level: AdministrativeLevel
    parent_id: UUID | None = None
    geometry: PlaceGeometry
    created_at: AwareDatetime
    revised_at: AwareDatetime

    def redraw(self, geometry: PlaceGeometry, *, revised_at: datetime) -> Self:
        """Return a copy of this place with a new geometry.

        Args:
            geometry: The replacement geometry, WGS84.
            revised_at: When the change was made; must be timezone-aware.

        Returns:
            A new ``Place``; this instance is unchanged.

        Raises:
            pydantic.ValidationError: If ``revised_at`` is naive or the geometry
                is invalid.
        """
        # Re-validate instead of model_copy so constraints and the aware-datetime
        # check run on the new values too.
        return self.model_validate(
            self.model_dump() | {"geometry": geometry, "revised_at": revised_at},
        )
```

### `src/yakhnama/modules/places/application/ports.py`
```python
"""Ports the places application layer depends on.

Patterns: Repository (port side).
"""

from typing import Protocol
from uuid import UUID

from yakhnama.modules.places.domain.entities import Place


class PlaceRepository(Protocol):
    """Persistence port for places.

    Implements: Repository.
    """

    async def get(self, place_id: UUID) -> Place | None:
        """Return the place with ``place_id``, or ``None`` if it does not exist."""
        ...

    async def save(self, place: Place) -> None:
        """Stage ``place`` for insert or update in the current unit of work."""
        ...
```

### `src/yakhnama/modules/places/infrastructure/orm.py`
```python
"""SQLAlchemy row models for the places bounded context.

Patterns: Repository (row models used by the SQLAlchemy adapters).
"""

from datetime import datetime
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID = 4326


class PlaceRow(Base):
    """Row model of the ``places`` table.

    Implements: Repository (row model of ``SqlAlchemyPlaceRepository``).

    Attributes:
        id: Primary key, the entity id.
        display_name: English display name.
        level: ``AdministrativeLevel`` value.
        parent_id: Enclosing place.
        geometry: WGS84 geometry.
        created_at: First recorded, UTC.
        revised_at: Last revised, UTC.
    """

    __tablename__ = "places"
    __table_args__ = (
        # Declared explicitly (spatial_index=False below) so Alembic autogenerate
        # sees the GiST index and names it by our convention.
        Index("ix_places_geometry_gist", "geometry", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("places.id", ondelete="RESTRICT"),
        index=True,
    )
    geometry: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=WGS84_SRID, spatial_index=False),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

### `src/yakhnama/modules/places/infrastructure/mappers.py`
```python
"""Translate between place entities and their row models.

Patterns: Anti-Corruption Layer (mapper).

Shapely and WKB stay on this side of the boundary; the domain sees only
GeoJSON models.
"""

from geoalchemy2.shape import from_shape, to_shape
from pydantic import TypeAdapter
from shapely.geometry import mapping, shape

from yakhnama.modules.places.domain.entities import Place
from yakhnama.modules.places.domain.value_objects import (
    AdministrativeLevel,
    PlaceGeometry,
)
from yakhnama.modules.places.infrastructure.orm import WGS84_SRID, PlaceRow

_GEOMETRY_ADAPTER: TypeAdapter[PlaceGeometry] = TypeAdapter(PlaceGeometry)


def place_to_row(place: Place) -> PlaceRow:
    """Build the row model for ``place``.

    Args:
        place: The entity to persist.

    Returns:
        A transient ``PlaceRow`` carrying the same values.
    """
    return PlaceRow(
        id=place.id,
        display_name=place.display_name,
        level=place.level.value,
        parent_id=place.parent_id,
        geometry=from_shape(shape(place.geometry), srid=WGS84_SRID),
        created_at=place.created_at,
        revised_at=place.revised_at,
    )


def row_to_place(row: PlaceRow) -> Place:
    """Rebuild the entity from its row model.

    Args:
        row: A row loaded from the ``places`` table.

    Returns:
        The validated ``Place``.
    """
    return Place(
        id=row.id,
        display_name=row.display_name,
        level=AdministrativeLevel(row.level),
        parent_id=row.parent_id,
        geometry=_GEOMETRY_ADAPTER.validate_python(mapping(to_shape(row.geometry))),
        created_at=row.created_at,
        revised_at=row.revised_at,
    )
```

### `src/yakhnama/modules/places/infrastructure/repositories.py`
```python
"""SQLAlchemy adapters for the places repository ports.

Patterns: Repository (adapter side).
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.places.domain.entities import Place
from yakhnama.modules.places.infrastructure.mappers import place_to_row, row_to_place
from yakhnama.modules.places.infrastructure.orm import PlaceRow


class SqlAlchemyPlaceRepository:
    """PostGIS-backed implementation of ``PlaceRepository``.

    Implements: Repository (port ``PlaceRepository``).

    The session belongs to the unit of work; this class never commits.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, place_id: UUID) -> Place | None:
        """Return the place with ``place_id``, or ``None`` if it does not exist.

        Args:
            place_id: Identity of the place.

        Returns:
            The entity, or ``None``.
        """
        row = await self._session.get(PlaceRow, place_id)
        return None if row is None else row_to_place(row)

    async def save(self, place: Place) -> None:
        """Stage ``place`` for insert or update.

        Args:
            place: The entity to persist.
        """
        await self._session.merge(place_to_row(place))
```

### `tests/factories/places.py`
```python
"""Polyfactory factories for the places domain.

Patterns: Factory.
"""

from datetime import UTC, datetime

from geojson_pydantic import Point
from geojson_pydantic.types import Position2D
from polyfactory import Use
from polyfactory.factories.pydantic_factory import ModelFactory

from yakhnama.modules.places.domain.entities import Place
from yakhnama.modules.places.domain.value_objects import AdministrativeLevel

# Fixed instants keep factories deterministic; tests that care about time set
# their own values explicitly.
RECORDED_AT = datetime(2025, 1, 1, tzinfo=UTC)


def _example_point() -> Point:
    """Return a synthetic point (example only, not a real place)."""
    return Point(type="Point", coordinates=Position2D(longitude=74.0, latitude=36.0))


class PlaceFactory(ModelFactory[Place]):
    """Build valid ``Place`` entities.

    Implements: Factory.
    """

    __check_model__ = True

    level = AdministrativeLevel.VILLAGE
    parent_id = None
    geometry = Use(_example_point)
    created_at = RECORDED_AT
    revised_at = RECORDED_AT
```

### `tests/unit/modules/places/domain/test_value_objects.py`
```python
"""Unit tests for the places value objects."""

import pydantic
import pytest
from hypothesis import given
from hypothesis import strategies as st

from yakhnama.modules.places.domain.value_objects import BoundingBox

LONGITUDES = st.floats(min_value=-180.0, max_value=180.0)
LATITUDES = st.floats(min_value=-90.0, max_value=90.0)


@st.composite
def bounding_boxes(draw: st.DrawFn) -> BoundingBox:
    """Draw a valid bounding box by sorting two random corners."""
    west, east = sorted((draw(LONGITUDES), draw(LONGITUDES)))
    south, north = sorted((draw(LATITUDES), draw(LATITUDES)))
    return BoundingBox(west=west, south=south, east=east, north=north)


@given(box=bounding_boxes())
def test_bounding_box_contains_its_own_corners_returns_true(box: BoundingBox) -> None:
    corners = [(box.west, box.south), (box.east, box.north)]

    results = [box.contains(longitude, latitude) for longitude, latitude in corners]

    assert all(results)


@given(box=bounding_boxes())
def test_bounding_box_round_trip_returns_equal_box(box: BoundingBox) -> None:
    payload = box.model_dump()

    restored = BoundingBox.model_validate(payload)

    assert restored == box


def test_bounding_box_with_south_above_north_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        BoundingBox(west=74.0, south=37.0, east=75.0, north=36.0)


@pytest.mark.parametrize("longitude", [-180.1, 180.1, float("nan"), float("inf")])
def test_bounding_box_with_out_of_range_longitude_raises_validation_error(
    longitude: float,
) -> None:
    with pytest.raises(pydantic.ValidationError):
        BoundingBox(west=longitude, south=35.0, east=75.0, north=36.0)
```

### `tests/unit/modules/places/domain/test_entities.py`
```python
"""Unit tests for the places entities."""

from datetime import UTC, datetime

import pydantic
import pytest
from geojson_pydantic import Point
from geojson_pydantic.types import Position2D

from tests.factories.places import PlaceFactory


def test_place_redraw_with_new_geometry_returns_new_instance() -> None:
    place = PlaceFactory.build()
    geometry = Point(
        type="Point",
        coordinates=Position2D(longitude=74.5, latitude=36.5),
    )
    revised_at = datetime(2025, 6, 1, tzinfo=UTC)

    redrawn = place.redraw(geometry, revised_at=revised_at)

    assert redrawn.geometry == geometry
    assert redrawn.revised_at == revised_at
    assert place.geometry != geometry


def test_place_redraw_with_naive_datetime_raises_validation_error() -> None:
    place = PlaceFactory.build()
    geometry = Point(
        type="Point",
        coordinates=Position2D(longitude=74.5, latitude=36.5),
    )
    naive = datetime(2025, 6, 1)  # noqa: DTZ001  # reason: asserting rejection

    with pytest.raises(pydantic.ValidationError):
        place.redraw(geometry, revised_at=naive)


def test_place_assignment_raises_validation_error() -> None:
    place = PlaceFactory.build()

    with pytest.raises(pydantic.ValidationError):
        place.display_name = "renamed"  # type: ignore[misc]  # reason: frozen check
```

### `tests/integration/modules/places/test_repositories.py`
```python
"""Integration tests for the places repositories against real PostGIS."""

import pytest
from geojson_pydantic import Polygon
from geojson_pydantic.types import Position2D
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.places import PlaceFactory
from yakhnama.modules.places.infrastructure.orm import WGS84_SRID, PlaceRow
from yakhnama.modules.places.infrastructure.repositories import (
    SqlAlchemyPlaceRepository,
)

pytestmark = pytest.mark.integration

EXAMPLE_POLYGON = Polygon(
    type="Polygon",
    coordinates=[
        [
            Position2D(74.0, 36.0),
            Position2D(74.1, 36.0),
            Position2D(74.1, 36.1),
            Position2D(74.0, 36.0),
        ],
    ],
)


async def test_place_repository_save_then_get_returns_equal_place(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyPlaceRepository(db_session)
    place = PlaceFactory.build(geometry=EXAMPLE_POLYGON)

    await repository.save(place)
    await db_session.flush()
    db_session.expunge_all()
    loaded = await repository.get(place.id)

    assert loaded == place


async def test_place_repository_save_stores_geometry_in_wgs84(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyPlaceRepository(db_session)
    place = PlaceFactory.build()

    await repository.save(place)
    await db_session.flush()
    srid = await db_session.scalar(
        select(func.ST_SRID(PlaceRow.geometry)).where(PlaceRow.id == place.id),
    )

    assert srid == WGS84_SRID


async def test_place_repository_get_when_missing_returns_none(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyPlaceRepository(db_session)
    place = PlaceFactory.build()

    loaded = await repository.get(place.id)

    assert loaded is None
```

### `docs/data-dictionary/places.md` — rows to add

```markdown
| Entity / table | Field | Type | Unit | Nullable | Meaning | Allowed values |
|----------------|-------|------|------|----------|---------|----------------|
| Place / `places` | `id` | uuid | — | no | Stable identity | — |
| Place / `places` | `display_name` | string (≤ 200) | — | no | English display name; local names live in PlaceName | — |
| Place / `places` | `level` | string (≤ 32) | — | no | Administrative level | see `AdministrativeLevel` |
| Place / `places` | `parent_id` | uuid | — | yes | Enclosing place; null only for a country | — |
| Place / `places` | `geometry` | geometry, EPSG:4326 | degrees | no | Point, Polygon or MultiPolygon, longitude first | — |
| Place / `places` | `created_at` | timestamptz | UTC | no | First recorded | — |
| Place / `places` | `revised_at` | timestamptz | UTC | no | Last geometry or name change | — |
```

## Required tests

- Value objects (hypothesis): `test_bounding_box_contains_its_own_corners_returns_true`,
  `test_bounding_box_round_trip_returns_equal_box`,
  `test_bounding_box_with_south_above_north_raises_validation_error`,
  `test_bounding_box_with_out_of_range_longitude_raises_validation_error`.
- Entity: `test_place_redraw_with_new_geometry_returns_new_instance`,
  `test_place_redraw_with_naive_datetime_raises_validation_error`,
  `test_place_assignment_raises_validation_error`.
- Integration (marker `integration`):
  `test_place_repository_save_then_get_returns_equal_place`,
  `test_place_repository_save_stores_geometry_in_wgs84`,
  `test_place_repository_get_when_missing_returns_none`.
- Migration: the upgrade/downgrade and `alembic check` tests from `write-migration`.

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/places -q
poetry run poe test-unit
poetry run poe up                   # PostGIS for integration tests
poetry run poe test-integration     # PostGIS/MinIO fixtures from Phase 1
poetry run alembic check            # no pending model changes (from Phase 1)
poetry run poe check
```

## Definition of Done

- [ ] Entity and value objects are frozen, bounded, timezone-aware, and declare
      `Implements: Entity` / `Implements: Value Object`.
- [ ] Every entity method returns a new instance and is unit tested; value objects have
      hypothesis tests.
- [ ] Port, fake, row model, mapper and adapter exist; the adapter's docstring names its
      port.
- [ ] Geometry is EPSG:4326 with a named GiST index; Shapely appears only in
      infrastructure.
- [ ] A reviewed, reversible migration exists (`write-migration`).
- [ ] Integration tests pass against real PostGIS; nothing mocks the database.
- [ ] The data dictionary lists every field with unit and meaning.
- [ ] `standards-reviewer` approved (`security-reviewer` too if personal data or
      location of people is stored).
- [ ] Conventional Commit, for example `feat(places): add place entity`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Naive datetimes.** `datetime` fields must be `AwareDatetime`; factories use
  `datetime(..., tzinfo=UTC)`. Ruff `DTZ` catches construction, Pydantic catches input.
- **`model_copy(update=...)` does not validate.** Use `model_validate` in entity methods.
- **Geometry order.** GeoJSON is longitude, latitude. `Position2D(longitude=...,
  latitude=...)` makes it explicit and satisfies mypy.
- **Automatic spatial index.** GeoAlchemy2 creates an unnamed GiST index unless
  `spatial_index=False`; the explicit `Index` keeps names stable across autogenerate.
- **`merge` versus `add`.** `save` uses `session.merge` for insert-or-update. For
  append-only records (impact claims, report revisions) the port has `add` only, and the
  adapter uses `session.add`; overwriting them is forbidden.
- **Expunge before asserting a round trip.** Without `expunge_all()` the identity map
  returns the cached row and the test proves nothing.
- **Unmarked integration tests never run.** `poe test-integration` selects
  `-m integration`; every module under `tests/integration/` sets
  `pytestmark = pytest.mark.integration`.
- **Personal data.** Reporter locations and casualty names never go into public fields;
  ask `security-reviewer` before modelling them.
