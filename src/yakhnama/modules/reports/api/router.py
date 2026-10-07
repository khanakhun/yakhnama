"""HTTP routes of the reports context, mounted under ``/api/v1``.

Every route needs a bearer token: reports are raw, untrusted observations and the
reporter's position is personal data. Privacy is decided by the application, not
here: ``AuthorisedReportQueryService`` returns the exact position and accuracy only
to the reporter and moderators, rounded positions to everyone else it lets read,
and listings are always rounded (``ReportSummary``).

Submission is idempotent twice over: on the client's ``client_report_id`` (a retry
without any header returns the stored report, **201 with the same body**, because
the handler cannot tell a retry from the first call and the client should not have
to), and on ``Idempotency-Key`` through the platform middleware. Revisions and
withdrawals carry the reporter's ``If-Match`` into the command's
``expected_version``, so a stale tag is refused inside the unit of work (412), and
a missing one is 428.

``moderation_router`` serves the moderators' review marks (ADR 0022) under
``/api/v1/moderation/reports``: reading a lineage's mark and history, marking one
report and marking many. A mark is versioned on its own (ETag
``"<lineage_id>:<version>"``, version 0 while unmarked) and never changes the report
or its ETag. ``If-Match`` is optional on these routes, as on the other moderation
routes (Q66); when sent, it is checked against the review's version inside the
unit of work, so a concurrent mark cannot slip in between (unlike Q155).

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from geojson_pydantic import Feature, FeatureCollection, Point

from yakhnama.modules.reports.api.dependencies import (
    CurrentActor,
    ModeratorActor,
    Services,
)
from yakhnama.modules.reports.api.schemas import (
    BulkMarkReviewRequest,
    ListReportsParameters,
    MarkReviewRequest,
    ReportFeatureProperties,
    ReportFormat,
    ReportPage,
    ReviseReportRequest,
    SubmitReportRequest,
    WithdrawReportRequest,
)
from yakhnama.modules.reports.public import (
    BulkReviewResult,
    GetReport,
    GetReportReview,
    ListReports,
    MarkReportReview,
    MarkReportReviewHandler,
    MarkReportReviews,
    MarkReportReviewsHandler,
    ReportDetail,
    ReportReviewDetail,
    ReviseReport,
    SubmitReport,
    WithdrawReport,
)
from yakhnama.platform.etag import expected_version_from_if_match, make_etag, set_etag
from yakhnama.platform.openapi_headers import ETAG, LINK, LOCATION, header_responses
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
REPORTS_PATH: Final = f"{API_PREFIX}/reports"
GEOJSON_MEDIA_TYPE: Final = "application/geo+json"
LINK_HEADER: Final = "Link"
LOCATION_HEADER: Final = "Location"
VARY_HEADER: Final = "Vary"
ACCEPT_MAX_LENGTH: Final = 1024
IF_MATCH_MAX_LENGTH: Final = 512

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_GEOJSON_ALTERNATIVE: Final[dict[str, Any]] = {
    "description": "JSON, or GeoJSON when negotiated",
    "content": {GEOJSON_MEDIA_TYPE: {}},
}
_COMMON_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _PROBLEM,
    status.HTTP_403_FORBIDDEN: _PROBLEM,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    status.HTTP_503_SERVICE_UNAVAILABLE: _PROBLEM,
}
_OPTIONAL_IF_MATCH_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_404_NOT_FOUND: _PROBLEM,
    status.HTTP_409_CONFLICT: _PROBLEM,
    status.HTTP_412_PRECONDITION_FAILED: _PROBLEM,
}
_CHANGE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **_OPTIONAL_IF_MATCH_RESPONSES,
    status.HTTP_428_PRECONDITION_REQUIRED: _PROBLEM,
}

ReportFeature = Feature[Point, ReportFeatureProperties]

router = APIRouter(prefix=API_PREFIX, tags=["reports"], responses=_COMMON_RESPONSES)
moderation_router = APIRouter(
    prefix=f"{API_PREFIX}/moderation",
    tags=["moderation"],
    responses=_COMMON_RESPONSES,
)

AcceptHeader = Annotated[str | None, Header(max_length=ACCEPT_MAX_LENGTH)]
IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=IF_MATCH_MAX_LENGTH,
        description='The report\'s ETag, for example "<id>:3".',
    ),
]
ReviewIfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=IF_MATCH_MAX_LENGTH,
        description=(
            'Optional: the review\'s ETag, for example "<lineage_id>:2" '
            "(version 0 while the lineage was never marked)."
        ),
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


def wants_geojson(output_format: ReportFormat | None, accept: str | None) -> bool:
    """Tell whether the client asked for GeoJSON.

    Args:
        output_format: The ``format`` query parameter, if sent.
        accept: The ``Accept`` header, if sent.

    Returns:
        ``True`` for ``format=geojson``, or with no ``format`` when ``Accept``
        names ``application/geo+json``.
    """
    if output_format is not None:
        return output_format is ReportFormat.GEOJSON
    # A substring match: the only alternative representation is JSON, which is also
    # the default, so quality values need not be weighed.
    return accept is not None and GEOJSON_MEDIA_TYPE in accept.lower()


def _detail_response(response: Response, detail: ReportDetail) -> ReportDetail:
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@router.post(
    "/reports",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: _PROBLEM,
        status.HTTP_409_CONFLICT: _PROBLEM,
        **header_responses(status.HTTP_201_CREATED, LOCATION, ETAG),
    },
)
async def submit_report(
    body: SubmitReportRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> ReportDetail:
    """Submit a report; a retry with the same ``client_report_id`` returns it again.

    With ``assisted`` the caller enters the report for a person without an
    account and records their consent (trusted reporters, moderators, and
    organisation members reporting for their organisation; others get 403).

    Args:
        body: The client id, the observation, the optional organisation and the
            optional assistance record.
        actor: The authenticated reporter.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The reporter's exact view of the report.
    """
    del idempotency_key
    detail = await services.submit_report_handler(
        SubmitReport(
            actor=actor,
            client_report_id=body.client_report_id,
            content=body.to_content(),
            organization_id=body.organization_id,
            assisted=body.assisted,
        )
    )
    response.headers[LOCATION_HEADER] = f"{REPORTS_PATH}/{detail.id}"
    return _detail_response(response, detail)


@router.get(
    "/reports",
    response_model=ReportPage,
    responses={
        status.HTTP_200_OK: {
            **_GEOJSON_ALTERNATIVE,
            **header_responses(status.HTTP_200_OK, LINK)[status.HTTP_200_OK],
        }
    },
)
async def list_reports(
    parameters: Annotated[ListReportsParameters, Query()],
    actor: CurrentActor,
    response: Response,
    services: Services,
    accept: AcceptHeader = None,
) -> ReportPage | Response:
    """List reports with rounded positions, as JSON or GeoJSON.

    Moderators see every report, each lineage's review mark and each report's
    triage flag kinds; anyone else only their own, without either.
    ``channel=guest`` is the moderators' queue of reports from people without an
    account; ``review_state`` and ``triage_flag`` (moderators only) their review
    queues; ``reporter=me`` narrows any listing to the caller's own.

    Args:
        parameters: Validated filters, format, cursor and limit.
        actor: The authenticated caller.
        response: Used to set ``Link`` and ``Vary`` on the JSON response.
        services: Use cases bound by the composition root.
        accept: ``application/geo+json`` selects a GeoJSON ``FeatureCollection``.

    Returns:
        One page of reports in the negotiated representation. The GeoJSON form
        carries the next page only in the ``Link`` header.
    """
    page = await services.report_queries.list_reports(
        ListReports(
            actor=actor,
            status=parameters.status,
            channel=parameters.channel,
            hazard_code=parameters.hazard_code,
            bbox=parameters.bounding_box(),
            observed_from=parameters.observed_from,
            observed_to=parameters.observed_to,
            review_state=parameters.review_state,
            triage_flag=parameters.triage_flag,
            is_own_only=parameters.reporter is not None,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    headers = {VARY_HEADER: "Accept"}
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(
            following.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        headers[LINK_HEADER] = f'<{REPORTS_PATH}?{query}>; rel="next"'
    if wants_geojson(parameters.output_format, accept):
        collection = FeatureCollection[ReportFeature](
            type="FeatureCollection",
            features=[
                ReportFeature(
                    type="Feature",
                    id=str(summary.id),
                    geometry=summary.coordinates.to_geojson_point(),
                    properties=ReportFeatureProperties.from_summary(summary),
                )
                for summary in page.items
            ],
        )
        return Response(
            content=collection.model_dump_json(),
            media_type=GEOJSON_MEDIA_TYPE,
            headers=headers,
        )
    response.headers.update(headers)
    return ReportPage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/reports/{report_id}",
    responses={
        status.HTTP_404_NOT_FOUND: _PROBLEM,
        **header_responses(status.HTTP_200_OK, ETAG),
    },
)
async def get_report(
    report_id: EntityId,
    actor: CurrentActor,
    response: Response,
    services: Services,
) -> ReportDetail:
    """Return one report as the caller may see it, with its ``ETag``.

    Args:
        report_id: The report (UUIDv7).
        actor: The authenticated caller.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The exact view for the reporter and moderators, the rounded view for
        members of the report's organisation. Moderators also get the review
        mark and the linked events.

    Raises:
        ReportNotFoundError: If the report does not exist or the caller may not
            read it (the two are not told apart).
    """
    detail = await services.report_queries.get_report(
        GetReport(actor=actor, report_id=report_id)
    )
    return _detail_response(response, detail)


@router.post(
    "/reports/{report_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    responses={
        **_CHANGE_RESPONSES,
        **header_responses(status.HTTP_201_CREATED, LOCATION, ETAG),
    },
)
async def revise_report(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    report_id: EntityId,
    body: ReviseReportRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> ReportDetail:
    """Correct a report by submitting its next revision; the reporter only.

    Args:
        report_id: The revision being corrected.
        body: The complete corrected content.
        actor: The authenticated reporter.
        response: Used to set ``Location`` and ``ETag`` of the new revision.
        services: Use cases bound by the composition root.
        if_match: The ETag of the revision being corrected.

    Returns:
        The reporter's exact view of the new revision.

    Raises:
        PreconditionRequiredError: Without ``If-Match``.
        PreconditionFailedError: If ``If-Match`` is stale or names another report.
    """
    detail = await services.revise_report_handler(
        ReviseReport(
            actor=actor,
            report_id=report_id,
            content=body.to_content(),
            expected_version=expected_version_from_if_match(if_match, report_id),
        )
    )
    response.headers[LOCATION_HEADER] = f"{REPORTS_PATH}/{detail.id}"
    return _detail_response(response, detail)


@router.post(
    "/reports/{report_id}/withdrawal",
    responses={**_CHANGE_RESPONSES, **header_responses(status.HTTP_200_OK, ETAG)},
)
async def withdraw_report(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    report_id: EntityId,
    body: WithdrawReportRequest,
    actor: CurrentActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> ReportDetail:
    """Withdraw a report with a reason; it stays on record. The reporter only.

    Args:
        report_id: The report.
        body: The reason.
        actor: The authenticated reporter.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: The report's ETag the withdrawal is based on.

    Returns:
        The reporter's exact view of the withdrawn report.

    Raises:
        PreconditionRequiredError: Without ``If-Match``.
        PreconditionFailedError: If ``If-Match`` is stale or names another report.
    """
    detail = await services.withdraw_report_handler(
        WithdrawReport(
            actor=actor,
            report_id=report_id,
            reason=body.reason,
            expected_version=expected_version_from_if_match(if_match, report_id),
        )
    )
    return _detail_response(response, detail)


def _review_response(
    response: Response, review: ReportReviewDetail
) -> ReportReviewDetail:
    set_etag(response, make_etag(review.version, review.lineage_id))
    return review


def _mark_handler(services: Services) -> MarkReportReviewHandler:
    return MarkReportReviewHandler(
        services.reports_uow_factory, services.clock, services.id_generator
    )


@moderation_router.get(
    "/reports/{report_id}/review",
    responses={
        status.HTTP_404_NOT_FOUND: _PROBLEM,
        **header_responses(status.HTTP_200_OK, ETAG),
    },
)
async def get_report_review(
    report_id: EntityId,
    actor: ModeratorActor,
    response: Response,
    services: Services,
) -> ReportReviewDetail:
    """Return the review mark of a report's lineage and its history.

    Args:
        report_id: Any revision of the lineage.
        actor: The moderator.
        response: Used to set the review's ``ETag``.
        services: Use cases bound by the composition root.

    Returns:
        The current mark, whether the lineage was revised since, and up to 200
        marks, newest first.
    """
    review = await services.report_queries.get_review(
        GetReportReview(actor=actor, report_id=report_id)
    )
    return _review_response(response, review)


@moderation_router.post(
    "/reports/{report_id}/review",
    responses={
        **_OPTIONAL_IF_MATCH_RESPONSES,
        **header_responses(status.HTTP_200_OK, ETAG),
    },
)
async def mark_report_review(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    report_id: EntityId,
    body: MarkReviewRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: ReviewIfMatch = None,
) -> ReportReviewDetail:
    """Mark a report's lineage ``new``, ``reviewed`` or ``archived``.

    The report is not changed: its status, its ETag and its reporter's rights
    stay as they were. Repeating the last mark changes nothing.

    Args:
        report_id: The revision the moderator looked at.
        body: The mark and its reason.
        actor: The moderator.
        response: Used to set the review's new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional: the review's ETag the mark is based on.

    Returns:
        The review after the mark, with its history. The ``ETag`` names the
        version this mark produced (or found, when it changed nothing), taken
        from the command's result: should another moderator's mark land between
        this commit and the read of the history, the body already shows it and
        an ``If-Match`` with this ETag answers 412, so nobody overwrites a mark
        they were not shown.

    Raises:
        PreconditionFailedError: If ``If-Match`` is stale or names another
            review (412).
        ReviewRevisionSupersededError: If the report is older than the revision
            the lineage was last marked on (409).
        ReviewReasonRequiredError: If the move needs a reason (422).
    """
    expected_version = None
    if if_match is not None:
        # Only the lineage id is needed to read the tag; the version itself is
        # compared inside the command's unit of work.
        current = await services.report_queries.get_review(
            GetReportReview(actor=actor, report_id=report_id)
        )
        expected_version = expected_version_from_if_match(if_match, current.lineage_id)
    result = await _mark_handler(services)(
        MarkReportReview(
            actor=actor,
            report_id=report_id,
            state=body.state,
            reason=body.reason,
            expected_version=expected_version,
        )
    )
    review = await services.report_queries.get_review(
        GetReportReview(actor=actor, report_id=report_id)
    )
    lineage_id = review.lineage_id if result.lineage_id is None else result.lineage_id
    version = review.version if result.version is None else result.version
    set_etag(response, make_etag(version, lineage_id))
    return review


@moderation_router.post("/reports/review")
async def mark_report_reviews(
    body: BulkMarkReviewRequest,
    actor: ModeratorActor,
    services: Services,
) -> BulkReviewResult:
    """Apply one mark to up to 100 reports, each on its own.

    Not atomic: every report gets its own outcome (``marked``, ``unchanged``,
    ``not_found``, ``reason_required``, ``conflict`` or ``error``), in request
    order, and one refusal never keeps the others from being marked. Revisions
    of one lineage are marked once, on the newest one named, and share its
    result.

    Args:
        body: The reports, the mark and its reason.
        actor: The moderator.
        services: Use cases bound by the composition root.

    Returns:
        One result per report.
    """
    return await MarkReportReviewsHandler(_mark_handler(services))(
        MarkReportReviews(
            actor=actor,
            report_ids=body.report_ids,
            state=body.state,
            reason=body.reason,
        )
    )
