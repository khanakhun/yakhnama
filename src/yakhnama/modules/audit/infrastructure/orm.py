"""SQLAlchemy row model of the ``audit`` bounded context.

``audit_entries`` is append-only. The application can only insert (the repository
has no update or delete), and migration 0009 adds a database-level guard, a
``BEFORE UPDATE OR DELETE`` row trigger that raises, so no other code path or ad-hoc
session can change or remove an entry either (**proposed**, see the migration). The
guard is not part of this metadata because Alembic does not compare triggers.

Every column holds an id, a code or a digest; no free text is ever stored here.

Patterns: Adapter (ORM row model behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

AUDIT_ENTRIES_TABLE: Final = "audit_entries"


class AuditEntryRow(Base):
    """Row model of the ``audit_entries`` table: one row per ``AuditEntry``.

    Implements: Adapter (ORM row model of ``SqlAlchemyAuditEntryRepository``).

    Attributes:
        id: Primary key, the entry id (UUIDv7).
        event_id: The recorded domain event; unique, so a redelivery is stored once.
        occurred_at: When the recorded change happened, UTC.
        recorded_at: When the row was written, set by the database (``now()``).
        actor_id: The user who caused the change; ``NULL`` for the system.
        actor_kind: ``user`` or ``system``.
        action: What happened, normally the event type.
        target_type: Snake-case type of the audited aggregate.
        target_id: The audited aggregate's id.
        before_digest: Digest of the state before the change, if known.
        after_digest: Digest of the state after the change, if known.
        payload_digest: Digest of the event's canonical JSON.
        request_id: Correlation id of the causing request or task, if any.
    """

    __tablename__ = AUDIT_ENTRIES_TABLE
    __table_args__ = (
        # Ascending on purpose: a B-tree is read backwards for the newest-first
        # listings just as fast, and plain column indexes compare cleanly in
        # ``alembic check`` (expression indexes such as ``occurred_at DESC`` do not).
        # ``id`` is the keyset tie-breaker.
        Index(
            "ix_audit_entries_target_type_target_id_occurred_at",
            "target_type",
            "target_id",
            "occurred_at",
            "id",
        ),
        Index("ix_audit_entries_occurred_at_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(unique=True)
    occurred_at: Mapped[datetime]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_id: Mapped[UUID | None]
    actor_kind: Mapped[str] = mapped_column(String(8))
    # Lengths follow the domain patterns: AUDIT_ACTION_PATTERN (at most 64),
    # TARGET_TYPE_MAX_LENGTH, DIGEST_PATTERN ("sha256:" plus 64 digits) and
    # REQUEST_ID_MAX_LENGTH.
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[UUID]
    before_digest: Mapped[str | None] = mapped_column(String(71))
    after_digest: Mapped[str | None] = mapped_column(String(71))
    payload_digest: Mapped[str] = mapped_column(String(71))
    request_id: Mapped[str | None] = mapped_column(String(128))
