---
name: add-api-endpoint
description: Expose a command or query over HTTP under /api/v1 - bounded request/response schemas, dependencies, Policy-backed authorisation, Problem Details errors, cursor pagination, Idempotency-Key on creating POSTs, ETag/If-Match on mutable resources, GeoJSON negotiation for spatial listings, API tests and the OpenAPI snapshot.
---

# add-api-endpoint

## When to use

- A command or query exists and must be reachable over HTTP.
- Not for new business rules: those belong in the domain or application layer first.

## Preconditions

- The command handler (`add-command`) and/or query service (`add-query`) exist with
  unit tests. `application/dto.py` re-exports any domain enum a schema needs (here
  `DatePrecision`), because the api layer imports application, not domain.
  `application/ports.py` declares callable aliases for decorated use cases,
  for example `RecordEventUseCase = Callable[[RecordEvent, Actor], Awaitable[UUID]]`.
- Platform pieces exist (architect; check the real names):
  - `yakhnama.platform.auth.require_actor` — FastAPI dependency returning the
    authenticated `Actor` or raising the error `main.py` maps to 401;
  - the exception handlers in `main.py` that render every `YakhnamaError` as
    `application/problem+json` (RFC 9457): `NotFoundError` → 404,
    `PermissionDeniedError` → 403, `ConflictError` → 409, `ValidationError` and request
    validation → 422, and `yakhnama.shared_kernel.errors.PreconditionFailedError` → 412.
    `PreconditionFailedError` is proposed in ADR 0012 and added to the shared kernel in
    Phase 1 once approved; handlers raise it
    (or a subclass) on a version mismatch, so `main.py` never imports module errors;
  - the idempotency Decorator in `platform/decorators.py` that the composition root
    wraps around creating handlers (replay returns the original result; same key with a
    different body is rejected).
- `tests/api/conftest.py` (test-engineer) provides `app: FastAPI` built by the app
  factory, `client: httpx.AsyncClient` over `ASGITransport`, and `moderator_headers` /
  other role headers carrying locally signed test JWTs. No network.
- `tests/contract/openapi.json` and the `poetry run poe openapi-snapshot` task exist. Both
  are added in Phase 2; before that, say in your return that the snapshot step could not
  run instead of skipping it silently.

## Owning subagent

`api-engineer` (router, schemas, dependencies, route registration lines in `main.py`,
API tests, OpenAPI snapshot). `security-reviewer` reviews every endpoint.

## Files

