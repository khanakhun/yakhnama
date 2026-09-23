r"""HTTP hardening middlewares: request ids, logging, headers, hosts, bodies.

Covered here: request ids, request logging, security headers, trusted hosts, CORS
options and request body limits.

Every class here is a pure ASGI middleware (no ``BaseHTTPMiddleware``), so none of
them buffers responses or breaks streaming, and each one is testable around a tiny
ASGI app. ``yakhnama.main`` installs them in this order, outermost first:

1. ``RequestIdMiddleware``: accepts a well-formed ``X-Request-ID`` or generates one,
   binds it to the structlog context and echoes it on the response.
2. ``RequestLoggingMiddleware``: one ``http_request`` line per request with the
   method, route template, status and duration. Never the IP, the query string,
   headers or the body, which can all carry personal data.
3. ``SecurityHeadersMiddleware``: ``nosniff``, ``no-referrer``, ``DENY`` framing, a
   ``default-src 'none'`` CSP (a relaxed one only on the API reference page,
   ADR 0014) and HSTS in production.
4. ``TrustedHostMiddleware``: 400 ``invalid-host`` for a ``Host`` not in
   ``trusted_hosts``. It replaces Starlette's middleware of the same name only to
   answer with Problem Details instead of plain text.
5. ``CORSMiddleware`` (Starlette's), configured by ``build_cors_options``.
6. then authentication and rate limiting (``platform/auth``, ``platform/ratelimit``),
7. ``RequestBodyGuardMiddleware``: 413 ``payload-too-large`` above
   ``max_request_body_bytes`` and 422 ``nul-character`` for a NUL character in the
   path, the query string or a JSON, form or text body,
8. and idempotency (``platform/idempotency``) closest to the routes.

NUL rejection scans the raw bytes rather than hooking every Pydantic string: one
pass over a body that is at most ``max_request_body_bytes`` long covers every
string field of every present and future schema, including strings nested in free
JSON, while a validator would have to be remembered on each model. A JSON body is
searched for the escape ``\u0000`` that is not itself escaped and for a raw NUL
byte; a form body for ``%00``; the path and query string likewise. Multipart and
binary bodies are not scanned (files may legitimately contain NUL); their text
fields are validated by the routes that accept them (Phase 3). PostgreSQL rejects
NUL in ``text`` columns, so without this a single character would turn a valid
request into a 500.

Patterns: Decorator (ASGI middlewares around the application), Value Object
(``CorsOptions``).
"""

import re
import time
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Final

import structlog
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yakhnama.platform.problem_details import build_problem, problem_response
from yakhnama.platform.request_state import get_request_id, set_request_id
from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.ids import IdGenerator

