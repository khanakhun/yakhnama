"""The ``Event`` aggregate and the ``EventGraph`` of relations between events.

An event is the canonical record of one real-world hazard occurrence, created by a
moderator from reports and changed only through the methods below. Each method returns
an ``AggregateChange`` with the new event and its domain events and bumps ``version``.
A retracted or merged event accepts no change at all: nothing verified is deleted, and
retraction is a status with a reason (``AGENTS.md`` §5).

Hazard attributes are one member of the hazards module's ``HazardAttributesUnion``,
selected by its ``hazard_type`` discriminator, which must equal the event's hazard type
code. A raw mapping (for example decoded JSONB) is validated into the matching schema
on construction, so a stored event reloads with its concrete attribute class.

Patterns: Aggregate Root, Value Object.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.modules.events.domain.errors import (
    AttributesMismatchError,
    EventImmutableError,
    InvalidEventStatusError,
    InvalidRelationError,
    ReportAlreadyLinkedError,
    ReportNotLinkedError,
)
from yakhnama.modules.events.domain.events import (
    AffectedPlaceAdded,
    EventAttributesChanged,
    EventGeometryChanged,
    EventMerged,
    EventPeriodChanged,
    EventPublished,
    EventRetracted,
    EventsRelated,
    ReportLinkedToEvent,
    ReportUnlinkedFromEvent,
)
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlace,
    ChangeReason,
    EventGeometry,
    EventPeriod,
    EventRelation,
    EventStatus,
    EventSummary,
    EventTitle,
    RelationKind,
    ReportForEvent,
    ReportLink,
    ReportLinkRole,
    ReportUnlink,
)
from yakhnama.modules.hazards.public import HazardAttributesUnion, HazardTypeRef
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import Coordinates


class Event(BaseModel):
    """The canonical record of one real-world hazard occurrence.

    Implements: Aggregate Root.

    Attributes:
        id: Identifier of the event (UUIDv7).
        hazard_type: The hazard type the event is classified as.
        title: Short name, 3 to 200 characters.
        summary: Moderator's summary, if any.
        period: When it happened.
        geometry: Where, as a point or an extent, if mapped.
        centroid: Representative point; equals ``geometry.centroid()`` when a
            geometry is set, otherwise the mean of the linked reports' public points.
        affected_places: Gazetteer places the event concerns; no duplicates.
        attributes: Hazard-specific attributes matching ``hazard_type``, if any.
        source_ids: Every source the event cites; at least one, no duplicates.
        report_links: The reports currently linked; one link per report.
        unlinked_reports: Every link removed so far, with its reason; append-only.
        status: ``draft``, ``published``, ``retracted`` or ``merged``.
        status_reason: Why it was retracted or merged; set exactly then.
        merged_into: The event it was merged into; set exactly when merged.
        created_by: The moderator who created it.
        version: Starts at 1 and grows by one with every change.
        created_at: When it was created, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    hazard_type: HazardTypeRef
    title: EventTitle
    summary: EventSummary | None = None
    period: EventPeriod
    geometry: EventGeometry | None = None
    centroid: Coordinates | None = None
    affected_places: tuple[AffectedPlace, ...] = ()
    attributes: HazardAttributesUnion | None = None
    source_ids: tuple[EntityId, ...] = Field(min_length=1)
    report_links: tuple[ReportLink, ...] = ()
    unlinked_reports: tuple[ReportUnlink, ...] = ()
    status: EventStatus = EventStatus.DRAFT
    status_reason: ChangeReason | None = None
    merged_into: EntityId | None = None
    created_by: EntityId
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        _require_unique(self.source_ids, "source_ids")
        _require_unique((link.report_id for link in self.report_links), "report links")
        _require_unique(self.affected_places, "affected_places")
        if (
            self.attributes is not None
            and self.attributes.hazard_type != self.hazard_type.code
        ):
            message = "attributes must follow the schema of the event's hazard type"
            raise ValueError(message)
        if self.geometry is not None and self.centroid != self.geometry.centroid():
            message = "centroid must be the geometry's centroid when a geometry is set"
            raise ValueError(message)
        if self.status.is_final != (self.status_reason is not None):
            message = "status_reason is required exactly when retracted or merged"
            raise ValueError(message)
        if (self.status is EventStatus.MERGED) != (self.merged_into is not None):
            message = "merged_into is required exactly when the status is merged"
            raise ValueError(message)
        if self.merged_into == self.id:
            message = "an event cannot be merged into itself"
            raise ValueError(message)
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    @property
    def report_ids(self) -> tuple[EntityId, ...]:
        """Return the ids of the linked reports, in link order."""
        return tuple(link.report_id for link in self.report_links)

    @property
    def place_codes(self) -> frozenset[str]:
        """Return the codes of every affected place, whatever its kind."""
        return frozenset(place.place_code for place in self.affected_places)

    def link_report(
        self,
        report: ReportForEvent,
        *,
        role: ReportLinkRole,
        linked_by: UUID,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Event"]:
        """Link a report and cite its source.

        Args:
            report: The report, as mapped by the application.
            role: ``primary``, ``supporting`` or ``contradicting``.
            linked_by: Who makes the link.
            clock: Source of ``linked_at``, ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The event with the new link (and the report's source cited if it was
            not yet) and a ``ReportLinkedToEvent`` event.

        Raises:
            EventImmutableError: If the event is retracted or merged.
            ReportAlreadyLinkedError: If the report is already linked.
        """
        self._require_mutable("link_report")
        if report.id in self.report_ids:
            raise ReportAlreadyLinkedError(self.id, report.id)
        now = clock.now()
        link = ReportLink(
            report_id=report.id, linked_by=linked_by, linked_at=now, role=role
        )
        source_ids = self.source_ids
        if report.source_id not in source_ids:
            source_ids = (*source_ids, report.source_id)
        state = self._changed(
            now, report_links=(*self.report_links, link), source_ids=source_ids
        )
        event = ReportLinkedToEvent(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=linked_by,
            report_id=report.id,
            role=role,
        )
        return _change(state, event)

    def unlink_report(
        self,
        report_id: UUID,
        *,
        reason: str,
        actor_id: UUID,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Event"]:
        """Remove a report link, keeping a record of it in ``unlinked_reports``.

        The report's source stays cited: a source, once referenced, is part of the
        record's provenance and is never dropped.

        Args:
            report_id: The report to unlink.
            reason: Why; 1 to 1000 characters of safe text.
            actor_id: Who removes the link.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The event without the link and a ``ReportUnlinkedFromEvent`` event.

        Raises:
            EventImmutableError: If the event is retracted or merged.
            ReportNotLinkedError: If the report is not linked.
            pydantic.ValidationError: If ``reason`` is blank, too long or unsafe.
        """
        self._require_mutable("unlink_report")
        link = next(
            (link for link in self.report_links if link.report_id == report_id), None
        )
        if link is None:
            raise ReportNotLinkedError(self.id, report_id)
        now = clock.now()
        unlink = ReportUnlink.model_validate(
            {
                "report_id": report_id,
                "role": link.role,
                "unlinked_by": actor_id,
                "unlinked_at": now,
                "reason": reason,
            }
        )
        state = self._changed(
            now,
            report_links=tuple(
                other for other in self.report_links if other.report_id != report_id
            ),
            unlinked_reports=(*self.unlinked_reports, unlink),
        )
        event = ReportUnlinkedFromEvent(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            report_id=report_id,
        )
        return _change(state, event)

    def add_affected_place(
        self, place: AffectedPlace, *, actor_id: UUID, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Event"]:
        """Add a gazetteer place; adding one already present is a no-op.

        That the place code exists in the gazetteer is checked by the application
        through the ``geography`` facade.

        Args:
            place: The place and how it relates to the event.
            actor_id: Who adds it.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The event with the place and an ``AffectedPlaceAdded`` event, or the
            unchanged event and no event.

        Raises:
            EventImmutableError: If the event is retracted or merged.
        """
        self._require_mutable("add_affected_place")
        if place in self.affected_places:
            return _change(self)
        now = clock.now()
        state = self._changed(now, affected_places=(*self.affected_places, place))
        event = AffectedPlaceAdded(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            place_code=place.place_code,
            kind=place.kind,
        )
        return _change(state, event)

    def set_geometry(
        self,
        geometry: EventGeometry | None,
        *,
        actor_id: UUID,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Event"]:
        """Set, replace or remove the geometry; the centroid follows it.

        Removing the geometry keeps the current centroid, which is still the best
        known representative point.

        Args:
            geometry: The new geometry, or ``None`` to remove it.
            actor_id: Who changes it.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The changed event and an ``EventGeometryChanged`` event, or the unchanged
            event and no event when ``geometry`` equals the current one.

        Raises:
            EventImmutableError: If the event is retracted or merged.
        """
        self._require_mutable("set_geometry")
        if geometry == self.geometry:
            return _change(self)
        centroid = self.centroid if geometry is None else geometry.centroid()
        now = clock.now()
        state = self._changed(now, geometry=geometry, centroid=centroid)
        event = EventGeometryChanged(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            geometry_type=None if geometry is None else geometry.geometry_type,
            centroid=centroid,
        )
        return _change(state, event)

    def set_period(
        self, period: EventPeriod, *, actor_id: UUID, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Event"]:
        """Correct when the event happened.

        Args:
            period: The new period.
            actor_id: Who changes it.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The changed event and an ``EventPeriodChanged`` event, or the unchanged
            event and no event when ``period`` equals the current one.

        Raises:
            EventImmutableError: If the event is retracted or merged.
        """
        self._require_mutable("set_period")
        if period == self.period:
            return _change(self)
        now = clock.now()
        state = self._changed(now, period=period)
        event = EventPeriodChanged(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            started_at=period.started_at,
            ended_at=period.ended_at,
        )
        return _change(state, event)

    def set_attributes(
        self,
        attributes: HazardAttributesUnion | None,
        *,
        actor_id: UUID,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Event"]:
        """Set, replace or clear the hazard-specific attributes.

        Args:
            attributes: A schema from ``HazardAttributesUnion`` whose ``hazard_type``
                is ``hazard_type.code`` (for example from
                ``DEFAULT_REGISTRY.validate``), or ``None`` to clear them.
            actor_id: Who changes them.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The changed event and an ``EventAttributesChanged`` event, or the
            unchanged event and no event when ``attributes`` equal the current ones.

        Raises:
            EventImmutableError: If the event is retracted or merged.
            AttributesMismatchError: If the attributes' ``hazard_type`` is not the
                event's hazard type code.
        """
        self._require_mutable("set_attributes")
        require_matching_attributes(self.hazard_type, attributes)
        if attributes == self.attributes:
            return _change(self)
        now = clock.now()
        state = self._changed(now, attributes=attributes)
        event = EventAttributesChanged(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            hazard_code=self.hazard_type.code,
            has_attributes=attributes is not None,
        )
        return _change(state, event)

    def publish(
        self, *, actor_id: UUID, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Event"]:
        """Publish a draft event.

        Whether the event must be verified first is decided by the application
        through the ``verification`` facade; this method checks only the status.

        Args:
            actor_id: Who publishes it.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The published event and an ``EventPublished`` event.

        Raises:
            EventImmutableError: If the event is retracted or merged.
            InvalidEventStatusError: If the event is already published.
        """
        self._require_mutable("publish")
        if self.status is not EventStatus.DRAFT:
            raise InvalidEventStatusError(self.id, self.status.value, "publish")
        now = clock.now()
        state = self._changed(now, status=EventStatus.PUBLISHED)
        event = EventPublished(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
        )
        return _change(state, event)

    def retract(
        self, reason: str, *, actor_id: UUID, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Event"]:
        """Retract the event; it stays stored and readable with its reason.

        Args:
            reason: Why; 1 to 1000 characters of safe text.
            actor_id: Who retracts it.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The retracted event and an ``EventRetracted`` event.

        Raises:
            EventImmutableError: If the event is already retracted or merged.
            pydantic.ValidationError: If ``reason`` is blank, too long or unsafe.
        """
        self._require_mutable("retract")
        now = clock.now()
        state = self._changed(now, status=EventStatus.RETRACTED, status_reason=reason)
        event = EventRetracted(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
        )
        return _change(state, event)

    def merge_into(
        self,
        target_id: UUID,
        *,
        reason: str,
        actor_id: UUID,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Event"]:
        """Mark the event as merged into ``target_id``; it stays stored and readable.

        Moving links, places and sources onto the target is the application's job,
        through the target's own methods, so each aggregate records its own change.

        Args:
            target_id: The surviving event.
            reason: Why; 1 to 1000 characters of safe text.
            actor_id: Who merges.
            clock: Source of ``updated_at`` and the event time.
            ids: Source of the event id.

        Returns:
            The merged event and an ``EventMerged`` event.

        Raises:
            EventImmutableError: If the event is already retracted or merged.
            InvalidRelationError: If ``target_id`` is this event's own id.
            pydantic.ValidationError: If ``reason`` is blank, too long or unsafe.
        """
        self._require_mutable("merge_into")
        if target_id == self.id:
            message = "an event cannot be merged into itself"
            raise InvalidRelationError(message, details={"event_id": str(self.id)})
        now = clock.now()
        state = self._changed(
            now,
            status=EventStatus.MERGED,
            status_reason=reason,
            merged_into=target_id,
        )
        event = EventMerged(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            target_event_id=target_id,
        )
        return _change(state, event)

    def _require_mutable(self, operation: str) -> None:
        if self.status.is_final:
            raise EventImmutableError(self.id, self.status.value, operation)

    def _changed(self, now: datetime, **changes: object) -> Self:
        # model_validate, not model_copy: model_copy skips validation, and the new
        # state must satisfy every invariant checked on construction.
        return self.model_validate(
            {
                **dict(self),
                **changes,
                "version": self.version + 1,
                "updated_at": now,
            }
        )


def require_matching_attributes(
    hazard_type: HazardTypeRef, attributes: HazardAttributesUnion | None
) -> None:
    """Check that ``attributes`` follow the schema of ``hazard_type``.

    Args:
        hazard_type: The event's hazard type.
        attributes: The attributes, or ``None``.

    Raises:
        AttributesMismatchError: If ``attributes.hazard_type`` is not
            ``hazard_type.code``.
    """
    if attributes is not None and attributes.hazard_type != hazard_type.code:
        raise AttributesMismatchError(hazard_type.code, attributes.hazard_type)


def _require_unique(values: Iterable[object], name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        message = f"{name} must not contain duplicates"
        raise ValueError(message)


def _change(state: Event, *events: DomainEvent) -> AggregateChange[Event]:
    return AggregateChange[Event](state=state, events=events)


# --------------------------------------------------------------------------- #
# Event graph                                                                 #
# --------------------------------------------------------------------------- #


class EventGraph(BaseModel):
    """A consistent set of relations between events.

    Rules: no duplicate relation (``same_as`` compared without direction), and no
    cycle of ``part_of`` relations, since nothing can be part of itself. The
    application loads the relations around the events involved, adds the new one
    through ``relate`` and saves it; that both events exist is checked there.

    Implements: Value Object.

    Attributes:
        relations: Every relation, in the order recorded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relations: tuple[EventRelation, ...] = ()

    @model_validator(mode="after")
    def _check_rules(self) -> Self:
        keys = [relation.key for relation in self.relations]
        if len(keys) != len(set(keys)):
            message = "the graph must not contain the same relation twice"
            raise ValueError(message)
        if self.find_part_of_cycle() is not None:
            message = "part_of relations must not form a cycle"
            raise ValueError(message)
        return self

    def related(self, event_id: UUID) -> tuple[EventRelation, ...]:
        """Return every relation that starts or ends at ``event_id``.

        Args:
            event_id: An event.

        Returns:
            The relations touching it, in the order recorded.
        """
        return tuple(
            relation for relation in self.relations if relation.involves(event_id)
        )

    def same_as_group(self, event_id: UUID) -> frozenset[UUID]:
        """Return every event recorded as the same occurrence as ``event_id``.

        ``same_as`` is symmetric and, read as "describes the same occurrence",
        transitive, so the group is the connected component over ``same_as``.

        Args:
            event_id: An event.

        Returns:
            The group, including ``event_id`` itself.
        """
        group = {event_id}
        pending = [event_id]
        while pending:
            current = pending.pop()
            for relation in self.relations:
                if relation.kind is RelationKind.SAME_AS and relation.involves(current):
                    other = relation.other_end(current)
                    if other not in group:
                        group.add(other)
                        pending.append(other)
        return frozenset(group)

    def find_part_of_cycle(self) -> tuple[UUID, ...] | None:
        """Return one cycle of ``part_of`` relations, if any.

        Returns:
            The event ids along the cycle, the first repeated at the end, or
            ``None`` if ``part_of`` relations form a forest.
        """
        edges: dict[UUID, list[UUID]] = {}
        for relation in self.relations:
            if relation.kind is RelationKind.PART_OF:
                edges.setdefault(relation.from_event_id, []).append(
                    relation.to_event_id
                )
        return _find_cycle(edges)

    def with_relation(self, relation: EventRelation) -> "EventGraph":
        """Return the graph with ``relation`` added.

        Args:
            relation: The new relation.

        Returns:
            A new graph.

        Raises:
            InvalidRelationError: If the relation is already recorded or would
                close a ``part_of`` cycle.
        """
        details = {
            "from_event_id": str(relation.from_event_id),
            "to_event_id": str(relation.to_event_id),
            "kind": relation.kind.value,
        }
        if relation.key in {existing.key for existing in self.relations}:
            message = "the relation is already recorded"
            raise InvalidRelationError(message, details=details)
        graph = EventGraph.model_construct(relations=(*self.relations, relation))
        if graph.find_part_of_cycle() is not None:
            message = "the relation would make an event part of itself"
            raise InvalidRelationError(message, details=details)
        return EventGraph(relations=graph.relations)

    def relate(
        self, relation: EventRelation, *, ids: IdGenerator
    ) -> AggregateChange["EventGraph"]:
        """Add ``relation`` and describe the change as a domain event.

        Args:
            relation: The new relation; its ``related_at`` is the event time.
            ids: Source of the event id.

        Returns:
            The new graph and an ``EventsRelated`` event on ``from_event_id``.

        Raises:
            InvalidRelationError: As for ``with_relation``.
        """
        graph = self.with_relation(relation)
        event = EventsRelated(
            event_id=ids.new_id(),
            occurred_at=relation.related_at,
            aggregate_id=relation.from_event_id,
            actor_id=relation.related_by,
            to_event_id=relation.to_event_id,
            kind=relation.kind,
        )
        return AggregateChange[EventGraph](state=graph, events=(event,))


def _find_cycle(edges: dict[UUID, list[UUID]]) -> tuple[UUID, ...] | None:
    # Iterative depth-first search with a path stack, so a long part_of chain cannot
    # exhaust the recursion limit.
    finished: set[UUID] = set()
    for start in edges:
        if start in finished:
            continue
        path: list[UUID] = [start]
        on_path = {start}
        iterators = [iter(edges.get(start, []))]
        while iterators:
            successor = next(iterators[-1], None)
            if successor is None:
                finished.add(path[-1])
                on_path.discard(path.pop())
                iterators.pop()
            elif successor in on_path:
                return (*path[path.index(successor) :], successor)
            elif successor not in finished:
                path.append(successor)
                on_path.add(successor)
                iterators.append(iter(edges.get(successor, [])))
    return None
