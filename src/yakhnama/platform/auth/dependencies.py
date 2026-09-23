"""FastAPI dependencies that hand the authenticated principal to routes.

Use ``CurrentPrincipal`` on routes that require a login and ``OptionalPrincipal`` on
public routes that behave differently for a logged-in caller::

    @router.get("/me")
    async def read_me(principal: CurrentPrincipal) -> MeResponse: ...

Both declare the ``bearerAuth`` HTTP bearer security scheme in the OpenAPI document,
so the API reference offers a token field. Both answer 401 (with
``WWW-Authenticate: Bearer``) when a token is presented but rejected, and 503 when
the identity provider's keys cannot be fetched.

Patterns: Dependency Injection.
"""

from typing import Annotated, Final

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import resolve_principal
from yakhnama.platform.auth.tokens import REJECTION_MESSAGE
from yakhnama.platform.container import get_container
from yakhnama.shared_kernel.errors import AuthenticationError

BEARER_SCHEME_NAME: Final = "bearerAuth"

# Only documents the scheme in OpenAPI; the header is parsed by resolve_principal,
# which also rejects the non-Bearer schemes this helper would silently ignore.
_bearer_scheme = HTTPBearer(
    scheme_name=BEARER_SCHEME_NAME,
    bearerFormat="JWT",
    description="An OpenID Connect access token from the Yakhnama identity provider.",
    auto_error=False,
)


async def optional_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_bearer_scheme)
    ],
) -> Principal | None:
    """Return the caller's principal, or ``None`` for an anonymous request.

    Args:
        request: The current request.
        credentials: Unused; present so OpenAPI documents the bearer scheme.

    Returns:
        The principal, or ``None`` when no ``Authorization`` header was sent.

    Raises:
        AuthenticationError: If credentials were sent but rejected.
        IdentityProviderUnavailableError: If the signing keys cannot be fetched.
    """
    del credentials
    resolution = await resolve_principal(
        request.scope, get_container(request).token_validator
    )
    if resolution.error is not None:
        raise resolution.error
    return resolution.principal


async def current_principal(
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> Principal:
    """Return the caller's principal, requiring one.

    Args:
        principal: The optional principal of the request.

    Returns:
        The principal.

    Raises:
        AuthenticationError: If the request is anonymous or its token is rejected.
        IdentityProviderUnavailableError: If the signing keys cannot be fetched.
    """
    if principal is None:
        raise AuthenticationError(REJECTION_MESSAGE)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
