"""FastAPI dependencies of the exchange HTTP API: services and the acting ``Actor``.

The composition root stores the ``Container`` on ``app.state.container``; this
package reads it through ``ExchangeApiServices`` and never imports
``yakhnama.platform.container`` (the layer contract forbids it, even indirectly).
The property names of ``ExchangeApiServices`` are the ``Container`` fields the
composition root binds for this module.

The actor is resolved as in ``modules/identity/api/dependencies.py``; the module
independence contract forbids importing that package, so the lines are repeated
here (open question: move them to ``yakhnama.platform.auth``).

Patterns: Dependency Injection.
"""

from typing import Annotated, Final, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from yakhnama.modules.exchange.public import (
    ArtifactStore,
    CancelExportHandler,
    ExchangeJobQueryService,
    RequestExportHandler,
    RequestImportHandler,
    import_policy,
)
from yakhnama.modules.identity.public import (
    Actor,
    EnsureUserFromPrincipal,
    EnsureUserFromPrincipalHandler,
    ExternalIdentity,
    IdentityUnitOfWorkFactory,
    Role,
    require_allowed,
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


class ExchangeApiServices(Protocol):
    """What the exchange router reads from the composition root.

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
        """Return the application id generator; it names import uploads."""
        ...

    @property
    def request_export_handler(self) -> RequestExportHandler:
        """Return the request-export use case."""
        ...

    @property
    def cancel_export_handler(self) -> CancelExportHandler:
        """Return the cancel-export use case."""
        ...

    @property
    def request_import_handler(self) -> RequestImportHandler:
        """Return the request-import use case."""
        ...

    @property
    def exchange_queries(self) -> ExchangeJobQueryService:
        """Return the authorised export and import job reads."""
        ...

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return the exchange object storage, for presigned import uploads."""
        ...


def get_exchange_services(request: Request) -> ExchangeApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The container, seen through ``ExchangeApiServices``.

    Raises:
        RuntimeError: If the app was not built by ``create_app``; a wiring bug.
    """
    services: ExchangeApiServices | None = getattr(request.app.state, "container", None)
    if services is None:
        raise RuntimeError(MISSING_CONTAINER_MESSAGE)
    return services


Services = Annotated[ExchangeApiServices, Depends(get_exchange_services)]


def _resolved_principal(request: Request) -> Principal | None:
    resolution = get_principal_resolution(request.scope)
    if resolution.error is not None:
        raise resolution.error
    return resolution.principal


async def _actor_for(principal: Principal, services: ExchangeApiServices) -> Actor:
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


async def current_actor(
    request: Request,
    services: Services,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
    ],
) -> Actor:
    """Return the authenticated caller's actor, requiring a valid bearer token.

    Every exchange route needs one: an export is a stored job with an owner, and
    imports are moderation work.

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


def import_moderator_actor(actor: CurrentActor) -> Actor:
    """Return the caller's actor if they may run imports.

    Every ``/moderation/imports`` route depends on this before anything is read,
    so a non-moderator learns nothing about import jobs or uploads.

    Args:
        actor: The authenticated caller.

    Returns:
        The same actor.

    Raises:
        PermissionDeniedError: If ``import_policy`` refuses the caller.
    """
    require_allowed(import_policy(), actor, action="use the import API")
    return actor


ImportModeratorActor = Annotated[Actor, Depends(import_moderator_actor)]
