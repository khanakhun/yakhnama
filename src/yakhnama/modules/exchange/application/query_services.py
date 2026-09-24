"""Authorised read use cases of the exchange module.

A job the actor may not see is reported as missing, so job ids cannot be probed:
an export job is visible to its owner and to moderators, an import job to
moderators only. A completed export comes with a presigned download link, asked of
the ``ArtifactStore`` at read time so it is always fresh, and only for a reader who
**now** may export the job's dataset and see its visibility (``moderation`` files
to current moderators only). A demoted owner still sees the job and its status,
without a link (Phase 4 security review).

Patterns: Query Service, Policy.
"""

from yakhnama.modules.exchange.application.authorisation import (
    export_job_policy,
    export_policy,
    import_policy,
)
from yakhnama.modules.exchange.application.dto import (
    ExportJobDetail,
    ExportJobSummary,
    ExportJobView,
    ImportJobDetail,
)
from yakhnama.modules.exchange.application.ports import (
    ArtifactStore,
    ExchangeQueryService,
)
from yakhnama.modules.exchange.application.queries import (
    GetExportJob,
    GetImportJob,
    ListExportJobs,
)
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    ImportJobNotFoundError,
)
from yakhnama.modules.exchange.domain.value_objects import (
    PUBLIC_VISIBILITY,
    JobStatus,
)
from yakhnama.modules.identity.public import (
    Actor,
    CanModerate,
    IsAuthenticated,
    require_allowed,
)
from yakhnama.shared_kernel.pagination import Page


class ExchangeJobQueryService:
    """Answers job queries with the visibility rules applied.

    Implements: Query Service.
    """

    def __init__(self, reads: ExchangeQueryService, artifacts: ArtifactStore) -> None:
        """Create the service.

        Args:
            reads: The exchange read port.
            artifacts: Presigns download links of completed exports.
        """
        self._reads = reads
        self._artifacts = artifacts

    async def get_export_job(self, query: GetExportJob) -> ExportJobView:
        """Return one export job the actor may see.

        Args:
            query: The job and the actor.

        Returns:
            The job, with a download link once completed if the reader may
            download it now (see the module docs).

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            ExportJobNotFoundError: If the job does not exist, or it belongs to
                someone else and the actor may not moderate.
        """
        require_allowed(IsAuthenticated(), query.actor, action="read export jobs")
        job = await self._reads.get_export_job(query.job_id)
        if job is None or not export_job_policy(job.requested_by).is_allowed(
            query.actor
        ):
            raise ExportJobNotFoundError.for_id(query.job_id)
        download_url = None
        if (
            job.status is JobStatus.COMPLETED
            and job.artifact is not None
            and _may_download(job, query.actor)
        ):
            extension = job.artifact.object_key.rsplit("/", maxsplit=1)[-1]
            download_url = await self._artifacts.presign_download(
                job.artifact.object_key,
                file_name=f"yakhnama-{job.id}-{extension}",
            )
        return ExportJobView(job=job, download_url=download_url)

    async def list_export_jobs(self, query: ListExportJobs) -> Page[ExportJobSummary]:
        """Return one page of the export jobs the actor may see, newest first.

        Args:
            query: The page request and the actor.

        Returns:
            Every job for a moderator, the actor's own jobs otherwise.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            ValidationError: If the cursor is invalid.
        """
        require_allowed(IsAuthenticated(), query.actor, action="list export jobs")
        requested_by = (
            None if CanModerate().is_allowed(query.actor) else query.actor.user_id
        )
        return await self._reads.list_export_jobs(requested_by, query.page)

    async def get_import_job(self, query: GetImportJob) -> ImportJobDetail:
        """Return one import job with its report.

        Args:
            query: The job and the actor.

        Returns:
            The job.

        Raises:
            PermissionDeniedError: If the actor may not moderate.
            ImportJobNotFoundError: If the job does not exist.
        """
        require_allowed(import_policy(), query.actor, action="read import jobs")
        job = await self._reads.get_import_job(query.job_id)
        if job is None:
            raise ImportJobNotFoundError.for_id(query.job_id)
        return job


def _may_download(job: ExportJobDetail, reader: Actor) -> bool:
    # Decided at read time: a role granted when the job was requested may be gone.
    return export_policy(job.dataset).is_allowed(reader) and (
        job.visibility == PUBLIC_VISIBILITY or CanModerate().is_allowed(reader)
    )
