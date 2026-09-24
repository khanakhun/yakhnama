"""Unit tests for ``yakhnama.modules.events.domain.factories``."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.events import ReportForEventTestFactory
from tests.fakes.clock import FrozenClock
from tests.unit.modules.events.domain.samples import MODERATOR_ID, NOW, at, ids
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.errors import AttributesMismatchError
from yakhnama.modules.events.domain.events import EventCreated
from yakhnama.modules.events.domain.factories import (
    MAX_REPORTS_ON_CREATION,
    EventFactory,
    ReportForEvent,
    coarsest,
)
from yakhnama.modules.events.domain.value_objects import EventStatus
from yakhnama.modules.hazards.domain.attributes import (
    GlofAttributes,
    LandslideAttributes,
)
from yakhnama.modules.hazards.public import HazardTypeRef
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

P = DatePrecision
GLOF = HazardTypeRef(code="glof")
TITLE = "Test GLOF event"
_SOURCE_IDS = ids(seed=70)
SOURCES = tuple(_SOURCE_IDS.new_id() for _ in range(3))
REPORTS = st.lists(
    st.builds(
        ReportForEvent,
        id=st.builds(ids(seed=71).new_id),
        observed_at=st.builds(
            DateWithPrecision,
            value=st.datetimes(
                min_value=datetime(2000, 1, 1),  # noqa: DTZ001  # reason: hypothesis bounds must be naive; timezones= adds UTC
                max_value=datetime(2030, 1, 1),  # noqa: DTZ001  # reason: hypothesis bounds must be naive; timezones= adds UTC
                timezones=st.just(UTC),
            ),
            precision=st.sampled_from(list(DatePrecision)),
        ),
        coordinates=st.builds(
            Coordinates,
            longitude=st.floats(min_value=72.0, max_value=78.0),
            latitude=st.floats(min_value=34.0, max_value=37.5),
        ),
        source_id=st.sampled_from(SOURCES),
    ),
    min_size=1,
    max_size=12,
)


def _create(reports: list[ReportForEvent]) -> AggregateChange[Event]:
    return EventFactory.from_reports(
        reports,
        hazard_type=GLOF,
        title=TITLE,
        created_by=MODERATOR_ID,
        clock=FrozenClock(NOW),
        ids=ids(seed=72),
    )


def test_from_reports_single_report_gives_open_ended_draft() -> None:
    report = ReportForEventTestFactory.build(observed_at=at(2022, 8, 14, P.HOUR, 9))

    change = _create([report])

    event = change.state
    assert event.status is EventStatus.DRAFT
    assert event.version == 1
    assert event.hazard_type == GLOF
    assert event.title == TITLE
    assert event.period.started_at == report.observed_at
    assert event.period.ended_at is None
    assert event.centroid == report.coordinates
    assert event.geometry is None
    assert event.source_ids == (report.source_id,)
    (link,) = event.report_links
    assert (link.report_id, link.role, link.linked_by) == (
        report.id,
        "primary",
        MODERATOR_ID,
    )
    assert event.created_at == event.updated_at == NOW


def test_from_reports_several_reports_span_and_coarsest_precision() -> None:
    early = ReportForEventTestFactory.build(
        observed_at=at(2022, 8, 14, P.HOUR, 9), source_id=SOURCES[0]
    )
    late = ReportForEventTestFactory.build(
        observed_at=at(2022, 8, 16, P.DAY), source_id=SOURCES[1]
    )
    again = ReportForEventTestFactory.build(
        observed_at=at(2022, 8, 15, P.EXACT, 3), source_id=SOURCES[0]
    )

    event = _create([late, early, again]).state

    assert event.period.started_at == at(2022, 8, 14, P.DAY, 9)
    assert event.period.ended_at == at(2022, 8, 16, P.DAY)
    assert event.source_ids == (SOURCES[1], SOURCES[0])
    assert event.report_ids == (late.id, early.id, again.id)


def test_from_reports_emits_event_created_with_reports_and_sources() -> None:
    reports = ReportForEventTestFactory.batch(2)

    change = _create(reports)

    (created,) = change.events
    assert isinstance(created, EventCreated)
    assert created.event_type == "events.event_created"
    assert created.aggregate_id == change.state.id
    assert created.actor_id == MODERATOR_ID
    assert created.hazard_code == "glof"
    assert created.report_ids == tuple(report.id for report in reports)
    assert created.source_ids == change.state.source_ids


def test_from_reports_matching_attributes_stored_on_event() -> None:
    attributes = GlofAttributes(mechanism="ice_dam_breach")

    event = EventFactory.from_reports(
        ReportForEventTestFactory.batch(1),
        hazard_type=GLOF,
        title=TITLE,
        created_by=MODERATOR_ID,
        clock=FrozenClock(NOW),
        ids=ids(),
        attributes=attributes,
    ).state

    assert event.attributes == attributes


def test_from_reports_without_attributes_leaves_them_empty() -> None:
    event = _create(ReportForEventTestFactory.batch(1)).state

    assert event.attributes is None


def test_from_reports_other_hazard_attributes_raise_attributes_mismatch() -> None:
    with pytest.raises(AttributesMismatchError) as raised:
        EventFactory.from_reports(
            ReportForEventTestFactory.batch(1),
            hazard_type=GLOF,
            title=TITLE,
            created_by=MODERATOR_ID,
            clock=FrozenClock(NOW),
            ids=ids(),
            attributes=LandslideAttributes(),
        )

    assert (raised.value.hazard_code, raised.value.attributes_code) == (
        "glof",
        "landslide",
    )


def test_from_reports_no_reports_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="at least one report"):
        _create([])


def test_from_reports_over_cap_raises_validation_error() -> None:
    reports = ReportForEventTestFactory.batch(MAX_REPORTS_ON_CREATION + 1)

    with pytest.raises(ValidationError) as raised:
        _create(reports)

    assert raised.value.details == {"count": MAX_REPORTS_ON_CREATION + 1}


def test_from_reports_repeated_report_raises_validation_error() -> None:
    report = ReportForEventTestFactory.build()

    with pytest.raises(ValidationError, match="only once"):
        _create([report, report])


def test_from_reports_unsafe_title_raises_pydantic_error() -> None:
    with pytest.raises(PydanticValidationError):
        EventFactory.from_reports(
            ReportForEventTestFactory.batch(1),
            hazard_type=GLOF,
            title="no",
            created_by=MODERATOR_ID,
            clock=FrozenClock(NOW),
            ids=ids(),
        )


@given(reports=REPORTS)
def test_from_reports_any_reports_derivations_hold(
    reports: list[ReportForEvent],
) -> None:
    unique = list({report.id: report for report in reports}.values())

    event = _create(unique).state

    instants = [report.observed_at.value for report in unique]
    precision = coarsest([report.observed_at.precision for report in unique])
    assert event.period.started_at.value == min(instants)
    assert event.period.started_at.precision is precision
    if event.period.ended_at is None:
        assert min(instants) == max(instants)
    else:
        assert event.period.ended_at.value == max(instants)
        assert event.period.ended_at.precision is precision
    assert event.centroid is not None
    box = BoundingBox.from_coordinates(report.coordinates for report in unique)
    assert box.contains(event.centroid)
    assert set(event.source_ids) == {report.source_id for report in unique}
    assert len(event.source_ids) == len(set(event.source_ids))
    assert all(link.role == "primary" for link in event.report_links)


@pytest.mark.parametrize(
    ("precisions", "expected"),
    [
        ([P.EXACT], P.EXACT),
        ([P.DAY, P.HOUR], P.DAY),
        ([P.MONTH, P.SEASON, P.DAY], P.SEASON),
        ([P.YEAR, P.EXACT], P.YEAR),
    ],
)
def test_coarsest_mixed_precisions_returns_least_precise(
    precisions: list[DatePrecision], expected: DatePrecision
) -> None:
    result = coarsest(precisions)

    assert result is expected


def test_coarsest_empty_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        coarsest([])
