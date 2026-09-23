"""HTTP routes of the events context, mounted under ``/api/v1``.

``router`` serves the public record: ``GET /events``, ``/events/{id}`` and
``/events/{id}/timeline`` are anonymous, and anyone but a moderator only ever sees
events that are both ``published`` and ``verified`` (``EventRecordQueryService``
narrows the search and reports hidden events as missing). The listing and the
detail negotiate GeoJSON like ``GET /places``: ``?format=geojson`` or
``Accept: application/geo+json``, ``?format=`` winning, ``Vary: Accept`` always.

``moderation_router`` serves ``/moderation/events/...``; every route requires
``CanModerate`` before anything is read. The events commands carry no
``expected_version``, so ``If-Match`` is optional there: when sent it is compared
with the event's current tag before the command runs (412 on mismatch). That check
narrows but does not close the race with a concurrent change; closing it needs the
version inside the command (open question). Each change answers with the event's
new detail and ``ETag``.

Errors are never mapped here: handlers and query services raise ``YakhnamaError``
subclasses and the exception handlers in ``main.py`` render Problem Details.

Patterns: none from the catalog (thin transport layer over commands and queries).
"""

from typing import Annotated, Any, Final
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from geojson_pydantic import Feature, FeatureCollection, MultiPolygon, Point, Polygon

from yakhnama.modules.events.api.dependencies import (
    EventsApiServices,
    ModeratorActor,
    OptionalActor,
    Services,
)
from yakhnama.modules.events.api.schemas import (
    AddAffectedPlaceRequest,
    CreateEventRequest,
    EventFeatureProperties,
    EventFormat,
    EventPage,
    EventTimeline,
    GetEventParameters,
    LinkReportRequest,
    ListEventsParameters,
    MergeEventsRequest,
    ReasonRequest,
    RelateEventsRequest,
    UpdateEventRequest,
)
from yakhnama.modules.events.public import (
    AddAffectedPlace,
    AddAffectedPlaceHandler,
    CreateEventFromReports,
    CreateEventFromReportsHandler,
    EventDetail,
    GetEvent,
    GetEventTimeline,
    LinkReportToEvent,
    LinkReportToEventHandler,
    ListEvents,
    MergeEvents,
    MergeEventsHandler,
    PublishEvent,
    PublishEventHandler,
    RelateEvents,
    RelateEventsHandler,
    RetractEvent,
    RetractEventHandler,
    SetEventAttributes,
    SetEventAttributesHandler,
    SetEventGeometry,
    SetEventGeometryHandler,
    SetEventPeriod,
    SetEventPeriodHandler,
    UnlinkReportFromEvent,
    UnlinkReportFromEventHandler,
)
from yakhnama.modules.hazards.public import HazardTypeRef
from yakhnama.modules.identity.public import Actor
from yakhnama.platform.etag import (
    ETAG_HEADER,
    PRECONDITION_FAILED_MESSAGE,
    if_match_matches,
    make_etag,
    set_etag,
)
from yakhnama.shared_kernel.errors import PreconditionFailedError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest

API_PREFIX: Final = "/api/v1"
EVENTS_PATH: Final = f"{API_PREFIX}/events"
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
_PUBLIC_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
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

EventListFeature = Feature[Point, EventFeatureProperties]
EventDetailFeature = Feature[Point | Polygon | MultiPolygon, EventDetail]

router = APIRouter(prefix=API_PREFIX, tags=["events"], responses=_PUBLIC_RESPONSES)
moderation_router = APIRouter(
    prefix=f"{API_PREFIX}/moderation",
    tags=["moderation"],
    responses=_PUBLIC_RESPONSES,
)

AcceptHeader = Annotated[str | None, Header(max_length=ACCEPT_MAX_LENGTH)]
IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=IF_MATCH_MAX_LENGTH,
        description='Optional: the event\'s ETag, for example "<id>:3".',
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


