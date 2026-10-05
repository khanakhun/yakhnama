"""Unit tests for ``LoadDistrictBoundariesHandler`` with in-memory fakes only.

The gazetteer is synthetic (``xx`` codes), the boundaries are a grid of synthetic
squares (``tests/factories/boundaries.py``) and the edges are prepared by a static
calculator, so these tests cover matching, footprints, snapshots and the report,
not geometry.
"""

from datetime import timedelta

import pytest

from tests.factories.boundaries import (
    boundary_source,
    grid_boundary_set,
    line,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.geography import (
    InMemoryGeographyUnitOfWork,
    StaticBoundaryLoader,
    StaticSharedEdgeCalculator,
)
from tests.fakes.identity import DenyAllPolicy, actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.geography.application.support import (
    NOW,
    entry,
    reference_file,
    retired,
    stored_places,
)
from yakhnama.modules.geography.application.authorisation import (
    AuthorisationPolicy,
    reference_data_policy,
)
from yakhnama.modules.geography.application.commands import LoadDistrictBoundaries
from yakhnama.modules.geography.application.dto import (
    DistrictEdgeFeatureCollection,
    SharedEdgeComputation,
)
from yakhnama.modules.geography.application.handlers import (
    LoadDistrictBoundariesHandler,
)
from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySet,
    DistrictBoundarySource,
    SharedEdge,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import BoundarySourceError
from yakhnama.modules.geography.domain.events import (
    PlaceCentroidChanged,
    PlaceGeometryChanged,
)
from yakhnama.modules.identity.public import Role
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.value_objects import Coordinates

ADMIN = actor_with({Role.ADMIN}, user_id=SequentialIdGenerator(seed=99).new_id())
CITIZEN = actor_with(user_id=SequentialIdGenerator(seed=98).new_id())

EDGE_AB = SharedEdge(
    source_codes=("XX101", "XX102"), geometry=line((11.0, 20.0), (11.0, 21.0))
)
EDGE_BC = SharedEdge(
    source_codes=("XX102", "XX103"), geometry=line((12.0, 20.0), (12.0, 21.0))
)


def _gazetteer() -> tuple[Place, ...]:
    # Country xx, region xx.gb with districts a and b, and a district c in another
    # region, so ancestry decides which districts belong to the region.
    return stored_places(
        reference_file(
            entry("xx", "country"),
            entry("xx.gb", "province_or_region", parent_code="xx"),
            entry("xx.other", "province_or_region", parent_code="xx"),
            entry(
                "xx.gb.a",
                "district",
                parent_code="xx.gb",
                names=[
                    {"text": "Synthetic XX101", "language": "en", "is_preferred": True}
                ],
            ),
            entry("xx.gb.b", "district", parent_code="xx.gb"),
            entry("xx.other.c", "district", parent_code="xx.other"),
        )
    )


def _source() -> DistrictBoundarySource:
    return boundary_source(
        [("XX101", "xx.gb.a"), ("XX102", "xx.gb.b"), ("XX103", None)],
        region_place_code="xx.gb",
    )


def _boundary_set() -> DistrictBoundarySet:
    return grid_boundary_set(columns=3, rows=1)


def _handler(
    uow: InMemoryGeographyUnitOfWork,
    *,
    edges: tuple[SharedEdge, ...] = (EDGE_AB, EDGE_BC),
    policy: AuthorisationPolicy | None = None,
    loader: StaticBoundaryLoader | None = None,
    clock: FrozenClock | None = None,
) -> LoadDistrictBoundariesHandler:
    return LoadDistrictBoundariesHandler(
        uow_factory=InMemoryUnitOfWorkFactory(uow),
        policy=policy or reference_data_policy(),
        loader=loader or StaticBoundaryLoader(_boundary_set()),
        calculator=StaticSharedEdgeCalculator(
            SharedEdgeComputation(edges=edges, is_coverage_valid=True, dropped_parts=1)
        ),
        clock=clock or FrozenClock(NOW),
        ids=SequentialIdGenerator(seed=41),
    )


def _command(*, dry_run: bool = False) -> LoadDistrictBoundaries:
    return LoadDistrictBoundaries(source=_source(), actor=ADMIN, dry_run=dry_run)


async def test_load_with_refused_actor_raises_before_loading() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    loader = StaticBoundaryLoader(_boundary_set())
    handler = _handler(uow, policy=DenyAllPolicy(), loader=loader)

    with pytest.raises(PermissionDeniedError):
        await handler(LoadDistrictBoundaries(source=_source(), actor=CITIZEN))

    assert loader.calls == []


async def test_load_links_districts_sets_footprints_and_publishes_edges() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    boundary_set = _boundary_set()

    report = await _handler(uow)(_command())

    stored = uow.places.committed_by_code()
    assert stored["xx.gb.a"].geometry == boundary_set.districts[0].geometry
    assert stored["xx.gb.b"].geometry == boundary_set.districts[1].geometry
    assert stored["xx.other.c"].geometry is None
    assert report.geometry_updated == ("xx.gb.a", "xx.gb.b")
    assert [link.source_code for link in report.match.unlinked] == ["XX103"]
    assert report.match.places_without_boundary == ()
    assert report.edges == 2
    assert report.edges_with_unlinked_district == 1
    assert report.is_edge_set_created is True
    assert report.dropped_parts == 1
    current = uow.district_edge_sets.committed[-1]
    assert [edge.place_codes for edge in current.edges] == [
        ("xx.gb.a", "xx.gb.b"),
        ("xx.gb.b", None),
    ]
    assert current.attribution == boundary_set.attribution
    assert report.edge_set_id == current.id
    assert report.positions == 4
    assert report.payload_bytes == len(
        DistrictEdgeFeatureCollection.from_edge_set(current).model_dump_json().encode()
    )
    events = [
        event
        for event in uow.committed_events
        if isinstance(event, PlaceGeometryChanged)
    ]
    assert len(events) == 2


async def test_load_reports_name_difference_against_gazetteer() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())

    report = await _handler(uow)(_command())

    assert [item.source_code for item in report.match.name_differences] == ["XX102"]


