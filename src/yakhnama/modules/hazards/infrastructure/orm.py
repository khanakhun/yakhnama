"""SQLAlchemy row models of the ``hazards`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``HazardType`` aggregate. Structured values (labels, description,
IRDR alignment, retirement) are stored as JSONB holding exactly the JSON form of
their Pydantic value objects (``model_dump(mode="json")``); the mapper validates them
back into those value objects on every read, so a ``dict`` never leaves this package.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

HAZARD_TYPES_TABLE: Final = "hazard_types"

# JSONB columns are annotated dict[str, object] inline rather than through a type
# alias, because SQLAlchemy derives nullability from the annotation and does not
# unwrap PEP 695 aliases for that. Only the mappers build or read these values.


class HazardTypeRow(Base):
    """Row model of the ``hazard_types`` table: one row per ``HazardType``.

    Implements: Adapter (ORM row model of ``SqlAlchemyHazardTypeRepository``).

    Attributes:
        id: Primary key, the aggregate id (UUIDv7).
        code: Stable hazard code, unique, never reused or deleted.
        parent_code: Code of the broader type; ``NULL`` for a root.
        labels: ``LocalizedText`` as JSON.
        description: ``LocalizedText`` as JSON, if written.
        alignment: ``IrdrAlignment`` as JSON.
        attributes_schema: Registry code of the attribute schema, if any.
        status: ``active`` or ``retired``.
        retirement: ``RetirementReason`` as JSON; set exactly when retired.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the type was created, UTC.
        updated_at: When the type last changed, UTC.
    """

    __tablename__ = HAZARD_TYPES_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    # The foreign key targets the unique code, not the id, because the domain links
    # parents by code; codes never change, so no ON UPDATE rule is needed.
    parent_code: Mapped[str | None] = mapped_column(
        ForeignKey("hazard_types.code", ondelete="RESTRICT"), index=True
    )
    labels: Mapped[dict[str, object]] = mapped_column(JSONB)
    description: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    alignment: Mapped[dict[str, object]] = mapped_column(JSONB)
    attributes_schema: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    retirement: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
