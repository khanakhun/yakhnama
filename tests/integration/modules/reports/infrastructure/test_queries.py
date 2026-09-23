"""The SQL report query service and nearby-reports finder against real PostGIS.

Every specification is checked against its own in-memory ``is_satisfied_by`` over
``ReportRecord.from_entity``, which is what the fakes do, so the SQL form and the
in-memory form cannot drift apart.

**Rounded bounding boxes** (decimals 2 unless stated): a report at longitude
73.996 lies *outside* a box starting at 74.0 but rounds to 74.00 and therefore
matches; a report at 74.0045 lies *inside* a box starting at 74.004 but rounds to
74.00 and therefore does not. Filtering on the exact point would do the opposite
in both cases and let a caller close in on a private position.
"""

import random
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportTestFactory
from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.application.specifications import (
    ReportHazardCodeSpecification,
    ReportInBoundingBoxSpecification,
    ReportObservedFromSpecification,
    ReportObservedToSpecification,
    ReportReporterSpecification,
    ReportStatusSpecification,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.triage import measure_distance_metres
from yakhnama.modules.reports.domain.value_objects import (
    GpsAccuracy,
    HazardGuess,
    ObservationPoint,
    ReportStatus,
)
from yakhnama.modules.reports.infrastructure.queries import (
    ReportSpecificationCompiler,
    SqlAlchemyNearbyReportsFinder,
    SqlAlchemyReportQueryService,
    decode_since,
)
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy, round_coordinates
from yakhnama.shared_kernel.specification import (
    FalseSpecification,
    Specification,
    TrueSpecification,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

pytestmark = pytest.mark.integration

type ReportsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyReportsUnitOfWork]

POLICY: Final = PublicCoordinatePolicy(decimals=2)
START: Final = datetime(2026, 3, 1, tzinfo=UTC)
REPORTER_A: Final = FACTORY_IDS.new_id()
REPORTER_B: Final = FACTORY_IDS.new_id()
# Metres per degree of latitude on the domain's mean-radius sphere.
METRES_PER_DEGREE: Final = 6_371_008.8 * 3.141592653589793 / 180
CENTER: Final = Coordinates(longitude=74.6, latitude=36.3)
RANDOM_SEED: Final = 20260923


def _observed(hours: int) -> DateWithPrecision:
    return DateWithPrecision(
        value=START + timedelta(hours=hours), precision=DatePrecision.HOUR
    )


def _point(longitude: float, latitude: float) -> ObservationPoint:
    return ObservationPoint(
        coordinates=Coordinates(longitude=longitude, latitude=latitude),
        accuracy=GpsAccuracy.metres(8.0),
    )


def _guess(code: str) -> HazardGuess:
    return HazardGuess(hazard_code=code, confidence=Confidence.HIGH)


async def _store(factory: ReportsFactory, reports: list[Report]) -> list[Report]:
    async with factory() as uow:
        for report in reports:
            await uow.reports.add(report)
        await uow.commit()
    return reports


def _newest_first(reports: list[Report]) -> list[EntityId]:
    ordered = sorted(reports, key=lambda report: (report.created_at, report.id))
    return [report.id for report in reversed(ordered)]


def _matching(
    reports: list[Report], specification: Specification[ReportRecord]
) -> list[EntityId]:
    return _newest_first(
        [
            report
            for report in reports
            if specification.is_satisfied_by(ReportRecord.from_entity(report))
        ]
    )


@pytest.fixture
async def stored(reports_uow_factory: ReportsFactory) -> list[Report]:
    """Store five varied reports; the last two share a creation instant."""
    created = [START + timedelta(days=index) for index in range(4)]
    created.append(created[-1])
    reports = [
        ReportTestFactory.build(
            created_at=created[0],
            reporter_id=REPORTER_A,
            observed_at=_observed(1),
            observation=_point(74.60, 36.30),
            hazard_guess=_guess("glof"),
        ),
        ReportTestFactory.build(
            created_at=created[1],
            reporter_id=REPORTER_B,
            observed_at=_observed(20),
            observation=_point(74.9, 36.5),
            status=ReportStatus.WITHDRAWN,
            withdrawal_reason="Test withdrawal reason",
        ),
        ReportTestFactory.build(
            created_at=created[2],
            reporter_id=REPORTER_A,
            observed_at=_observed(40),
            observation=_point(75.2, 35.9),
            hazard_guess=_guess("landslide"),
        ),
        ReportTestFactory.build(
            created_at=created[3],
            reporter_id=REPORTER_B,
            observed_at=_observed(60),
            observation=_point(74.61, 36.31),
            hazard_guess=_guess("glof"),
            status=ReportStatus.DRAFT,
            submitted_at=None,
        ),
        ReportTestFactory.build(
            created_at=created[4],
            reporter_id=REPORTER_A,
            observed_at=_observed(80),
            observation=_point(74.2, 36.1),
        ),
    ]
    return await _store(reports_uow_factory, reports)


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    policy: PublicCoordinatePolicy = POLICY,
) -> SqlAlchemyReportQueryService:
    return SqlAlchemyReportQueryService(session_factory, policy)


