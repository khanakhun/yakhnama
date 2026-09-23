"""PostgreSQL adapter of the ``OutboxStore`` port.

The claim is one ``UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)
RETURNING`` statement in its own transaction: two relays claiming at the same time
lock disjoint rows, and once the claim commits the lease (not a row lock) keeps
other relays away while the subscribers run outside any transaction.

Outcomes are recorded row by row, so a relay that dies halfway through a batch loses
only the outcome of the message it was delivering.

Patterns: Adapter.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.store import DeadLetteredMessage, LeaseClaim

# Deleting in bounded batches keeps each transaction, its locks and its WAL volume
# small even when a month of messages is due at once.
PURGE_BATCH_SIZE: Final = 5000


class SqlAlchemyOutboxStore:
    """``OutboxStore`` over the ``outbox_messages`` table.

    Implements: Adapter.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the store.

        Args:
            session_factory: Opens one session per call; it must not expire objects
                on commit, because claimed rows are read after the commit.
        """
        self._session_factory = session_factory

    async def claim(self, claim: LeaseClaim) -> Sequence[OutboxMessage]:
        """Lease claimable rows; see ``OutboxStore.claim``.

        Args:
            claim: Instants and limits of this claim.

        Returns:
            The claimed rows, oldest first.
        """
        claimable = (
            select(OutboxMessage.id)
            .where(
                OutboxMessage.published_at.is_(None),
                OutboxMessage.dead_lettered_at.is_(None),
                OutboxMessage.attempts < claim.max_attempts,
                or_(
                    OutboxMessage.leased_until.is_(None),
                    OutboxMessage.leased_until < claim.now,
                ),
            )
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .limit(claim.batch_size)
            .with_for_update(skip_locked=True)
        )
        async with self._session_factory.begin() as session:
            claimed = await session.scalars(
                update(OutboxMessage)
                .where(OutboxMessage.id.in_(claimable.scalar_subquery()))
                .values(
                    attempts=OutboxMessage.attempts + 1,
                    leased_until=claim.lease_until,
                )
                .returning(OutboxMessage)
                # The rows are new to this session; there is nothing to synchronise.
                .execution_options(synchronize_session=False)
            )
            rows = list(claimed.all())
        # RETURNING has no defined order; delivery goes oldest first.
        return sorted(rows, key=lambda row: (row.created_at, row.id))

    async def dead_letter_exhausted(
        self, now: datetime, max_attempts: int
    ) -> Sequence[DeadLetteredMessage]:
        """Dead-letter exhausted rows; see ``OutboxStore.dead_letter_exhausted``.

        Args:
            now: The current instant (UTC).
            max_attempts: Rows with at least this many attempts are exhausted.

        Returns:
            The rows dead-lettered by this call.
        """
        async with self._session_factory.begin() as session:
            exhausted = await session.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.published_at.is_(None),
                    OutboxMessage.dead_lettered_at.is_(None),
                    OutboxMessage.attempts >= max_attempts,
                    or_(
                        OutboxMessage.leased_until.is_(None),
                        OutboxMessage.leased_until < now,
                    ),
                )
                .values(dead_lettered_at=now, leased_until=None)
                .returning(
                    OutboxMessage.id, OutboxMessage.event_type, OutboxMessage.attempts
                )
                .execution_options(synchronize_session=False)
            )
            return [
                DeadLetteredMessage(
                    event_id=event_id, event_type=event_type, attempts=attempts
                )
                for event_id, event_type, attempts in exhausted.tuples()
            ]

    async def mark_published(self, event_id: UUID, now: datetime) -> bool:
        """Mark a row published; see ``OutboxStore.mark_published``.

        Args:
            event_id: The row's id.
            now: The publication instant (UTC).

        Returns:
            ``True`` if the row was pending and is now published.
        """
        async with self._session_factory.begin() as session:
            updated = await session.scalar(
                update(OutboxMessage)
                .where(
                    OutboxMessage.id == event_id,
                    OutboxMessage.published_at.is_(None),
                )
                .values(published_at=now, leased_until=None, dead_lettered_at=None)
                .returning(OutboxMessage.id)
                .execution_options(synchronize_session=False)
            )
            return updated is not None

    async def record_failure(
        self,
        event_id: UUID,
        lease_until: datetime,
        last_error: str,
        dead_lettered_at: datetime | None,
    ) -> bool:
        """Record a failed attempt; see ``OutboxStore.record_failure``.

        Args:
            event_id: The row's id.
            lease_until: The lease this relay set when it claimed the row.
            last_error: ``<subscriber>: <ErrorType>`` lines, already bounded.
            dead_lettered_at: When set, the row is dead-lettered at this instant.

        Returns:
            ``True`` if the row was updated, ``False`` if the lease was lost.
        """
        async with self._session_factory.begin() as session:
            updated = await session.scalar(
                update(OutboxMessage)
                .where(
                    OutboxMessage.id == event_id,
                    OutboxMessage.published_at.is_(None),
                    OutboxMessage.leased_until == lease_until,
                )
                .values(
                    last_error=last_error,
                    leased_until=None,
                    dead_lettered_at=dead_lettered_at,
                )
                .returning(OutboxMessage.id)
                .execution_options(synchronize_session=False)
            )
            return updated is not None

    async def purge_published(self, older_than: datetime) -> int:
        """Delete old published rows; see ``OutboxStore.purge_published``.

        Args:
            older_than: Rows with an earlier ``published_at`` are deleted.

        Returns:
            How many rows were deleted.
        """
        deleted_total = 0
        while True:
            deleted = await self._purge_batch(older_than)
            deleted_total += deleted
            if deleted < PURGE_BATCH_SIZE:
                return deleted_total

    async def _purge_batch(self, older_than: datetime) -> int:
        batch = (
            select(OutboxMessage.id)
            .where(
                OutboxMessage.published_at.is_not(None),
                OutboxMessage.published_at < older_than,
            )
            .limit(PURGE_BATCH_SIZE)
            # A row a relay is updating right now is left for the next batch.
            .with_for_update(skip_locked=True)
        )
        async with self._session_factory.begin() as session:
            deleted = await session.scalars(
                delete(OutboxMessage)
                .where(OutboxMessage.id.in_(batch.scalar_subquery()))
                .returning(OutboxMessage.id)
                .execution_options(synchronize_session=False)
            )
            return len(deleted.all())
