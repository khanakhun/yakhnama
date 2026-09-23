"""FastAPI dependencies of the geography HTTP API.

The composition root stores the ``Container`` on ``app.state.container``. This
package reads it through the ``GeographyApiServices`` protocol instead of importing
``yakhnama.platform.container``: that module imports every module's infrastructure,
so importing it here would break the layer contract, even indirectly.

``require_valid_credentials`` makes an anonymous route answer 401 when a bearer token
was sent but rejected, without declaring the bearer scheme in OpenAPI: these routes
need no token, so a client must never mistake a rejected token for a successful
anonymous call (``platform/auth/resolution.py``).

Patterns: Dependency Injection.
"""

from typing import Annotated, Protocol

from fastapi import Depends, Request

from yakhnama.modules.geography.application.ports import PlaceQueryService
from yakhnama.platform.auth.resolution import get_principal_resolution

MISSING_CONTAINER_MESSAGE = (
    "app.state.container is missing; build the app with create_app"
)


class GeographyApiServices(Protocol):
    """What the geography router reads from the composition root.

    Implements: Dependency Injection (provider port bound in the composition root).
    """

    @property
    def place_query_service(self) -> PlaceQueryService:
        """Return the place query service."""
        ...


def get_geography_services(request: Request) -> GeographyApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The container, seen through ``GeographyApiServices``.

    Raises:
        RuntimeError: If the app was not built by ``create_app``; a wiring bug, so a
            server error rather than a domain error.
    """
    services: GeographyApiServices | None = getattr(
        request.app.state, "container", None
    )
    if services is None:
        raise RuntimeError(MISSING_CONTAINER_MESSAGE)
    return services


def require_valid_credentials(request: Request) -> None:
    """Refuse a request whose bearer token was presented but rejected.

    Args:
        request: The current request; the principal resolution middleware has
            already checked its credentials.

    Raises:
        YakhnamaError: The stored ``AuthenticationError`` (401) or
            ``IdentityProviderUnavailableError`` (503).
    """
    error = get_principal_resolution(request.scope).error
    if error is not None:
        raise error


Services = Annotated[GeographyApiServices, Depends(get_geography_services)]
