"""Delivers pending outbox messages to in-process subscribers.

Delivery semantics (ADR 0007, Phase 3 plan §2):

- **Lease-based claiming.** A run first claims a batch in one short transaction
  (``SELECT ... FOR UPDATE SKIP LOCKED``): each row gets ``leased_until = now +
  lease`` and ``attempts + 1``, and the transaction commits. The subscribers then run
  outside any transaction and lock, so a slow subscriber holds no database
  connection. Another relay skips a leased row until the lease lapses, so two relays
  never deliver the same message at the same time while both are healthy; a relay
  that crashed simply lets its leases lapse, and the rows are claimed again.
- **At least once.** A message is marked published only after every subscriber for
  its ``event_type`` returned without raising. A crash in between, one failing
  subscriber, or a subscriber slower than the lease means the message is delivered
  again later, including to subscribers that already succeeded. Every subscriber
  must therefore be idempotent, keyed by ``OutboxEnvelope.event_id``.
- **Timeouts.** Each subscriber call is cancelled after ``subscriber_timeout_seconds``
  and counts as a failure (``<subscriber>: TimeoutError``).
- **Retries and dead-letter.** A failure records ``last_error`` (who failed and the
  error type, never the message) and releases the lease, so the next run retries
  it. When the attempt that failed was the ``max_attempts``-th, the row gets
  ``dead_lettered_at`` and an ``outbox_dead_lettered`` warning is logged with
  identifiers only; it is never claimed again until an operator clears the column.
  Rows whose last attempt never recorded an outcome are dead-lettered by the next
  run once their lease lapses. There is no back-off yet: a failed row is retried on
  the next run.
- **Order.** Rows are claimed oldest ``created_at`` first. A failed message is retried
  on a later run while newer messages continue, so there is no strict per-aggregate
  ordering; subscribers must not rely on one.
- **No subscribers.** A message whose ``event_type`` has no subscriber is marked
  published: nothing is waiting for it, and the row itself remains as the record.
- **Retention.** ``purge_published`` deletes published rows older than a cut-off;
  pending and dead-lettered rows are kept.

Patterns: Transactional Outbox, Observer, Registry.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

import structlog
from pydantic import BaseModel, ConfigDict, Field

from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.store import (
    DeadLetteredMessage,
    LeaseClaim,
    OutboxStore,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ConflictError, ValidationError
from yakhnama.shared_kernel.events import is_event_type

type Subscriber = Callable[[OutboxEnvelope], Awaitable[None]]

# Proposed operational defaults, not domain facts; the deployed values come from
# ``Settings`` (``outbox_*``) and are open questions until the maintainer confirms
# them.
DEFAULT_MAX_ATTEMPTS: Final = 5
DEFAULT_LEASE_SECONDS: Final = 120
DEFAULT_SUBSCRIBER_TIMEOUT_SECONDS: Final = 30
MAX_BATCH_SIZE: Final = 1000
# Bounded because a batch with many subscribers could otherwise build a long line.
LAST_ERROR_MAX_LENGTH: Final = 2000


class SubscriberRegistry:
    """Maps each ``event_type`` to the subscribers that react to it.

    Modules register their subscribers in the composition root. Registering the same
    subscriber twice for one ``event_type`` is refused, because it would receive every
    event twice.

    Implements: Observer + Registry.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._subscribers: dict[str, list[Subscriber]] = {}

    def subscribe(self, event_type: str, subscriber: Subscriber) -> None:
        """Register ``subscriber`` for every future event of ``event_type``.

        Args:
            event_type: Routing name, ``<bounded_context>.<snake_case_name>``.
            subscriber: Async callable receiving an ``OutboxEnvelope``.

        Raises:
            ValidationError: If ``event_type`` is malformed.
            ConflictError: If ``subscriber`` is already registered for it.
        """
        if not is_event_type(event_type):
            message = f"malformed event_type {event_type!r}"
            raise ValidationError(message)
        subscribers = self._subscribers.setdefault(event_type, [])
        if subscriber in subscribers:
            message = f"subscriber already registered for {event_type}"
            raise ConflictError(message)
        subscribers.append(subscriber)

    def subscribers_for(self, event_type: str) -> tuple[Subscriber, ...]:
        """Return the subscribers for ``event_type`` in registration order.

        Args:
            event_type: Routing name of an outbox message.

        Returns:
            The registered subscribers; empty if there are none.
        """
        return tuple(self._subscribers.get(event_type, ()))


