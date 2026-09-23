"""The rate-limit middleware (ADR 0017).

Each HTTP request is counted against one key: ``principal:<scope key>`` when the
bearer token was valid, otherwise ``ip:<hash of the client IP>``. Authenticated and
anonymous callers get their own limits from settings. A request over the limit gets
429 ``rate-limited`` with ``Retry-After``; every counted response carries
``X-RateLimit-Limit`` and ``X-RateLimit-Remaining``.

The client IP is hashed before it is used as a key so the limiter's store (Redis in
production) never holds a raw address, and it is never logged. The IP is the ASGI
peer address: behind a reverse proxy, run uvicorn with ``--proxy-headers`` and
``--forwarded-allow-ips`` set to the proxy, or every client shares the proxy's
bucket.

Health probes (``/health/...``) are not counted: orchestrators probe from a few
addresses on a fixed schedule, and a throttled probe would take a healthy process
out of service. ``/health/ready`` is protected by its short result cache instead
(``platform/health.py``).

Patterns: Decorator (ASGI middleware around the application).
"""

import hashlib
from http import HTTPStatus
from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yakhnama.platform.auth.resolution import get_principal_resolution
from yakhnama.platform.problem_details import build_problem, problem_response
from yakhnama.platform.ratelimit.limiter import RateLimitDecision, RateLimiter
from yakhnama.platform.request_state import get_request_id

EXEMPT_PATH_PREFIXES: Final = ("/health/",)
LIMIT_HEADER: Final = "X-RateLimit-Limit"
REMAINING_HEADER: Final = "X-RateLimit-Remaining"
RETRY_AFTER_HEADER: Final = "Retry-After"
UNKNOWN_CLIENT: Final = "unknown"
# 128 bits of the digest: collisions between real clients are negligible.
_IP_DIGEST_HEX_LENGTH: Final = 32


def client_key(scope: Scope) -> str:
    """Return the rate-limit key of a request.

    Args:
        scope: An HTTP ASGI scope, after principal resolution.

    Returns:
        ``principal:<scope key>`` for a valid token, else ``ip:<digest>``.
    """
    principal = get_principal_resolution(scope).principal
    if principal is not None:
        return f"principal:{principal.scope_key()}"
    client = scope.get("client")
    host = client[0] if client else UNKNOWN_CLIENT
    digest = hashlib.sha256(str(host).encode()).hexdigest()[:_IP_DIGEST_HEX_LENGTH]
    return f"ip:{digest}"


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    """Return the informational headers for a decision.

    Args:
        decision: The limiter's decision.

    Returns:
        ``X-RateLimit-Limit`` and ``X-RateLimit-Remaining``, plus ``Retry-After``
        when the request was refused.
    """
    headers = {
        LIMIT_HEADER: str(decision.limit),
        REMAINING_HEADER: str(decision.remaining),
    }
    if not decision.is_allowed:
        headers[RETRY_AFTER_HEADER] = str(decision.retry_after_seconds)
    return headers


class RateLimitMiddleware:
    """ASGI middleware that refuses requests over the caller's limit.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        anonymous_per_minute: int,
        authenticated_per_minute: int,
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            limiter: The rate limiter adapter.
            anonymous_per_minute: Limit per client IP without a valid token.
            authenticated_per_minute: Limit per principal.
        """
        self._app = app
        self._limiter = limiter
        self._anonymous_per_minute = anonymous_per_minute
        self._authenticated_per_minute = authenticated_per_minute

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Count the request and refuse it, or pass it on with the limit headers.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        path: str = scope.get("path", "")
        if scope["type"] != "http" or path.startswith(EXEMPT_PATH_PREFIXES):
            await self._app(scope, receive, send)
            return
        is_authenticated = get_principal_resolution(scope).principal is not None
        limit = (
            self._authenticated_per_minute
            if is_authenticated
            else self._anonymous_per_minute
        )
        decision = await self._limiter.check(client_key(scope), limit)
        headers = rate_limit_headers(decision)
        if not decision.is_allowed:
            problem = build_problem(
                HTTPStatus.TOO_MANY_REQUESTS,
                "rate-limited",
                detail="Too many requests; retry after the time in Retry-After.",
                instance=get_request_id(scope),
            )
            await problem_response(problem, headers)(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    response_headers[name] = value
            await send(message)

        await self._app(scope, receive, send_with_headers)
