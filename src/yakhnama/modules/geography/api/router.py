"""HTTP routes of the geography context, mounted under ``/api/v1``.

Places are public reference data, so both routes are anonymous. Each negotiates its
representation: ``?format=geojson`` or ``Accept: application/geo+json`` returns
GeoJSON (RFC 7946) whose geometry is the place's centroid (``null`` when unknown);
anything else returns JSON. ``?format=`` wins over ``Accept`` so a link can pin the
representation, and every response says ``Vary: Accept`` for caches.

Errors are never mapped here: query services and this router raise
``YakhnamaError`` subclasses and the exception handlers in ``main.py`` render
Problem Details.

Patterns: none from the catalog (thin transport layer over the query service).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, Query, Response, status
from geojson_pydantic import Feature, FeatureCollection, Point

from yakhnama.modules.geography.api.dependencies import (
    Services,
    require_valid_credentials,
)
from yakhnama.modules.geography.api.schemas import (
    GetPlaceParameters,
    ListPlacesParameters,
    PlaceFeatureProperties,
    PlaceFormat,
    PlacePage,
)
from yakhnama.modules.geography.application.dto import PlaceDetail
from yakhnama.modules.geography.application.queries import SearchPlaces
from yakhnama.platform.etag import ETAG_HEADER, make_etag
from yakhnama.shared_kernel.errors import NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

GEOJSON_MEDIA_TYPE: Final = "application/geo+json"
PLACES_PATH: Final = "/api/v1/places"
LINK_HEADER: Final = "Link"
VARY_HEADER: Final = "Vary"
ACCEPT_MAX_LENGTH: Final = 1024
NOT_FOUND_MESSAGE: Final = "No place has this id."

# Any: the value type of FastAPI's own ``responses`` argument.
_PROBLEM: Final[dict[str, Any]] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}
_GEOJSON_ALTERNATIVE: Final[dict[str, Any]] = {
    "description": "JSON, or GeoJSON when negotiated",
    "content": {GEOJSON_MEDIA_TYPE: {}},
}

PlaceSearchFeature = Feature[Point, PlaceFeatureProperties]
PlaceDetailFeature = Feature[Point, PlaceDetail]

router = APIRouter(
    prefix="/api/v1",
    tags=["geography"],
    dependencies=[Depends(require_valid_credentials)],
    responses={
        status.HTTP_401_UNAUTHORIZED: _PROBLEM,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
        status.HTTP_429_TOO_MANY_REQUESTS: _PROBLEM,
    },
)

AcceptHeader = Annotated[str | None, Header(max_length=ACCEPT_MAX_LENGTH)]


def wants_geojson(output_format: PlaceFormat | None, accept: str | None) -> bool:
    """Tell whether the client asked for GeoJSON.

    Args:
        output_format: The ``format`` query parameter, if sent.
        accept: The ``Accept`` header, if sent.

    Returns:
        ``True`` for ``format=geojson``, or with no ``format`` when ``Accept``
        names ``application/geo+json``.
    """
    if output_format is not None:
        return output_format is PlaceFormat.GEOJSON
    # A substring match: quality values are not weighed, because the only
    # alternative representation is JSON, which is also the default.
    return accept is not None and GEOJSON_MEDIA_TYPE in accept.lower()


def _geojson_response(body: str, headers: dict[str, str]) -> Response:
    return Response(content=body, media_type=GEOJSON_MEDIA_TYPE, headers=headers)


@router.get(
    "/places",
    response_model=PlacePage,
    responses={status.HTTP_200_OK: _GEOJSON_ALTERNATIVE},
)
async def list_places(
    parameters: Annotated[ListPlacesParameters, Query()],
    response: Response,
    services: Services,
    accept: AcceptHeader = None,
) -> PlacePage | Response:
    """Search places by name, as JSON or as a GeoJSON ``FeatureCollection``.

    Args:
        parameters: Validated search text, filters, cursor and limit.
        response: Used to set ``Link`` and ``Vary`` on the JSON response.
        services: Query services bound by the composition root.
        accept: ``application/geo+json`` selects GeoJSON.

    Returns:
        One page of places in the negotiated representation. The GeoJSON form
        carries the next page only in the ``Link`` header.
    """
    page = await services.place_query_service.search(
        SearchPlaces(
            text=parameters.q,
            language=parameters.language,
            level=parameters.level,
            include_inactive=parameters.include_inactive,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    headers = {VARY_HEADER: "Accept"}
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(
            following.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        headers[LINK_HEADER] = f'<{PLACES_PATH}?{query}>; rel="next"'
    if wants_geojson(parameters.output_format, accept):
        collection = FeatureCollection[PlaceSearchFeature](
            type="FeatureCollection",
            features=[
                PlaceSearchFeature(
                    type="Feature",
                    id=str(summary.id),
                    geometry=(
                        None
                        if summary.centroid is None
                        else summary.centroid.to_geojson_point()
                    ),
                    properties=PlaceFeatureProperties.from_summary(summary),
                )
                for summary in page.items
            ],
        )
        return _geojson_response(collection.model_dump_json(), headers)
    response.headers.update(headers)
    return PlacePage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/places/{place_id}",
    response_model=PlaceDetail,
    responses={
        status.HTTP_200_OK: _GEOJSON_ALTERNATIVE,
        status.HTTP_404_NOT_FOUND: _PROBLEM,
    },
)
async def get_place(
    place_id: EntityId,
    parameters: Annotated[GetPlaceParameters, Query()],
    response: Response,
    services: Services,
    accept: AcceptHeader = None,
) -> PlaceDetail | Response:
    """Return one place, whatever its status, with its ``ETag``.

    Args:
        place_id: The place id (UUIDv7).
        parameters: The optional ``format``.
        response: Used to set ``ETag`` and ``Vary`` on the JSON response.
        services: Query services bound by the composition root.
        accept: ``application/geo+json`` selects a GeoJSON ``Feature``.

    Returns:
        The place in the negotiated representation.

    Raises:
        NotFoundError: If no place has this id.
    """
    detail = await services.place_query_service.get(place_id)
    if detail is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    headers = {
        ETAG_HEADER: make_etag(detail.version, detail.id),
        VARY_HEADER: "Accept",
    }
    if wants_geojson(parameters.output_format, accept):
        feature = PlaceDetailFeature(
            type="Feature",
            id=str(detail.id),
            geometry=(
                None if detail.centroid is None else detail.centroid.to_geojson_point()
            ),
            properties=detail,
        )
        return _geojson_response(feature.model_dump_json(), headers)
    response.headers.update(headers)
    return detail
