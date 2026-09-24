"""Fakes of domain-event recording and of the ``events`` module's ports.

``RecordingEventRecorder`` is the kernel ``EventRecorder`` fake. The rest fake the
events application ports: repositories that stage writes until the unit of work
commits (as a rolled-back transaction would leave the tables unchanged), a query
service that evaluates the same specifications the SQL implementation compiles,
and the ports towards other modules.

Patterns: Fake.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence

from tests.fakes.uow import InMemoryUnitOfWork
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
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.specification import Specification


class RecordingEventRecorder:
    """``EventRecorder`` that appends every recorded event to ``events``.

    Implements: Fake.

    Attributes:
        events: The recorded events, in the order they were recorded.
    """

    def __init__(self) -> None:
        """Create an empty recorder."""
        self.events: list[DomainEvent] = []

    def record_event(self, event: DomainEvent) -> None:
        """Keep ``event``.

        Args:
            event: The event to record.
        """
        self.events.append(event)

    def events_of_type[EventT: DomainEvent](
        self, event_class: type[EventT]
    ) -> tuple[EventT, ...]:
        """Return the recorded events that are instances of ``event_class``.

        Args:
            event_class: The event class to filter by, subclasses included.

        Returns:
            The matching events, in recording order.
        """
        return tuple(event for event in self.events if isinstance(event, event_class))


# --------------------------------------------------------------------------- #
# events module: repositories and unit of work                                #
# --------------------------------------------------------------------------- #


class InMemoryEventRepository:
    """``EventRepository`` over a dictionary keyed by event id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored events, as a committed transaction left them.
    """

    def __init__(self, events: Iterable[Event] = ()) -> None:
        """Create the repository.

        Args:
            events: Events that exist before the test acts.
        """
        self.committed: dict[EntityId, Event] = {event.id: event for event in events}
        self._staged: dict[EntityId, Event] = {}

    def _current(self) -> dict[EntityId, Event]:
        return {**self.committed, **self._staged}

    async def get(self, event_id: EntityId) -> Event | None:
        """Return one event, staged changes included.

        Args:
            event_id: The event.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(event_id)

    async def add(self, event: Event) -> None:
        """Stage a new event.

        Args:
            event: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        if event.id in self._current():
            message = "the event already exists"
            raise ConflictError(message)
        self._staged[event.id] = event

    async def save(self, event: Event) -> None:
        """Stage a changed event, checking its version is newer than the stored one.

        Args:
            event: The new state.

        Raises:
            NotFoundError: If the event is not stored.
            ConflictError: If ``event.version`` is not greater than the stored one.
        """
        stored = self._current().get(event.id)
        if stored is None:
            message = "the event is not stored"
            raise NotFoundError(message)
        if event.version <= stored.version:
            message = "the event was changed concurrently"
            raise ConflictError(message)
        self._staged[event.id] = event

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryEventRelationRepository:
    """``EventRelationRepository`` over a list of relations.

    ``graph_around`` returns every stored relation, a superset of the reachable
    ones the port promises, which ``EventGraph`` handles the same way.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored relations, in the order recorded.
    """

    def __init__(self, relations: Iterable[EventRelation] = ()) -> None:
        """Create the repository.

        Args:
            relations: Relations that exist before the test acts.
        """
        self.committed: list[EventRelation] = list(relations)
        self._staged: list[EventRelation] = []

    async def graph_around(self, event_ids: Collection[EntityId]) -> EventGraph:
        """Return the graph of every stored and staged relation.

        Args:
            event_ids: Ignored; every relation is returned.

        Returns:
            The graph.
        """
        return EventGraph(relations=(*self.committed, *self._staged))

    async def add(self, relation: EventRelation) -> None:
        """Stage a new relation.

        Args:
            relation: The relation.

        Raises:
            ConflictError: If the same relation is stored.
        """
        if relation.key in {
            existing.key for existing in (*self.committed, *self._staged)
        }:
            message = "the relation already exists"
            raise ConflictError(message)
        self._staged.append(relation)

    def related(self, event_id: EntityId) -> tuple[EventRelation, ...]:
        """Return the committed relations touching ``event_id``.

        Args:
            event_id: An event.

        Returns:
            The relations, in the order recorded.
        """
        return EventGraph(relations=tuple(self.committed)).related(event_id)

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.extend(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryEventsUnitOfWork(InMemoryUnitOfWork):
    """``EventsUnitOfWork`` over in-memory repositories.

    Implements: Fake (of Unit of Work).

    Attributes:
        events: The event repository bound to this unit of work.
        event_relations: The relation repository bound to this unit of work.
    """

    def __init__(
        self,
        events: Iterable[Event] = (),
        relations: Iterable[EventRelation] = (),
    ) -> None:
        """Create the unit of work.

        Args:
            events: Events that exist before the test acts.
            relations: Relations that exist before the test acts.
        """
        super().__init__()
        self.events = InMemoryEventRepository(events)
        self.event_relations = InMemoryEventRelationRepository(relations)

    def _on_commit(self) -> None:
        self.events.apply_staged()
        self.event_relations.apply_staged()

    def _on_rollback(self) -> None:
        self.events.discard_staged()
        self.event_relations.discard_staged()


# --------------------------------------------------------------------------- #
# events module: read side                                                    #
# --------------------------------------------------------------------------- #


class InMemoryEventQueryService:
    """``EventQueryService`` over a fake unit of work's committed rows.

    ``verification_states`` plays the part of the verification read model the SQL
    implementation joins; tests set a state per event id.

    Implements: Fake (of Query Service).

    Attributes:
        verification_states: The mirrored state per event id.
    """

    def __init__(
        self,
        uow: InMemoryEventsUnitOfWork,
        verification_states: Mapping[EntityId, str] | None = None,
    ) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
            verification_states: Initial mirrored states per event id.
        """
        self._uow = uow
        self.verification_states: dict[EntityId, str] = dict(verification_states or {})

    def candidate(self, event: Event) -> EventSearchCandidate:
        """Return the flat search view of a committed event.

        Args:
            event: The event.

        Returns:
            Its candidate, with the mirrored verification state.
        """
        return EventSearchCandidate(
            id=event.id,
            centroid=event.centroid,
            hazard_code=event.hazard_type.code,
            place_codes=event.place_codes,
            status=event.status,
            period=event.period,
            verification_state=self.verification_states.get(event.id),
        )

    async def search(
        self,
        specification: Specification[EventSearchCandidate],
        page: PageRequest,
    ) -> Page[EventSummary]:
        """Filter, order newest first and page like the SQL implementation.

        Args:
            specification: The filter tree.
            page: Page size and cursor.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = page.decode_cursor()
        ordered = sorted(
            self._uow.events.committed.values(),
            key=lambda event: (event.period.earliest_instant().isoformat(), event.id),
            reverse=True,
        )
        matching = [
            event
            for event in ordered
            if specification.is_satisfied_by(self.candidate(event))
            and (
                cursor is None
                or (event.period.earliest_instant().isoformat(), event.id)
                < (cursor.sort_key, cursor.last_id)
            )
        ]
        window = matching[: page.limit]
        next_cursor = None
        if len(matching) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(
                    sort_key=last.period.earliest_instant().isoformat(),
                    last_id=last.id,
                )
            )
        return Page[EventSummary](
            items=tuple(
                EventSummary.from_entity(
                    event, verification_state=self.verification_states.get(event.id)
                )
                for event in window
            ),
            next_cursor=next_cursor,
        )

    async def get(self, event_id: EntityId) -> EventDetail | None:
        """Return the detail view of one committed event.

        Args:
            event_id: The event.

        Returns:
            The detail view, or ``None``.
        """
        event = self._uow.events.committed.get(event_id)
        if event is None:
            return None
        return EventDetail.from_entity(
            event,
            verification_state=self.verification_states.get(event_id),
            relations=self._uow.event_relations.related(event_id),
        )


# --------------------------------------------------------------------------- #
# events module: ports towards other modules                                  #
# --------------------------------------------------------------------------- #


class FakeReportFactsProvider:
    """``ReportFactsProvider`` answering from a fixed set of reports.

    It returns the points exactly as given, so tests can check that the handlers
    round them.

    Implements: Fake.
    """

    def __init__(self, reports: Iterable[ReportForEvent] = ()) -> None:
        """Create the provider.

        Args:
            reports: The reports that exist.
        """
        self._reports = {report.id: report for report in reports}

    async def get_many(
        self, report_ids: Sequence[EntityId]
    ) -> Sequence[ReportForEvent]:
        """Return the known reports among ``report_ids``, in reverse order.

        Reversing proves callers do not rely on the order of the answer.

        Args:
            report_ids: The reports asked for.

        Returns:
            The known ones.
        """
        return [
            self._reports[report_id]
            for report_id in reversed(report_ids)
            if report_id in self._reports
        ]


class RecordingSourceReferenceMarker:
    """``SourceReferenceMarker`` that records marked sources.

    Implements: Fake.

    Attributes:
        marked: Every source id marked, in call order, repeats included.
    """

    def __init__(self, unknown: Iterable[EntityId] = ()) -> None:
        """Create the marker.

        Args:
            unknown: Source ids that do not exist; marking one raises.
        """
        self._unknown = frozenset(unknown)
        self.marked: list[EntityId] = []

    async def mark_referenced(
        self, source_ids: Sequence[EntityId], *, actor: Actor
    ) -> None:
        """Record the sources.

        Args:
            source_ids: The sources.
            actor: Ignored.

        Raises:
            NotFoundError: If a source is in ``unknown``.
        """
        if any(source_id in self._unknown for source_id in source_ids):
            message = "a source does not exist"
            raise NotFoundError(message)
        self.marked.extend(source_ids)


class RecordingVerificationCaseOpener:
    """``VerificationCaseOpener`` that records the events it opened cases for.

    Implements: Fake.

    Attributes:
        opened: Event ids, in call order.
    """

    def __init__(self) -> None:
        """Create the opener."""
        self.opened: list[EntityId] = []

    async def open_for_event(self, event_id: EntityId, *, actor: Actor) -> None:
        """Record the event.

        Args:
            event_id: The new event.
            actor: Ignored.
        """
        self.opened.append(event_id)


class FakePlaceDirectory:
    """``PlaceDirectory`` knowing a fixed set of place codes.

    Implements: Fake.
    """

    def __init__(self, codes: Iterable[str] = ()) -> None:
        """Create the directory.

        Args:
            codes: The codes that exist.
        """
        self._codes = frozenset(codes)

    async def exists(self, place_code: str) -> bool:
        """Tell whether the code is known.

        Args:
            place_code: The code.

        Returns:
            ``True`` if known.
        """
        return place_code in self._codes


class FakeEventTimelineSources:
    """``EventTimelineSources`` answering from fixed entries per event.

    Implements: Fake.
    """

    def __init__(
        self,
        transitions: Mapping[EntityId, Sequence[TimelineEntry]] | None = None,
        claims: Mapping[EntityId, Sequence[TimelineEntry]] | None = None,
    ) -> None:
        """Create the fake.

        Args:
            transitions: Verification entries per event id.
            claims: Impact claim entries per event id.
        """
        self._transitions = dict(transitions or {})
        self._claims = dict(claims or {})

    async def verification_transitions(
        self, event_id: EntityId
    ) -> Sequence[TimelineEntry]:
        """Return the fixed verification entries.

        Args:
            event_id: The event.

        Returns:
            Its entries, or none.
        """
        return self._transitions.get(event_id, ())

    async def impact_claims(self, event_id: EntityId) -> Sequence[TimelineEntry]:
        """Return the fixed claim entries.

        Args:
            event_id: The event.

        Returns:
            Its entries, or none.
        """
        return self._claims.get(event_id, ())
