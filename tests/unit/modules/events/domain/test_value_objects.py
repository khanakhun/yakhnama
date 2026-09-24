"""Unit tests for ``yakhnama.modules.events.domain.value_objects``."""

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from geojson_pydantic import MultiPolygon, Point, Polygon
from geojson_pydantic.types import Position2D, Position3D
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.events import EventRelationTestFactory
from tests.unit.modules.events.domain.samples import (
    MODERATOR_ID,
    at,
    ids,
    ring,
    square,
    two_squares,
)
from yakhnama.modules.events.domain import value_objects
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventGeometry,
    EventPeriod,
    EventRelation,
    EventStatus,
    RelationKind,
    ReportLink,
    ReportUnlink,
    latest_instant_of,
    mean_coordinates,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

P = DatePrecision
LONGITUDES = st.floats(min_value=72.0, max_value=78.0, allow_nan=False)
LATITUDES = st.floats(min_value=34.0, max_value=37.5, allow_nan=False)
POINTS = st.builds(Coordinates, longitude=LONGITUDES, latitude=LATITUDES)
INSTANTS = st.datetimes(
    min_value=datetime(1900, 1, 1),  # noqa: DTZ001  # reason: hypothesis bounds must be naive; timezones= adds UTC
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001  # reason: hypothesis bounds must be naive; timezones= adds UTC
    timezones=st.just(UTC),
)
MOMENTS = st.builds(
    DateWithPrecision, value=INSTANTS, precision=st.sampled_from(list(DatePrecision))
)


# --------------------------------------------------------------------------- #
# EventStatus                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "is_final"),
    [
        (EventStatus.DRAFT, False),
        (EventStatus.PUBLISHED, False),
        (EventStatus.RETRACTED, True),
        (EventStatus.MERGED, True),
    ],
)
def test_event_status_is_final_only_for_retracted_and_merged(
    status: EventStatus, *, is_final: bool
) -> None:
    final = status.is_final

    assert final == is_final


# --------------------------------------------------------------------------- #
# EventGeometry                                                               #
# --------------------------------------------------------------------------- #


def test_event_geometry_point_centroid_and_box_are_the_point() -> None:
    point = Coordinates(longitude=74.5, latitude=36.3)

    geometry = EventGeometry.from_coordinates(point)

    assert geometry.geometry_type == "Point"
    assert geometry.centroid() == point
    box = geometry.bounding_box()
    assert (box.min_longitude, box.max_latitude) == (74.5, 36.3)


def test_event_geometry_square_centroid_ignores_closing_vertex() -> None:
    geometry = EventGeometry(geojson=square(74.0, 36.0, 1.0))

    centroid = geometry.centroid()

    assert geometry.geometry_type == "Polygon"
    assert centroid == Coordinates(longitude=74.5, latitude=36.5)


def test_event_geometry_polygon_hole_does_not_move_centroid() -> None:
    outer = square(74.0, 36.0, 1.0).coordinates[0]
    hole = ring((74.1, 36.1), (74.2, 36.1), (74.2, 36.2))
    geometry = EventGeometry(geojson=Polygon(type="Polygon", coordinates=[outer, hole]))

    centroid = geometry.centroid()

    assert centroid == Coordinates(longitude=74.5, latitude=36.5)


def test_event_geometry_multipolygon_centroid_is_mean_of_all_exteriors() -> None:
    geometry = EventGeometry(geojson=two_squares())

    centroid = geometry.centroid()
    box = geometry.bounding_box()

    assert geometry.geometry_type == "MultiPolygon"
    assert centroid == Coordinates(longitude=75.5, latitude=36.5)
    assert (box.min_longitude, box.max_longitude) == (74.0, 77.0)


def test_event_geometry_multipolygon_empty_member_skipped_in_centroid() -> None:
    geojson = MultiPolygon(
        type="MultiPolygon", coordinates=[[], square(74.0, 36.0, 1.0).coordinates]
    )

    centroid = EventGeometry(geojson=geojson).centroid()

    assert centroid == Coordinates(longitude=74.5, latitude=36.5)


def test_event_geometry_input_copied_so_later_mutation_has_no_effect() -> None:
    polygon = square(74.0, 36.0, 1.0)
    geometry = EventGeometry(geojson=polygon)

    polygon.coordinates[0][0] = Position2D(longitude=0.0, latitude=0.0)

    assert geometry.bounding_box().min_longitude == 74.0


def test_event_geometry_from_mapping_builds() -> None:
    geometry = EventGeometry.model_validate(
        {"geojson": {"type": "Point", "coordinates": [74.0, 36.0]}}
    )

    assert geometry.geometry_type == "Point"


@pytest.mark.parametrize(
    "geojson",
    [
        Polygon(type="Polygon", coordinates=[]),
        MultiPolygon(type="MultiPolygon", coordinates=[]),
    ],
)
def test_event_geometry_without_positions_rejected(
    geojson: Polygon | MultiPolygon,
) -> None:
    with pytest.raises(PydanticValidationError, match="at least one position"):
        EventGeometry(geojson=geojson)