class _UnknownSpecification(Specification[ReportRecord]):
    """A leaf the SQL compiler has never heard of.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: ReportRecord) -> bool:
        """Accept everything; only its type matters here.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True


# --------------------------------------------------------------------------- #
# get_report                                                                  #
# --------------------------------------------------------------------------- #


async def test_report_query_service_get_report_returns_exact_record(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Report]
) -> None:
    service = _service(session_factory)

    record = await service.get_report(stored[0].id)

    assert record == ReportRecord.from_entity(stored[0])


async def test_report_query_service_get_unknown_report_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _service(session_factory)

    record = await service.get_report(FACTORY_IDS.new_id())

    assert record is None


# --------------------------------------------------------------------------- #
# list_reports                                                                #
# --------------------------------------------------------------------------- #


async def test_report_query_service_list_pages_newest_first_with_cursor(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Report]
) -> None:
    service = _service(session_factory)

    first = await service.list_reports(TrueSpecification(), PageRequest(limit=2))
    second = await service.list_reports(
        TrueSpecification(), PageRequest(limit=2, cursor=first.next_cursor)
    )
    third = await service.list_reports(
        TrueSpecification(), PageRequest(limit=2, cursor=second.next_cursor)
    )

    listed = [item.id for page in (first, second, third) for item in page.items]
    assert listed == _newest_first(stored)
    assert third.next_cursor is None


async def test_report_query_service_list_returns_rounded_point_without_accuracy(
    session_factory: async_sessionmaker[AsyncSession], stored: list[Report]
) -> None:
    service = _service(session_factory)

    page = await service.list_reports(TrueSpecification(), PageRequest())

    by_id = {report.id: report for report in stored}
    for record in page.items:
        exact = by_id[record.id].observation.coordinates
        assert record.observation.coordinates == round_coordinates(exact, 2)
        assert record.observation.accuracy is None
        # Everything except the position matches the stored report.
        expected = ReportRecord.from_entity(by_id[record.id])
        assert record.model_dump(exclude={"observation"}) == expected.model_dump(
            exclude={"observation"}
        )


@pytest.mark.parametrize(
    "specification",
    [
        ReportStatusSpecification(ReportStatus.SUBMITTED),
        ReportHazardCodeSpecification("glof"),
        ReportReporterSpecification(REPORTER_A),
        ReportObservedFromSpecification(START + timedelta(hours=40)),
        ReportObservedToSpecification(START + timedelta(hours=40)),
        ReportInBoundingBoxSpecification(
            BoundingBox(
                min_longitude=74.5,
                min_latitude=36.2,
                max_longitude=75.0,
                max_latitude=36.6,
            ),
            POLICY,
        ),
        ~ReportHazardCodeSpecification("glof"),
        ReportStatusSpecification(ReportStatus.SUBMITTED)
        & ReportReporterSpecification(REPORTER_A)
        & ~ReportHazardCodeSpecification("landslide"),
        ReportHazardCodeSpecification("landslide")
        | ReportStatusSpecification(ReportStatus.WITHDRAWN),
        ~(ReportHazardCodeSpecification("glof") | FalseSpecification()),
    ],
    ids=[
        "status",
        "hazard",
        "reporter",
        "observed-from",
        "observed-to",
        "bbox",
        "not-hazard-keeps-no-guess",
        "and-not",
        "or",
        "not-or-false",
    ],
)
async def test_report_query_service_list_specification_matches_in_memory(
    session_factory: async_sessionmaker[AsyncSession],
    stored: list[Report],
    specification: Specification[ReportRecord],
) -> None:
    service = _service(session_factory)

    page = await service.list_reports(specification, PageRequest())

    assert [item.id for item in page.items] == _matching(stored, specification)


