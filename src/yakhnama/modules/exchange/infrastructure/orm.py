"""SQLAlchemy row models of the ``exchange`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``ExportJob`` and ``ImportJob`` aggregates, and nothing outside this
package sees them.

- ``export_jobs``: one row per export. ``filters``, ``artifact`` and ``sidecar``
  are the JSON dumps of ``ExportFilters``, ``ArtifactRef`` and ``MetadataSidecar``;
  the last two are set only once the job completed (the aggregate checks which
  facts each status has, and every read validates them back).
- ``import_jobs``: one row per import. ``source_artifact`` is the stored file,
  ``report`` the ``ValidationReport`` once produced, and ``writes`` the
  ``ImportWrites`` (created event ids, lineage source, batches applied), empty for a
  dry run.

``requested_by`` is a user id of the identity module and carries no foreign key,
like every id owned by another module. Jobs are never deleted.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

EXPORT_JOBS_TABLE: Final = "export_jobs"
IMPORT_JOBS_TABLE: Final = "import_jobs"


class ExportJobRow(Base):
    """Row model of the ``export_jobs`` table: one row per ``ExportJob``.

    Implements: Adapter (ORM row model of ``SqlAlchemyExportJobRepository``).

    Attributes:
        id: Primary key, the job id (UUIDv7).
        requested_by: The requesting user.
        dataset: ``ExportDataset`` value.
        format: ``ExportFormat`` value.
        filters: ``ExportFilters`` as JSON.
        status: ``JobStatus`` value.
        artifact: ``ArtifactRef`` as JSON, once completed.
        sidecar: ``MetadataSidecar`` as JSON, once completed.
        error_summary: Why it failed, once failed.
        requested_at: When it was requested, UTC.
        started_at: When a worker started it, UTC.
        finished_at: When it completed, failed or was cancelled, UTC.
        version: Optimistic-concurrency version, compared on every update.
    """

    __tablename__ = EXPORT_JOBS_TABLE
    __table_args__ = (
        # Serves a user's own listing, newest request first (read backwards).
        Index(
            "ix_export_jobs_requested_by_requested_at",
            "requested_by",
            "requested_at",
            "id",
        ),
        # Serves the moderators' listing of every job, newest request first.
        Index("ix_export_jobs_requested_at_id", "requested_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    requested_by: Mapped[UUID]
    # FORMAT_CODE_PATTERN (32) bounds both codes; JOB_ERROR_SUMMARY_MAX_LENGTH.
    dataset: Mapped[str] = mapped_column(String(32))
    format: Mapped[str] = mapped_column(String(32))
    filters: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), index=True)
    artifact: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    sidecar: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    requested_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer)


class ImportJobRow(Base):
    """Row model of the ``import_jobs`` table: one row per ``ImportJob``.

    Implements: Adapter (ORM row model of ``SqlAlchemyImportJobRepository``).

    Attributes:
        id: Primary key, the job id (UUIDv7).
        requested_by: The requesting moderator.
        format: ``ImportFormat`` value.
        dry_run: Whether the import only validates.
        source_artifact: ``ArtifactRef`` of the file to import, as JSON.
        status: ``JobStatus`` value.
        report: ``ValidationReport`` as JSON, once produced.
        writes: ``ImportWrites`` as JSON; empty for a dry run.
        error_summary: Why it failed, once failed.
        requested_at: When it was requested, UTC.
        started_at: When a worker started it, UTC.
        finished_at: When it completed or failed, UTC.
        version: Optimistic-concurrency version, compared on every update.
    """

    __tablename__ = IMPORT_JOBS_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    requested_by: Mapped[UUID]
    format: Mapped[str] = mapped_column(String(32))
    dry_run: Mapped[bool] = mapped_column(Boolean)
    source_artifact: Mapped[dict[str, object]] = mapped_column(JSONB)
    # No secondary index: import jobs are only read by id (ExchangeQueryService).
    status: Mapped[str] = mapped_column(String(16))
    report: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    writes: Mapped[dict[str, object]] = mapped_column(JSONB)
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    requested_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer)
