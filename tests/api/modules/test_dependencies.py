"""Edge cases of the api dependencies that HTTP requests alone cannot reach."""

from collections.abc import Callable

import pytest
from fastapi import FastAPI, Response
from starlette.requests import Request

from tests.factories.identity import ActorTestFactory
from tests.fakes.api import build_test_app
from yakhnama.modules.events.api.dependencies import get_events_services
from yakhnama.modules.geography.api.dependencies import get_geography_services
from yakhnama.modules.hazards.api.dependencies import get_hazards_services
from yakhnama.modules.identity.api.dependencies import get_identity_services
from yakhnama.modules.identity.api.router import authenticated_user_id, read_me
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.api.claims_dependencies import (
    get_impact_claims_services,
)
from yakhnama.modules.impacts.api.dependencies import get_impacts_services
from yakhnama.modules.media.api.dependencies import get_media_services
from yakhnama.modules.provenance.api.dependencies import get_provenance_services
from yakhnama.modules.reports.api.dependencies import get_reports_services
from yakhnama.modules.verification.api.dependencies import (
    get_verification_services,
)
from yakhnama.shared_kernel.errors import AuthenticationError, NotFoundError


@pytest.mark.parametrize(
    "provider",
    [
        get_geography_services,
        get_hazards_services,
        get_identity_services,
        get_impacts_services,
        get_reports_services,
        get_media_services,
        get_events_services,
        get_verification_services,
        get_impact_claims_services,
        get_provenance_services,
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
        "/api/v1/events",
        "/api/v1/sources",
        f"/api/v1/media/{ActorTestFactory.build().user_id}",
        f"/api/v1/events/{ActorTestFactory.build().user_id}/impacts",
        "/api/v1/reports",
        "/api/v1/moderation/verification-cases",
    ],
)
async def test_public_route_with_rejected_token_returns_401(path: str) -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(path, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/moderation/verification-cases"),
        ("POST", "/api/v1/moderation/sources"),
        ("POST", "/api/v1/moderation/infrastructure-assets"),
        ("POST", "/api/v1/media"),
    ],
)
async def test_protected_route_anonymous_returns_401_with_challenge(
    method: str, path: str
) -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.request(method, path, json={})

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