def test_event_geometry_three_dimensional_point_rejected() -> None:
    point = Point(
        type="Point",
        coordinates=Position3D(longitude=74.0, latitude=36.0, altitude=2500.0),
    )

    with pytest.raises(PydanticValidationError, match="two-dimensional"):
        EventGeometry(geojson=point)


@pytest.mark.parametrize(
    ("longitude", "latitude", "match"),
    [
        (181.0, 36.0, "longitude"),
        (math.inf, 36.0, "longitude"),
        (74.0, -90.5, "latitude"),
        (74.0, math.nan, "latitude"),
    ],
)
def test_event_geometry_position_outside_wgs84_rejected(
    longitude: float, latitude: float, match: str
) -> None:
    point = Point(
        type="Point",
        coordinates=Position2D(longitude=longitude, latitude=latitude),
    )

    with pytest.raises(PydanticValidationError, match=match):
        EventGeometry(geojson=point)


def test_event_geometry_too_many_positions_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(value_objects, "_MAX_POSITIONS", 4)

    with pytest.raises(PydanticValidationError, match="more than 4 positions"):
        EventGeometry(geojson=square(74.0, 36.0, 1.0))


@given(corners=st.lists(POINTS, min_size=3, max_size=12))
def test_event_geometry_polygon_centroid_always_inside_bounding_box(
    corners: list[Coordinates],
) -> None:
    geojson = Polygon(
        type="Polygon",
        coordinates=[ring(*((point.longitude, point.latitude) for point in corners))],
    )
    geometry = EventGeometry(geojson=geojson)

    centroid = geometry.centroid()

    assert geometry.bounding_box().contains(centroid)


# --------------------------------------------------------------------------- #
# mean_coordinates                                                            #
# --------------------------------------------------------------------------- #


def test_mean_coordinates_two_points_returns_midpoint() -> None:
    points = [
        Coordinates(longitude=74.0, latitude=36.0),
        Coordinates(longitude=75.0, latitude=37.0),
    ]

    mean = mean_coordinates(points)

    assert mean == Coordinates(longitude=74.5, latitude=36.5)


def test_mean_coordinates_empty_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        mean_coordinates([])


@given(points=st.lists(POINTS, min_size=1, max_size=30))
def test_mean_coordinates_any_points_inside_their_bounding_box(
    points: list[Coordinates],
) -> None:
    mean = mean_coordinates(points)

    assert min(p.longitude for p in points) <= mean.longitude
    assert mean.longitude <= max(p.longitude for p in points)
    assert min(p.latitude for p in points) <= mean.latitude
    assert mean.latitude <= max(p.latitude for p in points)


@given(point=POINTS, count=st.integers(min_value=1, max_value=50))
def test_mean_coordinates_repeated_point_returns_it_exactly(
    point: Coordinates, count: int
) -> None:
    mean = mean_coordinates([point] * count)

    assert mean == point


# --------------------------------------------------------------------------- #
# EventPeriod and latest_instant_of                                           #
# --------------------------------------------------------------------------- #


def test_event_period_without_end_builds() -> None:
    period = EventPeriod(started_at=at(2022, 8, 14))

    assert period.ended_at is None


def test_event_period_end_before_start_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="ended_at"):
        EventPeriod(started_at=at(2022, 8, 14), ended_at=at(2022, 8, 13))


def test_event_period_end_same_month_coarser_than_start_accepted() -> None:
    period = EventPeriod(started_at=at(2022, 8, 14), ended_at=at(2022, 8, 1, P.MONTH))

    assert period.latest_instant() == datetime(
        2022, 8, 31, 23, 59, 59, 999999, tzinfo=UTC
    )


def test_event_period_end_earlier_hour_same_day_accepted() -> None:
    period = EventPeriod(
        started_at=at(2022, 8, 14, P.DAY, hour=10),
        ended_at=at(2022, 8, 14, P.EXACT, hour=8),
    )

    assert period.earliest_instant() == datetime(2022, 8, 14, tzinfo=UTC)


def test_event_period_end_previous_month_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        EventPeriod(started_at=at(2022, 8, 14), ended_at=at(2022, 7, 1, P.MONTH))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at(2022, 8, 14, P.EXACT, hour=5), datetime(2022, 8, 14, 5, tzinfo=UTC)),
        (
            at(2022, 8, 14, P.HOUR, hour=5),
            datetime(2022, 8, 14, 5, 59, 59, 999999, tzinfo=UTC),
        ),
        (at(2022, 8, 14), datetime(2022, 8, 14, 23, 59, 59, 999999, tzinfo=UTC)),
        (
            at(2022, 12, 5, P.MONTH),
            datetime(2022, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
        ),
        (
            at(2022, 7, 5, P.SEASON),
            datetime(2022, 8, 31, 23, 59, 59, 999999, tzinfo=UTC),
        ),
        (
            at(2022, 12, 5, P.SEASON),
            datetime(2023, 2, 28, 23, 59, 59, 999999, tzinfo=UTC),
        ),
        (
            at(2022, 3, 5, P.YEAR),
            datetime(2022, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
        ),
    ],
)
def test_latest_instant_of_each_precision_returns_end_of_period(
    moment: DateWithPrecision, expected: datetime
) -> None:
    latest = latest_instant_of(moment)

    assert latest == expected


