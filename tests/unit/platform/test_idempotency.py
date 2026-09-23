"""Unit tests for the ``Idempotency-Key`` middleware and the store's row mapping."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from starlette.types import Receive, Scope, Send

from tests.fakes.auth import InMemoryIdempotencyStore
from tests.fakes.clock import FrozenClock
from tests.unit.platform.asgi import RecordingApp, client_for
from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import PrincipalResolution
from yakhnama.platform.idempotency.middleware import (
    MAX_STORED_BODY_BYTES,
    REPLAYED_HEADER,
    IdempotencyMiddleware,
    request_fingerprint,
)
from yakhnama.platform.idempotency.models import IdempotencyKeyRow
from yakhnama.platform.idempotency.sqlalchemy_store import record_from_row
from yakhnama.platform.idempotency.store import (
    PENDING_LEASE,
    IdempotencyReservation,
    StoredResponse,
)
from yakhnama.platform.request_state import PRINCIPAL_RESOLUTION_STATE_KEY, state_of

KEY = "0192f4c1-0000-7000-8000-000000000001"
TTL = timedelta(hours=72)
PRINCIPAL = Principal(
    subject="user-1",
    issuer="https://identity.test",
    expires_at=datetime(2026, 9, 23, 13, 0, tzinfo=UTC),
)
CREATED_HEADERS = (
    (b"content-type", b"application/json"),
    (b"location", b"/api/v1/organizations/1"),
    (b"etag", b'"1:0"'),
    (b"set-cookie", b"session=abc"),
)


class AuthenticatedAs:
    """Stands in for ``PrincipalResolutionMiddleware`` with a fixed outcome.

    Implements: Fake (of principal resolution).
    """

    def __init__(self, app: IdempotencyMiddleware, principal: Principal | None) -> None:
        """Wrap ``app``, resolving every request to ``principal``."""
        self._app = app
        self._principal = principal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Store the fixed resolution, then call the middleware."""
        if scope["type"] == "http":
            state_of(scope)[PRINCIPAL_RESOLUTION_STATE_KEY] = PrincipalResolution(
                principal=self._principal
            )
        await self._app(scope, receive, send)


def _stack(
    inner: RecordingApp,
    store: InMemoryIdempotencyStore,
    clock: FrozenClock,
    principal: Principal | None = PRINCIPAL,
) -> AuthenticatedAs:
    middleware = IdempotencyMiddleware(
        inner, store=store, clock=clock, ttl=TTL, path_prefix="/api/v1"
    )
    return AuthenticatedAs(middleware, principal)


def _created_app() -> RecordingApp:
    return RecordingApp(status=201, body=b'{"id": 1}', headers=CREATED_HEADERS)


async def _post(
    app: AuthenticatedAs,
    body: bytes = b'{"name": "a"}',
    key: str | None = KEY,
    path: str = "/api/v1/organizations",
) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with client_for(app) as client:
        return await client.post(path, content=body, headers=headers)


async def test_first_post_runs_route_and_stores_allow_listed_response(
    clock: FrozenClock,
) -> None:
    inner = _created_app()
    store = InMemoryIdempotencyStore()

    response = await _post(_stack(inner, store, clock))

    record = store.stored(PRINCIPAL.scope_key(), UUID(KEY))
    assert response.status_code == 201
    assert REPLAYED_HEADER.lower() not in response.headers
    assert inner.bodies == [b'{"name": "a"}']
    assert record is not None
    assert record.response == StoredResponse(
        status_code=201,
        headers=(
            ("content-type", "application/json"),
            ("location", "/api/v1/organizations/1"),
            ("etag", '"1:0"'),
        ),
        body=b'{"id": 1}',
    )
    assert record.expires_at == clock.now() + TTL


async def test_repeat_post_same_body_replays_without_running_route(
    clock: FrozenClock,
) -> None:
    inner = _created_app()
    store = InMemoryIdempotencyStore()
    app = _stack(inner, store, clock)
    first = await _post(app)

    replay = await _post(app)

    assert inner.calls == 1
    assert replay.status_code == first.status_code
    assert replay.content == first.content
    assert replay.headers[REPLAYED_HEADER] == "true"
    assert replay.headers["location"] == "/api/v1/organizations/1"
    assert replay.headers["etag"] == '"1:0"'
    assert "set-cookie" not in replay.headers


async def test_repeat_post_different_body_returns_409_reused(
    clock: FrozenClock,
) -> None:
    inner = _created_app()
    app = _stack(inner, InMemoryIdempotencyStore(), clock)
    await _post(app)

    response = await _post(app, body=b'{"name": "b"}')

    assert response.status_code == 409
    assert response.json()["type"] == (
        "https://yakhnama.org/problems/idempotency-key-reused"
    )
    assert inner.calls == 1


