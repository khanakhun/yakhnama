"""Unit tests for creating events from reports and linking reports."""

import pytest

from tests.factories.hazards import GlofAttributesFactory, LandslideAttributesFactory
from tests.fakes.identity import AllowAllPolicy
from tests.unit.modules.events.application.support import (
    CITIZEN,
    MODERATOR,
    MODERATOR_ID,
    REASON,
    RETIRED_HAZARD,
    EventsWorld,
    day,
    new_id,
    report,
    stored_event,
)
from yakhnama.modules.events.application.commands import (
    CreateEventFromReports,
    LinkReportToEvent,
    UnlinkReportFromEvent,
)
from yakhnama.modules.events.application.handlers import (
    CreateEventFromReportsHandler,
    LinkReportToEventHandler,
    UnlinkReportFromEventHandler,
)
from yakhnama.modules.events.domain.errors import (
    AttributesMismatchError,
    EventImmutableError,
    EventNotFoundError,
    ReportAlreadyLinkedError,
    ReportNotLinkedError,
)
from yakhnama.modules.events.domain.events import (
    EventCreated,
    ReportLinkedToEvent,
    ReportUnlinkedFromEvent,
)
from yakhnama.modules.events.domain.value_objects import EventStatus
from yakhnama.modules.hazards.public import HazardTypeRef
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.errors import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

GLOF = HazardTypeRef(code="glof")


def create(
    *report_ids: object,
    actor: Actor = MODERATOR,
    hazard_code: str = "glof",
    **fields: object,
) -> CreateEventFromReports:
    return CreateEventFromReports.model_validate(
        {
            "actor": actor,
            "report_ids": report_ids,
            "hazard_type": {"code": hazard_code},
            "title": "Outburst flood below the glacier",
            **fields,
        }
    )


# --------------------------------------------------------------------------- #
# CreateEventFromReports                                                      #
# --------------------------------------------------------------------------- #


async def test_create_event_derives_period_and_centroid_from_rounded_reports() -> None:
    first = report(74.123456, 36.987654, day(2026, 8, 10))
    second = report(74.134999, 36.991111, day(2026, 8, 12))
    world = EventsWorld(reports=(first, second))

    event_id = await CreateEventFromReportsHandler(world.deps)(
        create(first.id, second.id, summary="Two villages flooded.")
    )

    event = world.stored(event_id)
    assert event.period.started_at == day(2026, 8, 10)
    assert event.period.ended_at == day(2026, 8, 12)
    # Rounded to 2 decimals first (74.12, 36.99 and 74.13, 36.99), then averaged,
    # so the exact reporter positions never influence the centroid.
    assert event.centroid == Coordinates(longitude=74.125, latitude=36.99)
    assert event.report_ids == (first.id, second.id)
    assert {link.role for link in event.report_links} == {"primary"}
    assert event.source_ids == (first.source_id, second.source_id)
    assert event.summary == "Two villages flooded."
    assert event.status is EventStatus.DRAFT


async def test_create_event_single_report_centroid_is_rounded_point() -> None:
    only = report(74.126, 36.984)
    world = EventsWorld(reports=(only,))

    event_id = await CreateEventFromReportsHandler(world.deps)(create(only.id))

    event = world.stored(event_id)
    assert event.centroid == Coordinates(longitude=74.13, latitude=36.98)
    assert event.period.ended_at is None
    assert event.summary is None


async def test_create_event_records_created_and_link_events_marks_sources() -> None:
    first, second = report(), report()
    world = EventsWorld(reports=(first, second))

    event_id = await CreateEventFromReportsHandler(world.deps)(
        create(first.id, second.id)
    )

    kinds = [type(event) for event in world.uow.committed_events]
    assert kinds == [EventCreated, ReportLinkedToEvent, ReportLinkedToEvent]
    links = [
        event
        for event in world.uow.committed_events
        if isinstance(event, ReportLinkedToEvent)
    ]
    assert [link.report_id for link in links] == [first.id, second.id]
    assert all(link.actor_id == MODERATOR_ID for link in links)
    assert world.sources.marked == [first.source_id, second.source_id]
    assert world.cases.opened == [event_id]


async def test_create_event_with_matching_attributes_keeps_them() -> None:
    only = report()
    world = EventsWorld(reports=(only,))
    attributes = GlofAttributesFactory.build()

    event_id = await CreateEventFromReportsHandler(world.deps)(
        create(only.id, attributes=attributes)
    )

    assert world.stored(event_id).attributes == attributes


async def test_create_event_attributes_of_other_hazard_raise_mismatch() -> None:
    only = report()
    world = EventsWorld(reports=(only,))

    with pytest.raises(AttributesMismatchError):
        await CreateEventFromReportsHandler(world.deps)(
            create(only.id, attributes=LandslideAttributesFactory.build())
        )

    assert world.uow.commit_count == 0


async def test_create_event_citizen_is_denied_and_nothing_happens() -> None:
    only = report()
    world = EventsWorld(reports=(only,))

    with pytest.raises(PermissionDeniedError):
        await CreateEventFromReportsHandler(world.deps)(create(only.id, actor=CITIZEN))

    assert world.uow.events.committed == {}
    assert world.sources.marked == []
    assert world.cases.opened == []


async def test_create_event_anonymous_under_permissive_policy_is_denied() -> None:
    only = report()
    world = EventsWorld(reports=(only,), policy=AllowAllPolicy())

    with pytest.raises(PermissionDeniedError):
        await CreateEventFromReportsHandler(world.deps)(
            create(only.id, actor=Actor.anonymous())
        )