The example exposes an `events` module (example only: the real event model is the
domain-modeler's).

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `src/yakhnama/modules/<m>/api/schemas.py` | create/modify | Bounded request, response and query-parameter models |
| `src/yakhnama/modules/<m>/api/dependencies.py` | create/modify | Services provider, `Idempotency-Key` and `If-Match` dependencies |
| `src/yakhnama/modules/<m>/api/router.py` | create/modify | Routes |
| `src/yakhnama/main.py` | modify | `app.include_router(...)` inside `create_app` only; the composition root sets `app.state.<m>_services` (architect) |
| `tests/fakes/<m>.py` | modify | `Fake<M>ApiServices` |
| `tests/api/modules/<m>/test_router.py` | create/modify | HTTP tests |
| `tests/contract/openapi.json` | modify | Regenerated snapshot |

## Steps

1. Choose the paths: plural kebab-case resources under `/api/v1`; moderator routes under
   `/api/v1/moderation/`. Verified public reads are anonymous; everything else depends on
   `require_actor`.
2. Write the schemas (`Implements: API Schema`, ADR 0011). Every string has `max_length` (and a pattern when it is a code),
   every number has bounds and `allow_inf_nan=False`, datetimes are `AwareDatetime`,
   request models use `extra="forbid"`. JSON is `snake_case`. Query strings of listings
   are one model (`Annotated[Model, Query()]`).
3. Write `dependencies.py`: a `Protocol` of the services the router needs and a provider
   that reads them from `request.app.state` (never import `platform.container`), plus
   header dependencies for `Idempotency-Key` and `If-Match`.
4. Write the routes:
   - list: cursor pagination (`limit` ≤ 200, opaque `cursor`), `Link: <...>; rel="next"`
     header, and for spatial listings `Accept: application/geo+json` returns a GeoJSON
     `FeatureCollection` (documented as a second 200 content type);
   - read one: set a strong `ETag` from the resource version;
   - creating POST: require `Idempotency-Key`, return 201 with `Location` and `ETag`;
   - update: require `If-Match`, pass the expected version into the command, return the
     new `ETag`; a stale version surfaces as 412 via the central handler;
   - never catch domain errors in the router, never raise `HTTPException` for business
     rules; document each Problem Details status in `responses=`.
5. Register the router: inside `create_app` in `src/yakhnama/main.py`, add the import
   and one `app.include_router(<m>_router)` line. api-engineer owns only these lines.
6. Write the fake services and the API tests below.
7. Run `poetry run poe openapi-snapshot`, review the diff of
   `tests/contract/openapi.json` line by line (removed fields or narrowed types are
   breaking changes and need an ADR), and include the diff summary in your return.

## Templates

### `src/yakhnama/modules/events/api/schemas.py`
```python
"""Request and response bodies of the events HTTP API.

Patterns: API Schema.

Every field is bounded so a hostile client cannot send unbounded payloads.
"""

from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from yakhnama.modules.events.application.dto import DatePrecision, EventDTO
from yakhnama.shared_kernel.pagination import MAX_PAGE_SIZE

HazardCodeParam = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", max_length=64),
]
EventTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0, allow_inf_nan=False)]


class ListEventsParameters(BaseModel):
    """Query string of ``GET /api/v1/events``.

    Implements: API Schema.

    Attributes:
        cursor: Opaque cursor from a previous page.
        limit: Page size, at most 200.
        hazard_code: Only events of this hazard type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: Annotated[str | None, Field(max_length=512)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = 50
    hazard_code: HazardCodeParam | None = None


class RecordEventRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events``.

    Implements: API Schema.

    Attributes:
        hazard_code: Active hazard type code, for example ``"glof"``.
        title: Short human title.
        occurred_at: When the event happened, with an explicit UTC offset.
        date_precision: How precisely ``occurred_at`` is known.
        longitude: WGS84 longitude of the representative point.
        latitude: WGS84 latitude of the representative point.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hazard_code: HazardCodeParam
    title: EventTitle
    occurred_at: AwareDatetime
    date_precision: DatePrecision
    longitude: Longitude
    latitude: Latitude


class UpdateEventTitleRequest(BaseModel):
    """Body of ``PATCH /api/v1/moderation/events/{event_id}``.

    Implements: API Schema.

    Attributes:
        title: The new title.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: EventTitle


class EventResponse(BaseModel):
    """One event as returned by the API.

    Implements: API Schema.

    Attributes:
        id: Event id.
        hazard_code: Hazard type code.
        title: Short human title.
        occurred_at: When the event happened, UTC.
        date_precision: How precisely ``occurred_at`` is known.
        longitude: WGS84 longitude.
        latitude: WGS84 latitude.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    hazard_code: str
    title: str
    occurred_at: AwareDatetime
    date_precision: DatePrecision
    longitude: float
    latitude: float

    @classmethod
    def from_dto(cls, dto: EventDTO) -> "EventResponse":
        """Build the response body from the application DTO.

        Args:
            dto: The event read model.

        Returns:
            The transport representation.
        """
        return cls(
            id=dto.id,
            hazard_code=dto.hazard_code,
            title=dto.title,
            occurred_at=dto.occurred_at,
            date_precision=dto.date_precision,
            longitude=dto.longitude,
            latitude=dto.latitude,
        )


class EventPageResponse(BaseModel):
    """One page of events.

    Implements: API Schema.

    Attributes:
        items: The events on this page.
        next_cursor: Opaque cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[EventResponse, ...]
    next_cursor: str | None


class EventFeatureProperties(BaseModel):
    """GeoJSON ``properties`` of an event feature.

    Implements: API Schema.

    Attributes:
        id: Event id.
        hazard_code: Hazard type code.
        title: Short human title.
        occurred_at: When the event happened, UTC.
        date_precision: How precisely ``occurred_at`` is known.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    hazard_code: str
    title: str
    occurred_at: AwareDatetime
    date_precision: DatePrecision
```

### `src/yakhnama/modules/events/api/dependencies.py`
```python
"""FastAPI dependencies of the events HTTP API.

Patterns: Dependency Injection.

The composition root (``main.py``) stores an ``EventsApiServices`` implementation
on ``app.state.events_services``. Reading it from the request keeps this package
free of any import of ``yakhnama.platform.container`` or infrastructure, which the
layer contract forbids even indirectly.
"""

from typing import Annotated, Protocol

from fastapi import Depends, Header, Request

from yakhnama.modules.events.application.ports import (
    EventQueryService,
    RecordEventUseCase,
    UpdateEventTitleUseCase,
)


class EventsApiServices(Protocol):
    """Use cases and query services the events router needs.

    Implements: Dependency Injection (provider port bound in the composition root).
    """

    @property
    def record_event(self) -> RecordEventUseCase:
        """Return the (idempotency-decorated) record-event use case."""
        ...

    @property
    def update_event_title(self) -> UpdateEventTitleUseCase:
        """Return the update-title use case."""
        ...

    @property
    def event_queries(self) -> EventQueryService:
        """Return the event query service."""
        ...


def get_events_services(request: Request) -> EventsApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The events services.
    """
    services: EventsApiServices = request.app.state.events_services
    return services


def require_idempotency_key(
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=16,
            max_length=128,
            pattern=r"^[A-Za-z0-9_-]+$",
        ),
    ],
) -> str:
    """Return the validated ``Idempotency-Key`` header of a creating request.

    Args:
        idempotency_key: Client-chosen key, unique per intended creation.

    Returns:
        The key, unchanged.
    """
    return idempotency_key


def require_expected_version(
    if_match: Annotated[
        str,
        Header(alias="If-Match", max_length=24, pattern=r'^"[0-9]{1,18}"$'),
    ],
) -> int:
    """Return the resource version the client last saw, from ``If-Match``.

    Args:
        if_match: A strong ETag such as ``"3"``.

    Returns:
        The version number inside the ETag.
    """
    return int(if_match.strip('"'))


Services = Annotated[EventsApiServices, Depends(get_events_services)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
```

When a second module needs the `Idempotency-Key` or `If-Match` dependency, ask the
architect to move them to `yakhnama.platform` instead of copying them.

### `src/yakhnama/modules/events/api/router.py`
```python
"""HTTP routes of the events context, mounted under ``/api/v1``.

Patterns: none (thin transport layer over commands and query services).

Errors are never mapped here: handlers and query services raise
``YakhnamaError`` subclasses and the exception handlers in ``main.py`` turn them
into RFC 9457 Problem Details.
"""

from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from geojson_pydantic import Feature, FeatureCollection, Point
from geojson_pydantic.types import Position2D

from yakhnama.modules.events.api.dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    Services,
)
from yakhnama.modules.events.api.schemas import (
    EventFeatureProperties,
    EventPageResponse,
    EventResponse,
    ListEventsParameters,
    RecordEventRequest,
    UpdateEventTitleRequest,
)
from yakhnama.modules.events.application.commands import RecordEvent, UpdateEventTitle
from yakhnama.modules.events.application.dto import EventDTO
from yakhnama.modules.events.application.queries import GetEvent, ListEvents
from yakhnama.modules.identity.public import Actor
from yakhnama.platform.auth import require_actor
from yakhnama.shared_kernel.pagination import PageRequest

GEOJSON_MEDIA_TYPE = "application/geo+json"
EVENTS_PATH = "/api/v1/events"

# Any: this is FastAPI's own type for the ``responses`` argument.
_PROBLEM: dict[str, Any] = {
    "description": "RFC 9457 Problem Details",
    "content": {"application/problem+json": {}},
}

EventFeature = Feature[Point, EventFeatureProperties]

router = APIRouter(prefix="/api/v1", tags=["events"])

CurrentActor = Annotated[Actor, Depends(require_actor)]


def _etag(dto: EventDTO) -> str:
    """Return the strong ETag of an event version."""
    return f'"{dto.version}"'


def _to_feature(dto: EventDTO) -> EventFeature:
    """Build the GeoJSON feature of an event."""
    return EventFeature(
        type="Feature",
        id=str(dto.id),
        geometry=Point(
            type="Point",
            coordinates=Position2D(longitude=dto.longitude, latitude=dto.latitude),
        ),
        properties=EventFeatureProperties(
            id=dto.id,
            hazard_code=dto.hazard_code,
            title=dto.title,
            occurred_at=dto.occurred_at,
            date_precision=dto.date_precision,
        ),
    )


@router.get(
    "/events",
    response_model=EventPageResponse,
    responses={
        status.HTTP_200_OK: {"content": {GEOJSON_MEDIA_TYPE: {}}},
        status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    },
)
async def list_events(
    *,
    parameters: Annotated[ListEventsParameters, Query()],
    response: Response,
    services: Services,
    accept: Annotated[str | None, Header(max_length=512)] = None,
) -> Response | EventPageResponse:
    """List verified events as JSON or GeoJSON.

    Anonymous: the query service returns verified events only.

    Args:
        parameters: Validated cursor, limit and filters.
        response: Used to set the ``Link`` header.
        services: Use cases bound by the composition root.
        accept: ``application/geo+json`` selects a GeoJSON FeatureCollection.

    Returns:
        A page of events in the negotiated representation.
    """
    query = ListEvents(
        hazard_code=parameters.hazard_code,
        page=PageRequest(limit=parameters.limit, cursor=parameters.cursor),
    )
    page = await services.event_queries.list_events(query)
    headers: dict[str, str] = {}
    if page.next_cursor is not None:
        next_query = parameters.model_copy(update={"cursor": page.next_cursor})
        encoded = urlencode(next_query.model_dump(exclude_none=True))
        headers["Link"] = f'<{EVENTS_PATH}?{encoded}>; rel="next"'
    if accept is not None and GEOJSON_MEDIA_TYPE in accept:
        collection = FeatureCollection[EventFeature](
            type="FeatureCollection",
            features=[_to_feature(dto) for dto in page.items],
        )
        return Response(
            content=collection.model_dump_json(exclude_none=True),
            media_type=GEOJSON_MEDIA_TYPE,
            headers=headers,
        )
    response.headers.update(headers)
    return EventPageResponse(
        items=tuple(EventResponse.from_dto(dto) for dto in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/events/{event_id}",
    responses={status.HTTP_404_NOT_FOUND: _PROBLEM},
)
async def get_event(
    *,
    event_id: UUID,
    response: Response,
    services: Services,
) -> EventResponse:
    """Return one verified event with its ``ETag``.

    Args:
        event_id: The event id.
        response: Used to set the ``ETag`` header.
        services: Use cases bound by the composition root.

    Returns:
        The event.
    """
    dto = await services.event_queries.get_event(GetEvent(event_id=event_id))
    response.headers["ETag"] = _etag(dto)
    return EventResponse.from_dto(dto)


@router.post(
    "/moderation/events",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: _PROBLEM,
        status.HTTP_403_FORBIDDEN: _PROBLEM,
        status.HTTP_409_CONFLICT: _PROBLEM,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    },
)
async def record_event(
    *,
    body: RecordEventRequest,
    response: Response,
    services: Services,
    actor: CurrentActor,
    idempotency_key: IdempotencyKey,
) -> EventResponse:
    """Record a new canonical event (moderators only).

    Replaying the same ``Idempotency-Key`` returns the originally created event
    instead of creating a second one; the idempotency Decorator bound in the
    composition root enforces this.

    Args:
        body: The validated request body.
        response: Used to set ``Location`` and ``ETag``.
        services: Use cases bound by the composition root.
        actor: The authenticated caller; the handler's Policy decides access.
        idempotency_key: Client-chosen key, unique per intended creation.

    Returns:
        The created event.
    """
    command = RecordEvent(
        hazard_code=body.hazard_code,
        title=body.title,
        occurred_at=body.occurred_at,
        date_precision=body.date_precision,
        longitude=body.longitude,
        latitude=body.latitude,
        idempotency_key=idempotency_key,
    )
    event_id = await services.record_event(command, actor)
    dto = await services.event_queries.get_event(GetEvent(event_id=event_id))
    response.headers["Location"] = f"{EVENTS_PATH}/{event_id}"
    response.headers["ETag"] = _etag(dto)
    return EventResponse.from_dto(dto)


@router.patch(
    "/moderation/events/{event_id}",
    responses={
        status.HTTP_401_UNAUTHORIZED: _PROBLEM,
        status.HTTP_403_FORBIDDEN: _PROBLEM,
        status.HTTP_404_NOT_FOUND: _PROBLEM,
        status.HTTP_412_PRECONDITION_FAILED: _PROBLEM,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _PROBLEM,
    },
)
async def update_event_title(  # noqa: PLR0913  # reason: FastAPI injects each input
    *,
    event_id: UUID,
    body: UpdateEventTitleRequest,
    response: Response,
    services: Services,
    actor: CurrentActor,
    expected_version: ExpectedVersion,
) -> EventResponse:
    """Change an event's title if the client holds the current version.

    Args:
        event_id: The event id.
        body: The validated request body.
        response: Used to set the new ``ETag``.
        services: Use cases bound by the composition root.
        actor: The authenticated caller; the handler's Policy decides access.
        expected_version: Version from the ``If-Match`` ETag, for example 3.

    Returns:
        The updated event.
    """
    command = UpdateEventTitle(
        event_id=event_id,
        title=body.title,
        expected_version=expected_version,
    )
    await services.update_event_title(command, actor)
    dto = await services.event_queries.get_event(GetEvent(event_id=event_id))
    response.headers["ETag"] = _etag(dto)
    return EventResponse.from_dto(dto)
```

### `src/yakhnama/main.py` — the only lines api-engineer adds

Inside the existing `create_app` function (there is no separate registration function),
add the module-level import
`from yakhnama.modules.events.api.router import router as events_router` and, next to
the other routers, the line `app.include_router(events_router)`. Nothing else in
`main.py` is touched by api-engineer.

### `tests/fakes/events.py`
```python
"""In-memory fakes for the events ports used by API tests.

Patterns: Fake.
"""

from uuid import UUID

from yakhnama.modules.events.application.commands import RecordEvent, UpdateEventTitle
from yakhnama.modules.events.application.dto import EventDTO
from yakhnama.modules.events.application.queries import GetEvent, ListEvents
from yakhnama.modules.events.domain.errors import (
    EventModerationDeniedError,
    EventNotFoundError,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.errors import PreconditionFailedError
from yakhnama.shared_kernel.pagination import Page, decode_cursor, encode_cursor


class FakeEventQueryService:
    """In-memory ``EventQueryService`` ordered by id.

    Implements: Fake.

    Attributes:
        rows: Events keyed by id.
    """

    def __init__(self) -> None:
        """Create an empty query service."""
        self.rows: dict[UUID, EventDTO] = {}

    async def get_event(self, query: GetEvent) -> EventDTO:
        """Return the event or raise ``EventNotFoundError``."""
        row = self.rows.get(query.event_id)
        if row is None:
            message = f"event {query.event_id} does not exist"
            raise EventNotFoundError(message)
        return row

    async def list_events(self, query: ListEvents) -> Page[EventDTO]:
        """Filter by hazard code and page by id."""
        after = decode_cursor(query.page.cursor)[0] if query.page.cursor else ""
        matching = [
            row
            for key, row in sorted(self.rows.items(), key=lambda item: str(item[0]))
            if str(key) > after
            and (query.hazard_code is None or row.hazard_code == query.hazard_code)
        ]
        items = tuple(matching[: query.page.limit])
        has_more = len(matching) > query.page.limit
        next_cursor = encode_cursor((str(items[-1].id),)) if has_more else None
        return Page(items=items, next_cursor=next_cursor)


class FakeEventsApiServices:
    """``EventsApiServices`` backed by in-memory fakes.

    Implements: Fake.

    Attributes:
        event_queries: The fake query service.
        is_allowed: Whether the fake Policy lets actors moderate.
        received_keys: Idempotency keys passed to ``record_event``, in order.
    """

    def __init__(self, new_ids: list[UUID], *, is_allowed: bool = True) -> None:
        """Create the services.

        Args:
            new_ids: Ids handed out to recorded events, in order.
            is_allowed: Whether the fake Policy lets actors moderate.
        """
        self.event_queries = FakeEventQueryService()
        self.is_allowed = is_allowed
        self.received_keys: list[str] = []
        self._new_ids = list(new_ids)

    def _check(self, actor: Actor) -> None:
        """Raise ``EventModerationDeniedError`` unless the fake Policy allows."""
        if not self.is_allowed:
            message = f"actor {actor.id} may not moderate events"
            raise EventModerationDeniedError(message)

    async def record_event(self, command: RecordEvent, actor: Actor) -> UUID:
        """Store a new event version 1 and return its id."""
        self._check(actor)
        self.received_keys.append(command.idempotency_key)
        event_id = self._new_ids.pop(0)
        self.event_queries.rows[event_id] = EventDTO(
            id=event_id,
            hazard_code=command.hazard_code,
            title=command.title,
            occurred_at=command.occurred_at,
            date_precision=command.date_precision,
            longitude=command.longitude,
            latitude=command.latitude,
            version=1,
        )
        return event_id

    async def update_event_title(self, command: UpdateEventTitle, actor: Actor) -> None:
        """Change the title if ``expected_version`` matches, bumping the version."""
        self._check(actor)
        current = await self.event_queries.get_event(
            GetEvent(event_id=command.event_id),
        )
        if current.version != command.expected_version:
            message = f"event {command.event_id} is at version {current.version}"
            raise PreconditionFailedError(message)
        self.event_queries.rows[command.event_id] = current.model_copy(
            update={"title": command.title, "version": current.version + 1},
        )
```

### `tests/api/modules/events/test_router.py`
```python
"""HTTP tests for the events router, with fakes bound on ``app.state``."""

from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.fakes.events import FakeEventsApiServices

EVENT_ID = UUID(int=1)
SECOND_EVENT_ID = UUID(int=2)
IDEMPOTENCY_KEY = "test-key-0000000001"
# Synthetic example payload; not a real event.
RECORD_BODY: dict[str, Any] = {
    "hazard_code": "glof",
    "title": "Example outburst",
    "occurred_at": "2025-07-01T06:00:00Z",
    "date_precision": "hour",
    "longitude": 74.0,
    "latitude": 36.0,
}


@pytest.fixture
def services(app: FastAPI) -> FakeEventsApiServices:
    """Bind fake events services where the composition root would."""
    fake = FakeEventsApiServices([EVENT_ID, SECOND_EVENT_ID])
    app.state.events_services = fake
    return fake


async def record(client: AsyncClient, headers: dict[str, str]) -> None:
    """Record one example event through the API."""
    response = await client.post(
        "/api/v1/moderation/events",
        json=RECORD_BODY,
        headers=headers | {"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert response.status_code == 201


async def test_record_event_with_valid_body_returns_201_with_location_and_etag(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    headers = moderator_headers | {"Idempotency-Key": IDEMPOTENCY_KEY}

    response = await client.post(
        "/api/v1/moderation/events",
        json=RECORD_BODY,
        headers=headers,
    )

    assert response.status_code == 201
    assert response.headers["Location"] == f"/api/v1/events/{EVENT_ID}"
    assert response.headers["ETag"] == '"1"'
    assert services.received_keys == [IDEMPOTENCY_KEY]


async def test_record_event_without_token_returns_401_problem(
    client: AsyncClient,
    services: FakeEventsApiServices,
) -> None:
    headers = {"Idempotency-Key": IDEMPOTENCY_KEY}

    response = await client.post(
        "/api/v1/moderation/events",
        json=RECORD_BODY,
        headers=headers,
    )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert services.received_keys == []


async def test_record_event_when_policy_denies_returns_403_problem(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    services.is_allowed = False
    headers = moderator_headers | {"Idempotency-Key": IDEMPOTENCY_KEY}

    response = await client.post(
        "/api/v1/moderation/events",
        json=RECORD_BODY,
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["status"] == 403


async def test_record_event_without_idempotency_key_returns_422_problem(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/moderation/events",
        json=RECORD_BODY,
        headers=moderator_headers,
    )

    assert response.status_code == 422
    assert services.received_keys == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * 201),
        ("longitude", 180.5),
        ("occurred_at", "2025-07-01T06:00:00"),
        ("unexpected", "field"),
    ],
)
async def test_record_event_with_out_of_bounds_field_returns_422(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
    field: str,
    value: object,
) -> None:
    headers = moderator_headers | {"Idempotency-Key": IDEMPOTENCY_KEY}

    response = await client.post(
        "/api/v1/moderation/events",
        json=RECORD_BODY | {field: value},
        headers=headers,
    )

    assert response.status_code == 422
    assert services.received_keys == []


async def test_get_event_when_missing_returns_404_problem(
    client: AsyncClient,
    services: FakeEventsApiServices,
) -> None:
    response = await client.get(f"/api/v1/events/{EVENT_ID}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert services.event_queries.rows == {}


async def test_update_event_title_with_stale_etag_returns_412_problem(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    await record(client, moderator_headers)

    response = await client.patch(
        f"/api/v1/moderation/events/{EVENT_ID}",
        json={"title": "Renamed"},
        headers=moderator_headers | {"If-Match": '"7"'},
    )

    assert response.status_code == 412
    assert services.event_queries.rows[EVENT_ID].title == "Example outburst"


async def test_update_event_title_with_current_etag_returns_new_etag(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    await record(client, moderator_headers)

    response = await client.patch(
        f"/api/v1/moderation/events/{EVENT_ID}",
        json={"title": "Renamed"},
        headers=moderator_headers | {"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert services.event_queries.rows[EVENT_ID].title == "Renamed"


async def test_list_events_with_limit_returns_next_link_until_last_page(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    await record(client, moderator_headers)
    await record(client, moderator_headers)

    first = await client.get("/api/v1/events", params={"limit": 1})
    second = await client.get(
        "/api/v1/events",
        params={"limit": 1, "cursor": first.json()["next_cursor"]},
    )

    assert first.headers["Link"].endswith('rel="next"')
    assert [item["id"] for item in first.json()["items"]] == [str(EVENT_ID)]
    assert [item["id"] for item in second.json()["items"]] == [str(SECOND_EVENT_ID)]
    assert second.json()["next_cursor"] is None
    assert len(services.event_queries.rows) == 2


async def test_list_events_with_limit_above_max_returns_422(
    client: AsyncClient,
    services: FakeEventsApiServices,
) -> None:
    response = await client.get("/api/v1/events", params={"limit": 201})

    assert response.status_code == 422
    assert services.event_queries.rows == {}


async def test_list_events_accepting_geojson_returns_feature_collection(
    client: AsyncClient,
    services: FakeEventsApiServices,
    moderator_headers: dict[str, str],
) -> None:
    await record(client, moderator_headers)

    response = await client.get(
        "/api/v1/events",
        headers={"Accept": "application/geo+json"},
    )

    body = response.json()
    assert response.headers["content-type"] == "application/geo+json"
    assert body["type"] == "FeatureCollection"
    assert body["features"][0]["geometry"]["coordinates"] == [74.0, 36.0]
    assert len(services.event_queries.rows) == 1
```

These tests were run against a scratch app with the same router, exception mapping and a
stand-in for `require_actor`; all passed. Against the real app factory they rely only on
the `app`, `client` and `moderator_headers` fixtures.

## Required tests

Every endpoint covers, at least:

- **Authentication:** `test_record_event_without_token_returns_401_problem`.
- **Authorisation:** `test_record_event_when_policy_denies_returns_403_problem`.
- **Validation and bounds:** `test_record_event_with_out_of_bounds_field_returns_422`
  (over-long string, out-of-range number, naive datetime, unknown field),
  `test_list_events_with_limit_above_max_returns_422`.
- **Errors as Problem Details:** `test_get_event_when_missing_returns_404_problem`
  (content type `application/problem+json`).
- **Pagination:** `test_list_events_with_limit_returns_next_link_until_last_page`.
- **Idempotency:** `test_record_event_with_valid_body_returns_201_with_location_and_etag`
  (key passed through), `test_record_event_without_idempotency_key_returns_422_problem`.
  Replay semantics (same key → same resource, different body → rejected) are tested once
  against the real Decorator in the platform tests and in an API test wired with it.
- **Concurrency:** `test_update_event_title_with_stale_etag_returns_412_problem`,
  `test_update_event_title_with_current_etag_returns_new_etag`.
- **Negotiation (spatial listings):**
  `test_list_events_accepting_geojson_returns_feature_collection`.

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/api/modules/events -q
poetry run poe test-api
poetry run poe openapi-snapshot     # task added in Phase 2
git diff --stat tests/contract/openapi.json
poetry run poe cov
poetry run poe check
```

## Definition of Done

- [ ] Paths are plural kebab-case under `/api/v1`; moderator routes under `/moderation/`.
- [ ] Every request field is bounded; request models forbid extra fields.
- [ ] Router imports only application, `platform` dependencies and its own `api`
      package; `poetry run poe arch` passes.
- [ ] Errors come from the central Problem Details handlers; every status is documented.
- [ ] Listings use cursor pagination (≤ 200) with a `Link` header; spatial listings
      negotiate GeoJSON.
- [ ] Creating POSTs require `Idempotency-Key`; mutable resources return `ETag` and
      updates require `If-Match`.
- [ ] API tests cover authentication, authorisation, validation, errors, pagination,
      idempotency and concurrency.
- [ ] OpenAPI snapshot regenerated and its diff reviewed.
- [ ] `standards-reviewer` and `security-reviewer` approved.
- [ ] Conventional Commit, for example `feat(events): expose events over http`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Indirect infrastructure imports.** Importing `yakhnama.platform.container` from
  `api/` breaks the module's layer contract even though the import is indirect.
- **Returning a `Response` skips validation.** Only the GeoJSON branch builds a raw
  `Response`; the JSON branch returns the typed model so FastAPI validates it.
- **Weak ETags.** Use a strong, quoted version (`"3"`) and compare it in the handler
  inside the transaction; comparing in the router is a race.
- **Missing header status codes.** FastAPI reports a missing required header as 422. The
  Idempotency-Key draft recommends 400 and RFC 6585 recommends 428 for a missing
  `If-Match`; this skill keeps 422 until the architect decides (open question).
- **Accept parsing.** The template matches `application/geo+json` as a substring; if
  clients send quality values that must be honoured, move negotiation to a platform
  helper.
- **Coordinates of people.** Reporter GPS is private by default and rounded in public
  payloads; never expose it through a listing without `security-reviewer`.
- **Offset pagination is forbidden,** even "just for admin screens".
