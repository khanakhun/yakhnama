"""An in-memory ``OutboxStore`` with the same rules as ``SqlAlchemyOutboxStore``.

The SQL adapter is covered on real PostGIS by
``tests/integration/platform/test_outbox_relay_postgis.py``; this Fake lets the relay's
lease, retry and dead-letter decisions be tested without a database.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.store import DeadLetteredMessage, LeaseClaim


def _is_unleased(row: OutboxMessage, now: datetime) -> bool:
    return row.leased_until is None or row.leased_until < now


def _is_pending(row: OutboxMessage) -> bool:
    return row.published_at is None and row.dead_lettered_at is None


class InMemoryOutboxStore:
    """``OutboxStore`` over a dictionary of rows.

    Implements: Fake.

    Attributes:
        rows: The stored rows by id; tests add and inspect them directly.
        claims: Every ``LeaseClaim`` received, in order.
        failure_on_mark: Raised by ``mark_published`` when set, to simulate the
            database failing after delivery.
    """

    def __init__(self, rows: Sequence[OutboxMessage] = ()) -> None:
        """Create the store holding ``rows``."""
        self.rows: dict[UUID, OutboxMessage] = {row.id: row for row in rows}
        self.claims: list[LeaseClaim] = []
        self.failure_on_mark: Exception | None = None

    def add(self, row: OutboxMessage) -> None:
        """Store ``row`` as the writer would have."""
        self.rows[row.id] = row

    async def claim(self, claim: LeaseClaim) -> Sequence[OutboxMessage]:
        """Lease claimable rows, oldest first; see ``OutboxStore.claim``."""
        self.claims.append(claim)
        claimable = sorted(
            (
                row
                for row in self.rows.values()
                if _is_pending(row)
                and row.attempts < claim.max_attempts
                and _is_unleased(row, claim.now)
            ),
            key=lambda row: (row.created_at, row.id),
        )[: claim.batch_size]
        for row in claimable:
            row.attempts += 1
            row.leased_until = claim.lease_until
        return claimable

    async def dead_letter_exhausted(
        self, now: datetime, max_attempts: int
    ) -> Sequence[DeadLetteredMessage]:
        """Dead-letter exhausted, unleased rows."""
        exhausted = [
            row
            for row in self.rows.values()
            if _is_pending(row)
            and row.attempts >= max_attempts
            and _is_unleased(row, now)
        ]
        for row in exhausted:
            row.dead_lettered_at = now
            row.leased_until = None
        return [
            DeadLetteredMessage(
                event_id=row.id, event_type=row.event_type, attempts=row.attempts
            )
            for row in exhausted
        ]

    async def mark_published(self, event_id: UUID, now: datetime) -> bool:
        """Publish a pending row and release its lease."""
        if self.failure_on_mark is not None:
            raise self.failure_on_mark
        row = self.rows.get(event_id)
        if row is None or row.published_at is not None:
            return False
        row.published_at = now
        row.leased_until = None
        row.dead_lettered_at = None
        return True

    async def record_failure(
        self,
        event_id: UUID,
        lease_until: datetime,
        last_error: str,
        dead_lettered_at: datetime | None,
    ) -> bool:
        """Record a failure if the lease is still ``lease_until``."""
        row = self.rows.get(event_id)
        if row is None or row.published_at is not None:
            return False
        if row.leased_until != lease_until:
            return False
        row.last_error = last_error
        row.leased_until = None
        row.dead_lettered_at = dead_lettered_at
        return True

    async def purge_published(self, older_than: datetime) -> int:
        """Delete rows published before ``older_than``."""
        expired = [
            row.id
            for row in self.rows.values()
            if row.published_at is not None and row.published_at < older_than
        ]
        for event_id in expired:
            del self.rows[event_id]
        return len(expired)
