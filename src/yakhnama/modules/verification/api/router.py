"""HTTP routes of the verification context, under ``/api/v1/moderation``.

Every route requires ``CanModerate`` before anything is read: a case's history
names moderators and holds their reasons. Transitions are always sent with
``is_human=True``, because a person makes every move through the API; only the
state machine decides whether the move is allowed (409 ``invalid-transition``
otherwise), and ``verified`` is reachable only this way.

The verification commands carry no ``expected_version``, so ``If-Match`` is
optional: when sent it is compared with the case's current tag before the
command runs (412 on mismatch). That narrows but does not close the race with a
concurrent move (open question: version inside the command).

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Header, Query, Response, status

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.verification.api.dependencies import (
    ModeratorActor,
    Services,
    VerificationApiServices,
)
from yakhnama.modules.verification.api.schemas import (
    AssignmentRequest,
    ListCasesParameters,
    TransitionRequest,
    VerificationCasePage,
)
from yakhnama.modules.verification.public import (
    AssignVerificationCase,
    AssignVerificationCaseHandler,
    GetVerificationCase,
    ListVerificationCases,
    TransitionVerification,
    TransitionVerificationHandler,
    VerificationCaseDetail,
)
from yakhnama.platform.etag import (
    PRECONDITION_FAILED_MESSAGE,
    if_match_matches,
    make_etag,
    set_etag,
)
from yakhnama.shared_kernel.errors import PreconditionFailedError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
MODERATION_PREFIX: Final = f"{API_PREFIX}/moderation"
CASES_PATH: Final = f"{MODERATION_PREFIX}/verification-cases"
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
    status.HTTP_412_PRECONDITION_FAILED: _PROBLEM,
}

moderation_router = APIRouter(
    prefix=MODERATION_PREFIX, tags=["moderation"], responses=_COMMON_RESPONSES
)

IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=IF_MATCH_MAX_LENGTH,
        description='Optional: the case\'s ETag, for example "<id>:3".',
    ),
]


def _case_response(
    response: Response, detail: VerificationCaseDetail
) -> VerificationCaseDetail:
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


async def _check_if_match(
    services: VerificationApiServices,
    actor: Actor,
    case_id: EntityId,
    if_match: str | None,
) -> None:
    if if_match is None:
        return
    current = await services.verification_queries.get_case(
        GetVerificationCase(actor=actor, case_id=case_id)
    )
    if not if_match_matches(if_match, make_etag(current.version, current.id)):
        raise PreconditionFailedError(PRECONDITION_FAILED_MESSAGE)


@moderation_router.get("/verification-cases")
async def list_verification_cases(
    parameters: Annotated[ListCasesParameters, Query()],
    actor: ModeratorActor,
    response: Response,
    services: Services,
) -> VerificationCasePage:
    """List verification cases, oldest first, with optional filters.

    Args:
        parameters: Validated filters, cursor and limit.
        actor: The moderator.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        One page of cases.
    """
    page = await services.verification_queries.list_cases(
        ListVerificationCases(
            actor=actor,
            state=parameters.state,
            target_kind=parameters.target_kind,
            assigned_to=parameters.assigned_to,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(following.model_dump(mode="json", exclude_none=True))
        response.headers[LINK_HEADER] = f'<{CASES_PATH}?{query}>; rel="next"'
    return VerificationCasePage(items=page.items, next_cursor=page.next_cursor)


@moderation_router.get(
    "/verification-cases/{case_id}", responses={status.HTTP_404_NOT_FOUND: _PROBLEM}
)
async def get_verification_case(
    case_id: EntityId,
    actor: ModeratorActor,
    response: Response,
    services: Services,
) -> VerificationCaseDetail:
    """Return one case with its history, the states it may move to and its ETag.

    Args:
        case_id: The case.
        actor: The moderator.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The case.
    """
    detail = await services.verification_queries.get_case(
        GetVerificationCase(actor=actor, case_id=case_id)
    )
    return _case_response(response, detail)


@moderation_router.post(
    "/verification/{case_id}/transitions", responses=_CHANGE_RESPONSES
)
async def transition_verification(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    case_id: EntityId,
    body: TransitionRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> VerificationCaseDetail:
    """Move a case to another state of the transition table.

    Args:
        case_id: The case.
        body: The target state and the reason.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the move is based on.

    Returns:
        The case after the move.

    Raises:
        InvalidTransitionError: If the table does not allow the move (409).
        ValidationError: If a required reason is missing (422).
    """
    await _check_if_match(services, actor, case_id, if_match)
    detail = await TransitionVerificationHandler(
        services.verification_handler_dependencies
    )(
        TransitionVerification(
            actor=actor,
            case_id=case_id,
            to_state=body.to_state,
            reason=body.reason,
            is_human=True,
        )
    )
    return _case_response(response, detail)


@moderation_router.post(
    "/verification/{case_id}/assignment", responses=_CHANGE_RESPONSES
)
async def assign_verification_case(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    case_id: EntityId,
    body: AssignmentRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> VerificationCaseDetail:
    """Make a reviewer responsible for a case.

    Args:
        case_id: The case.
        body: The reviewer.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the assignment is based on.

    Returns:
        The case after the assignment.
    """
    await _check_if_match(services, actor, case_id, if_match)
    detail = await AssignVerificationCaseHandler(
        services.verification_handler_dependencies
    )(
        AssignVerificationCase(
            actor=actor, case_id=case_id, reviewer_id=body.reviewer_id
        )
    )
    return _case_response(response, detail)
