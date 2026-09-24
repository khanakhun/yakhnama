"""Write requests accepted by the events command handlers.

Every command carries the ``actor`` it runs as; the handler asks its
``AuthorisationPolicy`` about that actor before reading or staging anything. Free
text fields reuse the domain's safe-text types, so an unsafe value is refused when
the command is built.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.events.domain.factories import MAX_REPORTS_ON_CREATION
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    ChangeReason,
    EventGeometry,
    EventPeriod,
    EventTitle,
    RelationKind,
    RelationNote,
    ReportLinkRole,
)
from yakhnama.modules.events.domain.value_objects import (
    EventSummary as SummaryText,
)
from yakhnama.modules.hazards.public import HazardAttributesUnion, HazardTypeRef
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId


class CreateEventFromReports(BaseModel):
    """Create a draft event from the reports that describe it.

    Implements: Command.

    Attributes:
        actor: The moderator creating the event.
        report_ids: 1 to ``MAX_REPORTS_ON_CREATION`` distinct reports; each is
            linked as ``primary``.
        hazard_type: The hazard type the event is classified as; must be active.
        title: Short name, 3 to 200 characters of safe text.
        summary: Moderator's summary, if any.
        attributes: Hazard-specific attributes, already validated by the API with
            ``DEFAULT_REGISTRY.validate``; their ``hazard_type`` must match.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_ids: tuple[EntityId, ...] = Field(
        min_length=1, max_length=MAX_REPORTS_ON_CREATION
    )
    hazard_type: HazardTypeRef
    title: EventTitle
    summary: SummaryText | None = None
    attributes: HazardAttributesUnion | None = None


class LinkReportToEvent(BaseModel):
    """Link one more report to an event.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        report_id: The report.
        role: What the report contributes; ``supporting`` by default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    report_id: EntityId
    role: ReportLinkRole = "supporting"


class UnlinkReportFromEvent(BaseModel):
    """Remove a report link, keeping the record of it.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        report_id: The linked report.
        reason: Why the link is removed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    report_id: EntityId
    reason: ChangeReason


class RelateEvents(BaseModel):
    """Record a typed relation from one event to another.

    Implements: Command.

    Attributes:
        actor: The moderator.
        from_event_id: The event the relation starts from.
        to_event_id: The event it points to.
        kind: ``triggered_by``, ``part_of`` or ``same_as``.
        note: Why the events are related, if said.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    from_event_id: EntityId
    to_event_id: EntityId
    kind: RelationKind
    note: RelationNote | None = None


class SetEventGeometry(BaseModel):
    """Set, replace or remove an event's geometry.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        geometry: The new point or extent, or ``None`` to remove it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    geometry: EventGeometry | None


class SetEventPeriod(BaseModel):
    """Correct when an event happened.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        period: The new period.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    period: EventPeriod


class SetEventAttributes(BaseModel):
    """Set, replace or clear an event's hazard-specific attributes.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        attributes: A validated member of ``HazardAttributesUnion`` (the API runs
            ``DEFAULT_REGISTRY.validate(code, payload)`` on the JSON it receives),
            or ``None`` to clear them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    attributes: HazardAttributesUnion | None


class AddAffectedPlace(BaseModel):
    """Add a gazetteer place an event concerns.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        place: The place code and how it relates; the code must exist.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    place: AffectedPlace


class PublishEvent(BaseModel):
    """Publish a draft event.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId


class RetractEvent(BaseModel):
    """Retract an event; it stays stored and readable with its reason.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event.
        reason: Why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    reason: ChangeReason


class MergeEvents(BaseModel):
    """Merge one event into another that describes the same occurrence.

    Implements: Command.

    Attributes:
        actor: The moderator.
        event_id: The event that is merged and becomes final.
        into_event_id: The surviving event; it receives the report links and
            affected places it does not have yet.
        reason: Why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
    into_event_id: EntityId
    reason: ChangeReason
