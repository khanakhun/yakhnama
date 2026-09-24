"""Unit tests for relating, editing, publishing, retracting and merging events."""

import pytest

from tests.factories.hazards import GlofAttributesFactory, LandslideAttributesFactory
from tests.unit.modules.events.application.support import (
    CITIZEN,
    KNOWN_PLACE,
    MODERATOR,
    REASON,
    EventsWorld,
    day,
    new_id,
    report,
    stored_event,
)
from tests.unit.modules.events.domain.samples import square_geometry
from yakhnama.modules.events.application.commands import (
    AddAffectedPlace,
    MergeEvents,
    PublishEvent,
    RelateEvents,
    RetractEvent,
    SetEventAttributes,
    SetEventGeometry,
    SetEventPeriod,
)
from yakhnama.modules.events.application.handlers import (
    AddAffectedPlaceHandler,
    MergeEventsHandler,
    PublishEventHandler,
    RelateEventsHandler,
    RetractEventHandler,
    SetEventAttributesHandler,
    SetEventGeometryHandler,
    SetEventPeriodHandler,
)
from yakhnama.modules.events.domain.errors import (
    AttributesMismatchError,
    EventImmutableError,
    EventNotFoundError,
    InvalidEventStatusError,
    InvalidRelationError,
)
from yakhnama.modules.events.domain.events import (
    AffectedPlaceAdded,
    EventMerged,
    EventPublished,
    EventRetracted,
    EventsRelated,
    ReportLinkedToEvent,
)
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventPeriod,
    EventRelation,
    EventStatus,
    RelationKind,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError

PLACE = AffectedPlace(place_code=KNOWN_PLACE, kind="impacted")

# --------------------------------------------------------------------------- #
# RelateEvents                                                                #
# --------------------------------------------------------------------------- #


async def test_relate_events_stores_relation_and_event() -> None:
    cause, effect = stored_event(), stored_event()
    world = EventsWorld(cause, effect)

    await RelateEventsHandler(world.deps)(
        RelateEvents(
            actor=MODERATOR,
            from_event_id=effect.id,
            to_event_id=cause.id,
            kind=RelationKind.TRIGGERED_BY,
            note="The cloudburst triggered the debris flow.",
        )
    )

    (relation,) = world.uow.event_relations.committed
    assert (relation.from_event_id, relation.to_event_id) == (effect.id, cause.id)
    (related,) = world.uow.committed_events
    assert isinstance(related, EventsRelated)
    assert related.aggregate_id == effect.id


async def test_relate_events_same_event_is_invalid_before_loading() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(InvalidRelationError):
        await RelateEventsHandler(world.deps)(
            RelateEvents(
                actor=MODERATOR,
                from_event_id=event.id,
                to_event_id=event.id,
                kind=RelationKind.SAME_AS,
            )
        )

    assert world.uow.rollback_count == 0


async def test_relate_events_missing_target_raises_not_found() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(EventNotFoundError):
        await RelateEventsHandler(world.deps)(
            RelateEvents(
                actor=MODERATOR,
                from_event_id=event.id,
                to_event_id=new_id(),
                kind=RelationKind.PART_OF,
            )
        )


async def test_relate_events_cycle_of_part_of_is_refused() -> None:
    part, whole = stored_event(), stored_event()
    world = EventsWorld(part, whole)
    world.uow.event_relations.committed.append(
        EventRelation(
            from_event_id=part.id,
            to_event_id=whole.id,
            kind=RelationKind.PART_OF,
            related_by=new_id(),
            related_at=day(2026, 8, 1).value,
        )
    )

    with pytest.raises(InvalidRelationError):
        await RelateEventsHandler(world.deps)(
            RelateEvents(
                actor=MODERATOR,
                from_event_id=whole.id,
                to_event_id=part.id,
                kind=RelationKind.PART_OF,
            )
        )

    assert len(world.uow.event_relations.committed) == 1


async def test_relate_events_citizen_is_denied() -> None:
    first, second = stored_event(), stored_event()
    world = EventsWorld(first, second)

    with pytest.raises(PermissionDeniedError):
        await RelateEventsHandler(world.deps)(
            RelateEvents(
                actor=CITIZEN,
                from_event_id=first.id,
                to_event_id=second.id,
                kind=RelationKind.SAME_AS,
            )
        )


# --------------------------------------------------------------------------- #
# Geometry, period, attributes, places                                        #
# --------------------------------------------------------------------------- #


