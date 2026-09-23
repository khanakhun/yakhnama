"""Edge cases of the ASGI plumbing: disconnects, chunked responses, bare scopes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from tests.fakes.auth import InMemoryIdempotencyStore
from tests.fakes.clock import FrozenClock
from yakhnama.main import build_internal_error_handler, handle_request_validation_error
from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import PrincipalResolution
from yakhnama.platform.http import RequestBodyGuardMiddleware
from yakhnama.platform.idempotency.middleware import (
    MAX_STORED_BODY_BYTES,
    IdempotencyMiddleware,
)
from yakhnama.platform.request_state import PRINCIPAL_RESOLUTION_STATE_KEY
from yakhnama.platform.settings import Settings

KEY = "0192f4c1-0000-7000-8000-000000000001"
PRINCIPAL = Principal(
    subject="user-1",
    issuer="https://identity.test",
    expires_at=datetime(2026, 9, 23, 13, 0, tzinfo=UTC),
)


class ReceiveTwiceApp:
    """Reads the body, then waits for the disconnect, as a streaming route does.

    Implements: Fake (of the routed application).

    Attributes:
        messages: Every message received, in order.
    """

    def __init__(self, chunks: tuple[bytes, ...] = (b"{}",)) -> None:
        """Answer 201 with ``chunks`` as separate body messages."""
        self._chunks = chunks
        self.messages: list[Message] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Receive twice, then answer."""
        del scope
        self.messages.append(await receive())
        self.messages.append(await receive())
        await send({"type": "http.response.start", "status": 201, "headers": []})
        for index, chunk in enumerate(self._chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": index < len(self._chunks) - 1,
                }
            )


def _channel(*incoming: Message) -> tuple[Receive, Send, list[Message]]:
    queue = list(incoming)
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    return receive, send, sent


def _post_scope(*, is_authenticated: bool = True) -> Scope:
    state: dict[str, object] = {}
    if is_authenticated:
        state[PRINCIPAL_RESOLUTION_STATE_KEY] = PrincipalResolution(principal=PRINCIPAL)
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/things",
        "query_string": b"",
        "headers": [(b"idempotency-key", KEY.encode())],
        "state": state,
    }


async def test_body_guard_replays_body_then_passes_real_disconnect() -> None:
    inner = ReceiveTwiceApp()
    receive, send, _ = _channel(
        {"type": "http.request", "body": b"{}", "more_body": False}
    )

    await RequestBodyGuardMiddleware(inner, max_bytes=1024)(
        _post_scope(), receive, send
    )

    assert inner.messages[0]["body"] == b"{}"
    assert inner.messages[1] == {"type": "http.disconnect"}


async def test_body_guard_client_gone_before_body_passes_empty_body() -> None:
    inner = ReceiveTwiceApp()
    receive, send, _ = _channel({"type": "http.disconnect"})

    await RequestBodyGuardMiddleware(inner, max_bytes=1024)(
        _post_scope(), receive, send
    )

    assert inner.messages[0]["body"] == b""


async def test_idempotency_replays_body_then_passes_real_disconnect(
    clock: FrozenClock,
) -> None:
    inner = ReceiveTwiceApp()
    store = InMemoryIdempotencyStore()
    middleware = IdempotencyMiddleware(
        inner, store=store, clock=clock, ttl=timedelta(hours=1), path_prefix="/api/v1"
    )
    receive, send, _ = _channel({"type": "http.disconnect"})

    await middleware(_post_scope(), receive, send)

    assert inner.messages == [
        {"type": "http.request", "body": b"", "more_body": False},
        {"type": "http.disconnect"},
    ]
    assert store.stored(PRINCIPAL.scope_key(), UUID(KEY)) is not None


async def test_idempotency_multi_chunk_oversized_response_is_not_stored(
    clock: FrozenClock,
) -> None:
    half = b"x" * (MAX_STORED_BODY_BYTES // 2 + 1)
    inner = ReceiveTwiceApp(chunks=(half, half, b"tail"))
    store = InMemoryIdempotencyStore()
    middleware = IdempotencyMiddleware(
        inner, store=store, clock=clock, ttl=timedelta(hours=1), path_prefix="/api/v1"
    )
    receive, send, sent = _channel(
        {"type": "http.request", "body": b"{}", "more_body": False}
    )

    await middleware(_post_scope(), receive, send)

    assert (
        b"".join(message.get("body", b"") for message in sent) == half + half + b"tail"
    )
    assert store.stored(PRINCIPAL.scope_key(), UUID(KEY)) is None


async def test_request_validation_error_with_unusual_location_keeps_msg_only() -> None:
    request = Request({"type": "http", "headers": []})
    error = RequestValidationError([{"loc": "body", "msg": "bad"}])

    response = await handle_request_validation_error(request, error)

    assert b'"loc":[]' in bytes(response.body)
    assert b'"type"' in bytes(response.body)


async def test_internal_error_handler_without_request_id_omits_instance(
    settings: Settings,
) -> None:
    handler = build_internal_error_handler(settings)

    response = await handler(Request({"type": "http", "headers": []}), RuntimeError())

    assert b'"instance"' not in bytes(response.body)
    assert "x-request-id" not in response.headers


async def test_internal_error_handler_in_production_adds_hsts(
    settings: Settings,
) -> None:
    handler = build_internal_error_handler(
        settings.model_copy(update={"environment": "production"})
    )

    response = await handler(Request({"type": "http", "headers": []}), RuntimeError())

    assert "strict-transport-security" in response.headers
