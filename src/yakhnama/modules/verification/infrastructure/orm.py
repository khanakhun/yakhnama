"""SQLAlchemy row model of the ``verification`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``VerificationCase`` aggregate. ``history`` is a JSONB array holding
exactly the JSON dump of each ``Transition``, oldest first; the aggregate re-checks
the whole history against the state table on every read, so a row with an
impossible history fails loudly instead of loading.

A target has at most one case for life: ``(target_kind, target_id)`` is unique, so
two moderators opening a case for the same record at once cannot both succeed. The
target id carries no foreign key, because reports, events and claims belong to
other modules. The ``events`` read model joins this table by ``(target_kind,
target_id)`` in SQL (see ``yakhnama.modules.events.infrastructure.queries``); the
column names used there are part of this table's contract.

Patterns: Adapter (ORM row model behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

VERIFICATION_CASES_TABLE: Final = "verification_cases"


class VerificationCaseRow(Base):
    """Row model of the ``verification_cases`` table: one row per case.

    Implements: Adapter (ORM row model of ``SqlAlchemyVerificationCaseRepository``).

    Attributes:
        id: Primary key, the case id (UUIDv7).
        target_kind: ``TargetKind`` value: report, event or claim.
        target_id: The record under verification.
        initial_state: The state the case was opened in.
        state: The current ``VerificationState`` value.
        history: Every ``Transition`` as a JSON array, oldest first.
        assigned_to: The responsible reviewer, if any.
        opened_by: Who opened the case.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the case was opened, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = VERIFICATION_CASES_TABLE
    __table_args__ = (
        # Named uq_verification_cases_target_kind by the convention.
        UniqueConstraint("target_kind", "target_id"),
        # Serves the oldest-first keyset listing.
        Index("ix_verification_cases_created_at_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[UUID]
    initial_state: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), index=True)
    history: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    assigned_to: Mapped[UUID | None] = mapped_column(index=True)
    opened_by: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