@pytest.mark.parametrize(
    ("longitude", "min_longitude", "is_expected"),
    [(73.996, 74.0, True), (74.0045, 74.004, False)],
    ids=["outside-exactly-inside-rounded", "inside-exactly-outside-rounded"],
)
async def test_report_query_service_bbox_tests_rounded_not_exact_position(
    session_factory: async_sessionmaker[AsyncSession],
    reports_uow_factory: ReportsFactory,
    longitude: float,
    min_longitude: float,
    is_expected: bool,  # noqa: FBT001  # reason: pytest parametrised argument
) -> None:
    report = ReportTestFactory.build(observation=_point(longitude, 36.3))
    await _store(reports_uow_factory, [report])
    bbox = BoundingBox(
        min_longitude=min_longitude,
        min_latitude=36.0,
        max_longitude=75.0,
        max_latitude=37.0,
    )
    specification = ReportInBoundingBoxSpecification(bbox, POLICY)
    service = _service(session_factory)

    page = await service.list_reports(specification, PageRequest())

    assert bbox.contains(report.observation.coordinates) is not is_expected
    assert specification.is_satisfied_by(ReportRecord.from_entity(report)) is (
        is_expected
    )
    assert [item.id for item in page.items] == ([report.id] if is_expected else [])


@pytest.mark.parametrize("decimals", [0, 2, 3, 6])
async def test_report_query_service_sql_rounding_equals_python_rounding(
    session_factory: async_sessionmaker[AsyncSession],
    reports_uow_factory: ReportsFactory,
    decimals: int,
) -> None:
    generator = random.Random(RANDOM_SEED + decimals)  # noqa: S311  # reason: test data, not cryptography
    # Ties whose binary value lies below the decimal tie (36.125 is 36.12499...
    # in binary) and negative ties, then random full-precision points.
    tricky = [
        (36.125, 36.125),
        (74.005, 35.995),
        (-74.005, -0.125),
        (0.5, -0.5),
        (1e-05, 179.9999995 - 179.0),
    ]
    points = tricky + [
        (generator.uniform(-180, 180), generator.uniform(-90, 90)) for _ in range(60)
    ]
    reports = [
        ReportTestFactory.build(observation=_point(longitude, latitude))
        for longitude, latitude in points
    ]
    await _store(reports_uow_factory, reports)
    service = _service(session_factory, PublicCoordinatePolicy(decimals=decimals))

    page = await service.list_reports(TrueSpecification(), PageRequest(limit=200))

    by_id = {report.id: report.observation.coordinates for report in reports}
    mismatched = [
        (by_id[record.id], record.observation.coordinates)
        for record in page.items
        if record.observation.coordinates
        != round_coordinates(by_id[record.id], decimals)
    ]
    assert len(page.items) == len(reports)
    assert mismatched == []


def test_report_specification_compiler_unknown_leaf_raises_type_error() -> None:
    compiler = ReportSpecificationCompiler()

    with pytest.raises(TypeError, match="_UnknownSpecification"):
        _UnknownSpecification().accept(compiler)


@pytest.mark.parametrize("sort_key", ["not a date", "2026-01-01T00:00:00"])
def test_report_decode_since_invalid_or_naive_raises_validation_error(
    sort_key: str,
) -> None:
    with pytest.raises(ValidationError):
        decode_since(sort_key)


# --------------------------------------------------------------------------- #
# find_nearby                                                                 #
# --------------------------------------------------------------------------- #


def _north_of_center(metres: float) -> ObservationPoint:
    # Due north, the great-circle distance is exactly R * delta-latitude.
    return _point(CENTER.longitude, CENTER.latitude + metres / METRES_PER_DEGREE)


