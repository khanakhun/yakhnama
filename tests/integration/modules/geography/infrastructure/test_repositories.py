"""The SQLAlchemy place repository and unit of work against real PostGIS."""

from uuid import UUID

import pytest
from geojson_pydantic import MultiPolygon, Point, Polygon
from geojson_pydantic.types import Position2D
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.geography import PlaceTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.geography.application.ports import GeographyUnitOfWorkFactory
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import PlaceNotFoundError
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceGeometry,
    PlaceName,
    ScriptCode,
)
from yakhnama.modules.geography.infrastructure.mappers import (
    centroid_to_element,
    element_to_centroid,
    geometry_to_element,
)
from yakhnama.modules.geography.infrastructure.orm import (
    WGS84_SRID,
    PlaceNameRow,
    PlaceRow,
)
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError, InvariantViolationError
from yakhnama.shared_kernel.value_objects import Coordinates

pytestmark = pytest.mark.integration

type GeographyFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyGeographyUnitOfWork]

SOURCE_ID = UUID("0192a3b4-0000-7000-8000-00000000abcd")
# Coordinates with more digits than ST_AsGeoJSON keeps by default, so the round trip
# proves the geometry is stored bit-for-bit.
POLYGON = PlaceGeometry(
    geojson=Polygon(
        type="Polygon",
        coordinates=[
            [
                Position2D(longitude=74.123456789012345, latitude=36.1),
                Position2D(longitude=74.3, latitude=36.1),
                Position2D(longitude=74.3, latitude=36.312345678901234),
                Position2D(longitude=74.123456789012345, latitude=36.1),
            ]
        ],
    )
)
MULTI_POLYGON = PlaceGeometry(
    geojson=MultiPolygon(
        type="MultiPolygon",
        coordinates=[
            [
                [
                    Position2D(longitude=74.0, latitude=36.0),
                    Position2D(longitude=74.1, latitude=36.0),
                    Position2D(longitude=74.1, latitude=36.1),
                    Position2D(longitude=74.0, latitude=36.0),
                ]
            ],
            [
                [
                    Position2D(longitude=75.0, latitude=35.0),
                    Position2D(longitude=75.1, latitude=35.0),
                    Position2D(longitude=75.1, latitude=35.1),
                    Position2D(longitude=75.0, latitude=35.0),
                ]
            ],
        ],
    )
)
POINT = PlaceGeometry(
    geojson=Point(type="Point", coordinates=Position2D(longitude=74.5, latitude=36.5))
)


def _country() -> Place:
    return PlaceTestFactory.build(level=AdminLevel.COUNTRY, geometry=POLYGON)


def _district(parent: Place, *names: PlaceName) -> Place:
    return PlaceTestFactory.build(
        level=AdminLevel.DISTRICT,
        parent_id=parent.id,
        names=names or (PlaceName(text="Test District", language="en"),),
    )


async def _store(factory: GeographyUnitOfWorkFactory, *places: Place) -> None:
    async with factory() as uow:
        for place in places:
            await uow.places.add(place)
        await uow.commit()


async def _get(factory: GeographyUnitOfWorkFactory, place_id: UUID) -> Place | None:
    async with factory() as uow:
        return await uow.places.get(place_id)


