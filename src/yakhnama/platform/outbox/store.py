"""The ``OutboxStore`` port: the relay's bookkeeping on ``outbox_messages``.

``OutboxRelay`` decides *what* happens to a message (claim, publish, retry,
dead-letter, purge); the store only performs each change as one short transaction.
Keeping the SQL behind this port lets the lease rules be unit tested against an
in-memory Fake, while ``SqlAlchemyOutboxStore`` is tested on real PostGIS.

Every method takes the instants it needs from its caller, which reads them from the
injected ``Clock``, so the store never consults the database clock.

Patterns: Adapter (the port), DTO (the records).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.platform.outbox.models import OutboxMessage


class DeadLetteredMessage(BaseModel):
    """What the relay logs about a message it gave up on: identifiers only.

    Implements: DTO (proposed in ADR 0012).

    Attributes:
        event_id: The message's event id.
        event_type: Its routing name.
        attempts: Attempts it used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    event_type: str
    attempts: int = Field(ge=0)


class LeaseClaim(BaseModel):
    """The parameters of one claim.

    Implements: DTO (proposed in ADR 0012).

    Attributes:
        now: The claiming relay's current instant (UTC).
        lease_until: Until when the claimed rows are reserved; also the token that
            proves, when the outcome is recorded, that the lease is still ours.
        max_attempts: Rows with this many attempts are not claimed.
        batch_size: At most this many rows are claimed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    now: AwareDatetime
    lease_until: AwareDatetime
    max_attempts: int = Field(ge=1)
    batch_size: int = Field(ge=1)


class OutboxStore(Protocol):
    """Claims, updates and purges outbox rows, one short transaction per call.

    Implements: Adapter (port side).
    """

    async def claim(self, claim: LeaseClaim) -> Sequence[OutboxMessage]:
        """Lease up to ``claim.batch_size`` claimable rows and count an attempt.

        A row is claimable when it is not published, not dead-lettered, has fewer
        than ``claim.max_attempts`` attempts and is not leased (``leased_until`` is
        ``NULL`` or before ``claim.now``). Rows locked by a concurrent claim are
        skipped, never waited for. Each claimed row gets ``leased_until =
        claim.lease_until`` and ``attempts + 1``, and the transaction commits before
        this method returns.

        Args:
            claim: Instants and limits of this claim.

        Returns:
            The claimed rows, detached, oldest ``created_at`` first.
        """
        ...

    async def dead_letter_exhausted(
        self, now: datetime, max_attempts: int
    ) -> Sequence[DeadLetteredMessage]:
        """Dead-letter pending rows that have no attempt left and no live lease.

        This catches rows whose last attempt never recorded an outcome (the relay
        crashed), and rows exhausted before dead-lettering existed.

        Args:
            now: The current instant (UTC).
            max_attempts: Rows with at least this many attempts are exhausted.

        Returns:
            The rows dead-lettered by this call.
        """
        ...

    async def mark_published(self, event_id: UUID, now: datetime) -> bool:
        """Mark a row published and release its lease.

        Applies even if the lease lapsed meanwhile: every subscriber accepted the
        event, so whoever holds the row now only delivers a duplicate, which
        subscribers tolerate. A dead-letter mark is cleared, as delivery succeeded.

        Args:
            event_id: The row's id.
            now: The publication instant (UTC).

        Returns:
            ``True`` if the row was pending and is now published.
        """
        ...

    async def record_failure(
        self,
        event_id: UUID,
        lease_until: datetime,
        last_error: str,
        dead_lettered_at: datetime | None,
    ) -> bool:
        """Record a failed attempt and release the lease, if it is still ours.

        Args:
            event_id: The row's id.
            lease_until: The lease this relay set when it claimed the row; if the
                row carries another value, another relay has claimed it since and
                nothing is changed.
            last_error: ``<subscriber>: <ErrorType>`` lines, already bounded.
            dead_lettered_at: When set, the row is dead-lettered at this instant.

        Returns:
            ``True`` if the row was updated, ``False`` if the lease was lost.
        """
        ...

    async def purge_published(self, older_than: datetime) -> int:
        """Delete rows published before ``older_than``.

        Pending and dead-lettered rows are never deleted.

        Args:
            older_than: Rows with an earlier ``published_at`` are deleted.

        Returns:
            How many rows were deleted.
        """
        ...