async def test_set_geometry_moves_centroid_to_geometry() -> None:
    event = stored_event()
    world = EventsWorld(event)
    geometry = square_geometry()

    await SetEventGeometryHandler(world.deps)(
        SetEventGeometry(actor=MODERATOR, event_id=event.id, geometry=geometry)
    )

    stored = world.stored(event.id)
    assert stored.geometry == geometry
    assert stored.centroid == geometry.centroid()


async def test_set_geometry_same_value_changes_nothing() -> None:
    geometry = square_geometry()
    event = stored_event(geometry=geometry, centroid=geometry.centroid())
    world = EventsWorld(event)

    await SetEventGeometryHandler(world.deps)(
        SetEventGeometry(actor=MODERATOR, event_id=event.id, geometry=geometry)
    )

    assert world.stored(event.id) == event
    assert world.uow.committed_events == ()


async def test_set_geometry_missing_event_raises_not_found() -> None:
    world = EventsWorld()

    with pytest.raises(EventNotFoundError):
        await SetEventGeometryHandler(world.deps)(
            SetEventGeometry(actor=MODERATOR, event_id=new_id(), geometry=None)
        )


async def test_set_geometry_citizen_is_denied() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(PermissionDeniedError):
        await SetEventGeometryHandler(world.deps)(
            SetEventGeometry(actor=CITIZEN, event_id=event.id, geometry=None)
        )


async def test_set_period_replaces_period() -> None:
    event = stored_event()
    world = EventsWorld(event)
    period = EventPeriod(started_at=day(2026, 7, 1), ended_at=day(2026, 7, 3))

    await SetEventPeriodHandler(world.deps)(
        SetEventPeriod(actor=MODERATOR, event_id=event.id, period=period)
    )

    assert world.stored(event.id).period == period


async def test_set_attributes_matching_schema_is_stored() -> None:
    event = stored_event()
    world = EventsWorld(event)
    attributes = GlofAttributesFactory.build()

    await SetEventAttributesHandler(world.deps)(
        SetEventAttributes(actor=MODERATOR, event_id=event.id, attributes=attributes)
    )

    assert world.stored(event.id).attributes == attributes


async def test_set_attributes_other_hazard_schema_raises_mismatch() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(AttributesMismatchError):
        await SetEventAttributesHandler(world.deps)(
            SetEventAttributes(
                actor=MODERATOR,
                event_id=event.id,
                attributes=LandslideAttributesFactory.build(),
            )
        )

    assert world.uow.commit_count == 0


async def test_add_affected_place_known_code_is_added() -> None:
    event = stored_event()
    world = EventsWorld(event)

    await AddAffectedPlaceHandler(world.deps)(
        AddAffectedPlace(actor=MODERATOR, event_id=event.id, place=PLACE)
    )

    assert world.stored(event.id).affected_places == (PLACE,)
    (added,) = world.uow.committed_events
    assert isinstance(added, AffectedPlaceAdded)


async def test_add_affected_place_unknown_code_is_invalid() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(ValidationError):
        await AddAffectedPlaceHandler(world.deps)(
            AddAffectedPlace(
                actor=MODERATOR,
                event_id=event.id,
                place=AffectedPlace(place_code="test.place-unknown", kind="origin"),
            )
        )

    assert world.uow.rollback_count == 0


async def test_add_affected_place_citizen_is_denied() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(PermissionDeniedError):
        await AddAffectedPlaceHandler(world.deps)(
            AddAffectedPlace(actor=CITIZEN, event_id=event.id, place=PLACE)
        )


# --------------------------------------------------------------------------- #
# Publish and retract                                                         #
# --------------------------------------------------------------------------- #


async def test_publish_event_draft_becomes_published() -> None:
    event = stored_event()
    world = EventsWorld(event)

    await PublishEventHandler(world.deps)(
        PublishEvent(actor=MODERATOR, event_id=event.id)
    )

    assert world.stored(event.id).status is EventStatus.PUBLISHED
    (published,) = world.uow.committed_events
    assert isinstance(published, EventPublished)


async def test_publish_event_already_published_raises_invalid_status() -> None:
    event = stored_event(status=EventStatus.PUBLISHED)
    world = EventsWorld(event)

    with pytest.raises(InvalidEventStatusError):
        await PublishEventHandler(world.deps)(
            PublishEvent(actor=MODERATOR, event_id=event.id)
        )


