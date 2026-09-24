"""Unit tests for ``yakhnama.modules.events.domain.specifications``."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.unit.modules.events.domain.samples import at, ids
from yakhnama.modules.events.domain.specifications import (
    EventBboxSpecification,
    EventHazardTypeSpecification,
    EventPeriodOverlapsSpecification,
    EventPlaceSpecification,
    EventSearchCandidate,
    EventStatusSpecification,
    VerifiedEventSpecification,
)
from yakhnama.modules.events.domain.value_objects import EventPeriod, EventStatus
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates

HUNZA_BOX = BoundingBox(
    min_longitude=74.0, min_latitude=36.0, max_longitude=75.0, max_latitude=37.0
)
POINTS = st.builds(
    Coordinates,
    longitude=st.floats(min_value=72.0, max_value=78.0),
    latitude=st.floats(min_value=34.0, max_value=37.5),
)


def _candidate(**overrides: object) -> EventSearchCandidate:
    fields: dict[str, object] = {
        "id": ids(seed=80).new_id(),
        "centroid": Coordinates(longitude=74.5, latitude=36.5),
        "hazard_code": "glof",
        "place_codes": frozenset({"pk.gb.hunza"}),
        "status": EventStatus.PUBLISHED,
        "period": EventPeriod(started_at=at(2022, 8, 14), ended_at=at(2022, 8, 16)),
        "verification_state": "verified",
    }
    fields.update(overrides)
    return EventSearchCandidate.model_validate(fields)


def test_event_bbox_specification_inside_and_outside() -> None:
    specification = EventBboxSpecification(HUNZA_BOX)

    assert specification.bbox == HUNZA_BOX
    assert specification.is_satisfied_by(_candidate())
    assert not specification.is_satisfied_by(
        _candidate(centroid=Coordinates(longitude=76.0, latitude=36.5))
    )


def test_event_bbox_specification_without_centroid_never_matches() -> None:
    specification = EventBboxSpecification(HUNZA_BOX)

    assert not specification.is_satisfied_by(_candidate(centroid=None))


@given(point=POINTS)
def test_event_bbox_specification_any_point_agrees_with_box_contains(
    point: Coordinates,
) -> None:
    specification = EventBboxSpecification(HUNZA_BOX)

    satisfied = specification.is_satisfied_by(_candidate(centroid=point))

    assert satisfied == HUNZA_BOX.contains(point)


def test_event_hazard_type_specification_matches_exact_code() -> None:
    specification = EventHazardTypeSpecification("glof")

    assert specification.code == "glof"
    assert specification.is_satisfied_by(_candidate())
    assert not specification.is_satisfied_by(_candidate(hazard_code="flash_flood"))


def test_event_place_specification_matches_listed_code_only() -> None:
    specification = EventPlaceSpecification("pk.gb.hunza")

    assert specification.place_code == "pk.gb.hunza"
    assert specification.is_satisfied_by(_candidate())
    assert not specification.is_satisfied_by(_candidate(place_codes=frozenset()))


def test_event_status_specification_matches_status() -> None:
    specification = EventStatusSpecification(EventStatus.PUBLISHED)

    assert specification.status is EventStatus.PUBLISHED
    assert specification.is_satisfied_by(_candidate())
    assert not specification.is_satisfied_by(_candidate(status=EventStatus.DRAFT))


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, True),
        (datetime(2022, 8, 16, 23, tzinfo=UTC), None, True),
        (datetime(2022, 8, 17, tzinfo=UTC), None, False),
        (None, datetime(2022, 8, 14, tzinfo=UTC), True),
        (None, datetime(2022, 8, 13, tzinfo=UTC), False),
    ],
)
def test_event_period_overlaps_specification_window_edges(
    start: datetime | None, end: datetime | None, *, expected: bool
) -> None:
    specification = EventPeriodOverlapsSpecification(start, end)

    satisfied = specification.is_satisfied_by(_candidate())

    assert satisfied == expected


def test_event_period_overlaps_specification_normalises_bounds_to_utc() -> None:
    local = datetime(2022, 8, 14, 5, tzinfo=timezone(timedelta(hours=5)))

    specification = EventPeriodOverlapsSpecification(local, local)

    assert specification.start == datetime(2022, 8, 14, tzinfo=UTC)
    assert specification.end == specification.start


def test_event_period_overlaps_specification_naive_bound_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventPeriodOverlapsSpecification(datetime(2022, 8, 14), None)  # noqa: DTZ001  # reason: the naive value under test


def test_event_period_overlaps_specification_reversed_window_rejected() -> None:
    with pytest.raises(ValidationError, match="after its end"):
        EventPeriodOverlapsSpecification(
            datetime(2022, 8, 15, tzinfo=UTC), datetime(2022, 8, 14, tzinfo=UTC)
        )


@pytest.mark.parametrize(
    ("state", "expected"),
    [("verified", True), ("under_review", False), ("disputed", False), (None, False)],
)
def test_verified_event_specification_reads_mirrored_state(
    state: str | None, *, expected: bool
) -> None:
    specification = VerifiedEventSpecification()

    satisfied = specification.is_satisfied_by(_candidate(verification_state=state))

    assert satisfied == expected


def test_event_specifications_compose_with_kernel_combinators() -> None:
    specification = (
        EventHazardTypeSpecification("glof")
        & VerifiedEventSpecification()
        & ~EventStatusSpecification(EventStatus.RETRACTED)
    ) | EventPlaceSpecification("pk.gb.skardu")

    assert specification.is_satisfied_by(_candidate())
    assert not specification.is_satisfied_by(_candidate(verification_state=None))
    assert specification.is_satisfied_by(
        _candidate(verification_state=None, place_codes=frozenset({"pk.gb.skardu"}))
    )
