"""HTTP tests for the moderator directory, ``GET /api/v1/moderation/moderators``."""

from collections.abc import AsyncIterator
from typing import Any, Final

import httpx
import pytest

from tests.fakes.api import ApiHarness, auth_headers, build_test_app

ME: Final = "/api/v1/me"
DIRECTORY: Final = "/api/v1/moderation/moderators"
MODERATOR: Final = "moderator-subject"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield a client of a fresh app wired with empty fakes."""
    api: ApiHarness = build_test_app()
    async with api.client() as http_client:
        yield http_client


async def _person(
    client: httpx.AsyncClient,
    subject: str,
    roles: tuple[str, ...] = (),
    name: str | None = None,
) -> dict[str, Any]:
    headers = auth_headers(subject=subject, roles=roles)
    me = await client.get(ME, headers=headers)
    if name is None:
        body: dict[str, Any] = me.json()
        return body
    renamed = await client.patch(
        ME,
        json={"display_name": name},
        headers=headers | {"If-Match": me.headers["etag"]},
    )
    assert renamed.status_code == 200, renamed.text
    result: dict[str, Any] = renamed.json()
    return result


async def test_directory_lists_moderators_and_admins_by_name_with_id_and_name_only(
    client: httpx.AsyncClient,
) -> None:
    zara = await _person(client, MODERATOR, ("moderator",), "Zara")
    amin = await _person(client, "admin-subject", ("admin",), "Amin")
    unnamed = await _person(client, "quiet-subject", ("moderator",))
    await _person(client, "citizen-subject", (), "Citizen")
    await _person(client, "trusted-subject", ("trusted_reporter",), "Trusted")

    response = await client.get(
        DIRECTORY, headers=auth_headers(subject=MODERATOR, roles=("moderator",))
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": amin["id"], "display_name": "Amin"},
            {"id": zara["id"], "display_name": "Zara"},
            {"id": unnamed["id"], "display_name": None},
        ]
    }
    assert MODERATOR not in response.text


async def test_directory_for_a_citizen_returns_403(client: httpx.AsyncClient) -> None:
    response = await client.get(DIRECTORY, headers=auth_headers(subject="citizen"))

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


async def test_directory_anonymous_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.get(DIRECTORY)

    assert response.status_code == 401
