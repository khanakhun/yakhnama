"""Unit tests for ``Principal``, principal resolution and the auth dependencies."""

import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

from tests.fakes.auth import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    FakeJwksClient,
    access_token_claims,
    issue_token,
    session_key_pair,
)
from tests.fakes.clock import FrozenClock
from tests.unit.platform.asgi import RecordingApp, client_for
from yakhnama.main import create_app
from yakhnama.platform.auth.dependencies import CurrentPrincipal, OptionalPrincipal
from yakhnama.platform.auth.errors import IdentityProviderUnavailableError
from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import (
    ANONYMOUS,
    PrincipalResolution,
    PrincipalResolutionMiddleware,
    bearer_token_from_scope,
    get_principal_resolution,
    resolve_principal,
)
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.container import build_container
from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.errors import AuthenticationError

EXPIRES_AT = datetime(2026, 9, 23, 12, 5, tzinfo=UTC)


class UnavailableJwksClient:
    """``JwksClient`` whose provider is down.

    Implements: Fake (of ``JwksClient``).
    """

    async def get_signing_key(self, key_id: str) -> None:
        """Fail as an unreachable provider does."""
        del key_id
        message = "down"
        raise IdentityProviderUnavailableError(message)


def _validator(clock: FrozenClock) -> TokenValidator:
    return TokenValidator(
        jwks_client=FakeJwksClient([session_key_pair()]),
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        algorithms=["RS256"],
        leeway_seconds=0,
        clock=clock,
    )


def _scope(*headers: tuple[bytes, bytes]) -> dict[str, object]:
    return {"type": "http", "headers": list(headers)}


def _principal(subject: str = "user-1", issuer: str = TEST_ISSUER) -> Principal:
    return Principal(subject=subject, issuer=issuer, expires_at=EXPIRES_AT)


def test_principal_scope_key_is_stable_opaque_hex() -> None:
    key = _principal().scope_key()

    assert key == _principal().scope_key()
    assert len(key) == 64
    assert "user-1" not in key


def test_principal_scope_key_differs_per_issuer_and_subject() -> None:
    keys = {
        _principal().scope_key(),
        _principal(subject="user-2").scope_key(),
        _principal(issuer="https://other.test").scope_key(),
    }

    assert len(keys) == 3


def test_principal_naive_expiry_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Principal(
            subject="s",
            issuer=TEST_ISSUER,
            expires_at=datetime(2026, 1, 1),  # noqa: DTZ001  # reason: the naive value under test
        )


def test_principal_resolution_anonymous_flag() -> None:
    anonymous = ANONYMOUS.is_anonymous
    rejected = PrincipalResolution(error=AuthenticationError("x")).is_anonymous
    authenticated = PrincipalResolution(principal=_principal()).is_anonymous

    assert (anonymous, rejected, authenticated) == (True, False, False)


def test_bearer_token_from_scope_without_header_returns_none() -> None:
    token = bearer_token_from_scope(_scope())

    assert token is None


@pytest.mark.parametrize("scheme", [b"Bearer", b"bearer", b"BEARER"])
def test_bearer_token_from_scope_any_scheme_case_returns_token(scheme: bytes) -> None:
    credential = bearer_token_from_scope(_scope((b"authorization", scheme + b"  abc ")))

    assert credential == "abc"


@pytest.mark.parametrize(
    "value", [b"Basic dXNlcjpwYXNz", b"Bearer", b"Bearer   ", b"abc"]
)
def test_bearer_token_from_scope_other_scheme_or_empty_raises(value: bytes) -> None:
    with pytest.raises(AuthenticationError):
        bearer_token_from_scope(_scope((b"authorization", value)))


def test_bearer_token_from_scope_two_headers_raises() -> None:
    scope = _scope((b"authorization", b"Bearer a"), (b"authorization", b"Bearer b"))

    with pytest.raises(AuthenticationError):
        bearer_token_from_scope(scope)


async def test_resolve_principal_valid_token_returns_principal_and_caches(
    clock: FrozenClock,
) -> None:
    token = issue_token(access_token_claims(), session_key_pair())
    scope = _scope((b"authorization", f"Bearer {token}".encode()))

    first = await resolve_principal(scope, _validator(clock))
    second = await resolve_principal(scope, None)

    assert first.principal is not None
    assert second is first
    assert get_principal_resolution(scope) is first


async def test_resolve_principal_without_header_is_anonymous(
    clock: FrozenClock,
) -> None:
    resolution = await resolve_principal(_scope(), _validator(clock))

    assert resolution is ANONYMOUS


async def test_resolve_principal_token_without_validator_is_rejected() -> None:
    resolution = await resolve_principal(
        _scope((b"authorization", b"Bearer abc")), None
    )

    assert isinstance(resolution.error, AuthenticationError)


