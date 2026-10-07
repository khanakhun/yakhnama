"""HTTP routes of guest reporting, mounted under ``/api/v1/guest-submissions``.

A person without an account reports in five calls (ADR 0020), none of which takes a
bearer token:

1. ``POST /guest-submissions/challenges``: a signed proof-of-work challenge;
2. ``POST /guest-submissions`` with the challenge and its solution: a submission id
   and its **capability**, returned once;
3. ``POST /guest-submissions/{id}/media`` (up to three times): an upload grant for
   one photo of an announced type and exact ``byte_size``, which the client
   ``PUT``s straight to storage (the URL signs ``Content-Type`` and
   ``Content-Length`` and lives five minutes by default);
4. ``POST /guest-submissions/{id}/media/{asset_id}/complete``: the photo is checked
   (also after the report was submitted, until the capability expires);
5. ``POST /guest-submissions/{id}/report``: the report and its receipt reference.

None needs a bearer token, but one that is sent and rejected is answered with 401, as
everywhere in the API. Calls 3 to 5 carry the capability in the ``Guest-Capability``
request header, never
in the URL or the body, so it does not end up in access logs or caches. Every
response of these routes is ``Cache-Control: no-store`` (``main.py``). They stay
under the anonymous rate limit; behind the web portal every guest shares the
portal's address, so two global hourly caps (submissions opened, reports submitted)
protect the moderators' queue as well (429 ``rate-limited`` with ``Retry-After``,
documented on every 429 of these routes).

Patterns: none from the catalog (thin transport layer over commands).
"""

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Header, status

from yakhnama.modules.media.public import UploadGrant
from yakhnama.modules.reports.api.dependencies import (
    Services,
    require_valid_credentials,
)
from yakhnama.modules.reports.api.schemas import (
    GuestMediaUploadRequest,
    GuestReportRequest,
    OpenGuestSubmissionRequest,
)
from yakhnama.modules.reports.public import (
    CompleteGuestMediaUpload,
    GuestChallengeGrant,
    GuestMediaAsset,
    GuestReportReceipt,
    GuestSubmissionGrant,
    IssueGuestChallenge,
    OpenGuestSubmission,
    RequestGuestMediaUpload,
    SubmitGuestReport,
)
from yakhnama.shared_kernel.ids import EntityId

API_PREFIX: Final = "/api/v1"
GUEST_SUBMISSIONS_PATH: Final = f"{API_PREFIX}/guest-submissions"
GUEST_CAPABILITY_HEADER: Final = "Guest-Capability"
CAPABILITY_HEADER_MAX_LENGTH: Final = 256

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_RATE_LIMITED: Final[dict[str, Any]] = {
    **_PROBLEM,
    "description": (
        "RFC 9457 Problem Details, ``rate-limited``: the anonymous rate limit or "
        "an hourly guest cap is reached"
    ),
    "headers": {
        "Retry-After": {
            "description": "Seconds to wait before trying again.",
            "schema": {"type": "integer", "minimum": 1},
        }
    },
}
_COMMON_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _PROBLEM,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    status.HTTP_429_TOO_MANY_REQUESTS: _RATE_LIMITED,
    status.HTTP_503_SERVICE_UNAVAILABLE: _PROBLEM,
}
_CAPABILITY_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_403_FORBIDDEN: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
}

router = APIRouter(
    prefix=f"{API_PREFIX}/guest-submissions",
    tags=["guest-submissions"],
    responses=_COMMON_RESPONSES,
    dependencies=[Depends(require_valid_credentials)],
)

CapabilityHeader = Annotated[
    str | None,
    Header(
        alias=GUEST_CAPABILITY_HEADER,
        max_length=CAPABILITY_HEADER_MAX_LENGTH,
        description=(
            "The capability returned by POST /guest-submissions. Missing, wrong "
            "or for another submission: 403 guest-capability-invalid; expired: "
            "403 guest-capability-expired."
        ),
    ),
]