@pytest.mark.parametrize("hazard_code", ["test_hazard_unknown", RETIRED_HAZARD])
async def test_create_event_unknown_or_retired_hazard_type_is_invalid(
    hazard_code: str,
) -> None:
    only = report()
    world = EventsWorld(reports=(only,))

    with pytest.raises(ValidationError):
        await CreateEventFromReportsHandler(world.deps)(
            create(only.id, hazard_code=hazard_code)
        )

    # Refused before the unit of work was ever entered.
    assert world.uow.rollback_count == 0
    assert world.uow.commit_count == 0


async def test_create_event_missing_report_raises_not_found_nothing_committed() -> None:
    known = report()
    missing = new_id()
    world = EventsWorld(reports=(known,))

    with pytest.raises(NotFoundError) as caught:
        await CreateEventFromReportsHandler(world.deps)(create(known.id, missing))

    assert caught.value.details == {"report_ids": [str(missing)]}
    assert world.uow.commit_count == 0
    assert world.sources.marked == []


async def test_create_event_precision_is_coarsest_of_reports() -> None:
    by_day = report(observed_at=day(2026, 8, 10))
    by_month = report(
        observed_at=DateWithPrecision(
            value=day(2026, 8, 20).value, precision=DatePrecision.MONTH
        )
    )
    world = EventsWorld(reports=(by_day, by_month))

    event_id = await CreateEventFromReportsHandler(world.deps)(
        create(by_day.id, by_month.id)
    )

    assert world.stored(event_id).period.started_at.precision is DatePrecision.MONTH


# --------------------------------------------------------------------------- #
# LinkReportToEvent                                                           #
# --------------------------------------------------------------------------- #


async def test_link_report_adds_supporting_link_and_cites_source() -> None:
    event = stored_event()
    extra = report()
    world = EventsWorld(event, reports=(extra,))

    await LinkReportToEventHandler(world.deps)(
        LinkReportToEvent(actor=MODERATOR, event_id=event.id, report_id=extra.id)
    )

    stored = world.stored(event.id)
    assert stored.report_ids == (extra.id,)
    assert stored.report_links[0].role == "supporting"
    assert extra.source_id in stored.source_ids
    assert stored.version == event.version + 1
    assert world.sources.marked == [extra.source_id]
    (linked,) = world.uow.committed_events
    assert isinstance(linked, ReportLinkedToEvent)


async def test_link_report_citizen_is_denied() -> None:
    event = stored_event()
    extra = report()
    world = EventsWorld(event, reports=(extra,))

    with pytest.raises(PermissionDeniedError):
        await LinkReportToEventHandler(world.deps)(
            LinkReportToEvent(actor=CITIZEN, event_id=event.id, report_id=extra.id)
        )

    assert world.stored(event.id) == event


async def test_link_report_missing_event_raises_not_found() -> None:
    extra = report()
    world = EventsWorld(reports=(extra,))

    with pytest.raises(EventNotFoundError):
        await LinkReportToEventHandler(world.deps)(
            LinkReportToEvent(actor=MODERATOR, event_id=new_id(), report_id=extra.id)
        )


async def test_link_report_missing_report_raises_not_found() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(NotFoundError):
        await LinkReportToEventHandler(world.deps)(
            LinkReportToEvent(actor=MODERATOR, event_id=event.id, report_id=new_id())
        )

    assert world.uow.commit_count == 0


async def test_link_report_already_linked_raises_conflict() -> None:
    linked = report()
    event = stored_event(reports=(linked,))
    world = EventsWorld(event, reports=(linked,))

    with pytest.raises(ReportAlreadyLinkedError):
        await LinkReportToEventHandler(world.deps)(
            LinkReportToEvent(actor=MODERATOR, event_id=event.id, report_id=linked.id)
        )


async def test_link_report_retracted_event_is_immutable() -> None:
    event = stored_event(status=EventStatus.RETRACTED)
    extra = report()
    world = EventsWorld(event, reports=(extra,))

    with pytest.raises(EventImmutableError):
        await LinkReportToEventHandler(world.deps)(
            LinkReportToEvent(actor=MODERATOR, event_id=event.id, report_id=extra.id)
        )

    assert world.sources.marked == []


# --------------------------------------------------------------------------- #
# UnlinkReportFromEvent                                                       #
# --------------------------------------------------------------------------- #


async def test_unlink_report_removes_link_and_keeps_record() -> None:
    linked = report()
    event = stored_event(reports=(linked,))
    world = EventsWorld(event)

    await UnlinkReportFromEventHandler(world.deps)(
        UnlinkReportFromEvent(
            actor=MODERATOR, event_id=event.id, report_id=linked.id, reason=REASON
        )
    )

    stored = world.stored(event.id)
    assert stored.report_links == ()
    assert stored.unlinked_reports[0].report_id == linked.id
    (unlinked,) = world.uow.committed_events
    assert isinstance(unlinked, ReportUnlinkedFromEvent)


async def test_unlink_report_not_linked_raises_and_nothing_committed() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(ReportNotLinkedError):
        await UnlinkReportFromEventHandler(world.deps)(
            UnlinkReportFromEvent(
                actor=MODERATOR, event_id=event.id, report_id=new_id(), reason=REASON
            )
        )

    assert world.uow.commit_count == 0


async def test_unlink_report_citizen_is_denied() -> None:
    linked = report()
    event = stored_event(reports=(linked,))
    world = EventsWorld(event)

    with pytest.raises(PermissionDeniedError):
        await UnlinkReportFromEventHandler(world.deps)(
            UnlinkReportFromEvent(
                actor=CITIZEN, event_id=event.id, report_id=linked.id, reason=REASON
            )
        )
