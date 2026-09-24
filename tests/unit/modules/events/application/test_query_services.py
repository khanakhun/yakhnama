"""Unit tests for the events read side: visibility, filters, paging and timeline."""

from datetime import UTC, datetime

import pytest

from tests.unit.modules.events.application.support import (
    CITIZEN,
    KNOWN_PLACE,
    MODERATOR,
    EventsWorld,
    day,
    new_id,
    report,
    stored_event,
)
from yakhnama.modules.events.application.dto import TimelineEntry, TimelineEntryKind
from yakhnama.modules.events.application.queries import (
    GetEvent,
    GetEventTimeline,
    ListEvents,
)
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.errors import EventNotFoundError
from yakhnama.modules.events.domain.specifications import VERIFIED_STATE
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventPeriod,
    EventRelation,
    EventStatus,
    RelationKind,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import TrueSpecification
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

ANONYMOUS = Actor.anonymous()


def world_with_states(
    *entries: tuple[Event, str | None],
) -> EventsWorld:
    world = EventsWorld(*(event for event, _ in entries))
    for event, state in entries:
        if state is not None:
            world.reads.verification_states[event.id] = state
    return world


def visibility_matrix() -> tuple[EventsWorld, Event, dict[str, Event]]:
    public = stored_event(status=EventStatus.PUBLISHED)
    events = {
        "draft_verified": stored_event(),
        "published_unverified": stored_event(status=EventStatus.PUBLISHED),
        "published_under_review": stored_event(status=EventStatus.PUBLISHED),
        "retracted_verified": stored_event(status=EventStatus.RETRACTED),
    }
    world = world_with_states(
        (public, VERIFIED_STATE),
        (events["draft_verified"], VERIFIED_STATE),
        (events["published_unverified"], None),
        (events["published_under_review"], "under_review"),
        (events["retracted_verified"], VERIFIED_STATE),
    )
    return world, public, events


# --------------------------------------------------------------------------- #
# ListEvents                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("actor", [ANONYMOUS, CITIZEN])
async def test_list_events_non_moderator_sees_only_published_and_verified(
    actor: Actor,
) -> None:
    world, public, _ = visibility_matrix()

    page = await world.service.list_events(ListEvents(actor=actor, verified_only=False))

    assert [item.id for item in page.items] == [public.id]
    assert page.items[0].verification_state == VERIFIED_STATE


async def test_list_events_moderator_sees_every_event_by_default() -> None:
    world, public, events = visibility_matrix()

    page = await world.service.list_events(ListEvents(actor=MODERATOR))

    assert {item.id for item in page.items} == {
        public.id,
        *(event.id for event in events.values()),
    }


async def test_list_events_moderator_verified_only_filters_by_state() -> None:
    world, public, events = visibility_matrix()

    page = await world.service.list_events(
        ListEvents(actor=MODERATOR, verified_only=True)
    )

    assert {item.id for item in page.items} == {
        public.id,
        events["draft_verified"].id,
        events["retracted_verified"].id,
    }


async def test_list_events_filters_combine_with_and() -> None:
    place = AffectedPlace(place_code=KNOWN_PLACE, kind="impacted")
    period = EventPeriod(started_at=day(2026, 8, 10))
    wanted = stored_event(
        centroid=Coordinates(longitude=74.5, latitude=36.5),
        affected_places=(place,),
        period=period,
    )
    outside_box = stored_event(
        centroid=Coordinates(longitude=70.0, latitude=30.0),
        affected_places=(place,),
        period=period,
    )
    other_time = stored_event(
        centroid=Coordinates(longitude=74.5, latitude=36.5),
        affected_places=(place,),
        period=EventPeriod(started_at=day(2020, 1, 1)),
    )
    no_place = stored_event(
        centroid=Coordinates(longitude=74.5, latitude=36.5), period=period
    )
    world = EventsWorld(wanted, outside_box, other_time, no_place)

    page = await world.service.list_events(
        ListEvents(
            actor=MODERATOR,
            bbox=BoundingBox(
                min_longitude=74.0,
                min_latitude=36.0,
                max_longitude=75.0,
                max_latitude=37.0,
            ),
            hazard_type="glof",
            place_code=KNOWN_PLACE,
            status=EventStatus.DRAFT,
            period_from=datetime(2026, 8, 1, tzinfo=UTC),
            period_to=datetime(2026, 8, 31, tzinfo=UTC),
        )
    )

    assert [item.id for item in page.items] == [wanted.id]
    assert page.items[0].place_codes == (KNOWN_PLACE,)


