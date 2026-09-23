"""SQLAlchemy adapters of the ``EventRepository`` and ``EventRelationRepository`` ports.

Writes go straight to the unit of work's transaction, so a later read in the same
unit of work sees them and a rollback discards them. Inserts run inside a savepoint
so a unique violation leaves the transaction usable. Rows are expunged as soon as
they are read or written, so a stale identity-map entry never shadows a later write.

**Optimistic concurrency.** A handler may apply several changes to one event and
save it once, so the version can grow by more than one within a unit of work (the
port allows it). The event repository therefore remembers the version of every
event it loaded or stored in this unit of work and ``save`` updates the row only
``WHERE version = <remembered version>``. An event saved without having been loaded
here is assumed to carry exactly one change (expected ``version - 1``), which fails
safe. If no row matches, the id is looked up once more to tell a missing event
(``EventNotFoundError``) from a concurrent change (``ConflictError``).

Every write also rewrites the event's rows in ``event_report_links``, the narrow
projection of its current links, in the same transaction.

**Relation graph.** ``graph_around`` walks ``event_relations`` with one recursive
CTE: starting from the given events it follows every relation in either direction,
and ``UNION`` (not ``UNION ALL``) drops ids already reached, so cycles terminate.

There is no delete: events are retracted or merged, never removed (``AGENTS.md`` §5).

Patterns: Repository (adapter side).
"""

from collections.abc import Collection

from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.types import Uuid

from yakhnama.modules.events.domain.entities import Event, EventGraph
from yakhnama.modules.events.domain.errors import EventNotFoundError
from yakhnama.modules.events.domain.value_objects import EventRelation
from yakhnama.modules.events.infrastructure.mappers import (
    event_to_row,
    event_to_values,
    relation_to_row,
    report_link_rows,
    row_to_event,
    row_to_relation,
)
from yakhnama.modules.events.infrastructure.orm import (
    EventRelationRow,
    EventReportLinkRow,
    EventRow,
)
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyEventRepository:
    """PostgreSQL-backed implementation of ``EventRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``EventRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, event_id: EntityId) -> Event | None:
        """Return one event, whatever its status.

        Args:
            event_id: The event.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(select(EventRow).where(EventRow.id == event_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        event = row_to_event(row)
        self._versions[event.id] = event.version
        return event

    async def add(self, event: Event) -> None:
        """Insert a new event and its report-link projection.

        Args:
            event: The new aggregate at version 1.

        Raises:
            ConflictError: If an event with that id exists.
        """
        row = event_to_row(event)
        links = report_link_rows(event)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
                self._session.add_all(links)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"event {event.id} already exists"
            raise ConflictError(message, details={"event_id": str(event.id)}) from error
        self._session.expunge(row)
        for link in links:
            self._session.expunge(link)
        self._versions[event.id] = event.version

    async def save(self, event: Event) -> None:
        """Update a stored event, checking optimistic concurrency.

        Args:
            event: The new state; its version is greater than the loaded one.

        Raises:
            EventNotFoundError: If no event with that id is stored.
            ConflictError: If the stored version is not the one loaded in this unit
                of work (or ``event.version - 1`` if it was not loaded here), or
                ``event.version`` is not greater than it.
        """
        expected = self._versions.get(event.id, event.version - 1)
        statement = (
            update(EventRow)
            .where(EventRow.id == event.id, EventRow.version == expected)
            .values(event_to_values(event))
            .returning(EventRow.id)
            .execution_options(synchronize_session=False)
        )
        # A version that did not grow is stale whatever the row holds.
        if (
            event.version <= expected
            or (await self._session.execute(statement)).scalar_one_or_none() is None
        ):
            stored = await self._session.scalar(
                select(EventRow.version).where(EventRow.id == event.id)
            )
            if stored is None:
                raise EventNotFoundError(event.id)
            raise _stale(event, expected, stored)
        await self._session.execute(
            delete(EventReportLinkRow).where(EventReportLinkRow.event_id == event.id)
        )
        links = report_link_rows(event)
        self._session.add_all(links)
        await self._session.flush()
        for link in links:
            self._session.expunge(link)
        self._versions[event.id] = event.version


def _stale(event: Event, expected: int, stored: int) -> ConflictError:
    message = f"event {event.id} was changed concurrently (expected version {expected})"
    return ConflictError(
        message,
        details={
            "event_id": str(event.id),
            "expected_version": expected,
            "stored_version": stored,
        },
    )


class SqlAlchemyEventRelationRepository:
    """PostgreSQL-backed implementation of ``EventRelationRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``EventRelationRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def graph_around(self, event_ids: Collection[EntityId]) -> EventGraph:
        """Return every relation reachable from ``event_ids`` in either direction.

        Args:
            event_ids: The events a new relation will connect.

        Returns:
            The graph of those relations, ordered by ``related_at`` then row id.
        """
        if not event_ids:
            return EventGraph()
        seed = select(
            func.unnest(array(list(event_ids), type_=Uuid())).label("event_id")
        ).cte("reachable", recursive=True)
        # The far end of a relation touching an already reached event.
        other_end = case(
            (
                EventRelationRow.from_event_id == seed.c.event_id,
                EventRelationRow.to_event_id,
            ),
            else_=EventRelationRow.from_event_id,
        )
        reachable = seed.union(
            select(other_end).join(
                seed,
                or_(
                    EventRelationRow.from_event_id == seed.c.event_id,
                    EventRelationRow.to_event_id == seed.c.event_id,
                ),
            )
        )
        statement = (
            select(EventRelationRow)
            .where(
                or_(
                    EventRelationRow.from_event_id.in_(select(reachable.c.event_id)),
                    EventRelationRow.to_event_id.in_(select(reachable.c.event_id)),
                )
            )
            .order_by(EventRelationRow.related_at, EventRelationRow.id)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        relations = [row_to_relation(row) for row in rows]
        for row in rows:
            self._session.expunge(row)
        return EventGraph(relations=tuple(relations))

    async def add(self, relation: EventRelation) -> None:
        """Insert a new relation.

        Args:
            relation: The relation, already checked by ``EventGraph.relate``.

        Raises:
            ConflictError: If the same relation is already stored (for ``same_as``,
                in either direction).
            sqlalchemy.exc.IntegrityError: If an end is not a stored event, which
                the application layer rules out.
        """
        row = relation_to_row(relation)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = "the relation is already recorded"
            raise ConflictError(
                message,
                details={
                    "from_event_id": str(relation.from_event_id),
                    "to_event_id": str(relation.to_event_id),
                    "kind": relation.kind.value,
                },
            ) from error
        self._session.expunge(row)
