"""Domain events of the events bounded context.

Every event concerns one ``Event`` aggregate (``aggregate_type="event"``) and names
the actor who made the change, so the audit subscriber needs nothing else. Payloads
hold identifiers, codes, statuses and event geometry summaries only: reason and note
texts stay on the aggregate and never enter the outbox (Phase 2 security review).

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from yakhnama.modules.events.domain.value_objects import (
    AffectedPlaceKind,
    GeometryType,
    RelationKind,
    ReportLinkRole,
)
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Coordinates, DateWithPrecision

EVENT_AGGREGATE: Final = "event"


class EventRecordEvent(DomainEvent):
    """Fields shared by every event-record event; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"event"``.
        actor_id: Who made the change.
    """

    aggregate_type: Literal["event"] = EVENT_AGGREGATE
    actor_id: EntityId


class EventCreated(EventRecordEvent):
    """A moderator created an event from one or more reports.

    Implements: Domain Events.

    Attributes:
        hazard_code: The event's hazard type.
        report_ids: The reports linked as ``primary`` on creation.
        source_ids: The sources the event cites.
    """

    event_type: ClassVar[str] = "events.event_created"

    hazard_code: HazardCode
    report_ids: tuple[EntityId, ...]
    source_ids: tuple[EntityId, ...]


class ReportLinkedToEvent(EventRecordEvent):
    """A report was linked to an event.

    Implements: Domain Events.

    Attributes:
        report_id: The report.
        role: ``primary``, ``supporting`` or ``contradicting``.
    """

    event_type: ClassVar[str] = "events.report_linked_to_event"

    report_id: EntityId
    role: ReportLinkRole


class ReportUnlinkedFromEvent(EventRecordEvent):
    """A report was unlinked from an event; the reason stays on the record.

    Implements: Domain Events.

    Attributes:
        report_id: The report.
    """

    event_type: ClassVar[str] = "events.report_unlinked_from_event"

    report_id: EntityId


class AffectedPlaceAdded(EventRecordEvent):
    """A gazetteer place was added to an event.

    Implements: Domain Events.

    Attributes:
        place_code: The place.
        kind: ``origin``, ``impacted`` or ``reference``.
    """

    event_type: ClassVar[str] = "events.affected_place_added"

    place_code: PlaceCode
    kind: AffectedPlaceKind


class EventGeometryChanged(EventRecordEvent):
    """An event's geometry was set, replaced or removed.

    The geometry itself is not in the payload, to keep outbox messages small; the
    type and the representative point are enough for subscribers to react.

    Implements: Domain Events.

    Attributes:
        geometry_type: The new geometry type, or ``None`` if it was removed.
        centroid: The new representative point, if any.
    """

    event_type: ClassVar[str] = "events.event_geometry_changed"

    geometry_type: GeometryType | None
    centroid: Coordinates | None


class EventPeriodChanged(EventRecordEvent):
    """An event's period was corrected.

    Implements: Domain Events.

    Attributes:
        started_at: The new start.
        ended_at: The new end, if known.
    """

    event_type: ClassVar[str] = "events.event_period_changed"

    started_at: DateWithPrecision
    ended_at: DateWithPrecision | None


class EventAttributesChanged(EventRecordEvent):
    """An event's hazard-specific attributes were set, replaced or cleared.

    Implements: Domain Events.

    Attributes:
        hazard_code: The schema the attributes follow.
        has_attributes: ``False`` if they were cleared.
    """

    event_type: ClassVar[str] = "events.event_attributes_changed"

    hazard_code: HazardCode
    has_attributes: bool


class EventPublished(EventRecordEvent):
    """A draft event was published.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "events.event_published"


class EventRetracted(EventRecordEvent):
    """An event was retracted; the reason stays on the record.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "events.event_retracted"


class EventMerged(EventRecordEvent):
    """An event was merged into another; it stays readable and points at the target.

    Implements: Domain Events.

    Attributes:
        target_event_id: The event it was merged into.
    """

    event_type: ClassVar[str] = "events.event_merged"

    target_event_id: EntityId


class EventsRelated(EventRecordEvent):
    """A relation between two events was recorded; ``aggregate_id`` is its start.

    Implements: Domain Events.

    Attributes:
        to_event_id: The event the relation points to.
        kind: ``triggered_by``, ``part_of`` or ``same_as``.
    """

    event_type: ClassVar[str] = "events.events_related"

    to_event_id: EntityId
    kind: RelationKind
