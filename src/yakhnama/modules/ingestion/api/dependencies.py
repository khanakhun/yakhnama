"""FastAPI dependencies of the ingestion HTTP API: services and the acting ``Actor``.

The composition root stores the ``Container`` on ``app.state.container``; this
package reads it through ``IngestionApiServices`` and never imports
``yakhnama.platform.container`` (the layer contract forbids it, even indirectly).
The property names of ``IngestionApiServices`` are the ``Container`` fields the
composition root binds for this module.

The catalog, runs, observations and rasters are open data, read without a token;
``require_valid_credentials`` still refuses a token that was sent and rejected,
so a client never mistakes a rejection for an anonymous success. The
``/admin`` routes require ``catalog_policy`` (``IsAdmin``) before anything is
read. The actor is resolved as in ``modules/identity/api/dependencies.py``; the
module independence contract forbids importing that package, so the lines are
repeated here (open question: move them to ``yakhnama.platform.auth``).

Patterns: Dependency Injection.
"""

from typing import Annotated, Final, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from yakhnama.modules.identity.public import (
    Actor,
    EnsureUserFromPrincipal,
    EnsureUserFromPrincipalHandler,
    ExternalIdentity,
    IdentityUnitOfWorkFactory,
    Role,
    require_allowed,
)
from yakhnama.modules.ingestion.public import (
    CatalogueRasterAssetHandler,
    DeprecateDatasetHandler,
    IngestionQueryService,
    RecordDatasetVersionHandler,
    RegisterDatasetHandler,
    RetireDatasetHandler,
    RunIngestionHandler,
    catalog_policy,
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


class IngestionApiServices(Protocol):
    """What the ingestion router reads from the composition root.

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
    def register_dataset_handler(self) -> RegisterDatasetHandler:
        """Return the register-dataset use case."""
        ...

    @property
    def record_dataset_version_handler(self) -> RecordDatasetVersionHandler:
        """Return the record-dataset-version use case."""
        ...

    @property
    def deprecate_dataset_handler(self) -> DeprecateDatasetHandler:
        """Return the deprecate-dataset use case."""
        ...

    @property
    def retire_dataset_handler(self) -> RetireDatasetHandler:
        """Return the retire-dataset use case."""
        ...

    @property
    def run_ingestion_handler(self) -> RunIngestionHandler:
        """Return the request-run use case (enqueues ``ingestion.run``)."""
        ...

    @property
    def catalogue_raster_asset_handler(self) -> CatalogueRasterAssetHandler:
        """Return the catalogue-raster use case."""
        ...

    @property
    def ingestion_queries(self) -> IngestionQueryService:
        """Return the ingestion read port (open data, no policy)."""
        ...


def get_ingestion_services(request: Request) -> IngestionApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The container, seen through ``IngestionApiServices``.

    Raises:
        RuntimeError: If the app was not built by ``create_app``; a wiring bug.
    """
    services: IngestionApiServices | None = getattr(
        request.app.state, "container", None
    )
    if services is None:
        raise RuntimeError(MISSING_CONTAINER_MESSAGE)
    return services


Services = Annotated[IngestionApiServices, Depends(get_ingestion_services)]


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


def _resolved_principal(request: Request) -> Principal | None:
    resolution = get_principal_resolution(request.scope)
    if resolution.error is not None:
        raise resolution.error
    return resolution.principal


async def _actor_for(principal: Principal, services: IngestionApiServices) -> Actor:
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


def admin_actor(actor: CurrentActor) -> Actor:
    """Return the caller's actor if they may administer the ingestion catalog.

    Every ``/admin`` route depends on this before anything is read, so a
    non-administrator learns nothing (not even whether a dataset code exists).

    Args:
        actor: The authenticated caller.

    Returns:
        The same actor.

    Raises:
        PermissionDeniedError: If ``catalog_policy`` refuses the caller.
    """
    require_allowed(catalog_policy(), actor, action="administer the dataset catalog")
    return actor


AdminActor = Annotated[Actor, Depends(admin_actor)]