def test_latest_instant_of_year_9999_returns_datetime_max() -> None:
    moment = at(9999, 6, 1, P.YEAR)

    latest = latest_instant_of(moment)

    assert latest == datetime.max.replace(tzinfo=UTC)


@given(moment=MOMENTS)
def test_latest_instant_of_never_before_truncated_instant(
    moment: DateWithPrecision,
) -> None:
    latest = latest_instant_of(moment)

    assert latest >= moment.truncate().value
    assert latest >= moment.value


@given(first=MOMENTS, second=MOMENTS)
def test_event_period_validity_matches_precision_aware_rule(
    first: DateWithPrecision, second: DateWithPrecision
) -> None:
    is_valid = latest_instant_of(second) >= first.truncate().value

    if is_valid:
        period = EventPeriod(started_at=first, ended_at=second)
        assert period.earliest_instant() <= period.latest_instant()
    else:
        with pytest.raises(PydanticValidationError):
            EventPeriod(started_at=first, ended_at=second)


@given(first=MOMENTS, second=MOMENTS)
def test_event_period_truncated_order_always_accepted(
    first: DateWithPrecision, second: DateWithPrecision
) -> None:
    start, end = sorted((first, second), key=lambda moment: moment.truncate().value)

    period = EventPeriod(started_at=start, ended_at=end)

    assert period.overlaps(period.earliest_instant(), period.latest_instant())


def test_event_period_overlaps_without_bounds_is_true() -> None:
    period = EventPeriod(started_at=at(2022, 8, 14))

    overlaps = period.overlaps(None, None)

    assert overlaps


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (datetime(2022, 8, 14, 23, tzinfo=UTC), None, True),
        (datetime(2022, 8, 15, tzinfo=UTC), None, False),
        (None, datetime(2022, 8, 14, tzinfo=UTC), True),
        (None, datetime(2022, 8, 13, 23, tzinfo=UTC), False),
        (datetime(2022, 8, 1, tzinfo=UTC), datetime(2022, 8, 31, tzinfo=UTC), True),
    ],
)
def test_event_period_overlaps_day_without_end_uses_whole_start_day(
    start: datetime | None, end: datetime | None, *, expected: bool
) -> None:
    period = EventPeriod(started_at=at(2022, 8, 14))

    overlaps = period.overlaps(start, end)

    assert overlaps == expected


# --------------------------------------------------------------------------- #
# Links, places, relations                                                    #
# --------------------------------------------------------------------------- #


def test_affected_place_invalid_code_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        AffectedPlace.model_validate({"place_code": "Not A Code", "kind": "origin"})


def test_affected_place_unknown_kind_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        AffectedPlace.model_validate({"place_code": "pk.gb.hunza", "kind": "nearby"})


def test_report_link_offset_time_normalised_to_utc() -> None:
    local = datetime(2026, 9, 23, 17, tzinfo=timezone(timedelta(hours=5)))

    link = ReportLink(
        report_id=ids().new_id(),
        linked_by=MODERATOR_ID,
        linked_at=local,
        role="supporting",
    )

    assert link.linked_at.tzinfo is UTC


def test_report_unlink_blank_reason_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ReportUnlink(
            report_id=ids().new_id(),
            role="primary",
            unlinked_by=MODERATOR_ID,
            unlinked_at=datetime(2026, 9, 23, tzinfo=UTC),
            reason="  ",
        )


def test_event_relation_same_event_rejected() -> None:
    event_id = ids().new_id()

    with pytest.raises(PydanticValidationError, match="itself"):
        EventRelationTestFactory.build(from_event_id=event_id, to_event_id=event_id)


def test_event_relation_note_over_limit_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        EventRelationTestFactory.build(note="x" * 501)


def test_event_relation_same_as_key_ignores_direction() -> None:
    forward = EventRelationTestFactory.build(kind=RelationKind.SAME_AS)
    backward = EventRelationTestFactory.build(
        kind=RelationKind.SAME_AS,
        from_event_id=forward.to_event_id,
        to_event_id=forward.from_event_id,
    )

    assert forward.key == backward.key


def test_event_relation_directed_key_keeps_direction() -> None:
    forward = EventRelationTestFactory.build(kind=RelationKind.PART_OF)
    backward = EventRelationTestFactory.build(
        kind=RelationKind.PART_OF,
        from_event_id=forward.to_event_id,
        to_event_id=forward.from_event_id,
    )

    assert forward.key != backward.key


def test_event_relation_involves_and_other_end() -> None:
    relation: EventRelation = EventRelationTestFactory.build()
    stranger = ids(seed=99).new_id()

    assert relation.involves(relation.from_event_id)
    assert relation.involves(relation.to_event_id)
    assert not relation.involves(stranger)
    assert relation.other_end(relation.from_event_id) == relation.to_event_id
    assert relation.other_end(relation.to_event_id) == relation.from_event_id