class RelayOutcome(BaseModel):
    """Counts from one relay run.

    Implements: DTO (proposed in ADR 0012).

    Attributes:
        claimed: Messages claimed in this run.
        published: Messages every subscriber accepted.
        failed: Messages at least one subscriber rejected.
        dead_lettered: Messages given up on in this run, whether their last attempt
            failed now or crashed an earlier run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claimed: int = Field(ge=0)
    published: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_lettered: int = Field(default=0, ge=0)


class OutboxRelay:
    """Claims pending outbox messages and dispatches them to their subscribers.

    Implements: Transactional Outbox.
    """

    def __init__(  # noqa: PLR0913  # reason: each keyword is one documented, independently configured delivery bound
        self,
        registry: SubscriberRegistry,
        clock: Clock,
        store: OutboxStore,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        subscriber_timeout_seconds: float = DEFAULT_SUBSCRIBER_TIMEOUT_SECONDS,
    ) -> None:
        """Create the relay.

        Args:
            registry: Source of subscribers per ``event_type``.
            clock: Supplies every instant: leases, ``published_at``, dead-letters.
            store: Performs the bookkeeping on ``outbox_messages``.
            max_attempts: Attempts after which a message is dead-lettered.
            lease_seconds: How long a claimed message stays reserved.
            subscriber_timeout_seconds: Upper bound for one subscriber call.

        Raises:
            ValidationError: If a bound is not positive, or the lease is shorter
                than one subscriber call.
        """
        if max_attempts < 1:
            message = f"max_attempts must be at least 1, got {max_attempts}"
            raise ValidationError(message)
        if subscriber_timeout_seconds <= 0:
            message = "subscriber_timeout_seconds must be positive"
            raise ValidationError(message)
        if lease_seconds < subscriber_timeout_seconds:
            message = "lease_seconds must be at least subscriber_timeout_seconds"
            raise ValidationError(message)
        self._registry = registry
        self._clock = clock
        self._store = store
        self._max_attempts = max_attempts
        self._lease = timedelta(seconds=lease_seconds)
        self._subscriber_timeout_seconds = subscriber_timeout_seconds

    async def relay_once(self, batch_size: int) -> RelayOutcome:
        """Claim up to ``batch_size`` messages, deliver each and record the outcome.

        Args:
            batch_size: Maximum number of messages to claim, 1 to ``MAX_BATCH_SIZE``.

        Returns:
            How many messages were claimed, published, failed and dead-lettered.

        Raises:
            ValidationError: If ``batch_size`` is out of range.
        """
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            detail = f"batch_size must be between 1 and {MAX_BATCH_SIZE}"
            raise ValidationError(detail)
        now = self._clock.now()
        abandoned = await self._store.dead_letter_exhausted(now, self._max_attempts)
        for dead in abandoned:
            self._log_dead_letter(dead)
        claim = LeaseClaim(
            now=now,
            lease_until=now + self._lease,
            max_attempts=self._max_attempts,
            batch_size=batch_size,
        )
        messages = await self._store.claim(claim)
        published = dead_lettered = 0
        for message in messages:
            errors = await self._dispatch(message)
            if not errors:
                await self._store.mark_published(message.id, self._clock.now())
                published += 1
            elif await self._record_failure(message, claim.lease_until, errors):
                dead_lettered += 1
        return RelayOutcome(
            claimed=len(messages),
            published=published,
            failed=len(messages) - published,
            dead_lettered=len(abandoned) + dead_lettered,
        )

    async def purge_published(self, older_than: datetime) -> int:
        """Delete messages published before ``older_than``.

        Args:
            older_than: The retention cut-off (UTC).

        Returns:
            How many messages were deleted.

        Raises:
            ValidationError: If ``older_than`` is naive.
        """
        if older_than.utcoffset() is None:
            detail = "older_than must be timezone-aware"
            raise ValidationError(detail)
        deleted = await self._store.purge_published(older_than)
        structlog.get_logger(__name__).info("outbox_purged", deleted=deleted)
        return deleted

    async def _record_failure(
        self, message: OutboxMessage, lease_until: datetime, errors: list[str]
    ) -> bool:
        """Record one failed attempt; return whether the message was dead-lettered."""
        # ``attempts`` already counts this attempt: the claim incremented it.
        is_exhausted = message.attempts >= self._max_attempts
        recorded = await self._store.record_failure(
            message.id,
            lease_until,
            "; ".join(errors)[:LAST_ERROR_MAX_LENGTH],
            self._clock.now() if is_exhausted else None,
        )
        if not recorded:
            # Another relay claimed the row after our lease lapsed; its outcome
            # decides, and ours is dropped rather than overwriting it.
            structlog.get_logger(__name__).warning(
                "outbox_lease_lost",
                event_id=str(message.id),
                event_type=message.event_type,
            )
            return False
        if is_exhausted:
            self._log_dead_letter(
                DeadLetteredMessage(
                    event_id=message.id,
                    event_type=message.event_type,
                    attempts=message.attempts,
                )
            )
        return is_exhausted

    async def _dispatch(self, message: OutboxMessage) -> list[str]:
        """Call every subscriber for ``message``; return one line per failure."""
        try:
            envelope = OutboxEnvelope.from_message(message)
        # A corrupt payload is a delivery failure like any other: record it and move
        # on so that one bad row cannot stall the batch.
        except ValueError as error:
            self._log_failure(message, "envelope", error)
            return [f"envelope: {type(error).__name__}"]
        errors: list[str] = []
        for subscriber in self._registry.subscribers_for(message.event_type):
            name = getattr(subscriber, "__qualname__", type(subscriber).__qualname__)
            try:
                await asyncio.wait_for(
                    subscriber(envelope), timeout=self._subscriber_timeout_seconds
                )
            # Subscribers are arbitrary module code; any failure, a timeout included,
            # must be recorded on the row and retried, never allowed to abort the
            # other messages.
            except Exception as error:  # noqa: BLE001  # reason: failure is recorded in last_error and logged, not swallowed
                self._log_failure(message, name, error)
                errors.append(f"{name}: {type(error).__name__}")
        return errors

    @staticmethod
    def _log_dead_letter(dead: DeadLetteredMessage) -> None:
        # Identifiers only: the payload may carry report content (AGENTS.md §5).
        structlog.get_logger(__name__).warning(
            "outbox_dead_lettered",
            event_id=str(dead.event_id),
            event_type=dead.event_type,
            attempts=dead.attempts,
        )

    # ``last_error`` records only who failed and the error type, never ``str(error)``:
    # driver messages quote column values (asyncpg's DETAIL line), pydantic messages
    # carry input fragments, and a NUL character in free text would make the UPDATE
    # fail and leave the row leased until the lease lapses.
    @staticmethod
    def _log_failure(message: OutboxMessage, subscriber: str, error: Exception) -> None:
        # Only identifiers and the error type are logged: the error message and the
        # payload may carry report content (AGENTS.md §5).
        # Fetched per call, not held in a module global: with cache_logger_on_first_use
        # a global proxy keeps the processor chain it first saw, so a later
        # configure_logging (another app, a test) would never reach it.
        structlog.get_logger(__name__).warning(
            "outbox_delivery_failed",
            event_id=str(message.id),
            event_type=message.event_type,
            subscriber=subscriber,
            error_type=type(error).__name__,
        )
