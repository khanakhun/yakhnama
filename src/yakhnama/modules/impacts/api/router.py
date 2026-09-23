"""HTTP routes of the impacts context, mounted under ``/api/v1``.

The impact metric registry is public reference data, so both routes are anonymous.
Errors are never mapped here: query services and this router raise
``YakhnamaError`` subclasses and the exception handlers in ``main.py`` render
Problem Details.

Patterns: none from the catalog (thin transport layer over the query service).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query, Response, status

from yakhnama.modules.impacts.api.dependencies import (
    Services,
    require_valid_credentials,
)
from yakhnama.modules.impacts.api.schemas import (
    METRIC_CODE_MAX_LENGTH,
    METRIC_CODE_PATTERN,
    ImpactMetricPage,
    ListImpactMetricsParameters,
)
from yakhnama.modules.impacts.application.dto import ImpactMetricDetail
from yakhnama.modules.impacts.application.queries import ListImpactMetrics
from yakhnama.platform.etag import make_etag, set_etag
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.pagination import PageRequest

IMPACT_METRICS_PATH: Final = "/api/v1/impact-metrics"
LINK_HEADER: Final = "Link"
NOT_FOUND_MESSAGE: Final = "No impact metric has this code."

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}

router = APIRouter(
    prefix="/api/v1",
    tags=["impacts"],
    dependencies=[Depends(require_valid_credentials)],
    responses={
        status.HTTP_401_UNAUTHORIZED: _PROBLEM,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
        status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    },
)

MetricCodePath = Annotated[
    str, Path(pattern=METRIC_CODE_PATTERN, max_length=METRIC_CODE_MAX_LENGTH)
]


@router.get("/impact-metrics")
async def list_impact_metrics(
    parameters: Annotated[ListImpactMetricsParameters, Query()],
    response: Response,
    services: Services,
) -> ImpactMetricPage:
    """List impact metrics, ordered by code, active ones only unless asked.

    Args:
        parameters: Validated filters, cursor and limit.
        response: Used to set the ``Link`` header of the next page.
        services: Query services bound by the composition root.

    Returns:
        One page of impact metrics.
    """
    page = await services.impact_metric_query_service.list_impact_metrics(
        ListImpactMetrics(
            category=parameters.category,
            include_retired=parameters.include_retired,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(following.model_dump(mode="json", exclude_none=True))
        response.headers[LINK_HEADER] = f'<{IMPACT_METRICS_PATH}?{query}>; rel="next"'
    return ImpactMetricPage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/impact-metrics/{code}",
    responses={status.HTTP_404_NOT_FOUND: _PROBLEM},
)
async def get_impact_metric(
    code: MetricCodePath,
    response: Response,
    services: Services,
) -> ImpactMetricDetail:
    """Return one impact metric, active or retired, with its ``ETag``.

    Args:
        code: The metric code.
        response: Used to set the ``ETag`` header.
        services: Query services bound by the composition root.

    Returns:
        The impact metric.

    Raises:
        NotFoundError: If no impact metric has this code.
    """
    detail = await services.impact_metric_query_service.get(code)
    if detail is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    set_etag(response, make_etag(detail.version, detail.id))
    return detail
