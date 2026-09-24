"""SQLAlchemy row model of the ``reports`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``Report`` aggregate, and nothing outside this package sees them.

``observation`` is the reporter's **exact, private** position as a WGS84 point
(EPSG:4326, ADR 0002) with ``spatial_index=False`` and an explicit, named GiST index;
public reads round it in SQL (``queries.rounded_degrees``). ``media_ids`` is a JSONB
array of UUID strings in the reporter's order and ``triage`` a JSONB object holding
exactly ``TriageResult.model_dump(mode="json")``; both are validated back through the
domain on every read.

The revision chain is kept inside the table: ``supersedes_id`` and
``superseded_by_id`` reference ``reports.id`` (``ON DELETE RESTRICT``, and reports are
never deleted), and ``supersedes_id`` is unique, so at most one revision can ever
replace a report even if two corrections race. The reporter, organisation and source
ids carry no foreign key: they belong to the ``identity`` and ``provenance`` modules,
which this module knows only through their facades.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

REPORTS_TABLE: Final = "reports"


class ReportRow(Base):
    """Row model of the ``reports`` table: one row per ``Report`` revision.

    Implements: Adapter (ORM row model of ``SqlAlchemyReportRepository``).

    Attributes:
        id: Primary key, the client-generated report id (UUIDv7).
        reporter_id: The reporting user.
        organization_id: The organisation reported for, if any.
        source_id: The provenance source the report is attributed to.
        observed_at: Start of the observation-time period, UTC.
        observed_at_precision: ``DatePrecision`` of ``observed_at``.
        observation: Exact WGS84 point of the reporter; private.
        accuracy_metres: The device's horizontal accuracy radius, if known.
        description: The reporter's words.
        original_language: BCP 47 language code of ``description``.
        hazard_code: The reporter's hazard guess, if any.
        hazard_confidence: The reporter's confidence in the guess.
        place_hint: Code of the place the reporter picked, if any.
        media_ids: Attached media asset ids, as a JSON array of strings.
        status: ``ReportStatus`` value.
        revision: Position in the revision chain, 1 for the original.
        supersedes_id: The revision this one replaces, if any.
        superseded_by_id: The revision that replaced this one, if any.
        withdrawal_reason: Why the reporter withdrew it, if they did.
        triage: The latest ``TriageResult`` as a JSON object, if any.
        submitted_at: When the platform accepted the submission, UTC.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the record was created, UTC.
        updated_at: When the record last changed, UTC.
    """

    __tablename__ = REPORTS_TABLE
    __table_args__ = (
        CheckConstraint(
            "(hazard_code IS NULL) = (hazard_confidence IS NULL)",
            name="hazard_guess_complete",
        ),
        Index("ix_reports_observation_gist", "observation", postgresql_using="gist"),
        # Serves the newest-first keyset listing (read backwards).
        Index("ix_reports_created_at_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    reporter_id: Mapped[UUID] = mapped_column(index=True)
    organization_id: Mapped[UUID | None]
    source_id: Mapped[UUID]
    observed_at: Mapped[datetime] = mapped_column(index=True)
    observed_at_precision: Mapped[str] = mapped_column(String(16))
    observation: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
    )
    accuracy_metres: Mapped[float | None] = mapped_column(Float)
    # Lengths follow the domain bounds: DESCRIPTION_MAX_LENGTH, the kernel's
    # eight-character language code, HAZARD_CODE_PATTERN and PLACE_CODE_PATTERN
    # (at most 64 characters) and REASON_MAX_LENGTH.
    description: Mapped[str] = mapped_column(String(4000))
    original_language: Mapped[str] = mapped_column(String(8))
    hazard_code: Mapped[str | None] = mapped_column(String(64), index=True)
    hazard_confidence: Mapped[str | None] = mapped_column(String(16))
    place_hint: Mapped[str | None] = mapped_column(String(64))
    media_ids: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), unique=True
    )
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT")
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(String(500))
    triage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
