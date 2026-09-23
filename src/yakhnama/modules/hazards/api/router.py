"""HTTP routes of the hazards context, mounted under ``/api/v1``.

The hazard taxonomy is public reference data, so both routes are anonymous. Errors
are never mapped here: query services and this router raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over the query service).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query, Response, status

from yakhnama.modules.hazards.api.dependencies import (
    Services,
    require_valid_credentials,
)
from yakhnama.modules.hazards.api.schemas import (
    HAZARD_CODE_MAX_LENGTH,
    HAZARD_CODE_PATTERN,
    HazardTypePage,
    ListHazardTypesParameters,
)
from yakhnama.modules.hazards.application.dto import HazardTypeDetail
from yakhnama.modules.hazards.application.queries import ListHazardTypes
from yakhnama.platform.etag import make_etag, set_etag
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.pagination import PageRequest

HAZARD_TYPES_PATH: Final = "/api/v1/hazard-types"
LINK_HEADER: Final = "Link"
NOT_FOUND_MESSAGE: Final = "No hazard type has this code."

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}

router = APIRouter(
    prefix="/api/v1",
    tags=["hazards"],
    dependencies=[Depends(require_valid_credentials)],
    responses={
        status.HTTP_401_UNAUTHORIZED: _PROBLEM,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
        status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    },
)

HazardCodePath = Annotated[
    str, Path(pattern=HAZARD_CODE_PATTERN, max_length=HAZARD_CODE_MAX_LENGTH)
]


@router.get("/hazard-types")
async def list_hazard_types(
    parameters: Annotated[ListHazardTypesParameters, Query()],
    response: Response,
    services: Services,
) -> HazardTypePage:
    """List hazard types, ordered by code, active ones only unless asked.

    Args:
        parameters: Validated filters, cursor and limit.
        response: Used to set the ``Link`` header of the next page.
        services: Query services bound by the composition root.

    Returns:
        One page of hazard types.
    """
    page = await services.hazard_type_query_service.list_hazard_types(
        ListHazardTypes(
            include_retired=parameters.include_retired,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(following.model_dump(mode="json", exclude_none=True))
        response.headers[LINK_HEADER] = f'<{HAZARD_TYPES_PATH}?{query}>; rel="next"'
    return HazardTypePage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/hazard-types/{code}",
    responses={status.HTTP_404_NOT_FOUND: _PROBLEM},
)
async def get_hazard_type(
    code: HazardCodePath,
    response: Response,
    services: Services,
) -> HazardTypeDetail:
    """Return one hazard type, active or retired, with its ``ETag``.

    Args:
        code: The hazard code, for example ``glof``.
        response: Used to set the ``ETag`` header.
        services: Query services bound by the composition root.

    Returns:
        The hazard type.

    Raises:
        NotFoundError: If no hazard type has this code.
    """
    detail = await services.hazard_type_query_service.get(code)
    if detail is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    set_etag(response, make_etag(detail.version, detail.id))
    return detail
