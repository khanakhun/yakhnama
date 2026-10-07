"""District edge snapshots, ``list_at_level`` and the boundary load on real PostGIS.

The boundary load runs with the production adapters: ``CodAbBoundaryLoader`` reads
an archive built from the committed synthetic fixture (no network) and
``ShapelySharedEdgeCalculator`` computes the edges. Every district is synthetic.
"""

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from shapely.geometry import Point, shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.boundaries import (
    SYNTHETIC_SHA256,
    attribution,
    boundary_source,
    line,
)
from tests.factories.geography import PlaceTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.geography.application.authorisation import (
    reference_data_policy,
)
from yakhnama.modules.geography.application.commands import LoadDistrictBoundaries
from yakhnama.modules.geography.application.handlers import (
    LoadDistrictBoundariesHandler,
)
from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySource,
    DistrictCentroid,
    DistrictEdge,
    DistrictEdgeSet,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import BoundaryCoverageInvalidError
from yakhnama.modules.geography.domain.value_objects import AdminLevel, PlaceName
from yakhnama.modules.geography.infrastructure.adapters.cod_ab import (
    CodAbBoundaryLoader,
    cached_archive_path,
    sha256_of,
)
from yakhnama.modules.geography.infrastructure.adapters.shared_edges import (
    ShapelySharedEdgeCalculator,
)
from yakhnama.modules.geography.infrastructure.orm import DistrictEdgeRow
from yakhnama.modules.geography.infrastructure.queries import (
    SqlAlchemyDistrictEdgeQueryService,
)
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.modules.identity.public import Role
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Coordinates

pytestmark = pytest.mark.integration

type GeographyFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyGeographyUnitOfWork]

FIXTURE: Final = (
    Path(__file__).resolve().parents[5]
    / "data"
    / "fixtures"
    / "boundaries"
    / "synthetic_admin2.geojson"
)
GAP_FIXTURE: Final = FIXTURE.with_name("synthetic_admin2_gap.geojson")
FIRST_ID: Final = EntityId("0192a3b4-0000-7000-8000-0000000000f1")
SECOND_ID: Final = EntityId("0192a3b4-0000-7000-8000-0000000000f2")
CREATED_AT: Final = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
EDGES: Final = (
    DistrictEdge(
        source_codes=("XX101", "XX102"),
        place_codes=("xx.gb.a", None),
        geometry=line((11.0, 20.0005), (11.0, 20.5), (11.00001, 21.0)),
    ),
)
ADMIN: Final = actor_with({Role.ADMIN}, user_id=SequentialIdGenerator(seed=5).new_id())


CENTROIDS: Final = (
    DistrictCentroid(
        source_code="XX101",
        place_code="xx.gb.a",
        point=Coordinates(longitude=10.123456789012345, latitude=20.5),
    ),
)


def _edge_set(
    edge_set_id: EntityId,
    created_at: datetime,
    edges: tuple[DistrictEdge, ...] = EDGES,
    centroids: tuple[DistrictCentroid, ...] = CENTROIDS,
) -> DistrictEdgeSet:
    return DistrictEdgeSet(
        id=edge_set_id,
        region_code="XX1",
        attribution=attribution(),
        sha256=SYNTHETIC_SHA256,
        fingerprint=DistrictEdgeSet.fingerprint_of(
            "XX1", SYNTHETIC_SHA256, edges, centroids
        ),
        edges=edges,
        centroids=centroids,
        created_at=created_at,
    )


async def _add(factory: GeographyFactory, edge_set: DistrictEdgeSet) -> None:
    async with factory() as uow:
        await uow.district_edge_sets.add(edge_set)
        await uow.commit()


def _gazetteer() -> tuple[Place, ...]:
    country = PlaceTestFactory.build(level=AdminLevel.COUNTRY, code="xx")
    region = PlaceTestFactory.build(
        level=AdminLevel.PROVINCE_OR_REGION, code="xx.gb", parent_id=country.id
    )
    districts = tuple(
        PlaceTestFactory.build(
            level=AdminLevel.DISTRICT,
            code=f"xx.gb.{letter}",
            parent_id=region.id,
            names=(
                PlaceName(
                    text=f"Synthetic {letter.upper()}", language="en", is_preferred=True
                ),
            ),
            geometry=None,
            centroid=None,
        )
        for letter in "abcde"
    )
    return (country, region, *districts)


