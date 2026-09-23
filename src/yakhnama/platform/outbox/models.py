"""The ``outbox_messages`` table.

One row per domain event. The row id is the event's own ``event_id`` (a UUIDv7,
ADR 0006), so recording the same event twice fails on the primary key instead of
publishing it twice, and subscribers can de-duplicate by the same id.

The Alembic migration that creates the table (``0001_extensions_and_outbox``, task T4)
must match this model exactly; ``alembic check`` enforces it.

Patterns: Transactional Outbox.
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base
from yakhnama.shared_kernel.events import EVENT_TYPE_MAX_LENGTH

OUTBOX_TABLE_NAME: Final = "outbox_messages"
AGGREGATE_TYPE_MAX_LENGTH: Final = 100


class OutboxMessage(Base):
    """One domain event waiting for, or done with, delivery to its subscribers.

    Implements: Transactional Outbox.

    Attributes:
        id: The event's ``event_id``.
        aggregate_type: Snake-case aggregate name, for example ``"hazard_type"``.
        aggregate_id: Identifier of the aggregate that changed.
        event_type: Routing name ``<bounded_context>.<snake_case_name>``.
        payload: The complete event as JSON (``DomainEvent.model_dump(mode="json")``),
            so a subscriber can rebuild the typed event from it alone.
        occurred_at: When the change happened (UTC, from the injected ``Clock``).
        created_at: When the row was written (UTC); the relay's delivery order.
        published_at: When every subscriber accepted the event; ``NULL`` while
            pending.
        attempts: Failed delivery attempts so far.
        last_error: ``<subscriber>: <ErrorType>`` for each subscriber that failed in
            the most recent attempt; never the error message, which may quote data.
    """

    __tablename__ = OUTBOX_TABLE_NAME
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The relay's claim query filters on published_at IS NULL and orders by
        # created_at; this composite index serves both.
        Index(
            "ix_outbox_messages_published_at_created_at", "published_at", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(AGGREGATE_TYPE_MAX_LENGTH))
    aggregate_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(EVENT_TYPE_MAX_LENGTH))
    # JsonValue at the persistence boundary: the payload is the serialised event and
    # is only ever turned back into a typed DomainEvent by OutboxEnvelope.to_event.
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime]
    created_at: Mapped[datetime]
    published_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
