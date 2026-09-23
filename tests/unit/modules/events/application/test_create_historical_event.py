"""Unit tests for creating historical events without reports."""

import pydantic
import pytest

from tests.factories.hazards import GlofAttributesFactory, LandslideAttributesFactory
from tests.unit.modules.events.application.support import (
    CITIZEN,
    KNOWN_PLACE,
    MODERATOR,
    MODERATOR_ID,
    NOW,
    RETIRED_HAZARD,
    EventsWorld,
    day,
    new_id,
)
from yakhnama.modules.events.application.commands import CreateHistoricalEvent
from yakhnama.modules.events.application.dto import CreatedEvent
from yakhnama.modules.events.application.handlers import (
    CreateHistoricalEventHandler,
)
from yakhnama.modules.events.domain.errors import AttributesMismatchError
from yakhnama.modules.events.domain.events import AffectedPlaceAdded, EventCreated
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventGeometry,
    EventPeriod,
    EventStatus,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError
from yakhnama.shared_kernel.value_objects import Coordinates

PERIOD = EventPeriod(started_at=day(2010, 7, 28), ended_at=day(2010, 8, 5))
POINT = EventGeometry.from_coordinates(Coordinates(longitude=74.5, latitude=36.25))


def historical(actor: Actor = MODERATOR, **fields: object) -> CreateHistoricalEvent:
    return CreateHistoricalEvent.model_validate(
        {
            "actor": actor,
            "title": "Synthetic monsoon flood, lower valley",
            "hazard_type": {"code": "glof"},
            "period": PERIOD,
            "source_ids": (new_id(),),
            **fields,
        }
    )


async def test_create_historical_event_stores_draft_with_record_values() -> None:
    world = EventsWorld()
    source_id = new_id()

    created = await CreateHistoricalEventHandler(world.deps)(
        historical(
            geometry=POINT,
            place_codes=(KNOWN_PLACE,),
            summary="Synthetic summary.",
            source_ids=(source_id,),
        )
    )

    event = world.stored(created.id)
    assert isinstance(created, CreatedEvent)
    assert created.version == 1
    assert created.created_at == NOW
    assert event.status is EventStatus.DRAFT
    assert event.period == PERIOD
    assert event.geometry == POINT
    assert event.centroid == Coordinates(longitude=74.5, latitude=36.25)
    assert event.affected_places == (
        AffectedPlace(place_code=KNOWN_PLACE, kind="impacted"),
    )
    assert event.summary == "Synthetic summary."
    assert event.source_ids == (source_id,)
    assert event.report_links == ()
    assert event.created_by == MODERATOR_ID


async def test_create_historical_event_records_events_marks_sources_opens_case() -> (
    None
):
    world = EventsWorld()
    source_ids = (new_id(), new_id())

    created = await CreateHistoricalEventHandler(world.deps)(
        historical(place_codes=(KNOWN_PLACE,), source_ids=source_ids)
    )

    events = world.uow.committed_events
    assert [type(event) for event in events] == [EventCreated, AffectedPlaceAdded]
    first = events[0]
    assert isinstance(first, EventCreated)
    assert first.report_ids == ()
    assert first.source_ids == source_ids
    assert first.aggregate_id == created.id
    assert world.sources.marked == list(source_ids)
    assert world.cases.opened == [created.id]


async def test_create_historical_event_without_geometry_has_no_centroid() -> None:
    world = EventsWorld()

    created = await CreateHistoricalEventHandler(world.deps)(
        historical(place_codes=(KNOWN_PLACE,))
    )

    assert world.stored(created.id).centroid is None


async def test_create_historical_event_with_matching_attributes_keeps_them() -> None:
    world = EventsWorld()
    attributes = GlofAttributesFactory.build()

    created = await CreateHistoricalEventHandler(world.deps)(
        historical(attributes=attributes)
    )

    assert world.stored(created.id).attributes == attributes


async def test_create_historical_event_by_citizen_raises_permission_denied() -> None:
    world = EventsWorld()

    with pytest.raises(PermissionDeniedError):
        await CreateHistoricalEventHandler(world.deps)(historical(actor=CITIZEN))

    assert world.uow.events.committed == {}
    assert world.sources.marked == []


async def test_create_historical_event_with_retired_hazard_raises_validation() -> None:
    world = EventsWorld()

    with pytest.raises(ValidationError):
        await CreateHistoricalEventHandler(world.deps)(
            historical(hazard_type={"code": RETIRED_HAZARD})
        )

    assert world.uow.events.committed == {}


async def test_create_historical_event_with_unknown_place_raises_validation() -> None:
    world = EventsWorld()

    with pytest.raises(ValidationError) as caught:
        await CreateHistoricalEventHandler(world.deps)(
            historical(place_codes=("test.place-unknown",))
        )

    assert caught.value.details["field"] == "place_codes"
    assert world.uow.events.committed == {}


async def test_create_historical_event_with_foreign_attributes_raises_mismatch() -> (
    None
):
    world = EventsWorld()

    with pytest.raises(AttributesMismatchError):
        await CreateHistoricalEventHandler(world.deps)(
            historical(attributes=LandslideAttributesFactory.build())
        )

    assert world.uow.events.committed == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"source_ids": ()},
        {"place_codes": (KNOWN_PLACE, KNOWN_PLACE)},
        {"source_ids": ("0190a000-0000-7000-8000-000000000001",) * 2},
    ],
)
def test_create_historical_event_command_with_bad_lists_raises_validation_error(
    fields: dict[str, object],
) -> None:
    valid = historical().model_dump()

    with pytest.raises(pydantic.ValidationError):
        CreateHistoricalEvent.model_validate({**valid, **fields})
