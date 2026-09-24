"""HTTP routes of the media context, mounted under ``/api/v1``.

Uploads go straight to object storage through presigned URLs: a client asks for
an upload grant (``POST /media`` before its report exists, or
``POST /reports/{id}/media`` for its own submitted report), ``PUT``s the file to
the URL with the returned headers, then calls ``POST /media/{id}/complete``, which
checks the stored object (size, magic bytes, SHA-256 deduplication) and schedules
the malware scan. ``GET /media/{id}`` is open to anonymous callers, who only ever
see a published asset and only its EXIF-stripped public copy; the uploader and
moderators also get the private original. Moderators decide on
``/moderation/media/{id}/decision``.

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Header, Response, status

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.api.dependencies import (
    CurrentActor,
    MediaApiServices,
    ModeratorActor,
    OptionalActor,
    Services,
)
from yakhnama.modules.media.api.schemas import (
    MediaAssetResponse,
    ModerateMediaRequest,
    RequestUploadRequest,
)
from yakhnama.modules.media.public import (
    CompleteUpload,
    GetMediaAsset,
    MediaAssetDetail,
    ModerateMedia,
    ModerationStatus,
    RequestUpload,
    UploadGrant,
)
from yakhnama.platform.etag import make_etag, set_etag
from yakhnama.shared_kernel.ids import EntityId

API_PREFIX: Final = "/api/v1"
MEDIA_PATH: Final = f"{API_PREFIX}/media"
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
_CHANGE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}
_CREATE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _PROBLEM,
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}

router = APIRouter(prefix=API_PREFIX, tags=["media"], responses=_COMMON_RESPONSES)
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


def _asset_response(response: Response, detail: MediaAssetDetail) -> MediaAssetResponse:
    set_etag(response, make_etag(detail.version, detail.id))
    return MediaAssetResponse.from_detail(detail)


async def _grant(
    services: MediaApiServices,
    actor: Actor,
    report_id: EntityId | None,
    body: RequestUploadRequest,
) -> UploadGrant:
    return await services.request_upload_handler(
        RequestUpload(actor=actor, report_id=report_id, mime_type=body.mime_type)
    )


@router.post("/media", status_code=status.HTTP_201_CREATED, responses=_CREATE_RESPONSES)
async def request_upload(
    body: RequestUploadRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> UploadGrant:
    """Grant a presigned upload for a file whose report is not submitted yet.

    The returned ``asset_id`` can then be listed in ``media_ids`` of
    ``POST /reports``.

    Args:
        body: The declared media type.
        actor: The authenticated uploader.
        response: Used to set ``Location``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The asset id, upload URL, headers, expiry and size cap.
    """
    del idempotency_key
    grant = await _grant(services, actor, None, body)
    response.headers[LOCATION_HEADER] = f"{MEDIA_PATH}/{grant.asset_id}"
    return grant


@router.post(
    "/reports/{report_id}/media",
    status_code=status.HTTP_201_CREATED,
    responses=_CREATE_RESPONSES,
)
async def request_report_upload(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    report_id: EntityId,
    body: RequestUploadRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> UploadGrant:
    """Grant a presigned upload for a file of the caller's own report.

    Args:
        report_id: The caller's report.
        body: The declared media type.
        actor: The authenticated reporter.
        response: Used to set ``Location``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The asset id, upload URL, headers, expiry and size cap.

    Raises:
        PermissionDeniedError: If the report is not the caller's.
    """
    del idempotency_key
    grant = await _grant(services, actor, report_id, body)
    response.headers[LOCATION_HEADER] = f"{MEDIA_PATH}/{grant.asset_id}"
    return grant


@router.post("/media/{asset_id}/complete", responses=_CHANGE_RESPONSES)
async def complete_upload(
    asset_id: EntityId,
    actor: CurrentActor,
    response: Response,
    services: Services,
) -> MediaAssetResponse:
    """Confirm that the file was uploaded; repeating the call is safe.

    Args:
        asset_id: The asset the upload was granted for.
        actor: The authenticated uploader.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The completed asset, or the uploader's earlier asset with the same
        content.

    Raises:
        ValidationError: If the file has not arrived, is empty, too large or not
            an allowed type (422).
    """
    detail = await services.complete_upload_handler(
        CompleteUpload(actor=actor, asset_id=asset_id)
    )
    return _asset_response(response, detail)


@router.get("/media/{asset_id}", responses={status.HTTP_404_NOT_FOUND: _PROBLEM})
async def get_media_asset(
    asset_id: EntityId,
    actor: OptionalActor,
    response: Response,
    services: Services,
) -> MediaAssetResponse:
    """Return one asset with the download links the caller may use.

    Anonymous callers and other users see a published asset with its public copy
    only; anything else is reported as missing.

    Args:
        asset_id: The asset.
        actor: The caller, anonymous or authenticated.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The asset.
    """
    detail = await services.media_queries.get_media_asset(
        GetMediaAsset(actor=actor, asset_id=asset_id)
    )
    return _asset_response(response, detail)


@moderation_router.post("/media/{asset_id}/decision", responses=_CHANGE_RESPONSES)
async def moderate_media(
    asset_id: EntityId,
    body: ModerateMediaRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
) -> MediaAssetResponse:
    """Record a moderator's decision, publishing the public copy when approved.

    Args:
        asset_id: The asset.
        body: Decision, sensitivity and reason.
        actor: The moderator.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The asset after the decision.
    """
    detail = await services.moderate_media_handler(
        ModerateMedia(
            actor=actor,
            asset_id=asset_id,
            decision=ModerationStatus(body.decision),
            sensitivity=body.sensitivity,
            reason=body.reason,
        )
    )
    return _asset_response(response, detail)
