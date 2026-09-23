"""Resolve the bearer token of a request to a principal, once per request.

The rate limiter and the idempotency middleware key their state on the principal, and
routes need it through ``current_principal``; validating the token once and keeping
the outcome in ``scope["state"]`` serves all three and keeps them consistent.

A request without an ``Authorization`` header is anonymous. A request with one that
is not a single, valid ``Bearer`` token is *not* anonymous: it carries the error, and
every route, public or not, answers it with 401, so a client never mistakes a
rejected token for a successful anonymous call.

Patterns: DTO (``PrincipalResolution``), Decorator (``PrincipalResolutionMiddleware``).
"""

from typing import Final

from pydantic import BaseModel, ConfigDict
from starlette.types import ASGIApp, Receive, Scope, Send

from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.tokens import REJECTION_MESSAGE, TokenValidator
from yakhnama.platform.request_state import PRINCIPAL_RESOLUTION_STATE_KEY, state_of
from yakhnama.shared_kernel.errors import AuthenticationError, YakhnamaError

AUTHORIZATION_HEADER: Final = b"authorization"
BEARER_SCHEME: Final = "bearer"


class PrincipalResolution(BaseModel):
    """The outcome of checking one request's credentials.

    Implements: DTO.

    Attributes:
        principal: The authenticated principal; ``None`` when anonymous or rejected.
        error: Why the credentials were rejected (``AuthenticationError``) or could
            not be checked (``IdentityProviderUnavailableError``); ``None`` otherwise.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    principal: Principal | None = None
    error: YakhnamaError | None = None

    @property
    def is_anonymous(self) -> bool:
        """Whether the request carried no credentials at all."""
        return self.principal is None and self.error is None


ANONYMOUS: Final = PrincipalResolution()


def bearer_token_from_scope(scope: Scope) -> str | None:
    """Return the bearer token of a request, or ``None`` if it has no credentials.

    Args:
        scope: An HTTP ASGI scope.

    Returns:
        The token text, or ``None`` when there is no ``Authorization`` header.

    Raises:
        AuthenticationError: If there are several ``Authorization`` headers, the
            scheme is not ``Bearer`` or the token is empty.
    """
    # ASGI headers are (bytes, bytes) pairs; the scope itself is untyped.
    values: list[bytes] = [
        value
        for name, value in scope.get("headers", ())
        if name == AUTHORIZATION_HEADER
    ]
    if not values:
        return None
    if len(values) > 1:
        raise AuthenticationError(REJECTION_MESSAGE)
    scheme, _, raw_token = values[0].decode("latin-1").partition(" ")
    token = raw_token.strip()
    if scheme.lower() != BEARER_SCHEME or not token:
        raise AuthenticationError(REJECTION_MESSAGE)
    return token


async def resolve_principal(
    scope: Scope, validator: TokenValidator | None
) -> PrincipalResolution:
    """Check the request's credentials, reusing an earlier outcome for the request.

    Args:
        scope: An HTTP ASGI scope; the outcome is stored in its state.
        validator: The token validator, or ``None`` when OIDC is not configured, in
            which case every presented token is rejected.

    Returns:
        The outcome; never raises for bad credentials, the error is inside it.
    """
    state = state_of(scope)
    cached = state.get(PRINCIPAL_RESOLUTION_STATE_KEY)
    if isinstance(cached, PrincipalResolution):
        return cached
    resolution = await _resolve(scope, validator)
    state[PRINCIPAL_RESOLUTION_STATE_KEY] = resolution
    return resolution


async def _resolve(
    scope: Scope, validator: TokenValidator | None
) -> PrincipalResolution:
    try:
        token = bearer_token_from_scope(scope)
        if token is None:
            return ANONYMOUS
        if validator is None:
            raise AuthenticationError(REJECTION_MESSAGE)
        return PrincipalResolution(principal=await validator.validate(token))
    except YakhnamaError as error:
        return PrincipalResolution(error=error)


def get_principal_resolution(scope: Scope) -> PrincipalResolution:
    """Return the outcome stored by ``resolve_principal``, or anonymous if none.

    Args:
        scope: An HTTP ASGI scope.

    Returns:
        The stored outcome; ``ANONYMOUS`` when the credentials were not checked.
    """
    cached = state_of(scope).get(PRINCIPAL_RESOLUTION_STATE_KEY)
    return cached if isinstance(cached, PrincipalResolution) else ANONYMOUS


class PrincipalResolutionMiddleware:
    """ASGI middleware that checks every HTTP request's bearer token up front.

    It never rejects a request itself: the outcome goes into the scope state and the
    route (through ``current_principal`` or ``optional_principal``) decides.

    Implements: Decorator (ASGI middleware around the application).
    """

    def __init__(self, app: ASGIApp, *, validator: TokenValidator | None) -> None:
        """Wrap ``app``.

        Args:
            app: The inner ASGI application.
            validator: The token validator, or ``None`` when OIDC is off.
        """
        self._app = app
        self._validator = validator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Resolve the principal of an HTTP request, then call the inner app.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] == "http":
            await resolve_principal(scope, self._validator)
        await self._app(scope, receive, send)
