"""SQLAlchemy row models of the ``provenance`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``Source`` aggregate, and nothing outside this package sees them.

``licence`` is a JSONB object holding exactly ``Licence.model_dump(mode="json")``
and is validated back through the domain on every read. ``retrieved_at`` and
``retrieved_at_precision`` together hold one ``DateWithPrecision``; a check
constraint keeps them both set or both ``NULL``.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

SOURCES_TABLE: Final = "sources"


class SourceRow(Base):
    """Row model of the ``sources`` table: one row per ``Source`` aggregate.

    ``owner_actor_id`` and ``organization_id`` carry no foreign key on purpose: the
    users and organisations belong to the ``identity`` module, and modules share no
    schema-level coupling (a module only knows another through its facade).

    Implements: Adapter (ORM row model of ``SqlAlchemySourceRepository``).

    Attributes:
        id: Primary key, the aggregate id (UUIDv7).
        source_type: ``SourceType`` value.
        title: Short title.
        citation: How to cite the source.
        url: ``http`` or ``https`` URL, if any.
        licence: ``Licence`` as a JSON object, if known.
        retrieved_at: Start of the retrieval-time period, UTC, if known.
        retrieved_at_precision: ``DatePrecision`` of ``retrieved_at``.
        publisher: Who published it, if known.
        language: BCP 47 language code of the content, if known.
        owner_actor_id: The registering user; ``NULL`` for the system.
        organization_id: The organisation it was registered for, if any.
        is_referenced: Whether any fact cites it (it is then immutable).
        version: Optimistic-concurrency version, compared on every update.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    __tablename__ = SOURCES_TABLE
    __table_args__ = (
        CheckConstraint(
            "(retrieved_at IS NULL) = (retrieved_at_precision IS NULL)",
            name="retrieved_at_with_precision",
        ),
        # Serves the newest-first keyset listing (read backwards).
        Index("ix_sources_created_at_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    # Lengths follow the domain bounds: SOURCE_TITLE_MAX_LENGTH,
    # CITATION_MAX_LENGTH, SOURCE_URL_MAX_LENGTH and PUBLISHER_MAX_LENGTH.
    title: Mapped[str] = mapped_column(String(300))
    citation: Mapped[str] = mapped_column(String(1000))
    url: Mapped[str | None] = mapped_column(String(2048))
    licence: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    retrieved_at: Mapped[datetime | None]
    retrieved_at_precision: Mapped[str | None] = mapped_column(String(16))
    publisher: Mapped[str | None] = mapped_column(String(200))
    # The kernel's language code is at most eight characters.
    language: Mapped[str | None] = mapped_column(String(8))
    owner_actor_id: Mapped[UUID | None] = mapped_column(index=True)
    organization_id: Mapped[UUID | None]
    is_referenced: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
