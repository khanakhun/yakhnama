"""The SQL event query service against real PostGIS.

Every specification is checked against its own in-memory ``is_satisfied_by`` over
``EventSearchCandidate`` values built exactly as the fake query service builds them,
so the SQL form and the in-memory form cannot drift apart. Verification states come
from real ``verification_cases`` rows written through the verification unit of work,
so the join the read model relies on is exercised end to end.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest

from tests.factories.base import FACTORY_IDS
from tests.factories.events import EventRelationTestFactory, EventTestFactory
from tests.factories.verification import VerificationCaseTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.events.application.dto import EventDetail, EventSummary
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.specifications import (
    EventBboxSpecification,
    EventHazardTypeSpecification,
    EventPeriodOverlapsSpecification,
    EventPlaceSpecification,
    EventSearchCandidate,
    EventStatusSpecification,
    VerifiedEventSpecification,
)
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventPeriod,
    EventStatus,
    RelationKind,
)
from yakhnama.modules.events.infrastructure.queries import (
    SqlAlchemyEventQueryService,
    decode_since,
)
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.hazards.public import HazardTypeRef
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import CursorPayload, PageRequest, encode_cursor
from yakhnama.shared_kernel.specification import (
    FalseSpecification,
    Specification,
    TrueSpecification,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

pytestmark = pytest.mark.integration

type EventsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyEventsUnitOfWork]
type VerificationFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyVerificationUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BOX: Final = BoundingBox(
    min_longitude=73.5, min_latitude=35.5, max_longitude=74.5, max_latitude=36.5
)
MODERATOR: Final = FACTORY_IDS.new_id()


def _at(
    year: int, month: int, day: int, precision: DatePrecision, hour: int = 0
) -> DateWithPrecision:
    return DateWithPrecision(
        value=datetime(year, month, day, hour, tzinfo=UTC), precision=precision
    )


def _event(  # noqa: PLR0913  # reason: one keyword per varied field of the dataset
    *,
    hazard_code: str,
    centroid: Coordinates | None,
    places: tuple[AffectedPlace, ...],
    status: EventStatus,
    period: EventPeriod,
    event_id: UUID | None = None,
) -> Event:
    reason = "Recorded for the test." if status.is_final else None
    return EventTestFactory.build(
        id=event_id or FACTORY_IDS.new_id(),
        hazard_type=HazardTypeRef(code=hazard_code),
        centroid=centroid,
        affected_places=places,
        status=status,
        status_reason=reason,
        merged_into=FACTORY_IDS.new_id() if status is EventStatus.MERGED else None,
        period=period,
        created_at=CREATED,
    )


PLACE_A: Final = AffectedPlace(place_code="test.place-a", kind="origin")
PLACE_B: Final = AffectedPlace(place_code="test.place-b", kind="impacted")
PLACE_A_AS_REFERENCE: Final = AffectedPlace(place_code="test.place-a", kind="reference")


def _dataset() -> list[Event]:
    july_day = EventPeriod(started_at=_at(2024, 7, 15, DatePrecision.DAY, hour=9))
    return [
        # Inside the box, day precision, verified.
        _event(
            hazard_code="glof",
            centroid=Coordinates(longitude=74.0, latitude=36.0),
            places=(PLACE_A,),
            status=EventStatus.PUBLISHED,
            period=july_day,
        ),
        # Same earliest instant as the first: the id breaks the tie.
        _event(
            hazard_code="landslide",
            centroid=Coordinates(longitude=75.5, latitude=35.5),
            places=(PLACE_B,),
            status=EventStatus.DRAFT,
            period=july_day,
        ),
        # No centroid; a whole year; a case of another kind shares its id.
        _event(
            hazard_code="glof",
            centroid=None,
            places=(PLACE_A_AS_REFERENCE, PLACE_B),
            status=EventStatus.RETRACTED,
            period=EventPeriod(started_at=_at(2020, 5, 5, DatePrecision.YEAR)),
        ),
        # On the box's corner; a season; verified, then disputed.
        _event(
            hazard_code="glof",
            centroid=Coordinates(longitude=74.5, latitude=36.5),
            places=(),
            status=EventStatus.PUBLISHED,
            period=EventPeriod(started_at=_at(2023, 7, 10, DatePrecision.SEASON)),
        ),
        # Exact start with a month end; verified; outside the box.
        _event(
            hazard_code="flash_flood",
            centroid=Coordinates(longitude=73.0, latitude=35.0),
            places=(PLACE_A,),
            status=EventStatus.PUBLISHED,
            period=EventPeriod(
                started_at=_at(2022, 8, 20, DatePrecision.EXACT, hour=14),
                ended_at=_at(2022, 9, 1, DatePrecision.MONTH),
            ),
        ),
        # Merged; month precision; under review.
        _event(
            hazard_code="glof",
            centroid=Coordinates(longitude=73.5, latitude=35.5),
            places=(PLACE_B,),
            status=EventStatus.MERGED,
            period=EventPeriod(started_at=_at(2024, 6, 3, DatePrecision.MONTH)),
        ),
    ]


def _open_case(target: VerificationTarget) -> VerificationCase:
    return VerificationCaseTestFactory.build(target=target, created_at=CREATED)


def _walk(
    case: VerificationCase,
    states: tuple[VerificationState, ...],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> VerificationCase:
    for state in states:
        case = case.transition(
            state,
            actor_id=MODERATOR,
            reason="Checked against the sources.",
            is_human=True,
            clock=clock,
            ids=ids,
        ).state
    return case


TO_VERIFIED: Final = (VerificationState.UNDER_REVIEW, VerificationState.VERIFIED)


type Arranged = tuple[list[Event], dict[UUID, str]]


@pytest.fixture
async def arranged(
    events_uow_factory: EventsFactory,
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> Arranged:
    """Store the dataset and its verification cases.

    Returns:
        The six dataset events first, then the merge target, and the event case
        states by event id.
    """
    events = _dataset()
    # The merged event references its target, which must be stored first.
    targets = [
        EventTestFactory.build(id=event.merged_into, created_at=CREATED)
        for event in events
        if event.merged_into is not None
    ]
    async with events_uow_factory() as uow:
        for event in (*targets, *events):
            await uow.events.add(event)
        await uow.commit()
    first, _, third, fourth, fifth, sixth = events
    walks = {
        first.id: TO_VERIFIED,
        fourth.id: (*TO_VERIFIED, VerificationState.DISPUTED),
        fifth.id: TO_VERIFIED,
        sixth.id: (VerificationState.UNDER_REVIEW,),
    }
    cases = [
        _walk(
            _open_case(VerificationTarget(kind=TargetKind.EVENT, target_id=event_id)),
            states,
            clock,
            ids,
        )
        for event_id, states in walks.items()
    ]
    # A verified case of a report that happens to share an event's id must not
    # make that event verified.
    cases.append(
        _walk(
            _open_case(VerificationTarget(kind=TargetKind.REPORT, target_id=third.id)),
            TO_VERIFIED,
            clock,
            ids,
        )
    )
    async with verification_uow_factory() as uow:
        for case in cases:
            await uow.verification_cases.add(case)
        await uow.commit()
    states = {
        case.target.target_id: case.state.value
        for case in cases
        if case.target.kind is TargetKind.EVENT
    }
    return [*events, *targets], states


def _candidate(event: Event, states: dict[UUID, str]) -> EventSearchCandidate:
    return EventSearchCandidate(
        id=event.id,
        centroid=event.centroid,
        hazard_code=event.hazard_type.code,
        place_codes=event.place_codes,
        status=event.status,
        period=event.period,
        verification_state=states.get(event.id),
    )


def _expected_ids(
    events: list[Event],
    states: dict[UUID, str],
    specification: Specification[EventSearchCandidate],
) -> list[UUID]:
    ordered = sorted(
        events,
        key=lambda event: (event.period.earliest_instant(), event.id),
        reverse=True,
    )
    return [
        event.id
        for event in ordered
        if specification.is_satisfied_by(_candidate(event, states))
    ]


SPECIFICATIONS: Final[dict[str, Specification[EventSearchCandidate]]] = {
    "true": TrueSpecification(),
    "false": FalseSpecification(),
    "bbox": EventBboxSpecification(BOX),
    "not_bbox": EventBboxSpecification(BOX).not_(),
    "hazard": EventHazardTypeSpecification("glof"),
    "place": EventPlaceSpecification("test.place-a"),
    "not_place": EventPlaceSpecification("test.place-a").not_(),
    "status": EventStatusSpecification(EventStatus.PUBLISHED),
    "period_window": EventPeriodOverlapsSpecification(
        datetime(2022, 9, 15, tzinfo=UTC), datetime(2024, 6, 10, tzinfo=UTC)
    ),
    "period_from_only": EventPeriodOverlapsSpecification(
        datetime(2024, 6, 30, 23, 59, 59, 999999, tzinfo=UTC), None
    ),
    "period_to_only": EventPeriodOverlapsSpecification(
        None, datetime(2020, 12, 31, tzinfo=UTC)
    ),
    "period_unbounded": EventPeriodOverlapsSpecification(None, None),
    "verified": VerifiedEventSpecification(),
    "not_verified": VerifiedEventSpecification().not_(),
    "combined": EventHazardTypeSpecification("glof")
    .and_(EventStatusSpecification(EventStatus.PUBLISHED))
    .or_(
        EventPlaceSpecification("test.place-b").and_(
            VerifiedEventSpecification().not_()
        )
    ),
}


CONSTANT_SPECIFICATIONS: Final = frozenset({"true", "false", "period_unbounded"})


@pytest.mark.parametrize("name", list(SPECIFICATIONS))
async def test_event_query_service_search_matches_in_memory_specification(
    arranged: Arranged,
    event_queries: SqlAlchemyEventQueryService,
    name: str,
) -> None:
    events, states = arranged
    specification = SPECIFICATIONS[name]

    page = await event_queries.search(specification, PageRequest(limit=100))

    expected = _expected_ids(events, states, specification)
    assert [item.id for item in page.items] == expected
    assert page.next_cursor is None
    # The dataset must split on every non-constant filter, or the test proves
    # nothing about it.
    assert name in CONSTANT_SPECIFICATIONS or 0 < len(expected) < len(events)


async def test_event_query_service_search_returns_summaries_with_joined_state(
    arranged: Arranged,
    event_queries: SqlAlchemyEventQueryService,
) -> None:
    events, states = arranged
    by_id = {event.id: event for event in events}

    page = await event_queries.search(TrueSpecification(), PageRequest(limit=100))

    assert page.items == tuple(
        EventSummary.from_entity(by_id[item.id], verification_state=states.get(item.id))
        for item in page.items
    )


async def test_event_query_service_verified_join_follows_the_current_case_state(
    arranged: Arranged,
    event_queries: SqlAlchemyEventQueryService,
) -> None:
    events, _ = arranged
    first, _, third, fourth, fifth, _ = events[:6]

    page = await event_queries.search(
        VerifiedEventSpecification(), PageRequest(limit=100)
    )
    states = {
        item.id: item.verification_state
        for item in (
            await event_queries.search(TrueSpecification(), PageRequest(limit=100))
        ).items
    }

    assert {item.id for item in page.items} == {first.id, fifth.id}
    assert states[fourth.id] == VerificationState.DISPUTED.value
    assert states[third.id] is None


async def test_event_query_service_search_pages_by_keyset_without_gaps(
    arranged: Arranged,
    event_queries: SqlAlchemyEventQueryService,
) -> None:
    events, states = arranged
    seen: list[UUID] = []
    cursor: str | None = None

    while True:
        page = await event_queries.search(
            TrueSpecification(), PageRequest(limit=2, cursor=cursor)
        )
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == _expected_ids(events, states, TrueSpecification())


async def test_event_query_service_search_with_unparsable_cursor_raises(
    event_queries: SqlAlchemyEventQueryService,
) -> None:
    cursor = encode_cursor(
        CursorPayload(sort_key="not-a-date", last_id=FACTORY_IDS.new_id())
    )

    with pytest.raises(ValidationError):
        await event_queries.search(TrueSpecification(), PageRequest(cursor=cursor))


def test_decode_since_naive_instant_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        decode_since("2024-07-15T00:00:00")


async def test_event_query_service_get_returns_detail_with_direct_relations(
    arranged: Arranged,
    event_queries: SqlAlchemyEventQueryService,
    events_uow_factory: EventsFactory,
) -> None:
    events, states = arranged
    first, second, third = events[:3]
    direct = EventRelationTestFactory.build(
        from_event_id=first.id,
        to_event_id=second.id,
        kind=RelationKind.PART_OF,
        related_at=CREATED,
    )
    incoming = EventRelationTestFactory.build(
        from_event_id=third.id,
        to_event_id=first.id,
        kind=RelationKind.TRIGGERED_BY,
        related_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    # Reachable from first, but not touching it: not a direct relation.
    indirect = EventRelationTestFactory.build(
        from_event_id=second.id,
        to_event_id=third.id,
        kind=RelationKind.SAME_AS,
        related_at=CREATED,
    )
    async with events_uow_factory() as uow:
        for relation in (direct, incoming, indirect):
            await uow.event_relations.add(relation)
        await uow.commit()

    detail = await event_queries.get(first.id)

    assert detail == EventDetail.from_entity(
        first, verification_state=states[first.id], relations=[direct, incoming]
    )


async def test_event_query_service_get_unknown_event_returns_none(
    event_queries: SqlAlchemyEventQueryService,
) -> None:
    detail = await event_queries.get(FACTORY_IDS.new_id())

    assert detail is None