async def test_list_events_reversed_period_is_invalid() -> None:
    world = EventsWorld()

    with pytest.raises(ValidationError):
        await world.service.list_events(
            ListEvents(
                actor=MODERATOR,
                period_from=datetime(2026, 9, 1, tzinfo=UTC),
                period_to=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )


async def test_list_events_pages_newest_first_without_repeats() -> None:
    events = [
        stored_event(period=EventPeriod(started_at=day(2026, 8, number)))
        for number in (1, 2, 3)
    ]
    world = EventsWorld(*events)

    first = await world.service.list_events(
        ListEvents(actor=MODERATOR, page=PageRequest(limit=2))
    )
    second = await world.service.list_events(
        ListEvents(actor=MODERATOR, page=PageRequest(limit=2, cursor=first.next_cursor))
    )

    ordered = [item.id for item in (*first.items, *second.items)]
    assert ordered == [event.id for event in reversed(events)]
    assert second.next_cursor is None


def test_list_events_specification_moderator_without_filters_matches_all() -> None:
    specification = ListEvents(actor=MODERATOR).to_specification(may_moderate=True)

    assert isinstance(specification, TrueSpecification)


# --------------------------------------------------------------------------- #
# GetEvent                                                                    #
# --------------------------------------------------------------------------- #


async def test_get_event_anonymous_reads_published_verified_event() -> None:
    world, public, _ = visibility_matrix()

    detail = await world.service.get_event(
        GetEvent(actor=ANONYMOUS, event_id=public.id)
    )

    assert detail.id == public.id
    assert detail.status is EventStatus.PUBLISHED


@pytest.mark.parametrize(
    "hidden",
    [
        "draft_verified",
        "published_unverified",
        "published_under_review",
        "retracted_verified",
    ],
)
async def test_get_event_anonymous_hidden_event_looks_missing(hidden: str) -> None:
    world, _, events = visibility_matrix()

    with pytest.raises(EventNotFoundError):
        await world.service.get_event(
            GetEvent(actor=ANONYMOUS, event_id=events[hidden].id)
        )


async def test_get_event_moderator_reads_draft_with_relations_and_links() -> None:
    linked = report()
    draft, other = stored_event(reports=(linked,)), stored_event()
    world = EventsWorld(draft, other)
    relation = EventRelation(
        from_event_id=draft.id,
        to_event_id=other.id,
        kind=RelationKind.SAME_AS,
        related_by=new_id(),
        related_at=day(2026, 8, 1).value,
    )
    world.uow.event_relations.committed.append(relation)

    detail = await world.service.get_event(GetEvent(actor=MODERATOR, event_id=draft.id))

    assert detail.verification_state is None
    assert [link.report_id for link in detail.report_links] == [linked.id]
    assert [view.to_event_id for view in detail.relations] == [other.id]
    # Public views never name the moderator who linked or related.
    assert "linked_by" not in detail.report_links[0].model_dump()
    assert "related_by" not in detail.relations[0].model_dump()


async def test_get_event_missing_raises_not_found() -> None:
    world = EventsWorld()

    with pytest.raises(EventNotFoundError):
        await world.service.get_event(GetEvent(actor=MODERATOR, event_id=new_id()))


# --------------------------------------------------------------------------- #
# GetEventTimeline                                                            #
# --------------------------------------------------------------------------- #


async def test_get_timeline_orders_entries_by_time_then_kind() -> None:
    early = report(observed_at=day(2026, 8, 10))
    late = report(observed_at=day(2026, 8, 12))
    event = stored_event(
        status=EventStatus.PUBLISHED,
        reports=(early, late),
        period=EventPeriod(started_at=day(2026, 8, 10), ended_at=day(2026, 8, 13)),
    )
    case_id, claim_id = new_id(), new_id()
    transition = TimelineEntry(
        kind=TimelineEntryKind.VERIFICATION_TRANSITION,
        at=DateWithPrecision(
            value=datetime(2026, 8, 11, 9, tzinfo=UTC), precision=DatePrecision.EXACT
        ),
        subject_id=case_id,
        label="verified",
    )
    claim = TimelineEntry(
        kind=TimelineEntryKind.IMPACT_CLAIM,
        at=day(2026, 8, 12),
        subject_id=claim_id,
        label="test_metric_count",
    )
    world = EventsWorld(
        event,
        reports=(early, late),
        transitions={event.id: [transition]},
        claims={event.id: [claim]},
    )
    world.reads.verification_states[event.id] = VERIFIED_STATE

    timeline = await world.service.get_timeline(
        GetEventTimeline(actor=ANONYMOUS, event_id=event.id)
    )

    assert [(entry.kind, entry.subject_id) for entry in timeline] == [
        (TimelineEntryKind.REPORT_OBSERVED, early.id),
        (TimelineEntryKind.EVENT_STARTED, event.id),
        (TimelineEntryKind.VERIFICATION_TRANSITION, case_id),
        (TimelineEntryKind.REPORT_OBSERVED, late.id),
        (TimelineEntryKind.IMPACT_CLAIM, claim_id),
        (TimelineEntryKind.EVENT_ENDED, event.id),
    ]


async def test_get_timeline_without_end_or_other_facts_has_start_only() -> None:
    event = stored_event(period=EventPeriod(started_at=day(2026, 8, 10)))
    world = EventsWorld(event)

    timeline = await world.service.get_timeline(
        GetEventTimeline(actor=MODERATOR, event_id=event.id)
    )

    assert [entry.kind for entry in timeline] == [TimelineEntryKind.EVENT_STARTED]


async def test_get_timeline_hidden_event_looks_missing() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(EventNotFoundError):
        await world.service.get_timeline(
            GetEventTimeline(actor=ANONYMOUS, event_id=event.id)
        )