async def test_same_key_on_other_path_returns_409_reused(clock: FrozenClock) -> None:
    app = _stack(_created_app(), InMemoryIdempotencyStore(), clock)
    await _post(app)

    response = await _post(app, path="/api/v1/other")

    assert response.status_code == 409


async def test_concurrent_loser_while_winner_runs_returns_409_in_use(
    clock: FrozenClock,
) -> None:
    store = InMemoryIdempotencyStore()
    inner = _created_app()
    body = b'{"name": "a"}'
    scope = {
        "method": "POST",
        "path": "/api/v1/organizations",
        "query_string": b"",
    }
    await store.reserve(
        IdempotencyReservation(
            scope=PRINCIPAL.scope_key(),
            key=UUID(KEY),
            request_hash=request_fingerprint(scope, body),
            method="POST",
            path="/api/v1/organizations",
            created_at=clock.now(),
            expires_at=clock.now() + PENDING_LEASE,
        )
    )

    response = await _post(_stack(inner, store, clock), body=body)

    assert response.status_code == 409
    assert response.json()["type"] == (
        "https://yakhnama.org/problems/idempotency-key-in-use"
    )
    assert response.headers["retry-after"] == "1"
    assert inner.calls == 0


async def test_concurrent_loser_after_winner_finished_is_replayed(
    clock: FrozenClock,
) -> None:
    store = InMemoryIdempotencyStore()
    winner_app = _stack(_created_app(), store, clock)
    loser_inner = _created_app()
    await _post(winner_app)

    response = await _post(_stack(loser_inner, store, clock))

    assert response.status_code == 201
    assert response.headers[REPLAYED_HEADER] == "true"
    assert loser_inner.calls == 0


@pytest.mark.parametrize("status", [400, 422, 500])
async def test_non_2xx_response_releases_key_for_retry(
    clock: FrozenClock, status: int
) -> None:
    store = InMemoryIdempotencyStore()
    inner = RecordingApp(status=status)
    app = _stack(inner, store, clock)
    await _post(app)

    await _post(app)

    assert inner.calls == 2
    assert store.stored(PRINCIPAL.scope_key(), UUID(KEY)) is None


async def test_route_exception_releases_key_and_propagates(
    clock: FrozenClock,
) -> None:
    store = InMemoryIdempotencyStore()
    inner = RecordingApp(error=RuntimeError("boom"))

    response = await _post(_stack(inner, store, clock))

    assert response.status_code == 500
    assert store.released == [(PRINCIPAL.scope_key(), UUID(KEY))]


async def test_oversized_response_is_served_but_not_stored(
    clock: FrozenClock,
) -> None:
    store = InMemoryIdempotencyStore()
    big = b"x" * (MAX_STORED_BODY_BYTES + 1)
    inner = RecordingApp(status=201, body=big)

    response = await _post(_stack(inner, store, clock))

    assert response.content == big
    assert store.stored(PRINCIPAL.scope_key(), UUID(KEY)) is None


async def test_expired_record_lets_the_key_be_used_again(clock: FrozenClock) -> None:
    store = InMemoryIdempotencyStore()
    inner = _created_app()
    app = _stack(inner, store, clock)
    await _post(app)
    clock.advance(TTL)

    response = await _post(app, body=b'{"name": "new"}')

    assert response.status_code == 201
    assert inner.calls == 2


@pytest.mark.parametrize("key", ["not-a-uuid", "", "1234"])
async def test_invalid_key_returns_400_problem(clock: FrozenClock, key: str) -> None:
    inner = _created_app()

    response = await _post(_stack(inner, InMemoryIdempotencyStore(), clock), key=key)

    assert response.status_code == 400
    assert response.json()["type"] == (
        "https://yakhnama.org/problems/invalid-idempotency-key"
    )
    assert inner.calls == 0


async def test_two_key_headers_return_400_problem(clock: FrozenClock) -> None:
    app = _stack(_created_app(), InMemoryIdempotencyStore(), clock)

    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/organizations",
            content=b"{}",
            headers=[("Idempotency-Key", KEY), ("Idempotency-Key", KEY)],
        )

    assert response.status_code == 400


async def test_anonymous_post_passes_through_untouched(clock: FrozenClock) -> None:
    store = InMemoryIdempotencyStore()
    inner = _created_app()

    await _post(_stack(inner, store, clock, principal=None))
    await _post(_stack(inner, store, clock, principal=None))

    assert inner.calls == 2
    assert store.stored(PRINCIPAL.scope_key(), UUID(KEY)) is None


