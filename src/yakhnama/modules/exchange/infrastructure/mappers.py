"""Translate between the exchange job aggregates and their row models.

Every JSONB column holds exactly ``model_dump(mode="json")`` of its value object,
and every read goes through ``model_validate`` on the aggregate, so a row that no
longer satisfies the domain (a sidecar that does not describe its artifact, a dry
run with writes) fails loudly instead of leaking an invalid job.

Patterns: Anti-Corruption Layer (mapper).
"""

from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.infrastructure.orm import ExportJobRow, ImportJobRow
from yakhnama.platform.db import dump_json_column


def export_job_to_values(job: ExportJob) -> dict[str, object]:
    """Return every ``export_jobs`` column value of ``job`` except the key.

    Args:
        job: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    return {
        "requested_by": job.requested_by,
        "dataset": job.dataset.value,
        "format": job.format.value,
        "filters": dump_json_column(job.filters),
        "visibility": job.visibility,
        "status": job.status.value,
        "artifact": dump_json_column(job.artifact),
        "sidecar": dump_json_column(job.sidecar),
        "error_summary": job.error_summary,
        "requested_at": job.requested_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "version": job.version,
    }


def export_job_to_row(job: ExportJob) -> ExportJobRow:
    """Build the ``export_jobs`` row of ``job``.

    Args:
        job: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return ExportJobRow(id=job.id, **export_job_to_values(job))


def row_to_export_job(row: ExportJobRow) -> ExportJob:
    """Rebuild an export job from its row.

    Args:
        row: A row of ``export_jobs``.

    Returns:
        The validated ``ExportJob``.
    """
    return ExportJob.model_validate(
        {
            "id": row.id,
            "requested_by": row.requested_by,
            "dataset": row.dataset,
            "format": row.format,
            "filters": row.filters,
            "visibility": row.visibility,
            "status": row.status,
            "artifact": row.artifact,
            "sidecar": row.sidecar,
            "error_summary": row.error_summary,
            "requested_at": row.requested_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "version": row.version,
        }
    )


def import_job_to_values(job: ImportJob) -> dict[str, object]:
    """Return every ``import_jobs`` column value of ``job`` except the key.

    Args:
        job: The aggregate.

    Returns:
        Column name to value, for inserts and versioned updates alike.
    """
    return {
        "requested_by": job.requested_by,
        "format": job.format.value,
        "dry_run": job.dry_run,
        "source_artifact": dump_json_column(job.source_artifact),
        "status": job.status.value,
        "report": dump_json_column(job.report),
        "writes": dump_json_column(job.writes),
        "error_summary": job.error_summary,
        "requested_at": job.requested_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "version": job.version,
    }


def import_job_to_row(job: ImportJob) -> ImportJobRow:
    """Build the ``import_jobs`` row of ``job``.

    Args:
        job: The aggregate.

    Returns:
        A transient row carrying the same values.
    """
    return ImportJobRow(id=job.id, **import_job_to_values(job))


def row_to_import_job(row: ImportJobRow) -> ImportJob:
    """Rebuild an import job from its row.

    Args:
        row: A row of ``import_jobs``.

    Returns:
        The validated ``ImportJob``.
    """
    return ImportJob.model_validate(
        {
            "id": row.id,
            "requested_by": row.requested_by,
            "format": row.format,
            "dry_run": row.dry_run,
            "source_artifact": row.source_artifact,
            "status": row.status,
            "report": row.report,
            "writes": row.writes,
            "error_summary": row.error_summary,
            "requested_at": row.requested_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "version": row.version,
        }
    )
