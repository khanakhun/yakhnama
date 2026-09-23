"""Writes domain events to the outbox inside the caller's transaction.

Patterns: Transactional Outbox.
"""

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import DomainEvent


class OutboxWriter:
    """Serialises domain events into ``outbox_messages`` rows.

    The writer only stages rows with ``session.add``; it never flushes or commits.
    The unit of work calls it just before committing, so the rows are part of the same
    transaction as the aggregate write (ADR 0007).

    Implements: Transactional Outbox.
    """

    def __init__(self, clock: Clock) -> None:
        """Create the writer.

        Args:
            clock: Supplies ``created_at`` for every row.
        """
        self._clock = clock

    def to_message(self, event: DomainEvent) -> OutboxMessage:
        """Serialise one event into an unsaved outbox row.

        Args:
            event: A concrete domain event.

        Returns:
            A pending row whose id is ``event.event_id`` and whose payload is the whole
            event in JSON mode.
        """
        return OutboxMessage(
            id=event.event_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            payload=event.model_dump(mode="json"),
            occurred_at=event.occurred_at,
            created_at=self._clock.now(),
            published_at=None,
            attempts=0,
            last_error=None,
        )

    def write(self, session: AsyncSession, events: Iterable[DomainEvent]) -> None:
        """Stage one outbox row per event in ``session``, preserving order.

        Args:
            session: The session of the unit of work that is about to commit.
            events: The events it collected, in the order they happened.
        """
        for event in events:
            session.add(self.to_message(event))
