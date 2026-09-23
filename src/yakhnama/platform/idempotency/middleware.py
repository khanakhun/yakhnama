"""The ``Idempotency-Key`` middleware (ADR 0016).

It acts only on an authenticated ``POST`` under ``/api/v1`` that carries an
``Idempotency-Key`` header; every other request passes through untouched (an
anonymous ``POST`` reaches the route, which answers 401). For such a request:

- a key that is not a UUID, or several ``Idempotency-Key`` headers, get 400
  ``invalid-idempotency-key``;
- the method, path, query string and body are hashed with SHA-256;
- the key is reserved in the ``IdempotencyStore``, scoped to the principal. If the
  reservation wins, the route runs; a 2xx response is stored for
  ``idempotency_ttl_hours`` and anything else releases the key so the client can
  retry;
- if another request holds the key: the same hash with a stored response replays it
  with ``Idempotent-Replayed: true``; the same hash still running gets 409
  ``idempotency-key-in-use`` with ``Retry-After``; a different hash gets 409
  ``idempotency-key-reused``.

Concurrent requests with one key are decided by the store's unique constraint: the
loser never runs the route. It re-reads the winner's record and replays it when the
winner has finished, or gets ``idempotency-key-in-use`` while it runs.

Only ``Content-Type``, ``Location`` and ``ETag`` are stored with a response; other
headers (``Set-Cookie`` above all) are never replayed.

Patterns: Decorator (ASGI middleware around the application).
"""

import hashlib
from collections.abc import Mapping
from datetime import timedelta
from http import HTTPStatus
from typing import Final
from uuid import UUID

import structlog
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yakhnama.platform.auth.resolution import get_principal_resolution
from yakhnama.platform.idempotency.store import (
    PENDING_LEASE,
    IdempotencyRecord,
    IdempotencyReservation,
    IdempotencyStore,
    StoredResponse,
)
from yakhnama.platform.problem_details import build_problem, problem_response
from yakhnama.platform.request_state import get_request_id
from yakhnama.shared_kernel.clock import Clock

IDEMPOTENCY_KEY_HEADER: Final = b"idempotency-key"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
STORED_HEADERS: Final = frozenset({"content-type", "location", "etag"})
# Creating responses are small; a larger one is served but not kept, so the table
# never holds bulk data (and the key is released, not left pending).
MAX_STORED_BODY_BYTES: Final = 256 * 1024
IN_USE_RETRY_AFTER_SECONDS: Final = 1


def request_fingerprint(scope: Scope, body: bytes) -> str:
    """Return the SHA-256 hex digest that identifies a request's content.

    Args:
        scope: The HTTP ASGI scope.
        body: The complete request body.

    Returns:
        64 lowercase hexadecimal characters over method, path, query and body.
    """
    digest = hashlib.sha256()
    for part in (
        scope["method"].encode(),
        scope["path"].encode(),
        scope.get("query_string", b""),
    ):
        # Length-prefixed so that no two different requests hash the same bytes.
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    digest.update(body)
    return digest.hexdigest()