async def test_post_without_key_passes_through(clock: FrozenClock) -> None:
    store = InMemoryIdempotencyStore()
    inner = _created_app()
    app = _stack(inner, store, clock)

    await _post(app, key=None)
    await _post(app, key=None)

    assert inner.calls == 2


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE", "PUT"])
async def test_non_post_passes_through(clock: FrozenClock, method: str) -> None:
    inner = _created_app()
    app = _stack(inner, InMemoryIdempotencyStore(), clock)

    async with client_for(app) as client:
        for _ in range(2):
            await client.request(
                method, "/api/v1/organizations", headers={"Idempotency-Key": KEY}
            )

    assert inner.calls == 2


@pytest.mark.parametrize("path", ["/health/live", "/api/v10/x", "/api"])
async def test_post_outside_api_prefix_passes_through(
    clock: FrozenClock, path: str
) -> None:
    inner = _created_app()
    app = _stack(inner, InMemoryIdempotencyStore(), clock)

    await _post(app, path=path)
    await _post(app, path=path)

    assert inner.calls == 2


async def test_post_to_prefix_itself_is_handled(clock: FrozenClock) -> None:
    inner = _created_app()
    app = _stack(inner, InMemoryIdempotencyStore(), clock)

    await _post(app, path="/api/v1")
    await _post(app, path="/api/v1")

    assert inner.calls == 1


async def test_keys_are_scoped_per_principal(clock: FrozenClock) -> None:
    store = InMemoryIdempotencyStore()
    inner = _created_app()
    other = Principal(
        subject="user-2", issuer=PRINCIPAL.issuer, expires_at=PRINCIPAL.expires_at
    )
    await _post(_stack(inner, store, clock))

    response = await _post(_stack(inner, store, clock, principal=other))

    assert response.status_code == 201
    assert REPLAYED_HEADER.lower() not in response.headers
    assert inner.calls == 2


def test_request_fingerprint_depends_on_method_path_query_and_body() -> None:
    base = {"method": "POST", "path": "/a", "query_string": b"x=1"}

    digests = {
        request_fingerprint(base, b"body"),
        request_fingerprint({**base, "method": "PUT"}, b"body"),
        request_fingerprint({**base, "path": "/b"}, b"body"),
        request_fingerprint({**base, "query_string": b"x=2"}, b"body"),
        request_fingerprint(base, b"other"),
    }

    assert len(digests) == 5
    assert all(len(digest) == 64 for digest in digests)


def test_request_fingerprint_length_prefix_prevents_boundary_collisions() -> None:
    first = request_fingerprint(
        {"method": "POST", "path": "/ab", "query_string": b""}, b""
    )
    second = request_fingerprint(
        {"method": "POST", "path": "/a", "query_string": b"b"}, b""
    )

    assert first != second


def _row(**overrides: object) -> IdempotencyKeyRow:
    values: dict[str, object] = {
        "request_hash": "a" * 64,
        "status_code": None,
        "response_body": None,
        "response_headers": None,
        "expires_at": datetime(2026, 9, 26, tzinfo=UTC),
    }
    values.update(overrides)
    return IdempotencyKeyRow(**values)


def test_record_from_row_pending_row_has_no_response() -> None:
    record = record_from_row(_row())

    assert record.response is None
    assert record.request_hash == "a" * 64


def test_record_from_row_completed_row_maps_response() -> None:
    record = record_from_row(
        _row(
            status_code=201,
            response_body=b"{}",
            response_headers=[["content-type", "application/json"]],
        )
    )

    assert record.response == StoredResponse(
        status_code=201, headers=(("content-type", "application/json"),), body=b"{}"
    )


def test_record_from_row_completed_row_with_null_parts_maps_empty() -> None:
    record = record_from_row(_row(status_code=204))

    assert record.response == StoredResponse(status_code=204)


async def test_in_memory_store_purge_expired_deletes_only_lapsed(
    clock: FrozenClock,
) -> None:
    store = InMemoryIdempotencyStore()
    for index, lease in enumerate((timedelta(seconds=1), timedelta(hours=1))):
        await store.reserve(
            IdempotencyReservation(
                scope="s",
                key=UUID(int=index),
                request_hash="b" * 64,
                method="POST",
                path="/p",
                created_at=clock.now(),
                expires_at=clock.now() + lease,
            )
        )

    deleted = await store.purge_expired(clock.now() + timedelta(minutes=1))

    assert deleted == 1
    assert store.stored("s", UUID(int=1)) is not None
