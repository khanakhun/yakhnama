"""Read models of the exchange module: jobs as callers see them.

``ExportJobDetail`` and ``ImportJobDetail`` are what the ``ExchangeQueryService``
port returns; ``ExportJobView`` adds the presigned download link the application
asks the ``ArtifactStore`` for once an export is completed. Error summaries are the
fixed sentences the handlers write, never an exception's text.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.value_objects import (
    ARTIFACT_MAX_BYTES,
    EXPORT_MAX_ROWS,
    IMPORT_MAX_CREATED_IDS,
    IMPORT_MAX_ROWS,
    ArtifactRef,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ImportFormat,
    JobErrorSummary,
    JobStatus,
    JobVersion,
    MetadataSidecar,
    Sha256,
    ValidationReport,
)
from yakhnama.shared_kernel.ids import EntityId

DOWNLOAD_URL_MAX_LENGTH = 4096


class ExportSummary(BaseModel):
    """What an export wrote, as measured by the handler around the exporter.

    Implements: DTO.

    Attributes:
        row_count: Rows the exporter consumed.
        sha256: Digest of every byte written.
        byte_size: Number of bytes written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    row_count: int = Field(ge=0, le=EXPORT_MAX_ROWS)
    sha256: Sha256
    byte_size: int = Field(ge=0, le=ARTIFACT_MAX_BYTES)


class ExportJobDetail(BaseModel):
    """One export job with everything its owner and moderators may see.

    Implements: DTO.

    Attributes:
        id: The job.
        requested_by: The requesting user.
        dataset: Which dataset.
        format: Which format.
        filters: The filters the rows are selected with.
        status: Where the job is in its lifecycle.
        artifact: The stored file, once completed.
        sidecar: The file's metadata, once completed.
        error_summary: Why it failed, once failed.
        requested_at: When it was requested, UTC.
        started_at: When a worker started it, UTC.
        finished_at: When it finished, UTC.
        version: Optimistic-concurrency version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    requested_by: EntityId
    dataset: ExportDataset
    format: ExportFormat
    filters: ExportFilters
    status: JobStatus
    artifact: ArtifactRef | None
    sidecar: MetadataSidecar | None
    error_summary: JobErrorSummary | None
    requested_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    version: JobVersion

    @classmethod
    def from_entity(cls, job: ExportJob) -> Self:
        """Build the detail view of an export job.

        Args:
            job: The aggregate.

        Returns:
            Its view.
        """
        return cls(
            id=job.id,
            requested_by=job.requested_by,
            dataset=job.dataset,
            format=job.format,
            filters=job.filters,
            status=job.status,
            artifact=job.artifact,
            sidecar=job.sidecar,
            error_summary=job.error_summary,
            requested_at=job.requested_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            version=job.version,
        )


class ExportJobSummary(BaseModel):
    """One export job in a listing.

    Implements: DTO.

    Attributes:
        id: The job.
        dataset: Which dataset.
        format: Which format.
        status: Where the job is in its lifecycle.
        row_count: Rows written, once completed.
        requested_at: When it was requested, UTC.
        finished_at: When it finished, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    dataset: ExportDataset
    format: ExportFormat
    status: JobStatus
    row_count: int | None = Field(ge=0, le=EXPORT_MAX_ROWS)
    requested_at: AwareDatetime
    finished_at: AwareDatetime | None

    @classmethod
    def from_entity(cls, job: ExportJob) -> Self:
        """Build the summary of an export job.

        Args:
            job: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            id=job.id,
            dataset=job.dataset,
            format=job.format,
            status=job.status,
            row_count=None if job.sidecar is None else job.sidecar.row_count,
            requested_at=job.requested_at,
            finished_at=job.finished_at,
        )


class ExportJobView(BaseModel):
    """An export job with a download link once its file is stored.

    Implements: DTO.

    Attributes:
        job: The job.
        download_url: A short-lived presigned link to the file, only when the job
            is completed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job: ExportJobDetail
    download_url: str | None = Field(
        default=None, min_length=1, max_length=DOWNLOAD_URL_MAX_LENGTH
    )


class ImportJobDetail(BaseModel):
    """One import job with its validation report and what it wrote.

    Moderators only: the report names rows and columns of a curator's file.

    Implements: DTO.

    Attributes:
        id: The job.
        requested_by: The requesting moderator.
        format: The file's format.
        dry_run: Whether the import only validates.
        source_artifact: The stored file.
        status: Where the job is in its lifecycle.
        report: The row-level validation report, once produced.
        created_ids: The events created, in creation order.
        lineage_source_id: The ``dataset`` source the created records cite.
        batches_applied: Batches committed.
        error_summary: Why it failed, once failed.
        requested_at: When it was requested, UTC.
        started_at: When a worker started it, UTC.
        finished_at: When it finished, UTC.
        version: Optimistic-concurrency version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    requested_by: EntityId
    format: ImportFormat
    dry_run: bool
    source_artifact: ArtifactRef
    status: JobStatus
    report: ValidationReport | None
    created_ids: tuple[EntityId, ...] = Field(max_length=IMPORT_MAX_CREATED_IDS)
    lineage_source_id: EntityId | None
    batches_applied: int = Field(ge=0, le=IMPORT_MAX_ROWS)
    error_summary: JobErrorSummary | None
    requested_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    version: JobVersion

    @classmethod
    def from_entity(cls, job: ImportJob) -> Self:
        """Build the detail view of an import job.

        Args:
            job: The aggregate.

        Returns:
            Its view.
        """
        return cls(
            id=job.id,
            requested_by=job.requested_by,
            format=job.format,
            dry_run=job.dry_run,
            source_artifact=job.source_artifact,
            status=job.status,
            report=job.report,
            created_ids=job.writes.created_ids,
            lineage_source_id=job.writes.lineage_source_id,
            batches_applied=job.writes.batches_applied,
            error_summary=job.error_summary,
            requested_at=job.requested_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            version=job.version,
        )