@router.post("/challenges", status_code=status.HTTP_201_CREATED)
async def issue_guest_challenge(services: Services) -> GuestChallengeGrant:
    """Issue a proof-of-work challenge to an anonymous caller.

    The difficulty rises with the number of submissions opened in the last hour
    (one bit per configured step, up to 22 bits). Find a decimal ``nonce`` such
    that ``SHA-256(salt + nonce)`` (UTF-8, the nonce appended to the salt)
    starts with ``difficulty_bits`` zero bits, then
    send the challenge and the nonce to ``POST /guest-submissions`` before
    ``expires_at``.

    Args:
        services: Use cases bound by the composition root.

    Returns:
        The signed challenge, its salt, difficulty and expiry.
    """
    return await services.issue_guest_challenge_handler(IssueGuestChallenge())


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: _PROBLEM},
)
async def open_guest_submission(
    body: OpenGuestSubmissionRequest, services: Services
) -> GuestSubmissionGrant:
    """Redeem a solved challenge for one guest submission.

    A challenge opens one submission only (409 ``guest-challenge-spent`` on a
    replay); a forged one is 422 ``guest-challenge-invalid``, an expired one 422
    ``guest-challenge-expired``, a wrong nonce 422 ``guest-proof-invalid``. When
    too many submissions were opened, or reports submitted, in the last hour,
    429 ``rate-limited`` with ``Retry-After``.

    Args:
        body: The challenge and its solution.
        services: Use cases bound by the composition root.

    Returns:
        The submission id and its capability, which is shown only this once.
    """
    return await services.open_guest_submission_handler(
        OpenGuestSubmission(challenge=body.challenge, nonce=body.nonce)
    )


@router.post(
    "/{submission_id}/media",
    status_code=status.HTTP_201_CREATED,
    responses=_CAPABILITY_RESPONSES,
)
async def request_guest_media_upload(
    submission_id: EntityId,
    body: GuestMediaUploadRequest,
    services: Services,
    capability: CapabilityHeader = None,
) -> UploadGrant:
    """Grant a presigned upload of one photo to a guest submission.

    At most three per submission (409 ``guest-media-limit`` for a fourth); none
    once the report was submitted (409 ``guest-submission-closed``). ``PUT``
    exactly ``byte_size`` bytes with the returned headers before ``expires_at``:
    storage refuses a body of any other length or type. A browser sets
    ``Content-Length`` itself from the body; other clients must send it.

    Args:
        submission_id: The guest submission.
        body: The declared image type.
        services: Use cases bound by the composition root.
        capability: The ``Guest-Capability`` header.

    Returns:
        The asset id, upload URL, headers, expiry and size cap.
    """
    return await services.request_guest_media_upload_handler(
        RequestGuestMediaUpload(
            submission_id=submission_id,
            capability=capability,
            mime_type=body.mime_type,
            byte_size=body.byte_size,
        )
    )


@router.post(
    "/{submission_id}/media/{asset_id}/complete",
    responses={**_CAPABILITY_RESPONSES, status.HTTP_404_NOT_FOUND: _PROBLEM},
)
async def complete_guest_media_upload(
    submission_id: EntityId,
    asset_id: EntityId,
    services: Services,
    capability: CapabilityHeader = None,
) -> GuestMediaAsset:
    """Check and complete a guest's uploaded photo.

    Allowed until the capability expires, also after the report was submitted;
    a photo completed then is kept private and is not added to the report.

    Args:
        submission_id: The guest submission.
        asset_id: The asset from the upload grant.
        services: Use cases bound by the composition root.
        capability: The ``Guest-Capability`` header.

    Returns:
        The completed photo (type, size, status); no download links.
    """
    return await services.complete_guest_media_upload_handler(
        CompleteGuestMediaUpload(
            submission_id=submission_id, capability=capability, asset_id=asset_id
        )
    )


@router.post(
    "/{submission_id}/report",
    status_code=status.HTTP_201_CREATED,
    responses=_CAPABILITY_RESPONSES,
)
async def submit_guest_report(
    submission_id: EntityId,
    body: GuestReportRequest,
    services: Services,
    capability: CapabilityHeader = None,
) -> GuestReportReceipt:
    """Submit the one report of a guest submission.

    A retry with the same content returns the same receipt (201 again), so a
    lost response is safe to retry, also for 24 hours after the capability
    expired; different content is 409 ``guest-submission-closed``. When guests
    submitted too many reports in the last hour, 429 ``rate-limited`` with
    ``Retry-After``. The report enters the moderators' guest queue
    (``GET /reports?channel=guest``); the guest cannot revise or withdraw it.

    Args:
        submission_id: The guest submission.
        body: The observation; ``media_ids`` must be this submission's photos.
        services: Use cases bound by the composition root.
        capability: The ``Guest-Capability`` header.

    Returns:
        The receipt reference and the submission time.
    """
    return await services.submit_guest_report_handler(
        SubmitGuestReport(
            submission_id=submission_id,
            capability=capability,
            content=body.to_content(),
        )
    )