async def test_edge_set_repository_add_then_get_current_returns_equal_snapshot(
    geography_uow_factory: GeographyFactory,
) -> None:
    edge_set = _edge_set(FIRST_ID, CREATED_AT)

    await _add(geography_uow_factory, edge_set)
    async with geography_uow_factory() as uow:
        loaded = await uow.district_edge_sets.get_current()

    assert loaded == edge_set


async def test_edge_set_repository_get_current_returns_newest(
    geography_uow_factory: GeographyFactory,
) -> None:
    older = _edge_set(SECOND_ID, CREATED_AT)
    newer = _edge_set(
        FIRST_ID, CREATED_AT + timedelta(minutes=1), edges=(), centroids=()
    )

    await _add(geography_uow_factory, older)
    await _add(geography_uow_factory, newer)
    async with geography_uow_factory() as uow:
        loaded = await uow.district_edge_sets.get_current()

    assert loaded is not None
    assert loaded.id == FIRST_ID


async def test_edge_set_repository_get_current_on_empty_table_returns_none(
    geography_uow_factory: GeographyFactory,
) -> None:
    async with geography_uow_factory() as uow:
        loaded = await uow.district_edge_sets.get_current()

    assert loaded is None


async def test_edge_set_repository_add_with_taken_id_raises_conflict(
    geography_uow_factory: GeographyFactory,
) -> None:
    await _add(geography_uow_factory, _edge_set(FIRST_ID, CREATED_AT))

    async with geography_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.district_edge_sets.add(_edge_set(FIRST_ID, CREATED_AT))