def _header_values(scope: Scope, name: bytes) -> list[bytes]:
    # ASGI headers are (bytes, bytes) pairs; the scope itself is untyped.
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    return [value for header_name, value in headers if header_name == name]


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replaying_receive(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if sent:
            # After the body only a disconnect can arrive; wait for the real one.
            return await receive()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay


class _ResponseRecorder:
    """Forwards a response to the client while keeping a copy for the store.

    Implements: Decorator (of the ASGI ``send`` channel).

    Attributes:
        status_code: The response status, once started.
        headers: The allow-listed headers, once started.
        is_too_large: Whether the body exceeded ``MAX_STORED_BODY_BYTES``.
    """

    def __init__(self, send: Send) -> None:
        self._send = send
        self._chunks: list[bytes] = []
        self._size = 0
        self.status_code: int | None = None
        self.headers: tuple[tuple[str, str], ...] = ()
        self.is_too_large = False

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status_code = message["status"]
            raw_headers: list[tuple[bytes, bytes]] = message.get("headers", [])
            self.headers = tuple(
                (name.decode("latin-1").lower(), value.decode("latin-1"))
                for name, value in raw_headers
                if name.decode("latin-1").lower() in STORED_HEADERS
            )
        elif message["type"] == "http.response.body" and not self.is_too_large:
            chunk: bytes = message.get("body", b"")
            self._size += len(chunk)
            if self._size > MAX_STORED_BODY_BYTES:
                self.is_too_large = True
                self._chunks.clear()
            else:
                self._chunks.append(chunk)
        await self._send(message)

    def stored_response(self) -> StoredResponse | None:
        """Return the response to store, or ``None`` if it must not be stored.

        Returns:
            The recorded 2xx response within the size cap, else ``None``.
        """
        status = self.status_code
        if status is None or not 200 <= status <= 299 or self.is_too_large:  # noqa: PLR2004  # reason: the 2xx range is the definition
            return None
        return StoredResponse(
            status_code=status, headers=self.headers, body=b"".join(self._chunks)
        )


class IdempotencyMiddleware:
    """ASGI middleware that replays the response of a repeated creating ``POST``.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: IdempotencyStore,
        clock: Clock,
        ttl: timedelta,
        path_prefix: str,
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            store: Where reservations and responses are kept.
            clock: Source of the current instant.
            ttl: How long a stored response is replayed.
            path_prefix: Only ``POST`` requests under this prefix are handled.
        """
        self._app = app
        self._store = store
        self._clock = clock
        self._ttl = ttl
        self._path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one request; see the module docstring for the rules.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if not self._applies_to(scope):
            await self._app(scope, receive, send)
            return
        raw_keys = _header_values(scope, IDEMPOTENCY_KEY_HEADER)
        principal = get_principal_resolution(scope).principal
        if not raw_keys or principal is None:
            await self._app(scope, receive, send)
            return
        key = _parse_key(raw_keys)
        if key is None:
            await _send_problem(
                scope,
                receive,
                send,
                status=HTTPStatus.BAD_REQUEST,
                slug="invalid-idempotency-key",
                detail="Idempotency-Key must be a single UUID.",
            )
            return
        body = await _read_body(receive)
        now = self._clock.now()
        reservation = IdempotencyReservation(
            scope=principal.scope_key(),
            key=key,
            request_hash=request_fingerprint(scope, body),
            method=scope["method"],
            path=scope["path"][:2048],
            created_at=now,
            expires_at=now + PENDING_LEASE,
        )
        existing = await self._store.reserve(reservation)
        if existing is not None:
            await self._answer_existing(scope, receive, send, reservation, existing)
            return
        await self._run_and_store(
            scope, _replaying_receive(body, receive), send, reservation
        )

    def _applies_to(self, scope: Scope) -> bool:
        path: str = scope.get("path", "")
        return (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and (path == self._path_prefix or path.startswith(self._path_prefix + "/"))
        )

    async def _run_and_store(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        reservation: IdempotencyReservation,
    ) -> None:
        recorder = _ResponseRecorder(send)
        try:
            await self._app(scope, receive, recorder)
        except BaseException:
            # The route failed; free the key so a retry is not answered "in use".
            await self._store.release(reservation.scope, reservation.key)
            raise
        response = recorder.stored_response()
        if response is None:
            if recorder.is_too_large:
                structlog.get_logger(__name__).warning(
                    "idempotent_response_not_stored", reason="body_too_large"
                )
            await self._store.release(reservation.scope, reservation.key)
            return
        await self._store.complete(
            reservation.scope,
            reservation.key,
            response,
            self._clock.now() + self._ttl,
        )

    async def _answer_existing(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        reservation: IdempotencyReservation,
        existing: IdempotencyRecord,
    ) -> None:
        if existing.request_hash != reservation.request_hash:
            await _send_problem(
                scope,
                receive,
                send,
                status=HTTPStatus.CONFLICT,
                slug="idempotency-key-reused",
                detail="This Idempotency-Key was used with a different request.",
            )
            return
        if existing.response is None:
            await _send_problem(
                scope,
                receive,
                send,
                status=HTTPStatus.CONFLICT,
                slug="idempotency-key-in-use",
                detail="A request with this Idempotency-Key is still being processed.",
                headers={"Retry-After": str(IN_USE_RETRY_AFTER_SECONDS)},
            )
            return
        stored = existing.response
        replay = Response(content=stored.body, status_code=stored.status_code)
        for name, value in stored.headers:
            replay.headers.append(name, value)
        replay.headers[REPLAYED_HEADER] = "true"
        await replay(scope, receive, send)


async def _send_problem(  # noqa: PLR0913  # reason: the ASGI triple plus the problem
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status: HTTPStatus,
    slug: str,
    detail: str,
    headers: Mapping[str, str] | None = None,
) -> None:
    problem = build_problem(status, slug, detail=detail, instance=get_request_id(scope))
    await problem_response(problem, headers)(scope, receive, send)


def _parse_key(raw_keys: list[bytes]) -> UUID | None:
    if len(raw_keys) != 1:
        return None
    try:
        return UUID(raw_keys[0].decode("latin-1").strip())
    except ValueError:
        return None