def _nearby_query(
    exclude: EntityId, *, limit: int = 10, window: timedelta = timedelta(hours=6)
) -> FindNearbyReports:
    return FindNearbyReports(
        center=CENTER,
        observed_at=START,
        exclude_report_id=exclude,
        radius_metres=2_000.0,
        window=window,
        limit=limit,
    )


@pytest.fixture
async def nearby(reports_uow_factory: ReportsFactory) -> dict[str, Report]:
    """Store reports around ``CENTER`` at known distances, times and statuses."""
    reports = {
        "self": ReportTestFactory.build(
            observation=_point(CENTER.longitude, CENTER.latitude),
            observed_at=_observed(0),
        ),
        "near": ReportTestFactory.build(
            observation=_north_of_center(500), observed_at=_observed(-2)
        ),
        "middle": ReportTestFactory.build(
            observation=_north_of_center(1_500), observed_at=_observed(6)
        ),
        # Inside 2 km on the sphere; the WGS84 spheroid would say the same.
        "edge-in": ReportTestFactory.build(
            observation=_north_of_center(1_996), observed_at=_observed(1)
        ),
        # Outside 2 km on the sphere, but inside on the WGS84 spheroid (a degree
        # of latitude is shorter there at 36° N), so a spheroid query would
        # return a candidate the domain's haversine rule rejects.
        "edge-out": ReportTestFactory.build(
            observation=_north_of_center(2_004), observed_at=_observed(1)
        ),
        "far": ReportTestFactory.build(
            observation=_north_of_center(3_000), observed_at=_observed(1)
        ),
        "too-late": ReportTestFactory.build(
            observation=_north_of_center(200), observed_at=_observed(7)
        ),
        "withdrawn": ReportTestFactory.build(
            observation=_north_of_center(100),
            observed_at=_observed(0),
            status=ReportStatus.WITHDRAWN,
            withdrawal_reason="Test withdrawal reason",
        ),
        "draft": ReportTestFactory.build(
            observation=_north_of_center(100),
            observed_at=_observed(0),
            status=ReportStatus.DRAFT,
            submitted_at=None,
        ),
    }
    await _store(reports_uow_factory, list(reports.values()))
    return reports


async def test_nearby_finder_returns_current_reports_in_radius_and_window_nearest_first(
    session_factory: async_sessionmaker[AsyncSession], nearby: dict[str, Report]
) -> None:
    finder = SqlAlchemyNearbyReportsFinder(session_factory)

    found = await finder.find_nearby(_nearby_query(nearby["self"].id))

    assert [item.report_id for item in found] == [
        nearby[name].id for name in ("near", "middle", "edge-in")
    ]
    assert found[0].coordinates == nearby["near"].observation.coordinates
    assert found[0].observed_at == nearby["near"].observed_at


async def test_nearby_finder_radius_agrees_with_domain_haversine(
    session_factory: async_sessionmaker[AsyncSession], nearby: dict[str, Report]
) -> None:
    finder = SqlAlchemyNearbyReportsFinder(session_factory)

    found = await finder.find_nearby(_nearby_query(nearby["self"].id))

    edge_out = nearby["edge-out"].observation.coordinates
    assert measure_distance_metres(CENTER, edge_out) > 2_000.0
    assert nearby["edge-out"].id not in {item.report_id for item in found}
    assert all(
        measure_distance_metres(CENTER, item.coordinates) <= 2_000.0 for item in found
    )


async def test_nearby_finder_respects_limit_keeping_the_nearest(
    session_factory: async_sessionmaker[AsyncSession], nearby: dict[str, Report]
) -> None:
    finder = SqlAlchemyNearbyReportsFinder(session_factory)

    found = await finder.find_nearby(_nearby_query(nearby["self"].id, limit=1))

    assert [item.report_id for item in found] == [nearby["near"].id]


async def test_nearby_finder_narrow_window_drops_reports_outside_it(
    session_factory: async_sessionmaker[AsyncSession], nearby: dict[str, Report]
) -> None:
    finder = SqlAlchemyNearbyReportsFinder(session_factory)

    found = await finder.find_nearby(
        _nearby_query(nearby["self"].id, window=timedelta(hours=1))
    )

    assert [item.report_id for item in found] == [nearby["edge-in"].id]
