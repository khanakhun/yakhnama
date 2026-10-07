"""Response headers in the OpenAPI document (ADR 0022).

FastAPI documents bodies, not headers, so a client generated from the contract does
not know that a response carries an ``ETag`` it must send back as ``If-Match``. Two
kinds of header are declared here:

- **Route headers** (``ETag``, ``Location``, ``Link``) are set by a route, so the
  route declares them with ``header_responses`` in its ``responses`` argument.
  FastAPI merges an entry for the route's own status code into the generated
  response, keeping its description and body schema.
- **Middleware headers and answers** come from around every route, so
  ``declare_middleware_headers`` adds them to the finished document once, in
  ``yakhnama.main``: ``Retry-After`` on every ``429`` (``platform/ratelimit``), and
  on an authenticated ``POST`` under ``/api/v1`` (the only requests the
  idempotency middleware handles) ``Idempotent-Replayed`` on every success, the
  middleware's own Problem Details answers (``400`` ``invalid-idempotency-key``,
  ``409`` ``idempotency-key-reused`` and ``idempotency-key-in-use``) where the
  route does not declare that status itself, and ``Retry-After`` on ``409``,
  described as sent only with ``idempotency-key-in-use``.

Router-level ``responses`` are merged with a route's by status code without a deep
merge, so a route must never redeclare ``429`` or ``409`` only to add a header: it
would drop the Problem Details body. The post-processing avoids that trap.

Patterns: none from the catalog; module-level functions only.
"""

from collections.abc import Mapping
from typing import Any, Final

ETAG: Final = "ETag"
LOCATION: Final = "Location"
LINK: Final = "Link"
RETRY_AFTER: Final = "Retry-After"
IDEMPOTENT_REPLAYED: Final = "Idempotent-Replayed"

_STRING: Final[Mapping[str, str]] = {"type": "string"}

# Any: OpenAPI header objects are free JSON; this is the document's own boundary.
HEADER_OBJECTS: Final[Mapping[str, Mapping[str, Any]]] = {
    ETAG: {
        "description": (
            'Strong entity tag of the returned resource, "<id>:<version>"; send it '
            "back as If-Match to change the resource."
        ),
        "schema": _STRING,
    },
    LOCATION: {
        "description": "Path of the created resource.",
        "schema": _STRING,
    },
    LINK: {
        "description": 'The next page, as <...>; rel="next"; absent on the last page.',
        "schema": _STRING,
    },
    RETRY_AFTER: {
        "description": "Seconds to wait before retrying.",
        "schema": {"type": "integer", "minimum": 0},
    },
    IDEMPOTENT_REPLAYED: {
        "description": (
            '"true" when the response is the stored answer to an earlier request '
            "with the same Idempotency-Key; absent otherwise."
        ),
        "schema": {"type": "string", "enum": ["true"]},
    },
}

_SUCCESS_PREFIX: Final = "2"
_TOO_MANY_REQUESTS: Final = "429"
_CONFLICT: Final = "409"
_BAD_REQUEST: Final = "400"
_IDEMPOTENT_PATH_PREFIX: Final = "/api/v1/"
_PROBLEM_CONTENT: Final[Mapping[str, Mapping[str, Any]]] = {
    "application/problem+json": {}
}

# Any: OpenAPI response and header objects are free JSON.
IDEMPOTENCY_RESPONSES: Final[Mapping[str, Mapping[str, Any]]] = {
    _BAD_REQUEST: {
        "description": (
            "RFC 9457 Problem Details: invalid-idempotency-key (Idempotency-Key "
            "is not a single UUID)."
        ),
        "content": _PROBLEM_CONTENT,
    },
    _CONFLICT: {
        "description": (
            "RFC 9457 Problem Details: idempotency-key-reused (the key was used "
            "with another request) or idempotency-key-in-use (a request with the "
            "key is still running)."
        ),
        "content": _PROBLEM_CONTENT,
    },
}
"""What the idempotency middleware answers itself, for routes that do not
already declare the status."""

CONFLICT_RETRY_AFTER: Final[Mapping[str, Any]] = {
    "description": (
        "Seconds to wait before retrying; sent only with idempotency-key-in-use, "
        "never with any other 409."
    ),
    "schema": {"type": "integer", "minimum": 0},
}


def header_responses(status_code: int, *names: str) -> dict[int | str, dict[str, Any]]:
    """Return a ``responses`` entry declaring route-set headers on one status.

    Args:
        status_code: The route's success status, for example ``200`` or ``201``.
        *names: Header names from ``HEADER_OBJECTS`` (``ETag``, ``Location``,
            ``Link``).

    Returns:
        ``{status_code: {"headers": {...}}}``, ready to merge into ``responses``.

    Raises:
        KeyError: If a name is not a declared header.
    """
    return {
        status_code: {"headers": {name: dict(HEADER_OBJECTS[name]) for name in names}}
    }


def _add_header(
    response: dict[str, Any], name: str, header: Mapping[str, Any] | None = None
) -> None:
    headers: dict[str, Any] = response.setdefault("headers", {})
    headers.setdefault(name, dict(HEADER_OBJECTS[name] if header is None else header))


def _is_idempotent_post(path: str, method: str, operation: Mapping[str, Any]) -> bool:
    return (
        method == "post"
        and path.startswith(_IDEMPOTENT_PATH_PREFIX)
        and bool(operation.get("security"))
    )


def declare_middleware_headers(document: dict[str, Any]) -> dict[str, Any]:
    """Declare the headers the middlewares set on every operation they touch.

    Changes ``document`` in place, and returns it for chaining.

    Args:
        document: A finished OpenAPI document (FastAPI's ``app.openapi()``).

    Returns:
        The same document.
    """
    paths: dict[str, dict[str, Any]] = document.get("paths", {})
    for path, operations in paths.items():
        for method, operation in operations.items():
            responses: dict[str, dict[str, Any]] = operation.setdefault("responses", {})
            is_idempotent = _is_idempotent_post(path, method, operation)
            if is_idempotent:
                for status_code, problem in IDEMPOTENCY_RESPONSES.items():
                    # A route's own 400 or 409 keeps its description and body.
                    responses.setdefault(
                        status_code,
                        {
                            "description": problem["description"],
                            "content": dict(problem["content"]),
                        },
                    )
            for status_code, response in responses.items():
                if status_code == _TOO_MANY_REQUESTS:
                    _add_header(response, RETRY_AFTER)
                elif is_idempotent and status_code == _CONFLICT:
                    _add_header(response, RETRY_AFTER, CONFLICT_RETRY_AFTER)
                elif is_idempotent and status_code.startswith(_SUCCESS_PREFIX):
                    _add_header(response, IDEMPOTENT_REPLAYED)
    return document
