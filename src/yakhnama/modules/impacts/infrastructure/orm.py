"""SQLAlchemy row models of the ``impacts`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``ImpactMetric`` aggregate. Structured values (labels, description,
Sendai and DesInventar alignment, retirement) are stored as JSONB holding exactly the
JSON form of their Pydantic value objects (``model_dump(mode="json")``); the mapper
validates them back on every read, so a ``dict`` never leaves this package.

The registry itself is small (one row per metric); impact *values* are stored long and
narrow in Phase 3 claims, one row per metric and value (ADR 0002).

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

IMPACT_METRICS_TABLE: Final = "impact_metrics"

# JSONB columns are annotated dict[str, object] inline rather than through a type
# alias, because SQLAlchemy derives nullability from the annotation and does not
# unwrap PEP 695 aliases for that. Only the mappers build or read these values.


class ImpactMetricRow(Base):
    """Row model of the ``impact_metrics`` table: one row per ``ImpactMetric``.

    Implements: Adapter (ORM row model of ``SqlAlchemyImpactMetricRepository``).

    Attributes:
        id: Primary key, the aggregate id (UUIDv7).
        code: Stable metric code, unique, never reused or deleted.
        labels: ``LocalizedText`` as JSON.
        description: ``LocalizedText`` as JSON, if written.
        category: ``MetricCategory`` value.
        value_kind: ``ValueKind`` value.
        unit: Unit name; ``NULL`` for monetary metrics.
        currency: ISO 4217 code; set only for monetary metrics.
        sendai: ``SendaiIndicator`` as JSON, if mapped.
        desinventar: ``DesInventarField`` as JSON, if mapped.
        aggregation: ``sum``, ``max`` or ``latest``.
        status: ``active`` or ``retired``.
        retirement: ``RetirementReason`` as JSON; set exactly when retired.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the metric was created, UTC.
        updated_at: When the metric last changed, UTC.
    """

    __tablename__ = IMPACT_METRICS_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    labels: Mapped[dict[str, object]] = mapped_column(JSONB)
    description: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    category: Mapped[str] = mapped_column(String(32), index=True)
    value_kind: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(3))
    sendai: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    desinventar: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    aggregation: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    retirement: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
