"""Edge cases of the api dependencies that HTTP requests alone cannot reach."""

from collections.abc import Callable

import pytest
from fastapi import FastAPI, Response
from starlette.requests import Request

from tests.factories.identity import ActorTestFactory
from tests.fakes.api import build_test_app
from yakhnama.modules.geography.api.dependencies import get_geography_services
from yakhnama.modules.hazards.api.dependencies import get_hazards_services
from yakhnama.modules.identity.api.dependencies import get_identity_services
from yakhnama.modules.identity.api.router import authenticated_user_id, read_me
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.api.dependencies import get_impacts_services
from yakhnama.shared_kernel.errors import AuthenticationError, NotFoundError


@pytest.mark.parametrize(
    "provider",
    [
        get_geography_services,
        get_hazards_services,
        get_identity_services,
        get_impacts_services,
    ],
)
def test_services_provider_without_container_raises_runtime_error(
    provider: Callable[[Request], object],
) -> None:
    request = Request({"type": "http", "app": FastAPI()})

    with pytest.raises(RuntimeError, match=r"app\.state\.container"):
        provider(request)


def test_authenticated_user_id_of_anonymous_actor_raises_authentication_error() -> None:
    actor = Actor.anonymous()

    with pytest.raises(AuthenticationError):
        authenticated_user_id(actor)


async def test_read_me_when_user_record_is_missing_raises_not_found() -> None:
    services = build_test_app().app.state.container
    actor = ActorTestFactory.build()

    with pytest.raises(NotFoundError):
        await read_me(actor=actor, response=Response(), services=services)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/impact-metrics",
        "/api/v1/places?q=test",
        f"/api/v1/organizations/{ActorTestFactory.build().user_id}",
    ],
)
async def test_public_route_with_rejected_token_returns_401(path: str) -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(path, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