def wants_geojson(output_format: EventFormat | None, accept: str | None) -> bool:
    """Tell whether the client asked for GeoJSON.

    Args:
        output_format: The ``format`` query parameter, if sent.
        accept: The ``Accept`` header, if sent.

    Returns:
        ``True`` for ``format=geojson``, or with no ``format`` when ``Accept``
        names ``application/geo+json``.
    """
    if output_format is not None:
        return output_format is EventFormat.GEOJSON
    # A substring match: the only alternative representation is JSON, which is also
    # the default, so quality values need not be weighed.
    return accept is not None and GEOJSON_MEDIA_TYPE in accept.lower()


def _event_etag(detail: EventDetail) -> str:
    return make_etag(detail.version, detail.id)


def _detail_geometry(detail: EventDetail) -> Point | Polygon | MultiPolygon | None:
    if detail.geometry is not None:
        return detail.geometry
    return None if detail.centroid is None else detail.centroid.to_geojson_point()


async def _read_event(
    services: EventsApiServices, actor: Actor, event_id: EntityId
) -> EventDetail:
    return await services.event_queries.get_event(
        GetEvent(actor=actor, event_id=event_id)
    )


async def _check_if_match(
    services: EventsApiServices,
    actor: Actor,
    event_id: EntityId,
    if_match: str | None,
) -> None:
    if if_match is None:
        return
    current = await _read_event(services, actor, event_id)
    if not if_match_matches(if_match, _event_etag(current)):
        raise PreconditionFailedError(PRECONDITION_FAILED_MESSAGE)


async def _changed_event(
    services: EventsApiServices,
    actor: Actor,
    event_id: EntityId,
    response: Response,
) -> EventDetail:
    detail = await _read_event(services, actor, event_id)
    set_etag(response, _event_etag(detail))
    return detail


# --------------------------------------------------------------------------- #
# The public record                                                           #
# --------------------------------------------------------------------------- #


