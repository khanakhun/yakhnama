"""Delivers pending outbox messages to in-process subscribers.

Delivery semantics (ADR 0007):

- **At least once.** A message is marked published only after every subscriber for its
  ``event_type`` returned without raising, and the mark is committed after the
  subscribers ran. A crash in between, or one failing subscriber, means the message is
  delivered again later, including to subscribers that already succeeded. Every
  subscriber must therefore be idempotent, keyed by ``OutboxEnvelope.event_id``.
- **Concurrent relays are safe.** Rows are claimed with ``SELECT ... FOR UPDATE SKIP
  LOCKED`` inside one transaction, so two relays never deliver the same row at the
  same time; each skips the rows the other holds.
- **Order.** Rows are claimed oldest ``created_at`` first. A failed message is retried
  on a later run while newer messages continue, so there is no strict per-aggregate
  ordering; subscribers must not rely on one.
- **Retries.** A failure increments ``attempts`` and stores ``last_error``. After
  ``max_attempts`` failures a message is no longer claimed and waits for manual or
  dead-letter handling (the dead-letter process, back-off and retention are Phase 3
  work, with the task queue that triggers ``relay_once``).
- **No subscribers.** A message whose ``event_type`` has no subscriber is marked
  published: nothing is waiting for it, and the row itself remains as the record.

Subscribers run while the relay holds the row locks, so they must be short; a
subscriber that needs its own database work opens its own unit of work.

Patterns: Transactional Outbox, Observer, Registry.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Final

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ConflictError, ValidationError
from yakhnama.shared_kernel.events import is_event_type

type Subscriber = Callable[[OutboxEnvelope], Awaitable[None]]
type SessionFactory = Callable[[], AsyncSession]

# Proposed operational defaults, not domain facts; recorded as an open question in the
# Phase 1 report until the maintainer confirms them.
DEFAULT_MAX_ATTEMPTS: Final = 5
MAX_BATCH_SIZE: Final = 1000
# Bounded so a subscriber raising with a huge message cannot bloat the table.
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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claimed: int = Field(ge=0)
    published: int = Field(ge=0)
    failed: int = Field(ge=0)


class OutboxRelay:
    """Claims pending outbox messages and dispatches them to their subscribers.

    Implements: Transactional Outbox.
    """

    def __init__(
        self,
        registry: SubscriberRegistry,
        clock: Clock,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        """Create the relay.

        Args:
            registry: Source of subscribers per ``event_type``.
            clock: Supplies ``published_at``.
            max_attempts: Failed attempts after which a message is no longer claimed.

        Raises:
            ValidationError: If ``max_attempts`` is less than 1.
        """
        if max_attempts < 1:
            message = f"max_attempts must be at least 1, got {max_attempts}"
            raise ValidationError(message)
        self._registry = registry
        self._clock = clock
        self._max_attempts = max_attempts

    async def relay_once(
        self, session_factory: SessionFactory, batch_size: int
    ) -> RelayOutcome:
        """Claim up to ``batch_size`` pending messages, deliver them and commit.

        Args:
            session_factory: Opens the session for this run's transaction.
            batch_size: Maximum number of messages to claim, 1 to ``MAX_BATCH_SIZE``.

        Returns:
            How many messages were claimed, published and failed.

        Raises:
            ValidationError: If ``batch_size`` is out of range.
        """
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            message = f"batch_size must be between 1 and {MAX_BATCH_SIZE}"
            raise ValidationError(message)
        async with session_factory() as session, session.begin():
            claimed = await session.scalars(
                select(OutboxMessage)
                .where(
                    OutboxMessage.published_at.is_(None),
                    OutboxMessage.attempts < self._max_attempts,
                )
                .order_by(OutboxMessage.created_at, OutboxMessage.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            return await self.deliver(claimed.all())

    async def deliver(self, messages: Sequence[OutboxMessage]) -> RelayOutcome:
        """Dispatch each message and record the outcome on the row itself.

        The caller owns the transaction; ``relay_once`` commits it afterwards.

        Args:
            messages: Rows claimed (locked) by the caller.

        Returns:
            How many messages were handled, published and failed.
        """
        published = 0
        for message in messages:
            errors = await self._dispatch(message)
            if errors:
                message.attempts += 1
                message.last_error = "; ".join(errors)[:LAST_ERROR_MAX_LENGTH]
            else:
                message.published_at = self._clock.now()
                published += 1
        return RelayOutcome(
            claimed=len(messages),
            published=published,
            failed=len(messages) - published,
        )

    async def _dispatch(self, message: OutboxMessage) -> list[str]:
        """Call every subscriber for ``message``; return one line per failure."""
        try:
            envelope = OutboxEnvelope.from_message(message)
        # A corrupt payload is a delivery failure like any other: record it and move
        # on so that one bad row cannot stall the batch.
        except ValueError as error:
            self._log_failure(message, "envelope", error)
            return [f"envelope: {type(error).__name__}: {error}"]
        errors: list[str] = []
        for subscriber in self._registry.subscribers_for(message.event_type):
            name = getattr(subscriber, "__qualname__", type(subscriber).__qualname__)
            try:
                await subscriber(envelope)
            # Subscribers are arbitrary module code; any failure must be recorded on
            # the row and retried, never allowed to abort the other messages.
            except Exception as error:  # noqa: BLE001  # reason: failure is recorded in last_error and logged, not swallowed
                self._log_failure(message, name, error)
                errors.append(f"{name}: {type(error).__name__}: {error}")
        return errors

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
