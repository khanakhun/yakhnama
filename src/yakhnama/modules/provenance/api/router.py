"""HTTP routes of the provenance context, mounted under ``/api/v1``.

Sources are the provenance of the open dataset, so ``GET /sources`` and
``GET /sources/{id}`` are anonymous (``source_read_policy``). Registering a source
through the API is a moderation task (``POST /moderation/sources``, ``CanModerate``
first): citizen and organisation sources are registered by the platform itself
when a report is submitted, and the higher-ranked types need a moderator anyway.

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from yakhnama.modules.provenance.api.dependencies import (
    ModeratorActor,
    OptionalActor,
    Services,
)
from yakhnama.modules.provenance.api.schemas import (
    ListSourcesParameters,
    RegisterSourceRequest,
    SourcePage,
)
from yakhnama.modules.provenance.public import (
    GetSource,
    ListSources,
    RegisterSource,
    SourceDetail,
)
from yakhnama.platform.etag import make_etag, set_etag
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
SOURCES_PATH: Final = f"{API_PREFIX}/sources"
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

router = APIRouter(prefix=API_PREFIX, tags=["provenance"], responses=_COMMON_RESPONSES)
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


@router.get("/sources")
async def list_sources(
    parameters: Annotated[ListSourcesParameters, Query()],
    actor: OptionalActor,
    response: Response,
    services: Services,
) -> SourcePage:
    """List sources, newest first, optionally of one type.

    Args:
        parameters: Validated filter, cursor and limit.
        actor: The caller, anonymous or authenticated.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.

    Returns:
        One page of sources.
    """
    page = await services.source_queries.list_sources(
        ListSources(
            actor=actor,
            source_type=parameters.source_type,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(following.model_dump(mode="json", exclude_none=True))
        response.headers[LINK_HEADER] = f'<{SOURCES_PATH}?{query}>; rel="next"'
    return SourcePage(items=page.items, next_cursor=page.next_cursor)


@router.get("/sources/{source_id}", responses={status.HTTP_404_NOT_FOUND: _PROBLEM})
async def get_source(
    source_id: EntityId,
    actor: OptionalActor,
    response: Response,
    services: Services,
) -> SourceDetail:
    """Return one source with its ``ETag``.

    Args:
        source_id: The source.
        actor: The caller, anonymous or authenticated.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The source.
    """
    detail = await services.source_queries.get_source(
        GetSource(actor=actor, source_id=source_id)
    )
    set_etag(response, make_etag(detail.version, detail.id))
    return detail


@moderation_router.post(
    "/sources",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: _PROBLEM,
        status.HTTP_409_CONFLICT: _PROBLEM,
    },
)
async def register_source(
    body: RegisterSourceRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> SourceDetail:
    """Register a government, news, research, dataset or other source.

    Args:
        body: Type, details and optional organisation.
        actor: The moderator; they become the source's owner.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The registered source.
    """
    del idempotency_key
    detail = await services.source_registrar(
        RegisterSource(
            actor=actor,
            source_type=body.source_type,
            details=body.details,
            organization_id=body.organization_id,
        )
    )
    response.headers[LOCATION_HEADER] = f"{SOURCES_PATH}/{detail.id}"
    set_etag(response, make_etag(detail.version, detail.id))
    return detail
