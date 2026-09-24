"""Factories for the ``events`` domain: events, periods, places, reports, relations.

``EventTestFactory`` is suffixed ``TestFactory`` because the domain already has an
``EventFactory`` (``yakhnama.modules.events.domain.factories``) that derives an event
from reports; this factory builds an ``Event`` directly for arranging state. Titles
and place codes are placeholders (``"Test event <n>"``, ``test.place-<n>``), never
real events or places.

Patterns: Factory.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import get_args
from uuid import UUID

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
)
from tests.factories.shared_kernel import CoordinatesFactory, DateWithPrecisionFactory
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    AffectedPlaceKind,
    EventPeriod,
    EventRelation,
    EventStatus,
    RelationKind,
    ReportForEvent,
    ReportLink,
    ReportLinkRole,
)
from yakhnama.modules.hazards.public import HazardTypeRef
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

TEST_HAZARD_CODE = "glof"
"""Hazard code of every factory-built event; a registered attribute schema exists."""


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A record at version 1 has not changed since it was created.
    return values["created_at"]


def _one_source() -> tuple[UUID, ...]:
    return (FACTORY_IDS.new_id(),)


def _day_period() -> EventPeriod:
    start = random_instant()
    return EventPeriod(
        started_at=DateWithPrecision(value=start, precision=DatePrecision.DAY),
        ended_at=DateWithPrecision(
            value=start + timedelta(days=2), precision=DatePrecision.DAY
        ),
    )


def _test_hazard_type() -> HazardTypeRef:
    return HazardTypeRef(code=TEST_HAZARD_CODE)


class ReportForEventTestFactory(YakhnamaModelFactory[ReportForEvent]):
    """Builds report views with a point inside the test region and a fresh source.

    Implements: Factory.
    """

    __model__ = ReportForEvent

    id = Use(FACTORY_IDS.new_id)
    observed_at = Use(DateWithPrecisionFactory.build)
    coordinates = Use(CoordinatesFactory.build)
    source_id = Use(FACTORY_IDS.new_id)


class AffectedPlaceTestFactory(YakhnamaModelFactory[AffectedPlace]):
    """Builds affected places with unique placeholder codes and a random kind.

    Implements: Factory.
    """

    __model__ = AffectedPlace

    place_code = sequence("test.place-{:05d}")
    kind = pick(get_args(AffectedPlaceKind))


class ReportLinkTestFactory(YakhnamaModelFactory[ReportLink]):
    """Builds report links with a random role.

    Implements: Factory.
    """

    __model__ = ReportLink

    report_id = Use(FACTORY_IDS.new_id)
    linked_by = Use(FACTORY_IDS.new_id)
    linked_at = Use(random_instant)
    role = pick(get_args(ReportLinkRole))


class EventRelationTestFactory(YakhnamaModelFactory[EventRelation]):
    """Builds relations between two fresh event ids, without a note.

    Implements: Factory.
    """

    __model__ = EventRelation

    from_event_id = Use(FACTORY_IDS.new_id)
    to_event_id = Use(FACTORY_IDS.new_id)
    kind = pick(list(RelationKind))
    note = None
    related_by = Use(FACTORY_IDS.new_id)
    related_at = Use(random_instant)


class EventTestFactory(YakhnamaModelFactory[Event]):
    """Builds draft ``glof`` events at version 1 with one source and a centroid.

    No geometry, attributes, places or links; pass them explicitly.

    Implements: Factory.
    """

    __model__ = Event

    id = Use(FACTORY_IDS.new_id)
    hazard_type = Use(_test_hazard_type)
    title = sequence("Test event {:05d}")
    summary = None
    period = Use(_day_period)
    geometry = None
    centroid = Use(CoordinatesFactory.build)
    affected_places = ()
    attributes = None
    source_ids = Use(_one_source)
    report_links = ()
    unlinked_reports = ()
    status = EventStatus.DRAFT
    status_reason = None
    merged_into = None
    created_by = Use(FACTORY_IDS.new_id)
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
