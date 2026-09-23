"""Tiny ASGI applications and a client helper for middleware unit tests.

Each middleware is tested around ``RecordingApp``, which answers every request with
a fixed status and body and records what it received, so a test sees exactly what
the middleware passed on and what it sent back. No network is involved: requests go
through ``httpx.ASGITransport`` in process.

Patterns: Fake.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yakhnama.platform.request_state import get_request_id


class RecordingApp:
    """ASGI app that records each request and answers with a fixed response.

    Implements: Fake (of the routed application).

    Attributes:
        bodies: The full body of every request received.
        scopes: The scope of every request received.
        calls: How many requests reached the app.
    """

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b'{"ok": true}',
        headers: tuple[tuple[bytes, bytes], ...] = (
            (b"content-type", b"application/json"),
        ),
        error: Exception | None = None,
    ) -> None:
        """Fix the response.

        Args:
            status: The response status.
            body: The response body.
            headers: The response headers.
            error: Raised instead of answering, when set.
        """
        self._status = status
        self._body = body
        self._headers = headers
        self._error = error
        self.bodies: list[bytes] = []
        self.scopes: list[Scope] = []
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Read the body, record the request and answer.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.

        Raises:
            Exception: The configured ``error``, if any.
        """
        if scope["type"] != "http":
            return
        self.calls += 1
        self.scopes.append(scope)
        chunks: list[bytes] = []
        while True:
            message: Message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        self.bodies.append(b"".join(chunks))
        if self._error is not None:
            raise self._error
        await send(
            {
                "type": "http.response.start",
                "status": self._status,
                "headers": list(self._headers),
            }
        )
        await send({"type": "http.response.body", "body": self._body})


async def request_id_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Answer with the request id the middlewares recorded, as JSON.

    Args:
        scope: The ASGI scope.
        receive: The ASGI receive channel (unused).
        send: The ASGI send channel.
    """
    del receive
    body = json.dumps({"request_id": get_request_id(scope)}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def client_for(
    app: ASGIApp, *, base_url: str = "http://localhost"
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client calling ``app`` in process.

    Args:
        app: The ASGI app under test.
        base_url: Sets the ``Host`` header.

    Yields:
        The client.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        yield client