async def test_load_twice_changes_nothing_the_second_time() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    clock = FrozenClock(NOW)
    await _handler(uow, clock=clock)(_command())
    clock.advance(timedelta(hours=1))

    report = await _handler(uow, clock=clock)(_command())

    assert report.geometry_updated == ()
    assert report.geometry_unchanged == ("xx.gb.a", "xx.gb.b")
    assert report.is_edge_set_created is False
    assert len(uow.district_edge_sets.committed) == 1


async def test_load_with_changed_edges_adds_a_new_current_snapshot() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    clock = FrozenClock(NOW)
    await _handler(uow, clock=clock)(_command())
    clock.advance(timedelta(hours=1))

    report = await _handler(uow, edges=(EDGE_AB,), clock=clock)(_command())

    assert report.is_edge_set_created is True
    assert len(uow.district_edge_sets.committed) == 2
    assert uow.district_edge_sets.committed[-1].id == report.edge_set_id
    assert report.edges == 1


async def test_load_dry_run_rolls_everything_back() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())

    report = await _handler(uow)(_command(dry_run=True))

    assert report.dry_run is True
    assert report.is_edge_set_created is True
    assert uow.district_edge_sets.committed == []
    assert all(place.geometry is None for place in uow.places.committed.values())
    assert uow.committed is False


async def test_load_with_retired_and_missing_places_reports_them() -> None:
    places = {place.code: place for place in _gazetteer()}
    places["xx.gb.a"] = retired(places["xx.gb.a"])
    uow = InMemoryGeographyUnitOfWork(
        tuple(place for code, place in places.items() if code != "xx.gb.b")
    )

    report = await _handler(uow)(_command())

    assert report.match.missing_places == ("xx.gb.a", "xx.gb.b")
    assert report.match.linked == ()
    assert report.geometry_updated == ()
    assert report.edges_with_unlinked_district == 2


async def test_load_with_unlinked_gazetteer_district_reports_it() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    source = boundary_source(
        [("XX101", "xx.gb.a"), ("XX102", None), ("XX103", None)],
        region_place_code="xx.gb",
    )

    report = await _handler(uow)(LoadDistrictBoundaries(source=source, actor=ADMIN))

    assert report.match.places_without_boundary == ("xx.gb.b",)


