"""HTTP routes of the exchange context, mounted under ``/api/v1``.

Exports (``/exports``) are asynchronous jobs owned by the requesting user: any
authenticated user may export verified ``events`` and ``claims``, only moderators
``reports`` (``export_policy``, **proposed**). A job is visible to, and cancellable
by, its owner and moderators; anyone else gets 404, so job ids cannot be probed.
A completed job carries its metadata sidecar inline and a short-lived presigned
``download_url``, asked of storage at read time so it is always fresh.

Imports (``/moderation/imports``) are moderation work (``import_policy`` first):
the moderator asks for a presigned upload, uploads the file, then requests the
import with the file's size and digest and an explicit ``dry_run``.

Both creating ``POST``s answer ``202 Accepted``: the job is stored and queued, a
worker runs it later. They honour ``Idempotency-Key`` through the platform
middleware.

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from yakhnama.modules.exchange.api.dependencies import (
    CurrentActor,
    ImportModeratorActor,
    Services,
)
from yakhnama.modules.exchange.api.schemas import (
    IMPORT_UPLOAD_MAX_BYTES,
    ExportJobPage,
    ExportJobResponse,
    ImportUploadGrantResponse,
    ListExportJobsParameters,
    RequestExportRequest,
    RequestImportRequest,
    RequestImportUploadRequest,
)
from yakhnama.modules.exchange.public import (
    DEFAULT_FORMAT_REGISTRY,
    ArtifactRef,
    CancelExport,
    ExportJobView,
    GetExportJob,
    GetImportJob,
    ImportJobDetail,
    ListExportJobs,
    RequestExport,
    RequestImport,
    inline_import_key,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.platform.etag import make_etag, set_etag
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
EXPORTS_PATH: Final = f"{API_PREFIX}/exports"
IMPORTS_PATH: Final = f"{API_PREFIX}/moderation/imports"
LINK_HEADER: Final = "Link"
LOCATION_HEADER: Final = "Location"

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_COMMON_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _PROBLEM,
    status.HTTP_403_FORBIDDEN: _PROBLEM,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    status.HTTP_503_SERVICE_UNAVAILABLE: _PROBLEM,
}
_CREATING_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}

router = APIRouter(prefix=API_PREFIX, tags=["exchange"], responses=_COMMON_RESPONSES)
moderation_router = APIRouter(
    prefix=f"{API_PREFIX}/moderation",
    tags=["moderation"],
    responses=_COMMON_RESPONSES,
)

IdempotencyKey = Annotated[
    UUID | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "A UUID chosen by the client; replaying it with the same request "
            "returns the stored response."
        ),
    ),
]


def _export_response(view: ExportJobView) -> ExportJobResponse:
    return ExportJobResponse.model_validate(
        {**view.job.model_dump(), "download_url": view.download_url}
    )


async def _read_export(
    services: Services, actor: Actor, job_id: EntityId, response: Response
) -> ExportJobResponse:
    view = await services.exchange_queries.get_export_job(
        GetExportJob(actor=actor, job_id=job_id)
    )
    set_etag(response, make_etag(view.job.version, view.job.id))
    return _export_response(view)


@router.post(
    "/exports",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_CREATING_RESPONSES,
)
async def request_export(
    body: RequestExportRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> ExportJobResponse:
    """Queue an export of one dataset in one format.

    Args:
        body: Dataset, format and filters.
        actor: The requesting user; they own the job.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The queued job.
    """
    del idempotency_key
    job_id = await services.request_export_handler(
        RequestExport(
            actor=actor, dataset=body.dataset, format=body.format, filters=body.filters
        )
    )
    response.headers[LOCATION_HEADER] = f"{EXPORTS_PATH}/{job_id}"
    return await _read_export(services, actor, job_id, response)


@router.get("/exports")
async def list_exports(
    parameters: Annotated[ListExportJobsParameters, Query()],
    actor: CurrentActor,
    response: Response,
    services: Services,
) -> ExportJobPage:
    """List export jobs, newest first: every job for a moderator, else one's own.

    Args:
        parameters: Validated cursor and limit.
        actor: The caller.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        One page of job summaries.
    """
    page = await services.exchange_queries.list_export_jobs(
        ListExportJobs(
            actor=actor,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(following.model_dump(mode="json", exclude_none=True))
        response.headers[LINK_HEADER] = f'<{EXPORTS_PATH}?{query}>; rel="next"'
    return ExportJobPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/exports/{job_id}", responses={status.HTTP_404_NOT_FOUND: _PROBLEM})
async def get_export(
    job_id: EntityId,
    actor: CurrentActor,
    response: Response,
    services: Services,
) -> ExportJobResponse:
    """Return one export job with its ``ETag``.

    Args:
        job_id: The job.
        actor: The caller; the owner or a moderator.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The job; once completed, with its sidecar and a download link.
    """
    return await _read_export(services, actor, job_id, response)


@router.delete(
    "/exports/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: _PROBLEM,
        status.HTTP_409_CONFLICT: _PROBLEM,
    },
)
async def cancel_export(
    job_id: EntityId,
    actor: CurrentActor,
    services: Services,
) -> None:
    """Cancel an export no worker has started.

    Args:
        job_id: The job.
        actor: The caller; the owner or a moderator.
        services: Use cases bound by the composition root.
    """
    await services.cancel_export_handler(CancelExport(actor=actor, job_id=job_id))


@moderation_router.post(
    "/imports/uploads",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATING_RESPONSES,
)
async def request_import_upload(
    body: RequestImportUploadRequest,
    actor: ImportModeratorActor,
    services: Services,
) -> ImportUploadGrantResponse:
    """Grant a presigned upload of one import file.

    The key is chosen here, ``imports/<upload_id>/source<extension>``; nothing
    is stored until the client uploads.

    Args:
        body: The file's format.
        actor: The moderator.
        services: Use cases bound by the composition root.

    Returns:
        The key, URL, headers, size cap and expiry of the upload.
    """
    del actor
    descriptor = DEFAULT_FORMAT_REGISTRY.import_descriptor(body.format.value)
    key = inline_import_key(services.id_generator.new_id(), descriptor.extension)
    grant = await services.artifact_store.presign_upload(
        key, descriptor.media_type, IMPORT_UPLOAD_MAX_BYTES
    )
    return ImportUploadGrantResponse(
        object_key=key,
        upload_url=grant.url,
        headers=grant.headers,
        media_type=descriptor.media_type,
        max_bytes=IMPORT_UPLOAD_MAX_BYTES,
        expires_at=grant.expires_at,
    )


@moderation_router.post(
    "/imports",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_CREATING_RESPONSES,
)
async def request_import(
    body: RequestImportRequest,
    actor: ImportModeratorActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> ImportJobDetail:
    """Queue an import of an uploaded file of historical events and claims.

    Args:
        body: Format, the uploaded file and ``dry_run``.
        actor: The moderator; they own the job.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The queued job.
    """
    del idempotency_key
    descriptor = DEFAULT_FORMAT_REGISTRY.import_descriptor(body.format.value)
    job_id = await services.request_import_handler(
        RequestImport(
            actor=actor,
            format=body.format,
            artifact=ArtifactRef(
                object_key=body.artifact.object_key,
                byte_size=body.artifact.byte_size,
                sha256=body.artifact.sha256,
                media_type=descriptor.media_type,
            ),
            dry_run=body.dry_run,
        )
    )
    response.headers[LOCATION_HEADER] = f"{IMPORTS_PATH}/{job_id}"
    return await _read_import(services, actor, job_id, response)


async def _read_import(
    services: Services, actor: Actor, job_id: EntityId, response: Response
) -> ImportJobDetail:
    job = await services.exchange_queries.get_import_job(
        GetImportJob(actor=actor, job_id=job_id)
    )
    set_etag(response, make_etag(job.version, job.id))
    return job


@moderation_router.get(
    "/imports/{job_id}", responses={status.HTTP_404_NOT_FOUND: _PROBLEM}
)
async def get_import(
    job_id: EntityId,
    actor: ImportModeratorActor,
    response: Response,
    services: Services,
) -> ImportJobDetail:
    """Return one import job with its validation report and ``ETag``.

    Args:
        job_id: The job.
        actor: The moderator.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The job, its row-level report once produced, and what it wrote.
    """
    return await _read_import(services, actor, job_id, response)
