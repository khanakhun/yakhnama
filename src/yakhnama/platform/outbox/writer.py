"""Writes domain events to the outbox inside the caller's transaction.

Payload contract (Phase 2 security review): an event's serialised form is at most
``OUTBOX_PAYLOAD_MAX_BYTES``. The cap keeps one event from bloating the table, the
relay's memory and every subscriber, and it makes an event that carries a whole
document instead of identifiers fail loudly in the unit of work that raised it. The
stricter rule, identifiers and non-personal fields only, is the structural test
``tests/architecture/test_outbox_payloads.py``, which inspects every event class.

Patterns: Transactional Outbox.
"""

from collections.abc import Iterable
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.events import DomainEvent

# 64 KiB: far above any event that carries identifiers and short fields, far below
# what would strain a JSONB row or a relay batch of MAX_BATCH_SIZE messages. A
# proposed operational bound, not a domain fact.
OUTBOX_PAYLOAD_MAX_BYTES: Final = 64 * 1024


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

        Raises:
            InvariantViolationError: If the serialised event exceeds
                ``OUTBOX_PAYLOAD_MAX_BYTES``; the message names the event type and
                size only, never the content.
        """
        size = len(event.model_dump_json().encode())
        if size > OUTBOX_PAYLOAD_MAX_BYTES:
            message = (
                f"{event.event_type} payload is {size} bytes; the outbox accepts at "
                f"most {OUTBOX_PAYLOAD_MAX_BYTES}"
            )
            raise InvariantViolationError(
                message,
                details={"event_type": event.event_type, "size_bytes": size},
            )
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
            leased_until=None,
            dead_lettered_at=None,
        )

    def write(self, session: AsyncSession, events: Iterable[DomainEvent]) -> None:
        """Stage one outbox row per event in ``session``, preserving order.

        Args:
            session: The session of the unit of work that is about to commit.
            events: The events it collected, in the order they happened.

        Raises:
            InvariantViolationError: If an event exceeds the payload cap; nothing is
                staged for it or for the events after it, and the unit of work
                rolls back.
        """
        for event in events:
            session.add(self.to_message(event))