async def test_place_repository_add_then_get_returns_equal_place(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()
    district = _district(
        country,
        PlaceName(text="Test Valley", language="en", is_preferred=True),
        PlaceName(text="Test Vallēy", language="en", kind="alternative"),
        PlaceName(
            text="تست",
            language="ur",
            script=ScriptCode.ARAB,
            is_preferred=True,
            source_id=SOURCE_ID,
        ),
    )

    await _store(geography_uow_factory, country, district)
    loaded_country = await _get(geography_uow_factory, country.id)
    loaded_district = await _get(geography_uow_factory, district.id)

    assert loaded_country == country
    assert loaded_district == district


async def test_place_repository_get_by_code_returns_equal_place(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)

    async with geography_uow_factory() as uow:
        loaded = await uow.places.get_by_code(country.code)

    assert loaded == country


async def test_place_repository_get_when_missing_returns_none(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()

    async with geography_uow_factory() as uow:
        by_id = await uow.places.get(country.id)
        by_code = await uow.places.get_by_code(country.code)

    assert by_id is None
    assert by_code is None


async def test_place_repository_add_stores_geometry_and_centroid_in_wgs84(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    country = _country()

    await _store(geography_uow_factory, country)
    async with session_factory() as session:
        srids = (
            await session.execute(
                select(
                    func.ST_SRID(PlaceRow.geometry), func.ST_SRID(PlaceRow.centroid)
                ).where(PlaceRow.id == country.id)
            )
        ).one()

    assert tuple(srids) == (WGS84_SRID, WGS84_SRID)


async def test_place_repository_add_stores_folded_search_text_per_script(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    country = _country()
    district = _district(
        country,
        PlaceName(text="Test Łake Vallēy", language="en"),
        PlaceName(text="ہنزہ", language="ur", script=ScriptCode.ARAB),
    )

    await _store(geography_uow_factory, country, district)
    async with session_factory() as session:
        folded = (
            await session.execute(
                select(PlaceNameRow.text_folded)
                .where(PlaceNameRow.place_id == district.id)
                .order_by(PlaceNameRow.position)
            )
        ).scalars()

    assert list(folded) == ["test lake valley", "ہنزہ"]


async def test_place_repository_add_duplicate_code_raises_conflict_keeps_transaction(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)
    duplicate = PlaceTestFactory.build(level=AdminLevel.COUNTRY, code=country.code)
    other = _country()

    async with geography_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.places.add(duplicate)
        await uow.places.add(other)
        await uow.commit()

    assert await _get(geography_uow_factory, duplicate.id) is None
    assert await _get(geography_uow_factory, other.id) == other


async def test_place_repository_add_with_duplicate_id_raises_conflict(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)
    same_id = PlaceTestFactory.build(level=AdminLevel.COUNTRY, id=country.id)

    async with geography_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.places.add(same_id)


async def test_place_repository_add_with_unstored_parent_reraises_integrity_error(
    geography_uow_factory: GeographyFactory,
) -> None:
    orphan = _district(_country())

    async with geography_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.places.add(orphan)


async def test_place_repository_save_with_code_of_other_place_raises_conflict(
    geography_uow_factory: GeographyFactory,
) -> None:
    first = _country()
    second = _country()
    await _store(geography_uow_factory, first, second)
    # The domain never changes a code; a state built around it proves the unique
    # constraint still guards the table.
    clashing = Place.model_validate(
        {**dict(second), "code": first.code, "version": second.version + 1}
    )

    async with geography_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.places.save(clashing)

    assert await _get(geography_uow_factory, second.id) == second


async def test_place_repository_save_after_changes_returns_new_state(
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    district = _district(
        country, PlaceName(text="Test Old", language="en", is_preferred=True)
    )
    await _store(geography_uow_factory, country, district)

    async with geography_uow_factory() as uow:
        loaded = await uow.places.get(district.id)
        assert loaded is not None
        state = loaded.add_name(
            PlaceName(text="Test New", language="en", is_preferred=True),
            clock=clock,
            ids=ids,
        ).state
        state = state.set_preferred_name("en", "Test Old", clock=clock, ids=ids).state
        state = state.set_geometry(MULTI_POLYGON, clock=clock, ids=ids).state
        state = state.set_centroid(None, clock=clock, ids=ids).state
        await uow.places.save(state)
        await uow.commit()
    loaded_again = await _get(geography_uow_factory, district.id)

    assert loaded_again == state
    assert loaded_again is not None
    assert loaded_again.version == district.version + 4
    assert [name.text for name in loaded_again.names] == ["Test Old", "Test New"]


async def test_place_repository_save_twice_in_one_unit_of_work_tracks_version(
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)

    async with geography_uow_factory() as uow:
        first = country.set_geometry(POINT, clock=clock, ids=ids).state
        await uow.places.save(first)
        second = first.retire("Test retirement", clock=clock, ids=ids).state
        await uow.places.save(second)
        await uow.commit()

    assert await _get(geography_uow_factory, country.id) == second


async def test_place_repository_save_merged_place_stores_merge_target(
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    source = _district(country)
    target = _district(country)
    await _store(geography_uow_factory, country, source, target)

    async with geography_uow_factory() as uow:
        loaded = await uow.places.get(source.id)
        assert loaded is not None
        merged = loaded.merge_into(target.id, "Test merge", clock=clock, ids=ids)
        await uow.places.save(merged.record_into(uow))
        await uow.commit()

    assert await _get(geography_uow_factory, source.id) == merged.state


async def test_place_repository_save_with_stale_version_raises_conflict(
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)

    async with geography_uow_factory() as slow:
        stale = await slow.places.get(country.id)
        async with geography_uow_factory() as fast:
            fresh = await fast.places.get(country.id)
            assert fresh is not None
            await fast.places.save(
                fresh.set_geometry(POINT, clock=clock, ids=ids).state
            )
            await fast.commit()
        assert stale is not None
        with pytest.raises(ConflictError) as raised:
            await slow.places.save(
                stale.retire("Test retirement", clock=clock, ids=ids).state
            )

    assert raised.value.details["stored"] == country.version + 1


async def test_place_repository_save_unloaded_place_with_two_changes_raises_conflict(
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)
    changed = country.set_geometry(POINT, clock=clock, ids=ids).state
    changed = changed.set_centroid(None, clock=clock, ids=ids).state

    async with geography_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.places.save(changed)


async def test_place_repository_save_unloaded_place_with_one_change_succeeds(
    geography_uow_factory: GeographyFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)
    changed = country.set_geometry(POINT, clock=clock, ids=ids).state

    async with geography_uow_factory() as uow:
        await uow.places.save(changed)
        await uow.commit()

    assert await _get(geography_uow_factory, country.id) == changed


async def test_place_repository_save_when_missing_raises_not_found(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()

    async with geography_uow_factory() as uow:
        with pytest.raises(PlaceNotFoundError):
            await uow.places.save(country)


async def test_geography_unit_of_work_without_commit_discards_writes(
    geography_uow_factory: GeographyFactory,
) -> None:
    country = _country()

    async with geography_uow_factory() as uow:
        await uow.places.add(country)
        visible_inside = await uow.places.get(country.id)

    assert visible_inside == country
    assert await _get(geography_uow_factory, country.id) is None


async def test_geography_unit_of_work_commit_writes_recorded_events_to_outbox(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    country = _country()
    await _store(geography_uow_factory, country)

    async with geography_uow_factory() as uow:
        change = country.set_centroid(
            Coordinates(longitude=74.0, latitude=36.0), clock=clock, ids=ids
        )
        await uow.places.save(change.record_into(uow))
        await uow.commit()
    async with session_factory() as session:
        event_types = (
            await session.execute(select(OutboxMessage.event_type))
        ).scalars()

    assert list(event_types) == [change.events[0].event_type]


async def test_geography_unit_of_work_places_outside_block_raises_invariant_violation(
    geography_uow_factory: GeographyFactory,
) -> None:
    uow = geography_uow_factory()
    async with uow:
        pass

    with pytest.raises(InvariantViolationError):
        _ = uow.places


def test_element_to_centroid_with_non_point_raises_type_error() -> None:
    polygon = geometry_to_element(POLYGON)

    with pytest.raises(TypeError, match="Polygon"):
        element_to_centroid(polygon)


def test_centroid_round_trip_through_wkb_returns_equal_coordinates() -> None:
    centroid = Coordinates(longitude=74.123456789012345, latitude=36.312345678901234)

    restored = element_to_centroid(centroid_to_element(centroid))

    assert restored == centroid