@router.get(
    "/events",
    response_model=EventPage,
    responses={status.HTTP_200_OK: _GEOJSON_ALTERNATIVE},
)
async def list_events(
    parameters: Annotated[ListEventsParameters, Query()],
    actor: OptionalActor,
    response: Response,
    services: Services,
    accept: AcceptHeader = None,
) -> EventPage | Response:
    """List the events the caller may see, as JSON or GeoJSON.

    Args:
        parameters: Validated filters, format, cursor and limit.
        actor: The caller, anonymous or authenticated.
        response: Used to set ``Link`` and ``Vary`` on the JSON response.
        services: Use cases bound by the composition root.
        accept: ``application/geo+json`` selects a GeoJSON ``FeatureCollection``.

    Returns:
        One page of events. The GeoJSON form places each event at its centroid
        (``null`` when unknown) and carries the next page only in ``Link``.
    """
    page = await services.event_queries.list_events(
        ListEvents(
            actor=actor,
            bbox=parameters.bounding_box(),
            hazard_type=parameters.hazard_type,
            place_code=parameters.place_code,
            status=parameters.status,
            period_from=parameters.period_from,
            period_to=parameters.period_to,
            verified_only=parameters.verified_only,
            page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
        )
    )
    headers = {VARY_HEADER: "Accept"}
    if page.next_cursor is not None:
        following = parameters.model_copy(update={"cursor": page.next_cursor})
        query = urlencode(
            following.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        headers[LINK_HEADER] = f'<{EVENTS_PATH}?{query}>; rel="next"'
    if wants_geojson(parameters.output_format, accept):
        collection = FeatureCollection[EventListFeature](
            type="FeatureCollection",
            features=[
                EventListFeature(
                    type="Feature",
                    id=str(summary.id),
                    geometry=(
                        None
                        if summary.centroid is None
                        else summary.centroid.to_geojson_point()
                    ),
                    properties=EventFeatureProperties.from_summary(summary),
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
    return EventPage(items=page.items, next_cursor=page.next_cursor)


@router.get(
    "/events/{event_id}",
    response_model=EventDetail,
    responses={
        status.HTTP_200_OK: _GEOJSON_ALTERNATIVE,
        status.HTTP_404_NOT_FOUND: _PROBLEM,
    },
)
async def get_event(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    parameters: Annotated[GetEventParameters, Query()],
    actor: OptionalActor,
    response: Response,
    services: Services,
    accept: AcceptHeader = None,
) -> EventDetail | Response:
    """Return one event the caller may see, with its ``ETag``.

    Args:
        event_id: The event (UUIDv7).
        parameters: The optional ``format``.
        actor: The caller, anonymous or authenticated.
        response: Used to set ``ETag`` and ``Vary`` on the JSON response.
        services: Use cases bound by the composition root.
        accept: ``application/geo+json`` selects a GeoJSON ``Feature``.

    Returns:
        The event. The GeoJSON geometry is the event's own geometry, else its
        centroid, else ``null``.

    Raises:
        EventNotFoundError: If the event does not exist or the caller may not see
            it (the two are not told apart).
    """
    detail = await _read_event(services, actor, event_id)
    headers = {ETAG_HEADER: _event_etag(detail), VARY_HEADER: "Accept"}
    if wants_geojson(parameters.output_format, accept):
        feature = EventDetailFeature(
            type="Feature",
            id=str(detail.id),
            geometry=_detail_geometry(detail),
            properties=detail,
        )
        return Response(
            content=feature.model_dump_json(),
            media_type=GEOJSON_MEDIA_TYPE,
            headers=headers,
        )
    response.headers.update(headers)
    return detail


@router.get(
    "/events/{event_id}/timeline", responses={status.HTTP_404_NOT_FOUND: _PROBLEM}
)
async def get_event_timeline(
    event_id: EntityId, actor: OptionalActor, services: Services
) -> EventTimeline:
    """Return the dated history of one event the caller may see.

    Args:
        event_id: The event.
        actor: The caller, anonymous or authenticated.
        services: Use cases bound by the composition root.

    Returns:
        Report observations, start and end, verification transitions and impact
        claims, in timeline order.
    """
    entries = await services.event_queries.get_timeline(
        GetEventTimeline(actor=actor, event_id=event_id)
    )
    return EventTimeline(event_id=event_id, entries=entries)


# --------------------------------------------------------------------------- #
# Moderation                                                                  #
# --------------------------------------------------------------------------- #


@moderation_router.post(
    "/events",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: _PROBLEM,
        status.HTTP_404_NOT_FOUND: _PROBLEM,
        status.HTTP_409_CONFLICT: _PROBLEM,
    },
)
async def create_event(
    body: CreateEventRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    idempotency_key: IdempotencyKey = None,
) -> EventDetail:
    """Create a draft event from the reports that describe it.

    Its period and centroid are derived from the rounded report points, its
    sources are those of the reports, and its verification case is opened.

    Args:
        body: Reports, hazard type, title, summary and attributes.
        actor: The moderator.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        idempotency_key: Documented here; the idempotency middleware acts on it.

    Returns:
        The new event.
    """
    del idempotency_key
    event_id = await CreateEventFromReportsHandler(services.event_handler_dependencies)(
        CreateEventFromReports(
            actor=actor,
            report_ids=body.report_ids,
            hazard_type=HazardTypeRef(code=body.hazard_type),
            title=body.title,
            summary=body.summary,
            attributes=body.attributes,
        )
    )
    response.headers[LOCATION_HEADER] = f"{EVENTS_PATH}/{event_id}"
    return await _changed_event(services, actor, event_id, response)


@moderation_router.post("/events/{event_id}/reports", responses=_CHANGE_RESPONSES)
async def link_report(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    body: LinkReportRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Link one more report to an event.

    Args:
        event_id: The event.
        body: The report and its role.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await LinkReportToEventHandler(services.event_handler_dependencies)(
        LinkReportToEvent(
            actor=actor, event_id=event_id, report_id=body.report_id, role=body.role
        )
    )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.delete(
    "/events/{event_id}/reports/{report_id}", responses=_CHANGE_RESPONSES
)
async def unlink_report(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    report_id: EntityId,
    body: ReasonRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Remove a report link with a reason; the record of the link is kept.

    The reason travels in a JSON body, which ``DELETE`` allows (RFC 9110 §9.3.5)
    and which keeps it out of URLs and access logs.

    Args:
        event_id: The event.
        report_id: The linked report.
        body: Why the link is removed.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await UnlinkReportFromEventHandler(services.event_handler_dependencies)(
        UnlinkReportFromEvent(
            actor=actor, event_id=event_id, report_id=report_id, reason=body.reason
        )
    )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.post("/events/{event_id}/relations", responses=_CHANGE_RESPONSES)
async def relate_events(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    body: RelateEventsRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Record a typed relation from this event to another.

    Args:
        event_id: The event the relation starts from.
        body: The other event, the kind and an optional note.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change, with the relation.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await RelateEventsHandler(services.event_handler_dependencies)(
        RelateEvents(
            actor=actor,
            from_event_id=event_id,
            to_event_id=body.to_event_id,
            kind=body.kind,
            note=body.note,
        )
    )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.patch("/events/{event_id}", responses=_CHANGE_RESPONSES)
async def update_event(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    body: UpdateEventRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Change an event's geometry, period and/or attributes.

    Each member sent is applied by its own command, so a failure part-way leaves
    the members before it applied; the response always shows the current event.

    Args:
        event_id: The event.
        body: The members to change.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change.
    """
    await _check_if_match(services, actor, event_id, if_match)
    dependencies = services.event_handler_dependencies
    sent = body.model_fields_set
    if "geometry" in sent:
        await SetEventGeometryHandler(dependencies)(
            SetEventGeometry(
                actor=actor, event_id=event_id, geometry=body.event_geometry()
            )
        )
    if body.period is not None:
        await SetEventPeriodHandler(dependencies)(
            SetEventPeriod(actor=actor, event_id=event_id, period=body.period)
        )
    if "attributes" in sent:
        await SetEventAttributesHandler(dependencies)(
            SetEventAttributes(
                actor=actor, event_id=event_id, attributes=body.attributes
            )
        )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.post("/events/{event_id}/places", responses=_CHANGE_RESPONSES)
async def add_affected_place(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    body: AddAffectedPlaceRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Add a gazetteer place the event concerns.

    Args:
        event_id: The event.
        body: The place code and how it relates.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await AddAffectedPlaceHandler(services.event_handler_dependencies)(
        AddAffectedPlace(actor=actor, event_id=event_id, place=body.to_affected_place())
    )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.post("/events/{event_id}/publication", responses=_CHANGE_RESPONSES)
async def publish_event(
    event_id: EntityId,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Publish a draft event; it becomes public once it is also verified.

    Args:
        event_id: The event.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await PublishEventHandler(services.event_handler_dependencies)(
        PublishEvent(actor=actor, event_id=event_id)
    )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.post("/events/{event_id}/retraction", responses=_CHANGE_RESPONSES)
async def retract_event(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    body: ReasonRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Retract an event; it stays stored and readable with its reason.

    Args:
        event_id: The event.
        body: Why.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag the change is based on.

    Returns:
        The event after the change.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await RetractEventHandler(services.event_handler_dependencies)(
        RetractEvent(actor=actor, event_id=event_id, reason=body.reason)
    )
    return await _changed_event(services, actor, event_id, response)


@moderation_router.post("/events/{event_id}/merge", responses=_CHANGE_RESPONSES)
async def merge_events(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: EntityId,
    body: MergeEventsRequest,
    actor: ModeratorActor,
    response: Response,
    services: Services,
    if_match: IfMatch = None,
) -> EventDetail:
    """Merge this event into another that describes the same occurrence.

    Args:
        event_id: The event that is merged and becomes final.
        body: The surviving event and the reason.
        actor: The moderator.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        if_match: Optional ETag of the merged event.

    Returns:
        The merged event, pointing at the survivor in ``merged_into``.
    """
    await _check_if_match(services, actor, event_id, if_match)
    await MergeEventsHandler(services.event_handler_dependencies)(
        MergeEvents(
            actor=actor,
            event_id=event_id,
            into_event_id=body.into_event_id,
            reason=body.reason,
        )
    )
    return await _changed_event(services, actor, event_id, response)
