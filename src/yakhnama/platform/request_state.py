"""Typed accessors for the per-request values the platform keeps in the ASGI scope.

The middlewares run outside FastAPI's dependency injection, so the values they share
with each other and with routes (the request id, the resolved principal) travel in
``scope["state"]``, the dictionary Starlette exposes as ``request.state``. Keeping
the keys and their types in one module means no middleware reads another's value by
a string literal.

Patterns: none from the catalog; module-level accessor functions only.
"""

from typing import Final

from starlette.types import Scope

REQUEST_ID_STATE_KEY: Final = "request_id"
PRINCIPAL_RESOLUTION_STATE_KEY: Final = "principal_resolution"


def state_of(scope: Scope) -> dict[str, object]:
    """Return the scope's mutable state dictionary, creating it when absent.

    ``scope["state"]`` is a plain ``dict`` by the ASGI and Starlette contracts; it is
    the framework boundary, not a Yakhnama layer boundary.

    Args:
        scope: An HTTP ASGI scope.

    Returns:
        The dictionary behind ``request.state``.
    """
    state = scope.get("state")
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    return state


def get_request_id(scope: Scope) -> str | None:
    """Return the request id set by the request-id middleware, if any.

    Args:
        scope: An HTTP ASGI scope.

    Returns:
        The request id, or ``None`` when the middleware has not run.
    """
    value = state_of(scope).get(REQUEST_ID_STATE_KEY)
    return value if isinstance(value, str) else None


def set_request_id(scope: Scope, request_id: str) -> None:
    """Record the request id for the rest of the request.

    Args:
        scope: An HTTP ASGI scope.
        request_id: The accepted or generated request id.
    """
    state_of(scope)[REQUEST_ID_STATE_KEY] = request_id