async def test_edge_set_rows_go_with_their_snapshot_on_rollback(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with geography_uow_factory() as uow:
        await uow.district_edge_sets.add(_edge_set(FIRST_ID, CREATED_AT))
        staged = await uow.district_edge_sets.get_current()

    async with session_factory() as session:
        stored = await session.scalar(select(func.count()).select_from(DistrictEdgeRow))
    assert staged is not None
    assert stored == 0


async def test_edge_query_service_returns_current_collection_or_none(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyDistrictEdgeQueryService(session_factory)
    empty = await service.get_current()
    await _add(geography_uow_factory, _edge_set(FIRST_ID, CREATED_AT))

    snapshot = await service.get_current()

    assert empty is None
    assert snapshot is not None
    assert snapshot.edge_set_id == FIRST_ID
    feature = snapshot.collection.features[0]
    assert feature.properties.districts == ("xx.gb.a", None)
    assert feature.geometry == EDGES[0].geometry.geojson
    assert snapshot.collection.attribution == attribution()


async def test_place_repository_list_at_level_returns_active_places_by_code(
    geography_uow_factory: GeographyFactory,
) -> None:
    places = _gazetteer()
    async with geography_uow_factory() as uow:
        for place in places:
            await uow.places.add(place)
        await uow.commit()

    async with geography_uow_factory() as uow:
        districts = await uow.places.list_at_level(AdminLevel.DISTRICT)
        countries = await uow.places.list_at_level(AdminLevel.COUNTRY)

    assert [place.code for place in districts] == [f"xx.gb.{x}" for x in "abcde"]
    assert [place.code for place in countries] == ["xx"]


async def test_edge_query_service_get_current_id_returns_newest_or_none(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyDistrictEdgeQueryService(session_factory)
    empty = await service.get_current_id()
    await _add(geography_uow_factory, _edge_set(SECOND_ID, CREATED_AT))
    await _add(
        geography_uow_factory,
        _edge_set(FIRST_ID, CREATED_AT + timedelta(minutes=1), edges=(), centroids=()),
    )

    current = await service.get_current_id()

    assert empty is None
    assert current == FIRST_ID


async def _seed_gazetteer(factory: GeographyFactory) -> None:
    async with factory() as uow:
        for place in _gazetteer():
            await uow.places.add(place)
        await uow.commit()


def _cached_source(
    tmp_path: Path, fixture: Path, links: list[tuple[str, str | None]]
) -> DistrictBoundarySource:
    staging = tmp_path / "staging.zip"
    with zipfile.ZipFile(staging, "w") as archive:
        archive.writestr("synthetic_admin2.geojson", fixture.read_bytes())
    source = boundary_source(
        links, sha256=sha256_of(staging), region_place_code="xx.gb"
    )
    staging.rename(cached_archive_path(tmp_path, source))
    return source


def _production_handler(
    factory: GeographyFactory, cache_dir: Path
) -> LoadDistrictBoundariesHandler:
    return LoadDistrictBoundariesHandler(
        uow_factory=factory,
        policy=reference_data_policy(),
        loader=CodAbBoundaryLoader(cache_dir),
        calculator=ShapelySharedEdgeCalculator(),
        clock=SteppingClock(CREATED_AT, timedelta(seconds=1)),
        ids=SequentialIdGenerator(seed=77),
    )


async def test_boundary_load_with_sliver_gap_refuses_to_publish(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _seed_gazetteer(geography_uow_factory)
    source = _cached_source(
        tmp_path,
        GAP_FIXTURE,
        [("XX101", "xx.gb.a"), ("XX102", "xx.gb.b"), ("XX103", "xx.gb.c")],
    )
    handler = _production_handler(geography_uow_factory, tmp_path)

    with pytest.raises(BoundaryCoverageInvalidError) as raised:
        await handler(LoadDistrictBoundaries(source=source, actor=ADMIN))

    assert raised.value.details["districts"] == ["XX101", "XX102"]
    snapshot = await SqlAlchemyDistrictEdgeQueryService(session_factory).get_current()
    assert snapshot is None
    async with geography_uow_factory() as uow:
        stored = await uow.places.get_by_code("xx.gb.a")
    assert stored is not None
    assert stored.geometry is None


async def test_boundary_load_with_sliver_gap_publishes_when_allowed(
    geography_uow_factory: GeographyFactory,
    tmp_path: Path,
) -> None:
    await _seed_gazetteer(geography_uow_factory)
    source = _cached_source(
        tmp_path,
        GAP_FIXTURE,
        [("XX101", "xx.gb.a"), ("XX102", "xx.gb.b"), ("XX103", "xx.gb.c")],
    )
    handler = _production_handler(geography_uow_factory, tmp_path)

    report = await handler(
        LoadDistrictBoundaries(
            source=source, actor=ADMIN, is_invalid_coverage_allowed=True
        )
    )

    assert report.is_coverage_valid is False
    assert report.invalid_coverage_districts == ("XX101", "XX102")
    assert report.is_edge_set_created is True


async def test_boundary_load_with_production_adapters_is_idempotent(
    geography_uow_factory: GeographyFactory,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    async with geography_uow_factory() as uow:
        for place in _gazetteer():
            await uow.places.add(place)
        await uow.commit()
    staging = tmp_path / "staging.zip"
    with zipfile.ZipFile(staging, "w") as archive:
        archive.writestr("synthetic_admin2.geojson", FIXTURE.read_bytes())
    source = boundary_source(
        [
            ("XX101", "xx.gb.a"),
            ("XX102", "xx.gb.b"),
            ("XX103", "xx.gb.c"),
            ("XX104", "xx.gb.d"),
            ("XX105", "xx.gb.e"),
            ("XX106", None),
        ],
        sha256=sha256_of(staging),
        region_place_code="xx.gb",
    )
    staging.rename(cached_archive_path(tmp_path, source))
    handler = LoadDistrictBoundariesHandler(
        uow_factory=geography_uow_factory,
        policy=reference_data_policy(),
        loader=CodAbBoundaryLoader(tmp_path),
        calculator=ShapelySharedEdgeCalculator(),
        clock=SteppingClock(CREATED_AT, timedelta(seconds=1)),
        ids=SequentialIdGenerator(seed=77),
    )
    command = LoadDistrictBoundaries(source=source, actor=ADMIN)

    first = await handler(command)
    second = await handler(command)

    assert first.edges == 7
    assert first.edges_with_unlinked_district == 2
    assert len(first.geometry_updated) == 5
    assert first.match.has_mismatches is True
    assert second.geometry_updated == ()
    assert len(second.geometry_unchanged) == 5
    assert second.is_edge_set_created is False
    assert second.edge_set_id == first.edge_set_id
    snapshot = await SqlAlchemyDistrictEdgeQueryService(session_factory).get_current()
    assert snapshot is not None
    assert len(snapshot.collection.features) == 7
    assert first.centroid_updated == tuple(f"xx.gb.{x}" for x in "abcde")
    assert second.centroid_unchanged == first.centroid_updated
    async with geography_uow_factory() as uow:
        stored = await uow.places.get_by_code("xx.gb.a")
    assert stored is not None
    assert stored.centroid is not None
    assert stored.geometry is not None
    footprint = shape(stored.geometry.geojson.model_dump(mode="json"))
    centroid = Point(stored.centroid.longitude, stored.centroid.latitude)
    assert footprint.contains(centroid)
