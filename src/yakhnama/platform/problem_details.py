"""RFC 9457 Problem Details bodies and the problem type registry.

Every error response of the API is an ``application/problem+json`` body built here.
Exceptions are mapped to it by the handlers registered in ``yakhnama.main``
(``AGENTS.md`` §2.3); the platform middlewares, which run outside FastAPI's exception
handling, build their responses with ``problem_response`` directly.

Every problem carries a ``type`` URI ``https://yakhnama.org/problems/<slug>`` and an
``instance`` equal to the request id, so a client can quote one line to support and an
operator can find the matching log line. The slugs are a closed list,
``PROBLEM_TYPES``; a new slug is added here and documented in the API docs, never
invented at a call site.

| Slug | Status | Meaning |
|------|--------|---------|
| ``bad-request`` | 400 | The request is malformed (generic). |
| ``invalid-host`` | 400 | The ``Host`` header is not a trusted host. |
| ``invalid-idempotency-key`` | 400 | ``Idempotency-Key`` is not a UUID. |
| ``authentication-failed`` | 401 | No valid bearer token where one is required. |
| ``permission-denied`` | 403 | The principal may not perform the action. |
| ``not-found`` | 404 | The resource or route does not exist. |
| ``method-not-allowed`` | 405 | The route exists but not for this method. |
| ``conflict`` | 409 | The request conflicts with the current state. |
| ``invariant-violation`` | 409 | The change would break an invariant. |
| ``invalid-transition`` | 409 | The state machine forbids the transition. |
| ``idempotency-key-reused`` | 409 | The key was used with a different request. |
| ``idempotency-key-in-use`` | 409 | A request with the same key is still running. |
| ``precondition-failed`` | 412 | ``If-Match`` does not match the current version. |
| ``payload-too-large`` | 413 | The body exceeds ``max_request_body_bytes``. |
| ``validation-error`` | 422 | The data is invalid; see ``errors``. |
| ``nul-character`` | 422 | A string input contains the NUL character. |
| ``precondition-required`` | 428 | A conditional request came without ``If-Match``. |
| ``rate-limited`` | 429 | Too many requests; see ``Retry-After``. |
| ``internal-error`` | 500 | An unexpected server error. |
| ``service-unavailable`` | 503 | A dependency (the identity provider) is down. |
| ``http-error`` | any | Any other HTTP error raised by the framework. |

Patterns: API Schema (proposed in ADR 0011).
"""

from collections.abc import Mapping
from http import HTTPStatus
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

PROBLEM_JSON_MEDIA_TYPE: Final = "application/problem+json"
PROBLEM_TYPE_BASE_URL: Final = "https://yakhnama.org/problems/"
# RFC 9457 §4.2.1: "about:blank" means the status code's title is the whole semantics.
DEFAULT_PROBLEM_TYPE: Final = "about:blank"

# slug -> one-line meaning; the table in the module docstring is its readable form.
PROBLEM_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "bad-request": "The request is malformed.",
        "invalid-host": "The Host header is not a trusted host.",
        "invalid-idempotency-key": "Idempotency-Key is not a UUID.",
        "authentication-failed": "No valid bearer token where one is required.",
        "permission-denied": "The principal may not perform the action.",
        "not-found": "The resource or route does not exist.",
        "method-not-allowed": "The route exists but not for this method.",
        "conflict": "The request conflicts with the current state.",
        "invariant-violation": "The change would break an invariant.",
        "invalid-transition": "The state machine forbids the transition.",
        "idempotency-key-reused": "The key was used with a different request.",
        "idempotency-key-in-use": "A request with the same key is still running.",
        "precondition-failed": "If-Match does not match the current version.",
        "payload-too-large": "The request body is too large.",
        "validation-error": "The data is invalid.",
        "nul-character": "A string input contains the NUL character.",
        "precondition-required": "A conditional request came without If-Match.",
        "rate-limited": "Too many requests.",
        "internal-error": "An unexpected server error.",
        "service-unavailable": "A dependency is unavailable.",
        "http-error": "An HTTP error raised by the framework.",
    }
)


def problem_type(slug: str) -> str:
    """Return the ``type`` URI of a registered problem slug.

    Args:
        slug: One of the keys of ``PROBLEM_TYPES``.

    Returns:
        ``https://yakhnama.org/problems/<slug>``.

    Raises:
        KeyError: If ``slug`` is not registered; an unknown slug is a programming
            error that must fail in tests, not a new type made up at run time.
    """
    if slug not in PROBLEM_TYPES:
        raise KeyError(slug)
    return f"{PROBLEM_TYPE_BASE_URL}{slug}"


class ProblemFieldError(BaseModel):
    """One invalid field: where it is and what is wrong, never the value itself.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        loc: Path to the offending field, for example ``["body", "limit"]``.
        msg: Human-readable reason.
        type: Pydantic's machine-readable error type, for example ``"int_parsing"``;
            omitted when unknown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    loc: tuple[str | int, ...]
    msg: str
    type: str | None = None


class ProblemDetails(BaseModel):
    """An RFC 9457 problem object.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        type: Problem type URI, ``https://yakhnama.org/problems/<slug>``.
        title: Short summary, the HTTP status phrase.
        status: The HTTP status code, repeated for clients that lose the header.
        detail: Explanation for this occurrence, safe to show; omitted when absent.
        instance: The request id of this occurrence; omitted only when no request
            id is known.
        errors: Field errors for validation problems; omitted otherwise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = DEFAULT_PROBLEM_TYPE
    title: str
    status: int = Field(ge=400, le=599)
    detail: str | None = None
    instance: str | None = None
    errors: tuple[ProblemFieldError, ...] | None = None


def build_problem(
    status: HTTPStatus,
    slug: str,
    *,
    detail: str | None = None,
    instance: str | None = None,
    errors: tuple[ProblemFieldError, ...] | None = None,
) -> ProblemDetails:
    """Build a problem whose title is the status phrase and type the slug's URI.

    Args:
        status: The HTTP status.
        slug: A key of ``PROBLEM_TYPES``.
        detail: A client-safe explanation, if any.
        instance: The request id.
        errors: Field errors, for validation problems.

    Returns:
        The problem object.

    Raises:
        KeyError: If ``slug`` is not registered.
    """
    return ProblemDetails(
        type=problem_type(slug),
        title=status.phrase,
        status=status,
        detail=detail,
        instance=instance,
        errors=errors,
    )


def problem_response(
    problem: ProblemDetails, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """Render a problem as an ``application/problem+json`` response.

    Args:
        problem: The problem to send.
        headers: Extra response headers, for example ``Retry-After``.

    Returns:
        A response usable from a route, an exception handler or, as an ASGI app,
        from a middleware.
    """
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=problem.status,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers=dict(headers) if headers is not None else None,
    )
