"""Read models returned by the events query services.

DTOs are frozen and carry only what a public reader may see: report links and
relations are shown without the moderator who made them, and reporter positions
never appear (an event's centroid is derived from rounded report points only).
``from_entity`` builds the views for in-memory implementations; the SQL query
service builds them from selected columns with the same field meanings, joining
``verification_state`` from the verification read model.

Patterns: DTO.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    ChangeReason,
    EventGeoJson,
    EventPeriod,
    EventRelation,
    EventStatus,
    EventTitle,
    RelationKind,
    RelationNote,
    ReportLinkRole,
)
from yakhnama.modules.events.domain.value_objects import (
    EventSummary as SummaryText,
)
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardAttributesUnion, HazardCode
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Coordinates, DateWithPrecision

VERIFICATION_STATE_MAX_LENGTH: Final = 32
TIMELINE_LABEL_MAX_LENGTH: Final = 100

VerificationStateName = str
"""The value of a ``verification`` state (for example ``"verified"``), held as text
so this module does not depend on the verification module."""


class EventSummary(BaseModel):
    """One event in a search result.

    Implements: DTO.

    Attributes:
        id: The event.
        hazard_code: Its hazard type code.
        title: Its short name.
        period: When it happened.
        centroid: Its representative point, if known.
        status: Its editorial status.
        verification_state: The state of its verification case, or ``None``.
        place_codes: Codes of every affected place, sorted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    hazard_code: HazardCode
    title: EventTitle
    period: EventPeriod
    centroid: Coordinates | None
    status: EventStatus
    verification_state: VerificationStateName | None = Field(
        max_length=VERIFICATION_STATE_MAX_LENGTH
    )
    place_codes: tuple[PlaceCode, ...]

    @classmethod
    def from_entity(
        cls, event: Event, *, verification_state: VerificationStateName | None
    ) -> Self:
        """Build the summary of an event.

        Args:
            event: The aggregate.
            verification_state: The mirrored state of its case, if any.

        Returns:
            Its summary.
        """
        return cls(
            id=event.id,
            hazard_code=event.hazard_type.code,
            title=event.title,
            period=event.period,
            centroid=event.centroid,
            status=event.status,
            verification_state=verification_state,
            place_codes=tuple(sorted(event.place_codes)),
        )


class ReportLinkView(BaseModel):
    """A report an event rests on, without the moderator who linked it.

    Implements: DTO.

    Attributes:
        report_id: The report.
        role: ``primary``, ``supporting`` or ``contradicting``.
        linked_at: When it was linked, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId
    role: ReportLinkRole
    linked_at: AwareDatetime


class EventRelationView(BaseModel):
    """A relation touching an event, without the moderator who recorded it.

    Implements: DTO.

    Attributes:
        from_event_id: The event the relation starts from.
        to_event_id: The event it points to.
        kind: ``triggered_by``, ``part_of`` or ``same_as``.
        note: Why, if said.
        related_at: When it was recorded, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_event_id: EntityId
    to_event_id: EntityId
    kind: RelationKind
    note: RelationNote | None
    related_at: AwareDatetime

    @classmethod
    def from_relation(cls, relation: EventRelation) -> Self:
        """Build the view of a relation.

        Args:
            relation: The domain relation.

        Returns:
            Its view.
        """
        return cls(
            from_event_id=relation.from_event_id,
            to_event_id=relation.to_event_id,
            kind=relation.kind,
            note=relation.note,
            related_at=relation.related_at,
        )


class EventDetail(BaseModel):
    """One event with everything a reader of the record may see.

    Implements: DTO.

    Attributes:
        id: The event.
        hazard_code: Its hazard type code.
        title: Its short name.
        summary: The moderator's summary, if any.
        period: When it happened.
        geometry: Its GeoJSON point or extent (WGS84), if mapped.
        centroid: Its representative point, if known.
        affected_places: The gazetteer places it concerns.
        attributes: Its hazard-specific attributes, if any.
        source_ids: Every source it cites.
        report_links: The reports currently linked.
        relations: Every relation that starts or ends at it.
        status: Its editorial status.
        status_reason: Why it was retracted or merged, if it was.
        merged_into: The event it was merged into, if it was.
        verification_state: The state of its verification case, or ``None``.
        version: Optimistic-concurrency version.
        created_at: When it was created, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    hazard_code: HazardCode
    title: EventTitle
    summary: SummaryText | None
    period: EventPeriod
    geometry: EventGeoJson | None
    centroid: Coordinates | None
    affected_places: tuple[AffectedPlace, ...]
    attributes: HazardAttributesUnion | None
    source_ids: tuple[EntityId, ...]
    report_links: tuple[ReportLinkView, ...]
    relations: tuple[EventRelationView, ...]
    status: EventStatus
    status_reason: ChangeReason | None
    merged_into: EntityId | None
    verification_state: VerificationStateName | None = Field(
        max_length=VERIFICATION_STATE_MAX_LENGTH
    )
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(
        cls,
        event: Event,
        *,
        verification_state: VerificationStateName | None,
        relations: Iterable[EventRelation] = (),
    ) -> Self:
        """Build the detail view of an event.

        Args:
            event: The aggregate.
            verification_state: The mirrored state of its case, if any.
            relations: The relations touching it, in the order recorded.

        Returns:
            Its detail view.
        """
        return cls(
            id=event.id,
            hazard_code=event.hazard_type.code,
            title=event.title,
            summary=event.summary,
            period=event.period,
            geometry=None if event.geometry is None else event.geometry.geojson,
            centroid=event.centroid,
            affected_places=event.affected_places,
            attributes=event.attributes,
            source_ids=event.source_ids,
            report_links=tuple(
                ReportLinkView(
                    report_id=link.report_id, role=link.role, linked_at=link.linked_at
                )
                for link in event.report_links
            ),
            relations=tuple(
                EventRelationView.from_relation(relation) for relation in relations
            ),
            status=event.status,
            status_reason=event.status_reason,
            merged_into=event.merged_into,
            verification_state=verification_state,
            version=event.version,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )


class TimelineEntryKind(StrEnum):
    """What a timeline entry records; the order below breaks ties at equal times.

    Implements: Value Object.
    """

    REPORT_OBSERVED = "report_observed"
    EVENT_STARTED = "event_started"
    EVENT_ENDED = "event_ended"
    VERIFICATION_TRANSITION = "verification_transition"
    IMPACT_CLAIM = "impact_claim"


class TimelineEntry(BaseModel):
    """One dated step in the history of an event.

    Implements: DTO.

    Attributes:
        kind: What happened.
        at: When, with its precision.
        subject_id: The report, event, verification case or claim concerned.
        label: A short, non-personal label: the verification state reached or the
            impact metric code, for example; ``None`` when the kind says it all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TimelineEntryKind
    at: DateWithPrecision
    subject_id: EntityId
    label: str | None = Field(
        default=None, min_length=1, max_length=TIMELINE_LABEL_MAX_LENGTH
    )
