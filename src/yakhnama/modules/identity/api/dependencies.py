"""FastAPI dependencies of the identity HTTP API: services and the acting ``Actor``.

The composition root stores the ``Container`` on ``app.state.container``. This
package reads it through the ``IdentityApiServices`` protocol instead of importing
``yakhnama.platform.container`` (or ``platform.auth.dependencies``, which imports
it): the container imports every module's infrastructure, so the import would break
the layer contract, even indirectly.

The principal comes from the outcome ``PrincipalResolutionMiddleware`` stored for
the request, so a token is validated once per request:

- ``optional_actor`` (public routes): ``Actor.anonymous()`` without credentials,
  otherwise the actor of the mirrored user. It declares no security scheme, so the
  route stays anonymous in OpenAPI.
- ``current_actor`` (protected routes): requires a principal and declares the
  ``bearerAuth`` scheme in OpenAPI.

Both answer 401 when a token was presented but rejected and 503 when the identity
provider's keys cannot be fetched. Realm role names from the token are mapped to
``Role`` values; names that are not Yakhnama roles (Keycloak's
``offline_access``, ``default-roles-*`` and the like) are ignored.

Patterns: Dependency Injection.
"""

from collections.abc import Iterable
from typing import Annotated, Final, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from yakhnama.modules.identity.application.commands import EnsureUserFromPrincipal
from yakhnama.modules.identity.application.handlers import (
    EnsureUserFromPrincipalHandler,
)
from yakhnama.modules.identity.application.ports import (
    IdentityQueryService,
    IdentityUnitOfWorkFactory,
)
from yakhnama.modules.identity.public import Actor, ExternalIdentity, Role
from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import get_principal_resolution
from yakhnama.platform.auth.tokens import REJECTION_MESSAGE
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import AuthenticationError
from yakhnama.shared_kernel.ids import IdGenerator

# The scheme name ``platform.auth.dependencies`` documents; kept equal so OpenAPI
# lists one ``bearerAuth`` scheme.
BEARER_SCHEME_NAME: Final = "bearerAuth"
MISSING_CONTAINER_MESSAGE: Final = (
    "app.state.container is missing; build the app with create_app"
)
_ROLES_BY_NAME: Final = {role.value: role for role in Role}

# Only documents the scheme in OpenAPI; the header was already parsed and checked
# by the principal resolution middleware.
_bearer_scheme = HTTPBearer(
    scheme_name=BEARER_SCHEME_NAME,
    bearerFormat="JWT",
    description="An OpenID Connect access token from the Yakhnama identity provider.",
    auto_error=False,
)


class IdentityApiServices(Protocol):
    """What the identity router reads from the composition root.

    Implements: Dependency Injection (provider port bound in the composition root).
    """

    @property
    def identity_uow_factory(self) -> IdentityUnitOfWorkFactory:
        """Return the identity unit-of-work factory."""
        ...

    @property
    def identity_query_service(self) -> IdentityQueryService:
        """Return the identity query service."""
        ...

    @property
    def clock(self) -> Clock:
        """Return the application clock."""
        ...

    @property
    def id_generator(self) -> IdGenerator:
        """Return the application id generator."""
        ...


def get_identity_services(request: Request) -> IdentityApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The container, seen through ``IdentityApiServices``.

    Raises:
        RuntimeError: If the app was not built by ``create_app``; a wiring bug, so a
            server error rather than a domain error.
    """
    services: IdentityApiServices | None = getattr(request.app.state, "container", None)
    if services is None:
        raise RuntimeError(MISSING_CONTAINER_MESSAGE)
    return services


Services = Annotated[IdentityApiServices, Depends(get_identity_services)]


def map_realm_roles(names: Iterable[str]) -> frozenset[Role]:
    """Map identity-provider role names to Yakhnama roles.

    Args:
        names: Realm role names from the verified token.

    Returns:
        The roles whose value equals a name exactly; other names are ignored.
    """
    return frozenset(_ROLES_BY_NAME[name] for name in names if name in _ROLES_BY_NAME)


def resolved_principal(request: Request) -> Principal | None:
    """Return the request's principal as the resolution middleware left it.

    Args:
        request: The current request.

    Returns:
        The principal, or ``None`` for an anonymous request.

    Raises:
        YakhnamaError: The stored ``AuthenticationError`` (401) or
            ``IdentityProviderUnavailableError`` (503) when the credentials were
            rejected or could not be checked.
    """
    resolution = get_principal_resolution(request.scope)
    if resolution.error is not None:
        raise resolution.error
    return resolution.principal


async def actor_for(principal: Principal, services: IdentityApiServices) -> Actor:
    """Ensure the principal's user mirror exists and return its actor.

    The token's display name is not passed on: a new user starts without one and
    sets it through ``PATCH /me``.

    Args:
        principal: The verified caller.
        services: Supplies the identity unit of work, clock and ids.

    Returns:
        The actor with the user's roles and memberships.

    Raises:
        AccountSuspendedError: If the user is suspended (403).
    """
    handler = EnsureUserFromPrincipalHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )
    return await handler(
        EnsureUserFromPrincipal(
            identity=ExternalIdentity(
                issuer=principal.issuer, subject=principal.subject
            ),
            realm_roles=map_realm_roles(principal.realm_roles),
        )
    )


async def optional_actor(request: Request, services: Services) -> Actor:
    """Return the caller's actor, or the anonymous actor without credentials.

    Args:
        request: The current request.
        services: Identity services bound by the composition root.

    Returns:
        The actor.

    Raises:
        YakhnamaError: 401 or 503 for rejected or uncheckable credentials, 403
            for a suspended user.
    """
    principal = resolved_principal(request)
    if principal is None:
        return Actor.anonymous()
    return await actor_for(principal, services)


async def current_actor(
    request: Request,
    services: Services,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
    ],
) -> Actor:
    """Return the authenticated caller's actor, requiring a valid bearer token.

    Args:
        request: The current request.
        services: Identity services bound by the composition root.
        credentials: Unused; present so OpenAPI documents the bearer scheme.

    Returns:
        The actor of the mirrored user.

    Raises:
        AuthenticationError: If the request is anonymous or its token rejected.
        YakhnamaError: 503 when the keys cannot be fetched, 403 for a suspended
            user.
    """
    del credentials
    principal = resolved_principal(request)
    if principal is None:
        raise AuthenticationError(REJECTION_MESSAGE)
    return await actor_for(principal, services)


OptionalActor = Annotated[Actor, Depends(optional_actor)]
CurrentActor = Annotated[Actor, Depends(current_actor)]