async def test_resolve_principal_invalid_token_carries_error(
    clock: FrozenClock,
) -> None:
    resolution = await resolve_principal(
        _scope((b"authorization", b"Bearer not-a-jwt")), _validator(clock)
    )

    assert resolution.principal is None
    assert isinstance(resolution.error, AuthenticationError)


async def test_resolve_principal_provider_down_carries_unavailable_error(
    clock: FrozenClock,
) -> None:
    validator = TokenValidator(
        jwks_client=UnavailableJwksClient(),
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        algorithms=["RS256"],
        leeway_seconds=0,
        clock=clock,
    )
    token = issue_token(access_token_claims(), session_key_pair())

    resolution = await resolve_principal(
        _scope((b"authorization", f"Bearer {token}".encode())), validator
    )

    assert isinstance(resolution.error, IdentityProviderUnavailableError)


def test_get_principal_resolution_unresolved_scope_is_anonymous() -> None:
    resolution = get_principal_resolution(_scope())

    assert resolution is ANONYMOUS


async def test_principal_resolution_middleware_stores_outcome(
    clock: FrozenClock,
) -> None:
    inner = RecordingApp()
    app = PrincipalResolutionMiddleware(inner, validator=_validator(clock))
    token = issue_token(access_token_claims(subject="user-9"), session_key_pair())

    async with client_for(app) as client:
        await client.get("/", headers={"Authorization": f"Bearer {token}"})

    principal = get_principal_resolution(inner.scopes[0]).principal
    assert principal is not None
    assert principal.subject == "user-9"


async def test_principal_resolution_middleware_ignores_lifespan_scope() -> None:
    inner = RecordingApp()
    app = PrincipalResolutionMiddleware(inner, validator=None)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        del message

    await app({"type": "lifespan"}, receive, send)

    assert inner.calls == 0


@pytest.fixture
def auth_app(settings: Settings, clock: FrozenClock) -> FastAPI:
    container = dataclasses.replace(
        build_container(settings), token_validator=_validator(clock)
    )
    app = create_app(settings, container)

    async def read_me(principal: CurrentPrincipal) -> dict[str, str]:
        return {"subject": principal.subject}

    async def read_public(principal: OptionalPrincipal) -> dict[str, str | None]:
        return {"subject": principal.subject if principal is not None else None}

    app.add_api_route("/me", read_me)
    app.add_api_route("/public", read_public)
    return app


@pytest.fixture
async def auth_client(auth_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(auth_app) as client:
        yield client


async def test_current_principal_valid_token_returns_subject(
    auth_client: httpx.AsyncClient,
) -> None:
    token = issue_token(access_token_claims(subject="user-7"), session_key_pair())

    response = await auth_client.get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.json() == {"subject": "user-7"}


async def test_current_principal_anonymous_returns_401_with_challenge(
    auth_client: httpx.AsyncClient,
) -> None:
    response = await auth_client.get("/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="yakhnama"'
    assert (
        response.json()["type"] == "https://yakhnama.org/problems/authentication-failed"
    )


async def test_current_principal_invalid_token_returns_401_without_token_text(
    auth_client: httpx.AsyncClient,
) -> None:
    response = await auth_client.get(
        "/me", headers={"Authorization": "Bearer secret-looking-token"}
    )

    assert response.status_code == 401
    assert "secret-looking-token" not in response.text


async def test_optional_principal_anonymous_returns_none(
    auth_client: httpx.AsyncClient,
) -> None:
    response = await auth_client.get("/public")

    assert response.json() == {"subject": None}


async def test_optional_principal_rejected_token_returns_401(
    auth_client: httpx.AsyncClient,
) -> None:
    response = await auth_client.get(
        "/public", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )

    assert response.status_code == 401


async def test_optional_principal_valid_token_returns_subject(
    auth_client: httpx.AsyncClient,
) -> None:
    token = issue_token(access_token_claims(subject="user-8"), session_key_pair())

    response = await auth_client.get(
        "/public", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.json() == {"subject": "user-8"}


def test_auth_dependencies_declare_bearer_scheme_in_openapi(auth_app: FastAPI) -> None:
    schemes = auth_app.openapi()["components"]["securitySchemes"]

    assert schemes == {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "An OpenID Connect access token from the Yakhnama identity provider."
            ),
        }
    }


async def test_current_principal_without_middleware_resolves_itself(
    settings: Settings, clock: FrozenClock
) -> None:
    container = dataclasses.replace(
        build_container(settings), token_validator=_validator(clock)
    )
    app = create_app(settings, container)
    app.user_middleware.clear()

    async def read_me(principal: CurrentPrincipal) -> dict[str, str]:
        return {"subject": principal.subject}

    app.add_api_route("/me", read_me)
    token = issue_token(access_token_claims(subject="user-3"), session_key_pair())

    async with client_for(app) as client:
        response = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.json() == {"subject": "user-3"}