async def test_load_with_unknown_region_reports_no_region_districts() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    source = boundary_source(
        [("XX101", "xx.gb.a"), ("XX102", "xx.gb.b"), ("XX103", None)],
        region_place_code="xx.nowhere",
    )

    report = await _handler(uow)(LoadDistrictBoundaries(source=source, actor=ADMIN))

    assert report.match.places_without_boundary == ()
    assert len(report.match.linked) == 2


async def test_load_with_failing_loader_raises_and_opens_no_transaction() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    error = BoundarySourceError("checksum", details={"reason": "checksum_mismatch"})
    handler = _handler(uow, loader=StaticBoundaryLoader(error=error))

    with pytest.raises(BoundarySourceError):
        await handler(_command())

    assert uow.commit_count == 0
    assert uow.rollback_count == 0


# --------------------------------------------------------------------------- #
# Centroids, open question Q239                                              #
# --------------------------------------------------------------------------- #

INSIDE_A = Coordinates(longitude=10.5, latitude=20.5)
ELSEWHERE = Coordinates(longitude=10.25, latitude=20.75)


def _moved_point(boundary_set: DistrictBoundarySet) -> DistrictBoundarySet:
    first, *rest = boundary_set.districts
    moved = first.model_copy(update={"representative_point": ELSEWHERE})
    return boundary_set.model_copy(update={"districts": (moved, *rest)})


def _with_centroid(place: Place, point: Coordinates) -> Place:
    return place.set_centroid(
        point, clock=FrozenClock(NOW), ids=SequentialIdGenerator(seed=61)
    ).state


async def test_load_sets_missing_centroids_to_representative_points() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())

    report = await _handler(uow)(_command())

    stored = uow.places.committed_by_code()
    assert stored["xx.gb.a"].centroid == INSIDE_A
    assert stored["xx.gb.b"].centroid == Coordinates(longitude=11.5, latitude=20.5)
    assert report.centroid_updated == ("xx.gb.a", "xx.gb.b")
    assert report.centroid_kept == ()
    recorded = uow.district_edge_sets.committed[-1]
    assert recorded.centroid_set_for("xx.gb.a") == INSIDE_A
    events = [e for e in uow.committed_events if isinstance(e, PlaceCentroidChanged)]
    assert len(events) == 2


async def test_load_twice_leaves_its_own_centroids_unchanged() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    clock = FrozenClock(NOW)
    await _handler(uow, clock=clock)(_command())
    clock.advance(timedelta(hours=1))

    report = await _handler(uow, clock=clock)(_command())

    assert report.centroid_unchanged == ("xx.gb.a", "xx.gb.b")
    assert report.centroid_updated == ()
    assert report.is_edge_set_created is False


async def test_load_keeps_a_centroid_set_by_another_source() -> None:
    places = {place.code: place for place in _gazetteer()}
    places["xx.gb.a"] = _with_centroid(places["xx.gb.a"], ELSEWHERE)
    uow = InMemoryGeographyUnitOfWork(tuple(places.values()))

    report = await _handler(uow)(_command())

    assert uow.places.committed_by_code()["xx.gb.a"].centroid == ELSEWHERE
    assert report.centroid_kept == ("xx.gb.a",)
    assert uow.district_edge_sets.committed[-1].centroid_set_for("xx.gb.a") is None


async def test_load_replaces_its_own_centroid_when_the_data_moves_it() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    clock = FrozenClock(NOW)
    await _handler(uow, clock=clock)(_command())
    clock.advance(timedelta(hours=1))
    loader = StaticBoundaryLoader(_moved_point(_boundary_set()))

    report = await _handler(uow, clock=clock, loader=loader)(_command())

    assert uow.places.committed_by_code()["xx.gb.a"].centroid == ELSEWHERE
    assert report.centroid_updated == ("xx.gb.a",)
    assert report.is_edge_set_created is True


async def test_load_keeps_a_centroid_edited_after_its_last_load() -> None:
    uow = InMemoryGeographyUnitOfWork(_gazetteer())
    clock = FrozenClock(NOW)
    await _handler(uow, clock=clock)(_command())
    edited = _with_centroid(uow.places.committed_by_code()["xx.gb.a"], ELSEWHERE)
    uow.places.committed[edited.id] = edited
    clock.advance(timedelta(hours=1))

    report = await _handler(uow, clock=clock)(_command())

    assert uow.places.committed_by_code()["xx.gb.a"].centroid == ELSEWHERE
    assert report.centroid_kept == ("xx.gb.a",)
