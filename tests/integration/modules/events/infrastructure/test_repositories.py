"""The SQLAlchemy event and relation repositories and events unit of work.

Runs against real PostGIS: geometries, JSONB collections and hazard attributes must
reload exactly, optimistic concurrency must tolerate several changes saved once, and
the recursive relation walk must follow both directions and stop on cycles.
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from geojson_pydantic import MultiPolygon, Point, Polygon
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.events import (
    EventRelationTestFactory,
    EventTestFactory,
    ReportForEventTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.errors import EventNotFoundError
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    EventGeometry,
    EventPeriod,
    EventRelation,
    RelationKind,
)
from yakhnama.modules.events.infrastructure.orm import EventReportLinkRow
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.hazards.public import DEFAULT_REGISTRY
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

pytestmark = pytest.mark.integration

type EventsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyEventsUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)
# Seventeen significant digits: WKB must keep every bit, or the centroid invariant
# (centroid equals the geometry's centroid) would fail on reload.
POLYGON: Final = Polygon.model_validate(
    {
        "type": "Polygon",
        "coordinates": [
            [
                (74.61234567891234, 36.31234567891234),
                (74.71234567891234, 36.31234567891234),
                (74.71234567891234, 36.41234567891234),
                (74.61234567891234, 36.31234567891234),
            ],
            [
                (74.65, 36.33),
                (74.66, 36.33),
                (74.66, 36.34),
                (74.65, 36.33),
            ],
        ],
    }
)
MULTIPOLYGON: Final = MultiPolygon.model_validate(
    {
        "type": "MultiPolygon",
        "coordinates": [
            [[(74.1, 36.1), (74.2, 36.1), (74.2, 36.2), (74.1, 36.1)]],
            [[(75.1, 35.1), (75.3, 35.1), (75.3, 35.4), (75.1, 35.1)]],
        ],
    }
)
POINT: Final = Point.model_validate(
    {"type": "Point", "coordinates": (74.63651234567891, 36.31234567891234)}
)


def _geometry_event(geojson: Point | Polygon | MultiPolygon) -> Event:
    geometry = EventGeometry(geojson=geojson)
    return EventTestFactory.build(
        created_at=CREATED, geometry=geometry, centroid=geometry.centroid()
    )


def _full_event(clock: SteppingClock, ids: SequentialIdGenerator) -> Event:
    geometry = EventGeometry(geojson=POLYGON)
    event = EventTestFactory.build(
        created_at=CREATED,
        summary="Line one.\nLine two.",
        period=EventPeriod(
            started_at=DateWithPrecision(
                value=datetime(2025, 7, 15, 9, 30, tzinfo=UTC),
                precision=DatePrecision.DAY,
            ),
            ended_at=DateWithPrecision(
                value=datetime(2025, 7, 1, tzinfo=UTC), precision=DatePrecision.MONTH
            ),
        ),
        geometry=geometry,
        centroid=geometry.centroid(),
        affected_places=(
            AffectedPlace(place_code="test.place-a", kind="origin"),
            AffectedPlace(place_code="test.place-b", kind="impacted"),
        ),
        attributes=DEFAULT_REGISTRY.validate(
            "glof",
            {
                "mechanism": "moraine_dam_breach",
                "peak_discharge": {
                    "value": 1234.5,
                    "unit": "cubic_metre_per_second",
                },
            },
        ),
    )
    actor = FACTORY_IDS.new_id()
    for role in ("primary", "supporting"):
        event = event.link_report(
            ReportForEventTestFactory.build(),
            role=role,
            linked_by=actor,
            clock=clock,
            ids=ids,
        ).state
    return event.unlink_report(
        event.report_ids[1],
        reason="Not the same flood.",
        actor_id=actor,
        clock=clock,
        ids=ids,
    ).state


async def _store(factory: EventsFactory, *events: Event) -> None:
    async with factory() as uow:
        for event in events:
            await uow.events.add(event)
        await uow.commit()


async def _get(factory: EventsFactory, event_id: UUID) -> Event | None:
    async with factory() as uow:
        return await uow.events.get(event_id)


async def _link_rows(
    session_factory: async_sessionmaker[AsyncSession], event_id: UUID
) -> set[tuple[UUID, str]]:
    async with session_factory() as session:
        rows = await session.execute(
            select(EventReportLinkRow.report_id, EventReportLinkRow.role).where(
                EventReportLinkRow.event_id == event_id
            )
        )
        return {(report_id, role) for report_id, role in rows.tuples()}


async def test_event_repository_add_then_get_returns_equal_event_with_every_field(
    events_uow_factory: EventsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    event = _full_event(clock, ids)

    await _store(events_uow_factory, event)
    loaded = await _get(events_uow_factory, event.id)

    assert loaded == event
    assert loaded is not None
    assert type(loaded.attributes) is type(event.attributes)
    assert loaded.attributes is not None
    assert loaded.attributes.hazard_type == "glof"
    assert len(loaded.unlinked_reports) == 1


@pytest.mark.parametrize(
    "geojson", [POINT, POLYGON, MULTIPOLYGON], ids=["point", "polygon", "multi"]
)
async def test_event_repository_geometry_round_trip_keeps_geometry_and_centroid(
    events_uow_factory: EventsFactory, geojson: Point | Polygon | MultiPolygon
) -> None:
    event = _geometry_event(geojson)

    await _store(events_uow_factory, event)
    loaded = await _get(events_uow_factory, event.id)

    assert loaded is not None
    assert loaded.geometry == event.geometry
    assert loaded.centroid == event.centroid


async def test_event_repository_without_geometry_or_centroid_reloads_as_none(
    events_uow_factory: EventsFactory,
) -> None:
    event = EventTestFactory.build(created_at=CREATED, centroid=None)

    await _store(events_uow_factory, event)

    assert await _get(events_uow_factory, event.id) == event


async def test_event_repository_get_unknown_id_returns_none(
    events_uow_factory: EventsFactory,
) -> None:
    result = await _get(events_uow_factory, FACTORY_IDS.new_id())

    assert result is None


async def test_event_repository_add_duplicate_id_raises_conflict(
    events_uow_factory: EventsFactory,
) -> None:
    event = EventTestFactory.build(created_at=CREATED)
    await _store(events_uow_factory, event)

    async with events_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.events.add(event)


async def test_event_repository_save_several_changes_at_once_persists_final_state(
    events_uow_factory: EventsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    event = EventTestFactory.build(created_at=CREATED)
    await _store(events_uow_factory, event)
    report = ReportForEventTestFactory.build()
    actor = FACTORY_IDS.new_id()

    async with events_uow_factory() as uow:
        loaded = await uow.events.get(event.id)
        assert loaded is not None
        changed = loaded.link_report(
            report, role="primary", linked_by=actor, clock=clock, ids=ids
        ).state
        changed = changed.publish(actor_id=actor, clock=clock, ids=ids).state
        await uow.events.save(changed)
        await uow.commit()

    assert changed.version == event.version + 2
    assert await _get(events_uow_factory, event.id) == changed
    assert await _link_rows(session_factory, event.id) == {(report.id, "primary")}


async def test_event_repository_save_twice_in_one_unit_of_work_tracks_version(
    events_uow_factory: EventsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    event = EventTestFactory.build(created_at=CREATED)
    await _store(events_uow_factory, event)
    report = ReportForEventTestFactory.build()
    actor = FACTORY_IDS.new_id()

    async with events_uow_factory() as uow:
        linked = event.link_report(
            report, role="supporting", linked_by=actor, clock=clock, ids=ids
        ).state
        await uow.events.save(linked)
        unlinked = linked.unlink_report(
            report.id, reason="Wrong event.", actor_id=actor, clock=clock, ids=ids
        ).state
        await uow.events.save(unlinked)
        await uow.commit()

    assert await _get(events_uow_factory, event.id) == unlinked
    assert await _link_rows(session_factory, event.id) == set()


async def test_event_repository_save_after_concurrent_change_raises_conflict(
    events_uow_factory: EventsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    event = EventTestFactory.build(created_at=CREATED)
    await _store(events_uow_factory, event)
    actor = FACTORY_IDS.new_id()

    async with events_uow_factory() as first, events_uow_factory() as second:
        mine = await first.events.get(event.id)
        theirs = await second.events.get(event.id)
        assert mine is not None
        assert theirs is not None
        await second.events.save(
            theirs.publish(actor_id=actor, clock=clock, ids=ids).state
        )
        await second.commit()

        with pytest.raises(ConflictError):
            await first.events.save(
                mine.retract(
                    "Duplicate record.", actor_id=actor, clock=clock, ids=ids
                ).state
            )


async def test_event_repository_save_unchanged_version_raises_conflict(
    events_uow_factory: EventsFactory,
) -> None:
    event = EventTestFactory.build(created_at=CREATED)
    await _store(events_uow_factory, event)

    async with events_uow_factory() as uow:
        loaded = await uow.events.get(event.id)
        assert loaded is not None
        with pytest.raises(ConflictError):
            await uow.events.save(loaded)


async def test_event_repository_save_unknown_event_raises_not_found(
    events_uow_factory: EventsFactory,
) -> None:
    event = EventTestFactory.build(created_at=CREATED, version=2)

    async with events_uow_factory() as uow:
        with pytest.raises(EventNotFoundError):
            await uow.events.save(event)


async def test_events_unit_of_work_rollback_discards_added_event(
    events_uow_factory: EventsFactory,
) -> None:
    event = EventTestFactory.build(created_at=CREATED)

    async with events_uow_factory() as uow:
        await uow.events.add(event)
        await uow.rollback()

    assert await _get(events_uow_factory, event.id) is None


async def test_events_unit_of_work_merged_event_references_target(
    events_uow_factory: EventsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    target = EventTestFactory.build(created_at=CREATED)
    source = EventTestFactory.build(created_at=CREATED)
    await _store(events_uow_factory, target, source)
    merged = source.merge_into(
        target.id,
        reason="Same flood.",
        actor_id=FACTORY_IDS.new_id(),
        clock=clock,
        ids=ids,
    ).state

    async with events_uow_factory() as uow:
        await uow.events.save(merged)
        await uow.commit()

    assert await _get(events_uow_factory, source.id) == merged


# --------------------------------------------------------------------------- #
# Relations                                                                   #
# --------------------------------------------------------------------------- #


def _relation(from_event: Event, to_event: Event, kind: RelationKind) -> EventRelation:
    return EventRelationTestFactory.build(
        from_event_id=from_event.id,
        to_event_id=to_event.id,
        kind=kind,
        related_at=CREATED,
    )


async def _store_events(factory: EventsFactory, count: int) -> list[Event]:
    events = [EventTestFactory.build(created_at=CREATED) for _ in range(count)]
    await _store(factory, *events)
    return events


async def _add_relations(factory: EventsFactory, *relations: EventRelation) -> None:
    async with factory() as uow:
        for relation in relations:
            await uow.event_relations.add(relation)
        await uow.commit()


async def _graph(factory: EventsFactory, *event_ids: UUID) -> set[EventRelation]:
    async with factory() as uow:
        graph = await uow.event_relations.graph_around(event_ids)
    return set(graph.relations)


async def test_relation_repository_graph_around_follows_both_directions(
    events_uow_factory: EventsFactory,
) -> None:
    first, second, third, fourth, unrelated, other = await _store_events(
        events_uow_factory, 6
    )
    # first -> second <- third -> fourth: reaching fourth from first needs a step
    # against the direction of second <- third.
    chain = (
        _relation(first, second, RelationKind.PART_OF),
        _relation(third, second, RelationKind.TRIGGERED_BY),
        _relation(third, fourth, RelationKind.SAME_AS),
    )
    elsewhere = _relation(unrelated, other, RelationKind.PART_OF)
    await _add_relations(events_uow_factory, *chain, elsewhere)

    from_start = await _graph(events_uow_factory, first.id)
    from_end = await _graph(events_uow_factory, fourth.id)

    assert from_start == set(chain)
    assert from_end == set(chain)


async def test_relation_repository_graph_around_terminates_on_cycles(
    events_uow_factory: EventsFactory,
) -> None:
    first, second, third = await _store_events(events_uow_factory, 3)
    cycle = (
        _relation(first, second, RelationKind.TRIGGERED_BY),
        _relation(second, third, RelationKind.TRIGGERED_BY),
        _relation(third, first, RelationKind.TRIGGERED_BY),
        _relation(second, first, RelationKind.SAME_AS),
    )
    await _add_relations(events_uow_factory, *cycle)

    graph = await _graph(events_uow_factory, second.id)

    assert graph == set(cycle)


async def test_relation_repository_graph_around_joins_components_of_every_id(
    events_uow_factory: EventsFactory,
) -> None:
    first, second, third, fourth = await _store_events(events_uow_factory, 4)
    left = _relation(first, second, RelationKind.PART_OF)
    right = _relation(third, fourth, RelationKind.PART_OF)
    await _add_relations(events_uow_factory, left, right)

    graph = await _graph(events_uow_factory, first.id, fourth.id)

    assert graph == {left, right}


async def test_relation_repository_graph_around_orders_by_recording_time(
    events_uow_factory: EventsFactory,
) -> None:
    first, second, third = await _store_events(events_uow_factory, 3)
    later = EventRelationTestFactory.build(
        from_event_id=second.id,
        to_event_id=third.id,
        kind=RelationKind.PART_OF,
        related_at=CREATED + timedelta(hours=1),
    )
    earlier = _relation(first, second, RelationKind.PART_OF)
    await _add_relations(events_uow_factory, later, earlier)

    async with events_uow_factory() as uow:
        graph = await uow.event_relations.graph_around([first.id])

    assert graph.relations == (earlier, later)


async def test_relation_repository_graph_around_no_ids_returns_empty_graph(
    events_uow_factory: EventsFactory,
) -> None:
    graph = await _graph(events_uow_factory)

    assert graph == set()


async def test_relation_repository_graph_around_isolated_event_returns_empty_graph(
    events_uow_factory: EventsFactory,
) -> None:
    (event,) = await _store_events(events_uow_factory, 1)

    graph = await _graph(events_uow_factory, event.id)

    assert graph == set()


async def test_relation_repository_add_duplicate_raises_conflict(
    events_uow_factory: EventsFactory,
) -> None:
    first, second = await _store_events(events_uow_factory, 2)
    relation = _relation(first, second, RelationKind.TRIGGERED_BY)
    await _add_relations(events_uow_factory, relation)

    async with events_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.event_relations.add(relation)


async def test_relation_repository_add_reversed_same_as_raises_conflict(
    events_uow_factory: EventsFactory,
) -> None:
    first, second = await _store_events(events_uow_factory, 2)
    await _add_relations(
        events_uow_factory, _relation(first, second, RelationKind.SAME_AS)
    )

    async with events_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.event_relations.add(
                _relation(second, first, RelationKind.SAME_AS)
            )


async def test_relation_repository_add_reversed_directed_relation_is_accepted(
    events_uow_factory: EventsFactory,
) -> None:
    first, second = await _store_events(events_uow_factory, 2)
    forward = _relation(first, second, RelationKind.TRIGGERED_BY)
    backward = _relation(second, first, RelationKind.TRIGGERED_BY)

    await _add_relations(events_uow_factory, forward, backward)

    assert await _graph(events_uow_factory, first.id) == {forward, backward}


async def test_relation_repository_add_to_unknown_event_raises_integrity_error(
    events_uow_factory: EventsFactory,
) -> None:
    (event,) = await _store_events(events_uow_factory, 1)
    relation = EventRelationTestFactory.build(
        from_event_id=event.id, to_event_id=FACTORY_IDS.new_id()
    )

    async with events_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.event_relations.add(relation)


async def test_relation_repository_relation_with_note_round_trips(
    events_uow_factory: EventsFactory,
) -> None:
    first, second = await _store_events(events_uow_factory, 2)
    relation = EventRelationTestFactory.build(
        from_event_id=first.id,
        to_event_id=second.id,
        note="Cloudburst upstream.",
        related_at=CREATED,
    )
    await _add_relations(events_uow_factory, relation)

    assert await _graph(events_uow_factory, first.id) == {relation}