async def test_publish_event_citizen_is_denied() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(PermissionDeniedError):
        await PublishEventHandler(world.deps)(
            PublishEvent(actor=CITIZEN, event_id=event.id)
        )

    assert world.stored(event.id).status is EventStatus.DRAFT


async def test_retract_event_keeps_record_with_reason() -> None:
    event = stored_event(status=EventStatus.PUBLISHED)
    world = EventsWorld(event)

    await RetractEventHandler(world.deps)(
        RetractEvent(actor=MODERATOR, event_id=event.id, reason=REASON)
    )

    stored = world.stored(event.id)
    assert stored.status is EventStatus.RETRACTED
    assert stored.status_reason == REASON
    (retracted,) = world.uow.committed_events
    assert isinstance(retracted, EventRetracted)


async def test_retract_event_already_retracted_is_immutable() -> None:
    event = stored_event(status=EventStatus.RETRACTED)
    world = EventsWorld(event)

    with pytest.raises(EventImmutableError):
        await RetractEventHandler(world.deps)(
            RetractEvent(actor=MODERATOR, event_id=event.id, reason=REASON)
        )


# --------------------------------------------------------------------------- #
# MergeEvents                                                                 #
# --------------------------------------------------------------------------- #


async def test_merge_events_moves_new_links_and_places_to_survivor() -> None:
    shared, only_merged = report(), report()
    merged = stored_event(reports=(shared, only_merged), affected_places=(PLACE,))
    survivor = stored_event(reports=(shared,))
    world = EventsWorld(merged, survivor, reports=(shared, only_merged))

    await MergeEventsHandler(world.deps)(
        MergeEvents(
            actor=MODERATOR,
            event_id=merged.id,
            into_event_id=survivor.id,
            reason=REASON,
        )
    )

    final = world.stored(merged.id)
    assert final.status is EventStatus.MERGED
    assert final.merged_into == survivor.id
    assert final.report_ids == merged.report_ids
    kept = world.stored(survivor.id)
    assert kept.report_ids == (shared.id, only_merged.id)
    assert kept.affected_places == (PLACE,)
    kinds = [type(event) for event in world.uow.committed_events]
    assert kinds == [EventMerged, ReportLinkedToEvent, AffectedPlaceAdded]


async def test_merge_events_nothing_to_move_leaves_survivor_unchanged() -> None:
    merged, survivor = stored_event(), stored_event()
    world = EventsWorld(merged, survivor)

    await MergeEventsHandler(world.deps)(
        MergeEvents(
            actor=MODERATOR,
            event_id=merged.id,
            into_event_id=survivor.id,
            reason=REASON,
        )
    )

    assert world.stored(survivor.id) == survivor


async def test_merge_events_into_itself_is_invalid() -> None:
    event = stored_event()
    world = EventsWorld(event)

    with pytest.raises(InvalidRelationError):
        await MergeEventsHandler(world.deps)(
            MergeEvents(
                actor=MODERATOR,
                event_id=event.id,
                into_event_id=event.id,
                reason=REASON,
            )
        )


async def test_merge_events_into_retracted_event_is_immutable() -> None:
    merged = stored_event()
    retracted = stored_event(status=EventStatus.RETRACTED)
    world = EventsWorld(merged, retracted)

    with pytest.raises(EventImmutableError):
        await MergeEventsHandler(world.deps)(
            MergeEvents(
                actor=MODERATOR,
                event_id=merged.id,
                into_event_id=retracted.id,
                reason=REASON,
            )
        )

    assert world.stored(merged.id) == merged


async def test_merge_events_missing_survivor_raises_not_found() -> None:
    merged = stored_event()
    world = EventsWorld(merged)

    with pytest.raises(EventNotFoundError):
        await MergeEventsHandler(world.deps)(
            MergeEvents(
                actor=MODERATOR,
                event_id=merged.id,
                into_event_id=new_id(),
                reason=REASON,
            )
        )


async def test_merge_events_citizen_is_denied() -> None:
    merged, survivor = stored_event(), stored_event()
    world = EventsWorld(merged, survivor)

    with pytest.raises(PermissionDeniedError):
        await MergeEventsHandler(world.deps)(
            MergeEvents(
                actor=CITIZEN,
                event_id=merged.id,
                into_event_id=survivor.id,
                reason=REASON,
            )
        )
