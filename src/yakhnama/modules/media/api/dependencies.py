"""FastAPI dependencies of the media HTTP API: services and the acting ``Actor``.

The composition root stores the ``Container`` on ``app.state.container``; this
package reads it through ``MediaApiServices`` and never imports
``yakhnama.platform.container`` (the layer contract forbids it, even indirectly).

The actor is resolved as in ``modules/identity/api/dependencies.py``; the module
independence contract forbids importing that package, so the lines are repeated
here (open question: move them to ``yakhnama.platform.auth``).

Patterns: Dependency Injection.
"""

from typing import Annotated, Final, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from yakhnama.modules.identity.public import (
    Actor,
    CanModerate,
    EnsureUserFromPrincipal,
    EnsureUserFromPrincipalHandler,
    ExternalIdentity,
    IdentityUnitOfWorkFactory,
    Role,
    require_allowed,
)
from yakhnama.modules.media.public import (
    AuthorisedMediaQueryService,
    CompleteUploadHandler,
    ModerateMediaHandler,
    RequestUploadHandler,
)
from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import get_principal_resolution
from yakhnama.platform.auth.tokens import REJECTION_MESSAGE
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import AuthenticationError
from yakhnama.shared_kernel.ids import IdGenerator

# Kept equal to the identity API's scheme so OpenAPI lists one ``bearerAuth``.
BEARER_SCHEME_NAME: Final = "bearerAuth"
MISSING_CONTAINER_MESSAGE: Final = (
    "app.state.container is missing; build the app with create_app"
)
_ROLES_BY_NAME: Final = {role.value: role for role in Role}

_bearer_scheme = HTTPBearer(
    scheme_name=BEARER_SCHEME_NAME,
    bearerFormat="JWT",
    description="An OpenID Connect access token from the Yakhnama identity provider.",
    auto_error=False,
)


class MediaApiServices(Protocol):
    """What the media router reads from the composition root.

    Implements: Dependency Injection (provider port bound in the composition root).
    """

    @property
    def identity_uow_factory(self) -> IdentityUnitOfWorkFactory:
        """Return the identity unit-of-work factory, to mirror the caller."""
        ...

    @property
    def clock(self) -> Clock:
        """Return the application clock."""
        ...

    @property
    def id_generator(self) -> IdGenerator:
        """Return the application id generator."""
        ...

    @property
    def request_upload_handler(self) -> RequestUploadHandler:
        """Return the request-upload use case."""
        ...

    @property
    def complete_upload_handler(self) -> CompleteUploadHandler:
        """Return the complete-upload use case."""
        ...

    @property
    def moderate_media_handler(self) -> ModerateMediaHandler:
        """Return the moderate-media use case."""
        ...

    @property
    def media_queries(self) -> AuthorisedMediaQueryService:
        """Return the authorised media query service."""
        ...


def get_media_services(request: Request) -> MediaApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The container, seen through ``MediaApiServices``.

    Raises:
        RuntimeError: If the app was not built by ``create_app``; a wiring bug.
    """
    services: MediaApiServices | None = getattr(request.app.state, "container", None)
    if services is None:
        raise RuntimeError(MISSING_CONTAINER_MESSAGE)
    return services


Services = Annotated[MediaApiServices, Depends(get_media_services)]


def _resolved_principal(request: Request) -> Principal | None:
    resolution = get_principal_resolution(request.scope)
    if resolution.error is not None:
        raise resolution.error
    return resolution.principal


async def _actor_for(principal: Principal, services: MediaApiServices) -> Actor:
    handler = EnsureUserFromPrincipalHandler(
        services.identity_uow_factory, services.clock, services.id_generator
    )
    return await handler(
        EnsureUserFromPrincipal(
            identity=ExternalIdentity(
                issuer=principal.issuer, subject=principal.subject
            ),
            realm_roles=frozenset(
                _ROLES_BY_NAME[name]
                for name in principal.realm_roles
                if name in _ROLES_BY_NAME
            ),
        )
    )


async def optional_actor(request: Request, services: Services) -> Actor:
    """Return the caller's actor, or the anonymous actor without credentials.

    Args:
        request: The current request.
        services: Supplies the identity unit of work, clock and ids.

    Returns:
        The actor.

    Raises:
        YakhnamaError: 401 or 503 for rejected or uncheckable credentials, 403
            for a suspended user.
    """
    principal = _resolved_principal(request)
    if principal is None:
        return Actor.anonymous()
    return await _actor_for(principal, services)


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
        services: Supplies the identity unit of work, clock and ids.
        credentials: Unused; present so OpenAPI documents the bearer scheme.

    Returns:
        The actor of the mirrored user.

    Raises:
        AuthenticationError: If the request is anonymous or its token rejected.
    """
    del credentials
    principal = _resolved_principal(request)
    if principal is None:
        raise AuthenticationError(REJECTION_MESSAGE)
    return await _actor_for(principal, services)


CurrentActor = Annotated[Actor, Depends(current_actor)]
OptionalActor = Annotated[Actor, Depends(optional_actor)]


def moderator_actor(actor: CurrentActor) -> Actor:
    """Return the caller's actor if they may moderate.

    Every ``/moderation`` route depends on this before anything is read, so a
    non-moderator learns nothing about the resource.

    Args:
        actor: The authenticated caller.

    Returns:
        The same actor.

    Raises:
        PermissionDeniedError: If ``CanModerate`` refuses the caller.
    """
    require_allowed(CanModerate(), actor, action="use the moderation API")
    return actor


ModeratorActor = Annotated[Actor, Depends(moderator_actor)]