# An accepted inbound request id is short and cannot carry markup, spaces or
# anything that would need escaping in a log line or a URI reference.
REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
API_CONTENT_SECURITY_POLICY: Final = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
# Two years, the value hstspreload.org requires.
STRICT_TRANSPORT_SECURITY: Final = "max-age=63072000; includeSubDomains"
BASE_SECURITY_HEADERS: Final[Mapping[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
CORS_ALLOWED_METHODS: Final = ("GET", "POST", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOWED_HEADERS: Final = (
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "If-Match",
    "If-None-Match",
)
CORS_EXPOSED_HEADERS: Final = (
    "ETag",
    "Location",
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "Idempotent-Replayed",
)
CORS_MAX_AGE_SECONDS: Final = 600
UNMATCHED_ROUTE: Final = "<unmatched>"

_ESCAPED_NUL: Final = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000")
_RAW_NUL: Final = b"\x00"
_PERCENT_NUL: Final = b"%00"
_JSON_MEDIA_TYPE: Final = "application/json"
_JSON_SUFFIX: Final = "+json"
_FORM_MEDIA_TYPE: Final = "application/x-www-form-urlencoded"
_TEXT_PREFIX: Final = "text/"


async def _send_problem(  # noqa: PLR0913  # reason: the ASGI triple plus the problem
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status: HTTPStatus,
    slug: str,
    detail: str,
) -> None:
    problem = build_problem(status, slug, detail=detail, instance=get_request_id(scope))
    await problem_response(problem)(scope, receive, send)


class RequestIdMiddleware:
    """Gives every HTTP request an id, in the logs and on the response.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(
        self, app: ASGIApp, *, header_name: str, id_generator: IdGenerator
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            header_name: The request and response header, ``X-Request-ID``.
            id_generator: Source of generated ids.
        """
        self._app = app
        self._header_name = header_name
        self._id_generator = id_generator

    def request_id_for(self, scope: Scope) -> str:
        """Return the inbound request id if well formed, else a new one.

        Args:
            scope: An HTTP ASGI scope.

        Returns:
            The request id to use.
        """
        inbound = Headers(scope=scope).get(self._header_name)
        if inbound is not None and REQUEST_ID_PATTERN.fullmatch(inbound):
            return inbound
        return str(self._id_generator.new_id())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Assign the id, bind it for logging and echo it on the response.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = self.request_id_for(scope)
        set_request_id(scope, request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[self._header_name] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            await self._app(scope, receive, send_with_id)


class RequestLoggingMiddleware:
    """Logs one line per HTTP request without any personal data.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(
        self, app: ASGIApp, *, timer: Callable[[], float] = time.perf_counter
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            timer: Monotonic seconds, for the duration; injectable for tests.
        """
        self._app = app
        self._timer = timer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the request and log its outcome, also when it raises.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = self._timer()
        status = HTTPStatus.INTERNAL_SERVER_ERROR.value

        async def send_recording_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, send_recording_status)
        finally:
            route = scope.get("route")
            # The template ("/api/v1/places/{place_id}"), never the concrete path,
            # which can embed identifiers that point at a person.
            template = getattr(route, "path", None)
            structlog.get_logger(__name__).info(
                "http_request",
                method=scope["method"],
                route=template if isinstance(template, str) else UNMATCHED_ROUTE,
                status=status,
                duration_ms=round((self._timer() - started) * 1000, 2),
                request_id=get_request_id(scope),
            )


def security_headers(
    content_security_policy: str, *, is_hsts_enabled: bool
) -> dict[str, str]:
    """Return the security headers for one response.

    Args:
        content_security_policy: The CSP for this response.
        is_hsts_enabled: Whether to add ``Strict-Transport-Security``.

    Returns:
        Header names and values.
    """
    headers = dict(BASE_SECURITY_HEADERS)
    headers["Content-Security-Policy"] = content_security_policy
    if is_hsts_enabled:
        headers["Strict-Transport-Security"] = STRICT_TRANSPORT_SECURITY
    return headers


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response that lacks them.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        is_hsts_enabled: bool,
        page_policies: Mapping[str, str] | None = None,
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            is_hsts_enabled: Add HSTS (production only: HSTS on a local http
                origin would break development browsers for its whole max-age).
            page_policies: A CSP per exact path for HTML pages (the API
                reference); every other response gets ``API_CONTENT_SECURITY_POLICY``.
        """
        self._app = app
        self._is_hsts_enabled = is_hsts_enabled
        self._page_policies = dict(page_policies or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on and add the headers to its response.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        policy = self._page_policies.get(
            scope.get("path", ""), API_CONTENT_SECURITY_POLICY
        )
        headers = security_headers(policy, is_hsts_enabled=self._is_hsts_enabled)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    if name not in response_headers:
                        response_headers[name] = value
            await send(message)

        await self._app(scope, receive, send_with_headers)


def host_of(scope: Scope) -> str:
    """Return the lower-case host of the ``Host`` header, without the port.

    Args:
        scope: An HTTP ASGI scope.

    Returns:
        The host, ``""`` when the header is missing; IPv6 literals keep no brackets.
    """
    raw = Headers(scope=scope).get("host", "").strip().lower()
    if raw.startswith("["):
        return raw[1:].partition("]")[0]
    return raw.partition(":")[0]


def is_trusted_host(host: str, trusted_hosts: Sequence[str]) -> bool:
    """Tell whether ``host`` matches one of ``trusted_hosts``.

    Args:
        host: The request's host, from ``host_of``.
        trusted_hosts: Exact names, ``*.domain`` for one subdomain level or more,
            or ``*`` for any host.

    Returns:
        ``True`` if the host is allowed.
    """
    if not host:
        return False
    for pattern in trusted_hosts:
        candidate = pattern.lower()
        if candidate in {"*", host}:
            return True
        if candidate.startswith("*.") and host.endswith(candidate[1:]):
            return True
    return False


class TrustedHostMiddleware:
    """Refuses requests whose ``Host`` is not trusted, as Problem Details.

    A forged ``Host`` is how DNS rebinding reaches an API on a private network and
    how poisoned absolute URLs get into responses; answering only known names
    closes both.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(self, app: ASGIApp, *, trusted_hosts: Sequence[str]) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            trusted_hosts: The allowed host patterns, see ``is_trusted_host``.
        """
        self._app = app
        self._trusted_hosts = tuple(trusted_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 400 for an untrusted host, else pass the request on.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] not in {"http", "websocket"} or is_trusted_host(
            host_of(scope), self._trusted_hosts
        ):
            await self._app(scope, receive, send)
            return
        await _send_problem(
            scope,
            receive,
            send,
            status=HTTPStatus.BAD_REQUEST,
            slug="invalid-host",
            detail="The Host header does not name this service.",
        )


class CorsOptions(BaseModel):
    """The CORS policy, as keyword arguments of Starlette's ``CORSMiddleware``.

    Implements: Value Object.

    Attributes:
        allow_origins: Exact origins allowed to call the API from a browser.
        allow_methods: Methods allowed cross-origin.
        allow_headers: Request headers allowed cross-origin.
        expose_headers: Response headers a browser script may read.
        allow_credentials: Always ``False``, see ``build_cors_options``.
        max_age: Seconds a browser may cache a preflight answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allow_origins: tuple[str, ...]
    allow_methods: tuple[str, ...]
    allow_headers: tuple[str, ...]
    expose_headers: tuple[str, ...]
    allow_credentials: bool
    max_age: int


def build_cors_options(settings: Settings) -> CorsOptions | None:
    """Return the CORS policy for Starlette's ``CORSMiddleware``.

    Credentials are never allowed: the API authenticates with bearer tokens, not
    cookies, so a browser has nothing ambient to send and the ``*``/credentials
    pitfall cannot arise.

    Args:
        settings: Supplies ``cors_allow_origins`` and ``request_id_header``.

    Returns:
        The policy, or ``None`` when no origin is allowed (the middleware is then
        not installed and browsers refuse every cross-origin call).
    """
    if not settings.cors_allow_origins:
        return None
    return CorsOptions(
        allow_origins=tuple(settings.cors_allow_origins),
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=(*CORS_ALLOWED_HEADERS, settings.request_id_header),
        expose_headers=(*CORS_EXPOSED_HEADERS, settings.request_id_header),
        allow_credentials=False,
        max_age=CORS_MAX_AGE_SECONDS,
    )


def is_scanned_media_type(content_type: str | None) -> bool:
    """Tell whether a body of this media type is scanned for NUL characters.

    Args:
        content_type: The ``Content-Type`` header, or ``None`` when absent (FastAPI
            then parses the body as JSON, so it is scanned).

    Returns:
        ``True`` for JSON, ``+json``, form-urlencoded and ``text/*`` bodies.
    """
    if content_type is None:
        return True
    media_type = content_type.partition(";")[0].strip().lower()
    return (
        media_type in {_JSON_MEDIA_TYPE, _FORM_MEDIA_TYPE}
        or media_type.endswith(_JSON_SUFFIX)
        or media_type.startswith(_TEXT_PREFIX)
    )


def contains_nul(data: bytes, *, is_json: bool) -> bool:
    r"""Tell whether raw request bytes encode a NUL character.

    Args:
        data: A body, a query string or an encoded path.
        is_json: Also look for the JSON escape ``\u0000`` (not itself escaped).

    Returns:
        ``True`` if a raw NUL byte, ``%00`` or (for JSON) ``\u0000`` is present.
    """
    if _RAW_NUL in data or _PERCENT_NUL in data:
        return True
    return is_json and _ESCAPED_NUL.search(data) is not None


class RequestBodyGuardMiddleware:
    """Enforces the body size limit and rejects NUL characters in text inputs.

    The body is read in full (at most ``max_bytes``) before the route runs and then
    handed on unchanged, so an oversized body is refused before any work is done.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            max_bytes: The largest body accepted.
        """
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check the request, then pass it on with its buffered body.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if self._declares_too_large(headers.get("content-length")):
            await self._too_large(scope, receive, send)
            return
        path: str = scope.get("path", "")
        query: bytes = scope.get("query_string", b"")
        if "\x00" in path or contains_nul(query, is_json=False):
            await self._nul(scope, receive, send)
            return
        body = await self._read_body(receive)
        if body is None:
            await self._too_large(scope, receive, send)
            return
        content_type = headers.get("content-type")
        if body and is_scanned_media_type(content_type):
            is_json = content_type is None or "json" in content_type.lower()
            if contains_nul(body, is_json=is_json):
                await self._nul(scope, receive, send)
                return
        await self._app(scope, _replay(body, receive), send)

    def _declares_too_large(self, content_length: str | None) -> bool:
        if content_length is None:
            return False
        # A malformed Content-Length is left to the server, which rejects it; here
        # only an honest oversized declaration is refused early.
        return (
            content_length.strip().isdigit() and int(content_length) > self._max_bytes
        )

    async def _read_body(self, receive: Receive) -> bytes | None:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk: bytes = message.get("body", b"")
            size += len(chunk)
            if size > self._max_bytes:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
        await _send_problem(
            scope,
            receive,
            send,
            status=HTTPStatus.CONTENT_TOO_LARGE,
            slug="payload-too-large",
            detail="The request body is larger than this service accepts.",
        )

    @staticmethod
    async def _nul(scope: Scope, receive: Receive, send: Send) -> None:
        await _send_problem(
            scope,
            receive,
            send,
            status=HTTPStatus.UNPROCESSABLE_CONTENT,
            slug="nul-character",
            detail="Text inputs must not contain the NUL character.",
        )


def _replay(body: bytes, receive: Receive) -> Receive:
    is_sent = False

    async def replay() -> Message:
        nonlocal is_sent
        if is_sent:
            # After the body only a disconnect can arrive; wait for the real one.
            return await receive()
        is_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay
