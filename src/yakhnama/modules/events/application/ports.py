"""Ports the events application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1). The ports towards other modules (reports, provenance,
verification, geography, impacts) are declared here, in this module's own terms, and
bound in the composition root to adapters over those modules' facades, so the events
module never imports them and no import cycle can form.

Cross-module calls made by a command handler run inside its unit of work, before
``commit``; if they succeed and the commit then fails, a retry is safe because every
such port is idempotent (a source already referenced stays referenced; an event that
already has a verification case keeps it).

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from collections.abc import Collection, Sequence
from typing import Protocol

from yakhnama.modules.events.application.dto import (
    EventDetail,
    EventSummary,
    TimelineEntry,
)
from yakhnama.modules.events.domain.entities import Event, EventGraph
from yakhnama.modules.events.domain.specifications import EventSearchCandidate
from yakhnama.modules.events.domain.value_objects import (
    EventRelation,
    ReportForEvent,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class EventRepository(Protocol):
    """Loads and stages ``Event`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, event_id: EntityId) -> Event | None:
        """Return one event, whatever its status.

        Args:
            event_id: The event.

        Returns:
            The aggregate, or ``None`` if no event has that id.
        """
        ...

    async def add(self, event: Event) -> None:
        """Stage a new event.

        Args:
            event: The new aggregate at version 1.

        Raises:
            ConflictError: If an event with that id exists.
        """
        ...

    async def save(self, event: Event) -> None:
        """Stage a changed event.

        Several changes to one event in one unit of work are saved once, so the
        version may have grown by more than one since the event was loaded.

        Args:
            event: The new state; its version is greater than the loaded one.

        Raises:
            NotFoundError: If no event with that id is stored.
            ConflictError: If the stored row changed since it was loaded in this
                unit of work (optimistic concurrency on ``version``).
        """
        ...


class EventRelationRepository(Protocol):
    """Loads the relation graph around events and stages new relations.

    Implements: Repository (port side).
    """

    async def graph_around(self, event_ids: Collection[EntityId]) -> EventGraph:
        """Return every relation reachable from ``event_ids``.

        Reachable means connected through any chain of relations of any kind, in
        either direction; that is enough for ``EventGraph`` to detect a duplicate
        relation and a ``part_of`` cycle a new relation would close.

        Args:
            event_ids: The events a new relation will connect.

        Returns:
            The graph of those relations, in the order recorded.
        """
        ...

    async def add(self, relation: EventRelation) -> None:
        """Stage a new relation.

        Args:
            relation: The relation, already checked by ``EventGraph.relate``.

        Raises:
            ConflictError: If the same relation is already stored.
        """
        ...


class EventsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the events repositories.

    Implements: Unit of Work.
    """

    @property
    def events(self) -> EventRepository:
        """Return the event repository bound to this transaction."""
        ...

    @property
    def event_relations(self) -> EventRelationRepository:
        """Return the relation repository bound to this transaction."""
        ...


type EventsUnitOfWorkFactory = UnitOfWorkFactory[EventsUnitOfWork]
"""Opens a fresh events unit of work per use case."""


class ReportFactsProvider(Protocol):
    """Maps reports from the ``reports`` facade to what the events domain needs.

    The adapter must return each report's **public** point, rounded by
    ``PublicCoordinatePolicy``; the handlers apply the policy again, which leaves a
    rounded point unchanged, so a misconfigured adapter still cannot leak a
    reporter's exact position into an event's published centroid.

    Implements: Adapter (port side).
    """

    async def get_many(
        self, report_ids: Sequence[EntityId]
    ) -> Sequence[ReportForEvent]:
        """Return the reports that exist among ``report_ids``.

        Args:
            report_ids: The reports asked for.

        Returns:
            One ``ReportForEvent`` per existing report, in any order; missing ids
            are left out.
        """
        ...


class SourceReferenceMarker(Protocol):
    """Marks provenance sources as referenced, through the ``provenance`` facade.

    A referenced source becomes immutable. Marking an already referenced source is
    a no-op, so the call is safe to repeat.

    Implements: Adapter (port side).
    """

    async def mark_referenced(
        self, source_ids: Sequence[EntityId], *, actor: Actor
    ) -> None:
        """Mark every source in ``source_ids`` as referenced.

        Args:
            source_ids: The sources an event now cites.
            actor: The moderator whose change cites them.

        Raises:
            NotFoundError: If a source does not exist.
        """
        ...


class VerificationCaseOpener(Protocol):
    """Opens an event's verification case, through the ``verification`` facade.

    Implements: Adapter (port side).
    """

    async def open_for_event(self, event_id: EntityId, *, actor: Actor) -> None:
        """Open the event's case unless it already has one.

        Args:
            event_id: The new event.
            actor: The moderator who created it.
        """
        ...


class PlaceDirectory(Protocol):
    """Tells whether a place code exists, through the ``geography`` facade.

    Implements: Adapter (port side).
    """

    async def exists(self, place_code: str) -> bool:
        """Tell whether the gazetteer has a place with ``place_code``.

        Args:
            place_code: The code.

        Returns:
            ``True`` if the place exists, active or retired.
        """
        ...


class EventTimelineSources(Protocol):
    """Dated facts about an event held by other modules, for its timeline.

    Bound in the composition root to adapters over the ``verification`` and
    ``impacts`` facades. Entries carry states and metric codes only, never who
    made a change or why.

    Implements: Adapter (port side).
    """

    async def verification_transitions(
        self, event_id: EntityId
    ) -> Sequence[TimelineEntry]:
        """Return one entry per transition of the event's verification case.

        Args:
            event_id: The event.

        Returns:
            ``verification_transition`` entries at the transition time
            (``exact``), labelled with the state reached, subject the case id;
            empty if the event has no case.
        """
        ...

    async def impact_claims(self, event_id: EntityId) -> Sequence[TimelineEntry]:
        """Return one entry per impact claim recorded for the event.

        Args:
            event_id: The event.

        Returns:
            ``impact_claim`` entries at each claim's ``claimed_at``, labelled with
            the metric code, subject the claim id; retracted claims included.
        """
        ...


class EventQueryService(Protocol):
    """Read port for events, implemented with optimised SQL in infrastructure.

    ``verification_state`` is not part of the event aggregate: the persistence
    layer joins it from the verification cases of target kind ``event`` (or a
    table mirroring them), so specifications on ``verification_state`` compile
    to SQL on that column. Visibility is decided by ``EventRecordQueryService`` in
    this layer and arrives here already folded into the specification.

    Implements: Query Service.
    """

    async def search(
        self,
        specification: Specification[EventSearchCandidate],
        page: PageRequest,
    ) -> Page[EventSummary]:
        """Return one page of events satisfying ``specification``.

        Ordered by the earliest instant of the period, newest first, then by id
        descending; the cursor's ``sort_key`` is that instant in ISO 8601 and its
        ``last_id`` the last event's id.

        Args:
            specification: A tree of the events domain specifications, combined
                with ``and_``, ``or_``, ``not_`` and ``TrueSpecification``.
            page: Page size and cursor.

        Returns:
            Up to ``page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get(self, event_id: EntityId) -> EventDetail | None:
        """Return one event with its relations, whatever its status.

        Args:
            event_id: The event.

        Returns:
            The detail view, or ``None`` if no event has that id.
        """
        ...


class EventCitationQueryService(Protocol):
    """Read port telling whether a publicly visible event cites a source.

    Separate from ``EventQueryService`` because its one consumer, the provenance
    read side (through the composition root), needs nothing else; "publicly
    visible" is the events module's own rule, published and verified, the same one
    ``is_publicly_visible`` applies to a detail view.

    Implements: Query Service.
    """

    async def is_source_cited_by_public_event(self, source_id: EntityId) -> bool:
        """Tell whether a published and verified event lists ``source_id``.

        Only the event's own ``source_ids`` count; a source reached only through a
        linked report's or an impact claim's own source is not an event citation.

        Args:
            source_id: The source.

        Returns:
            ``True`` if at least one published event whose verification case is
            ``verified`` cites it.
        """
        ...
