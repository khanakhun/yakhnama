"""FastAPI dependencies of the reports HTTP API: services and the acting ``Actor``.

The composition root stores the ``Container`` on ``app.state.container``. This
package reads it through ``ReportsApiServices`` instead of importing
``yakhnama.platform.container``, which imports every module's infrastructure and
would break the layer contract even indirectly.

The actor is resolved exactly as ``modules/identity/api/dependencies.py`` does it,
from the principal ``PrincipalResolutionMiddleware`` stored for the request and
the identity facade's ``EnsureUserFromPrincipalHandler``. The module independence
contract forbids importing the identity API package, so the few lines are repeated
here; moving them to ``yakhnama.platform.auth`` is an open question for the
architect.

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
)
from yakhnama.modules.reports.public import (
    AuthorisedReportQueryService,
    ReviseReportHandler,
    SubmitReportHandler,
    WithdrawReportHandler,
)
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

# Only documents the scheme in OpenAPI; the header was already parsed and checked
# by the principal resolution middleware.
_bearer_scheme = HTTPBearer(
    scheme_name=BEARER_SCHEME_NAME,
    bearerFormat="JWT",
    description="An OpenID Connect access token from the Yakhnama identity provider.",
    auto_error=False,
)


class ReportsApiServices(Protocol):
    """What the reports router reads from the composition root.

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
    def submit_report_handler(self) -> SubmitReportHandler:
        """Return the submit-report use case."""
        ...

    @property
    def revise_report_handler(self) -> ReviseReportHandler:
        """Return the revise-report use case."""
        ...

    @property
    def withdraw_report_handler(self) -> WithdrawReportHandler:
        """Return the withdraw-report use case."""
        ...

    @property
    def report_queries(self) -> AuthorisedReportQueryService:
        """Return the authorised report query service."""
        ...


def get_reports_services(request: Request) -> ReportsApiServices:
    """Return the services the composition root bound for this app.

    Args:
        request: The current request.

    Returns:
        The container, seen through ``ReportsApiServices``.

    Raises:
        RuntimeError: If the app was not built by ``create_app``; a wiring bug, so a
            server error rather than a domain error.
    """
    services: ReportsApiServices | None = getattr(request.app.state, "container", None)
    if services is None:
        raise RuntimeError(MISSING_CONTAINER_MESSAGE)
    return services


Services = Annotated[ReportsApiServices, Depends(get_reports_services)]


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
        The actor of the mirrored user, with the roles from the token.

    Raises:
        AuthenticationError: If the request is anonymous or its token rejected.
        YakhnamaError: 503 when the keys cannot be fetched, 403 for a suspended
            user.
    """
    del credentials
    resolution = get_principal_resolution(request.scope)
    if resolution.error is not None:
        raise resolution.error
    principal = resolution.principal
    if principal is None:
        raise AuthenticationError(REJECTION_MESSAGE)
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


CurrentActor = Annotated[Actor, Depends(current_actor)]
