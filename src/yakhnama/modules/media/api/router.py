"""HTTP routes of the media context, mounted under ``/api/v1``.

Uploads go straight to object storage through presigned URLs: a client asks for
an upload grant (``POST /media`` before its report exists, or
``POST /reports/{id}/media`` for its own submitted report) with the file's type and
exact ``byte_size``, ``PUT``s exactly those bytes to the URL with the returned
headers (the URL signs ``Content-Type`` and ``Content-Length``, so storage refuses
any other type or length; a browser sets ``Content-Length`` itself from the body),
then calls ``POST /media/{id}/complete``, which
checks the stored object (size, magic bytes, SHA-256 deduplication) and schedules
the malware scan. ``GET /media/{id}`` is open to anonymous callers, who only ever
see a published asset and only its EXIF-stripped public copy; the uploader and
moderators also get the private original. Moderators decide on
``/moderation/media/{id}/decision`` and work through ``GET /moderation/media``, the
queue of completed uploads (oldest first, no download links in the list).

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.api.dependencies import (
    CurrentActor,
    MediaApiServices,
    ModeratorActor,
    OptionalActor,
    Services,
)
from yakhnama.modules.media.api.schemas import (
    ListMediaQueueParameters,
    MediaAssetResponse,
    MediaQueuePage,
    ModerateMediaRequest,
    RequestUploadRequest,
)
from yakhnama.modules.media.public import (
    CompleteUpload,
    GetMediaAsset,
    ListMediaQueue,
    MediaAssetDetail,
    ModerateMedia,
    ModerationStatus,
    RequestUpload,
    UploadGrant,
)
from yakhnama.platform.etag import expected_version_from_if_match, make_etag, set_etag
from yakhnama.platform.openapi_headers import ETAG, LINK, LOCATION, header_responses
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
MEDIA_PATH: Final = f"{API_PREFIX}/media"
MEDIA_QUEUE_PATH: Final = f"{API_PREFIX}/moderation/media"
LOCATION_HEADER: Final = "Location"
LINK_HEADER: Final = "Link"
IF_MATCH_MAX_LENGTH: Final = 512

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

IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=IF_MATCH_MAX_LENGTH,
        description='Optional: the asset\'s ETag, for example "<asset_id>:3".',
    ),
]
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
        RequestUpload(
            actor=actor,
            report_id=report_id,
            mime_type=body.mime_type,
            byte_size=body.byte_size,
        )
    )


@router.post(
    "/media",
    status_code=status.HTTP_201_CREATED,
    responses={**_CREATE_RESPONSES, **header_responses(201, LOCATION)},
)
async def request_upload(
    body: RequestUploadRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> UploadGrant:
    """Grant a presigned upload for a file whose report is not submitted yet.

    The returned ``asset_id`` can then be listed in ``media_ids`` of
    ``POST /reports``. ``PUT`` exactly ``byte_size`` bytes with the returned
    headers; a body of any other length is refused by storage (403), a
    ``byte_size`` above ``max_bytes`` here (422).

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
    responses={**_CREATE_RESPONSES, **header_responses(201, LOCATION)},
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


@router.post(
    "/media/{asset_id}/complete",
    responses={**_CHANGE_RESPONSES, **header_responses(200, ETAG)},
)
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


@router.get(
    "/media/{asset_id}",
    responses={status.HTTP_404_NOT_FOUND: _PROBLEM, **header_responses(200, ETAG)},
)
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


@moderation_router.get("/media", responses=header_responses(200, LINK))
async def list_media_queue(
    parameters: Annotated[ListMediaQueueParameters, Query()],
    actor: ModeratorActor,
    response: Response,
    services: Services,
) -> MediaQueuePage:
    """List completed uploads awaiting or past a decision, oldest first.

    Download links are left out of the list; ``GET /media/{asset_id}`` presigns
    them for one asset.

    Args:
        parameters: Validated filters, cursor and limit.
        actor: The moderator.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        One page of assets.
    """
    page = await services.media_queries.list_media_queue(
        ListMediaQueue(
            actor=actor,
            moderation_status=parameters.moderation_status_value(),
            scan_status=parameters.scan_status,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(following.model_dump(mode="json", exclude_none=True))
        response.headers[LINK_HEADER] = f'<{MEDIA_QUEUE_PATH}?{query}>; rel="next"'
    return MediaQueuePage(
        items=tuple(MediaAssetResponse.from_detail(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@moderation_router.post(
    "/media/{asset_id}/decision",
    responses={
        **_CHANGE_RESPONSES,
        status.HTTP_412_PRECONDITION_FAILED: _PROBLEM,
        **header_responses(200, ETAG),
    },
)
async def moderate_media(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    asset_id: EntityId,
    body: ModerateMediaRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> MediaAssetResponse:
    """Record a moderator's decision, publishing the public copy when approved.

    ``If-Match`` is optional, as on every moderation route (Q66); when sent it is
    compared with the asset's version inside the command's unit of work, so two
    moderators deciding at once cannot let a stale approval overwrite a
    rejection.

    Args:
        asset_id: The asset.
        body: Decision, sensitivity and reason.
        actor: The moderator.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.
        if_match: Optional: the asset's ETag the decision is based on.

    Returns:
        The asset after the decision.

    Raises:
        PreconditionFailedError: If ``If-Match`` is stale, names another asset
            or is not a single strong tag (412).
    """
    detail = await services.moderate_media_handler(
        ModerateMedia(
            actor=actor,
            asset_id=asset_id,
            decision=ModerationStatus(body.decision),
            sensitivity=body.sensitivity,
            reason=body.reason,
            expected_version=(
                None
                if if_match is None
                else expected_version_from_if_match(if_match, asset_id)
            ),
        )
    )
    return _asset_response(response, detail)
