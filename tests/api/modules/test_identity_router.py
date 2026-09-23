"""HTTP tests for ``/api/v1/me``, ``/organizations``, ``/users`` and moderation.

Users are mirrored through the API itself: the first authenticated request of a
subject creates its user, and realm roles on that first token become its roles.
Members are added by a platform administrator: organisation admins may not add
members until members can consent (Q-I9).
"""

from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import UUID

import httpx
import pytest

from tests.fakes.api import ApiHarness, auth_headers, build_test_app
from yakhnama.modules.identity.api.dependencies import map_realm_roles
from yakhnama.modules.identity.public import Role
from yakhnama.platform.etag import make_etag

ME: Final = "/api/v1/me"
ORGANIZATIONS: Final = "/api/v1/organizations"
IDEMPOTENCY_KEY: Final = "0192f4c1-0000-7000-8000-0000000000aa"
ORGANIZATION_BODY: Final[dict[str, str]] = {
    "slug": "test-org",
    "name": "Test organisation",
    "organization_type": "research",
}
ADMIN: Final = "admin-subject"
OWNER: Final = "owner-subject"
MEMBER: Final = "member-subject"
OUTSIDER: Final = "outsider-subject"


@pytest.fixture
def api() -> ApiHarness:
    """Return a fresh app wired with empty fakes."""
    return build_test_app()


@pytest.fixture
async def client(api: ApiHarness) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a client of the fresh app."""
    async with api.client() as http_client:
        yield http_client


async def _mirror(
    client: httpx.AsyncClient, subject: str, roles: tuple[str, ...] = ()
) -> dict[str, Any]:
    response = await client.get(ME, headers=auth_headers(subject=subject, roles=roles))
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


async def _create_organization(
    client: httpx.AsyncClient, subject: str = OWNER
) -> httpx.Response:
    response = await client.post(
        ORGANIZATIONS, json=ORGANIZATION_BODY, headers=auth_headers(subject=subject)
    )
    assert response.status_code == 201
    return response


def _admin_headers() -> dict[str, str]:
    return auth_headers(subject=ADMIN, roles=["admin"])


async def _add_member(
    client: httpx.AsyncClient, organization_id: str, user_id: str
) -> httpx.Response:
    added = await client.post(
        f"{ORGANIZATIONS}/{organization_id}/members",
        json={"user_id": user_id},
        headers=_admin_headers(),
    )
    assert added.status_code == 201
    return added


async def _organization_with_member(client: httpx.AsyncClient) -> tuple[str, str]:
    organization_id, member_id, _ = await _organization_with_member_tag(client)
    return organization_id, member_id


async def _organization_with_member_tag(
    client: httpx.AsyncClient,
) -> tuple[str, str, str]:
    organization_id = (await _create_organization(client)).json()["id"]
    member_id = (await _mirror(client, MEMBER))["id"]
    added = await _add_member(client, organization_id, member_id)
    return organization_id, member_id, added.headers["etag"]


# --------------------------------------------------------------------------- #
# /me                                                                         #
# --------------------------------------------------------------------------- #


async def test_read_me_anonymous_returns_401_problem_with_challenge(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(ME)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_read_me_first_request_mirrors_user_with_known_realm_roles(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    headers = auth_headers(roles=["moderator", "offline_access"], name="Test Person")

    response = await client.get(ME, headers=headers)

    body = response.json()
    assert response.status_code == 200
    assert set(body["roles"]) == {"citizen", "moderator"}
    # The token's name is never copied; the user sets one through PATCH /me.
    assert body["display_name"] is None
    assert response.headers["etag"] == make_etag(body["version"], body["id"])
    assert len(api.identity.users.committed) == 1


async def test_read_me_second_request_reuses_the_mirrored_user(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    first = await _mirror(client, OWNER)

    second = await _mirror(client, OWNER, roles=("admin",))

    assert second["id"] == first["id"]
    assert "admin" not in second["roles"]
    assert len(api.identity.users.committed) == 1


async def test_read_me_with_malformed_authorization_returns_401(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(ME, headers={"Authorization": "Basic dXNlcjpwdw=="})

    assert response.status_code == 401


async def test_update_me_with_current_etag_renames_and_returns_new_etag(
    client: httpx.AsyncClient,
) -> None:
    me = await client.get(ME, headers=auth_headers())

    response = await client.patch(
        ME,
        json={"display_name": "Renamed Person"},
        headers=auth_headers() | {"If-Match": me.headers["etag"]},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["display_name"] == "Renamed Person"
    assert response.headers["etag"] == make_etag(body["version"], body["id"])
    assert response.headers["etag"] != me.headers["etag"]


async def test_update_me_with_null_display_name_clears_it(
    client: httpx.AsyncClient,
) -> None:
    me = await client.get(ME, headers=auth_headers(name="Test Person"))

    response = await client.patch(
        ME,
        json={"display_name": None},
        headers=auth_headers() | {"If-Match": me.headers["etag"]},
    )

    assert response.json()["display_name"] is None


async def test_update_me_without_if_match_returns_428_problem(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch(
        ME, json={"display_name": "Renamed"}, headers=auth_headers()
    )

    assert response.status_code == 428
    assert response.json()["type"].endswith("/precondition-required")


async def test_update_me_with_stale_etag_returns_412_and_keeps_name(
    client: httpx.AsyncClient,
) -> None:
    me = await _mirror(client, OWNER)
    stale = make_etag(me["version"] + 5, me["id"])

    response = await client.patch(
        ME,
        json={"display_name": "Renamed"},
        headers=auth_headers(subject=OWNER) | {"If-Match": stale},
    )

    assert response.status_code == 412
    assert (await _mirror(client, OWNER))["display_name"] is None


async def test_update_me_with_etag_of_another_user_returns_412(
    client: httpx.AsyncClient,
) -> None:
    other = await _mirror(client, OUTSIDER)
    await _mirror(client, OWNER)

    response = await client.patch(
        ME,
        json={"display_name": "Renamed"},
        headers=auth_headers(subject=OWNER)
        | {"If-Match": make_etag(other["version"], other["id"])},
    )

    assert response.status_code == 412


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "x" * 121},
        {"display_name": "Name", "email": "someone@example.test"},
        {},
    ],
)
async def test_update_me_with_invalid_body_returns_422_without_echo(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    me = await client.get(ME, headers=auth_headers())

    response = await client.patch(
        ME, json=body, headers=auth_headers() | {"If-Match": me.headers["etag"]}
    )

    assert response.status_code == 422
    assert "someone@example.test" not in response.text
    assert "x" * 121 not in response.text


@pytest.mark.parametrize(
    "escaped_name",
    [
        pytest.param(b"a\\ud800b", id="lone-surrogate"),
        pytest.param(b"a\\u202eb", id="right-to-left-override"),
        pytest.param(b"a\\u2066b", id="left-to-right-isolate"),
    ],
)
async def test_update_me_with_surrogate_or_bidi_character_returns_422(
    client: httpx.AsyncClient, api: ApiHarness, escaped_name: bytes
) -> None:
    me = await client.get(ME, headers=auth_headers())

    response = await client.patch(
        ME,
        content=b'{"display_name": "' + escaped_name + b'"}',
        headers=auth_headers()
        | {"If-Match": me.headers["etag"], "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    (user,) = api.identity.users.committed.values()
    assert user.display_name is None


async def test_update_me_with_nul_character_returns_422_nul_problem(
    client: httpx.AsyncClient,
) -> None:
    me = await client.get(ME, headers=auth_headers())

    response = await client.patch(
        ME,
        content=b'{"display_name": "a\\u0000b"}',
        headers=auth_headers()
        | {"If-Match": me.headers["etag"], "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/nul-character")


# --------------------------------------------------------------------------- #
# Organisations                                                               #
# --------------------------------------------------------------------------- #


async def test_create_organization_anonymous_returns_401_problem(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    response = await client.post(
        ORGANIZATIONS,
        json=ORGANIZATION_BODY,
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert api.identity.organizations.committed == {}


async def test_create_organization_returns_201_with_location_and_etag(
    client: httpx.AsyncClient,
) -> None:
    response = await _create_organization(client)

    body = response.json()
    assert response.headers["location"] == f"{ORGANIZATIONS}/{body['id']}"
    assert response.headers["etag"] == make_etag(body["version"], body["id"])
    assert body["member_count"] == 1
    assert body["slug"] == "test-org"


async def test_create_organization_replay_with_same_key_returns_stored_response(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    headers = auth_headers(subject=OWNER) | {"Idempotency-Key": IDEMPOTENCY_KEY}

    first = await client.post(ORGANIZATIONS, json=ORGANIZATION_BODY, headers=headers)
    replay = await client.post(ORGANIZATIONS, json=ORGANIZATION_BODY, headers=headers)

    assert first.status_code == 201
    assert replay.status_code == first.status_code
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.headers["location"] == first.headers["location"]
    assert len(api.identity.organizations.committed) == 1


async def test_create_organization_reusing_key_with_other_body_returns_409(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    headers = auth_headers(subject=OWNER) | {"Idempotency-Key": IDEMPOTENCY_KEY}
    await client.post(ORGANIZATIONS, json=ORGANIZATION_BODY, headers=headers)

    response = await client.post(
        ORGANIZATIONS, json=ORGANIZATION_BODY | {"slug": "other-org"}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/idempotency-key-reused")
    assert len(api.identity.organizations.committed) == 1


async def test_create_organization_with_non_uuid_key_returns_400(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        ORGANIZATIONS,
        json=ORGANIZATION_BODY,
        headers=auth_headers() | {"Idempotency-Key": "not-a-uuid"},
    )

    assert response.status_code == 400
    assert response.json()["type"].endswith("/invalid-idempotency-key")


async def test_create_organization_with_taken_slug_returns_409_problem(
    client: httpx.AsyncClient,
) -> None:
    await _create_organization(client)

    response = await client.post(
        ORGANIZATIONS, json=ORGANIZATION_BODY, headers=auth_headers(subject=OUTSIDER)
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "Not A Slug"),
        ("slug", "a" * 65),
        ("name", "n" * 201),
        ("organization_type", "cartel"),
        ("unexpected", "field"),
    ],
)
async def test_create_organization_with_invalid_field_returns_422(
    client: httpx.AsyncClient, api: ApiHarness, field: str, value: str
) -> None:
    response = await client.post(
        ORGANIZATIONS, json=ORGANIZATION_BODY | {field: value}, headers=auth_headers()
    )

    assert response.status_code == 422
    assert value not in response.text
    assert api.identity.organizations.committed == {}


async def test_read_organization_anonymous_returns_detail_with_etag(
    client: httpx.AsyncClient,
) -> None:
    created = (await _create_organization(client)).json()

    response = await client.get(f"{ORGANIZATIONS}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created
    assert response.headers["etag"] == make_etag(created["version"], created["id"])


async def test_read_organization_authenticated_returns_200(
    client: httpx.AsyncClient,
) -> None:
    created = (await _create_organization(client)).json()

    response = await client.get(
        f"{ORGANIZATIONS}/{created['id']}", headers=auth_headers(subject=OUTSIDER)
    )

    assert response.status_code == 200


async def test_read_organization_when_missing_returns_404_problem(
    client: httpx.AsyncClient,
) -> None:
    missing = (await _mirror(client, OWNER))["id"]

    response = await client.get(f"{ORGANIZATIONS}/{missing}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_rename_organization_by_its_admin_returns_new_etag(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_organization(client)

    response = await client.patch(
        created.headers["location"],
        json={"name": "Renamed organisation"},
        headers=auth_headers(subject=OWNER) | {"If-Match": created.headers["etag"]},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed organisation"
    assert response.headers["etag"] == make_etag(2, created.json()["id"])


async def test_rename_organization_by_outsider_returns_403_problem(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_organization(client)

    response = await client.patch(
        created.headers["location"],
        json={"name": "Hijacked"},
        headers=auth_headers(subject=OUTSIDER) | {"If-Match": created.headers["etag"]},
    )

    assert response.status_code == 403
    assert response.json()["type"].endswith("/permission-denied")


async def test_rename_organization_by_platform_admin_is_allowed(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_organization(client)
    await _mirror(client, ADMIN, roles=("admin",))

    response = await client.patch(
        created.headers["location"],
        json={"name": "Renamed by admin"},
        headers=auth_headers(subject=ADMIN) | {"If-Match": created.headers["etag"]},
    )

    assert response.status_code == 200


async def test_rename_organization_with_stale_etag_returns_412(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_organization(client)
    stale = make_etag(9, created.json()["id"])

    response = await client.patch(
        created.headers["location"],
        json={"name": "Renamed"},
        headers=auth_headers(subject=OWNER) | {"If-Match": stale},
    )

    assert response.status_code == 412


async def test_rename_organization_without_if_match_returns_428(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_organization(client)

    response = await client.patch(
        created.headers["location"],
        json={"name": "Renamed"},
        headers=auth_headers(subject=OWNER),
    )

    assert response.status_code == 428


async def test_rename_organization_anonymous_returns_401(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_organization(client)

    response = await client.patch(
        created.headers["location"],
        json={"name": "Renamed"},
        headers={"If-Match": created.headers["etag"]},
    )

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Members                                                                     #
# --------------------------------------------------------------------------- #


async def test_add_member_by_platform_admin_returns_201_with_membership_etag(
    client: httpx.AsyncClient,
) -> None:
    organization_id = (await _create_organization(client)).json()["id"]
    member_id = (await _mirror(client, MEMBER))["id"]

    response = await client.post(
        f"{ORGANIZATIONS}/{organization_id}/members",
        json={"user_id": member_id, "role": "member"},
        headers=_admin_headers(),
    )

    body = response.json()
    assert response.status_code == 201
    assert body["user_id"] == member_id
    assert body["role"] == "member"
    assert body["membership_id"] != member_id
    assert response.headers["etag"] == make_etag(
        body["version"], UUID(body["membership_id"])
    )


async def test_add_member_by_organization_admin_returns_403(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    organization_id = (await _create_organization(client)).json()["id"]
    member_id = (await _mirror(client, MEMBER))["id"]

    response = await client.post(
        f"{ORGANIZATIONS}/{organization_id}/members",
        json={"user_id": member_id},
        headers=auth_headers(subject=OWNER),
    )

    assert response.status_code == 403
    assert "display_name" not in response.text
    assert len(api.identity.memberships.committed) == 1


async def test_add_member_by_non_admin_returns_403(client: httpx.AsyncClient) -> None:
    organization_id, _ = await _organization_with_member(client)
    outsider_id = (await _mirror(client, OUTSIDER))["id"]

    response = await client.post(
        f"{ORGANIZATIONS}/{organization_id}/members",
        json={"user_id": outsider_id},
        headers=auth_headers(subject=MEMBER),
    )

    assert response.status_code == 403


async def test_add_member_twice_returns_409(client: httpx.AsyncClient) -> None:
    organization_id, member_id = await _organization_with_member(client)

    response = await client.post(
        f"{ORGANIZATIONS}/{organization_id}/members",
        json={"user_id": member_id},
        headers=_admin_headers(),
    )

    assert response.status_code == 409


async def test_list_members_by_member_walks_every_page(
    client: httpx.AsyncClient,
) -> None:
    organization_id, member_id = await _organization_with_member(client)
    outsider_id = (await _mirror(client, OUTSIDER))["id"]
    await _add_member(client, organization_id, outsider_id)
    path = f"{ORGANIZATIONS}/{organization_id}/members"

    first = await client.get(
        path, params={"limit": 2}, headers=auth_headers(subject=MEMBER)
    )
    second = await client.get(
        first.headers["link"].split(";")[0].strip("<>"),
        headers=auth_headers(subject=MEMBER),
    )

    seen = [
        item["user_id"] for page in (first, second) for item in page.json()["items"]
    ]
    assert first.status_code == 200
    assert len(seen) == len(set(seen)) == 3
    assert all(
        "membership_id" in item
        for page in (first, second)
        for item in page.json()["items"]
    )
    assert {member_id, outsider_id} <= set(seen)
    assert second.json()["next_cursor"] is None


async def test_list_members_by_outsider_returns_403(
    client: httpx.AsyncClient,
) -> None:
    organization_id, _ = await _organization_with_member(client)

    response = await client.get(
        f"{ORGANIZATIONS}/{organization_id}/members",
        headers=auth_headers(subject=OUTSIDER),
    )

    assert response.status_code == 403


async def test_list_members_anonymous_returns_401(client: httpx.AsyncClient) -> None:
    organization_id, _ = await _organization_with_member(client)

    response = await client.get(f"{ORGANIZATIONS}/{organization_id}/members")

    assert response.status_code == 401


async def test_list_members_with_limit_above_max_returns_422(
    client: httpx.AsyncClient,
) -> None:
    organization_id, _ = await _organization_with_member(client)

    response = await client.get(
        f"{ORGANIZATIONS}/{organization_id}/members",
        params={"limit": 500},
        headers=auth_headers(subject=OWNER),
    )

    assert response.status_code == 422


async def test_change_member_role_with_current_etag_promotes_member(
    client: httpx.AsyncClient,
) -> None:
    organization_id, member_id, tag = await _organization_with_member_tag(client)
    path = f"{ORGANIZATIONS}/{organization_id}/members/{member_id}"

    response = await client.patch(
        path,
        json={"role": "admin"},
        headers=auth_headers(subject=OWNER) | {"If-Match": tag},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["role"] == "admin"
    assert response.headers["etag"] == make_etag(
        body["version"], UUID(body["membership_id"])
    )


@pytest.mark.parametrize(
    "if_match",
    [
        pytest.param("user", id="tag-naming-the-user-id"),
        pytest.param("not-a-uuid", id="tag-without-uuid"),
        pytest.param("weak", id="weak-tag"),
    ],
)
async def test_change_member_role_with_foreign_tag_returns_412(
    client: httpx.AsyncClient, if_match: str
) -> None:
    organization_id, member_id, tag = await _organization_with_member_tag(client)
    headers = {
        "user": make_etag(1, UUID(member_id)),
        "not-a-uuid": '"member:1"',
        "weak": f"W/{tag}",
    }

    response = await client.patch(
        f"{ORGANIZATIONS}/{organization_id}/members/{member_id}",
        json={"role": "admin"},
        headers=auth_headers(subject=OWNER) | {"If-Match": headers[if_match]},
    )

    assert response.status_code == 412


async def test_remove_member_with_tag_of_earlier_membership_returns_412(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    organization_id, member_id, old_tag = await _organization_with_member_tag(client)
    path = f"{ORGANIZATIONS}/{organization_id}/members/{member_id}"
    removed = await client.delete(path, headers=auth_headers(subject=OWNER))
    assert removed.status_code == 204
    await _add_member(client, organization_id, member_id)

    response = await client.delete(
        path, headers=auth_headers(subject=OWNER) | {"If-Match": old_tag}
    )

    assert response.status_code == 412
    assert len(api.identity.memberships.committed) == 2


async def test_change_member_role_with_stale_etag_returns_412(
    client: httpx.AsyncClient,
) -> None:
    organization_id, member_id, tag = await _organization_with_member_tag(client)
    membership_id = UUID(tag.strip('"').partition(":")[0])

    response = await client.patch(
        f"{ORGANIZATIONS}/{organization_id}/members/{member_id}",
        json={"role": "admin"},
        headers=auth_headers(subject=OWNER) | {"If-Match": make_etag(4, membership_id)},
    )

    assert response.status_code == 412


async def test_change_member_role_without_if_match_is_applied(
    client: httpx.AsyncClient,
) -> None:
    organization_id, member_id = await _organization_with_member(client)

    response = await client.patch(
        f"{ORGANIZATIONS}/{organization_id}/members/{member_id}",
        json={"role": "admin"},
        headers=auth_headers(subject=OWNER),
    )

    assert response.status_code == 200


async def test_remove_member_by_admin_returns_204(
    client: httpx.AsyncClient, api: ApiHarness
) -> None:
    organization_id, member_id = await _organization_with_member(client)

    response = await client.delete(
        f"{ORGANIZATIONS}/{organization_id}/members/{member_id}",
        headers=auth_headers(subject=OWNER),
    )

    assert response.status_code == 204
    assert response.content == b""
    assert len(api.identity.memberships.committed) == 1


async def test_remove_last_admin_returns_409_problem(
    client: httpx.AsyncClient,
) -> None:
    organization_id = (await _create_organization(client)).json()["id"]
    owner_id = (await _mirror(client, OWNER))["id"]

    response = await client.delete(
        f"{ORGANIZATIONS}/{organization_id}/members/{owner_id}",
        headers=auth_headers(subject=OWNER),
    )

    assert response.status_code == 409


async def test_remove_member_by_member_returns_403(client: httpx.AsyncClient) -> None:
    organization_id, member_id = await _organization_with_member(client)

    response = await client.delete(
        f"{ORGANIZATIONS}/{organization_id}/members/{member_id}",
        headers=auth_headers(subject=MEMBER),
    )

    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Users, for administrators only                                              #
# --------------------------------------------------------------------------- #


async def test_grant_and_revoke_role_by_admin_return_user_with_etag(
    client: httpx.AsyncClient,
) -> None:
    await _mirror(client, ADMIN, roles=("admin",))
    user_id = (await _mirror(client, MEMBER))["id"]
    admin = auth_headers(subject=ADMIN)

    granted = await client.post(
        f"/api/v1/users/{user_id}/roles",
        json={"role": "trusted_reporter"},
        headers=admin | {"If-Match": make_etag(1, user_id)},
    )
    revoked = await client.delete(
        f"/api/v1/users/{user_id}/roles/trusted_reporter",
        headers=admin | {"If-Match": granted.headers["etag"]},
    )

    assert granted.status_code == 200
    assert "trusted_reporter" in granted.json()["roles"]
    assert granted.headers["etag"] == make_etag(2, user_id)
    assert revoked.status_code == 200
    assert "trusted_reporter" not in revoked.json()["roles"]


async def test_grant_role_by_non_admin_returns_403(client: httpx.AsyncClient) -> None:
    user_id = (await _mirror(client, MEMBER))["id"]

    response = await client.post(
        f"/api/v1/users/{user_id}/roles",
        json={"role": "admin"},
        headers=auth_headers(subject=MEMBER),
    )

    assert response.status_code == 403
    assert "admin" not in (await _mirror(client, MEMBER))["roles"]


async def test_grant_role_with_stale_etag_returns_412(
    client: httpx.AsyncClient,
) -> None:
    await _mirror(client, ADMIN, roles=("admin",))
    user_id = (await _mirror(client, MEMBER))["id"]

    response = await client.post(
        f"/api/v1/users/{user_id}/roles",
        json={"role": "moderator"},
        headers=auth_headers(subject=ADMIN) | {"If-Match": make_etag(7, user_id)},
    )

    assert response.status_code == 412


async def test_revoke_unknown_role_name_returns_422(client: httpx.AsyncClient) -> None:
    await _mirror(client, ADMIN, roles=("admin",))
    user_id = (await _mirror(client, MEMBER))["id"]

    response = await client.delete(
        f"/api/v1/users/{user_id}/roles/emperor", headers=auth_headers(subject=ADMIN)
    )

    assert response.status_code == 422


async def test_suspend_user_blocks_them_and_reinstate_restores_access(
    client: httpx.AsyncClient,
) -> None:
    await _mirror(client, ADMIN, roles=("admin",))
    user_id = (await _mirror(client, MEMBER))["id"]
    admin = auth_headers(subject=ADMIN)

    suspended = await client.post(
        f"/api/v1/users/{user_id}/suspension",
        json={"reason": "Test suspension"},
        headers=admin,
    )
    blocked = await client.get(ME, headers=auth_headers(subject=MEMBER))
    reinstated = await client.delete(
        f"/api/v1/users/{user_id}/suspension", headers=admin
    )
    restored = await client.get(ME, headers=auth_headers(subject=MEMBER))

    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    assert "reason" not in suspended.json()
    assert "Test suspension" not in suspended.text
    assert blocked.status_code == 403
    assert reinstated.json()["status"] == "active"
    assert restored.status_code == 200


async def test_suspend_user_with_empty_reason_returns_422(
    client: httpx.AsyncClient,
) -> None:
    await _mirror(client, ADMIN, roles=("admin",))
    user_id = (await _mirror(client, MEMBER))["id"]

    response = await client.post(
        f"/api/v1/users/{user_id}/suspension",
        json={"reason": ""},
        headers=auth_headers(subject=ADMIN),
    )

    assert response.status_code == 422


async def test_suspend_missing_user_returns_404(client: httpx.AsyncClient) -> None:
    await _mirror(client, ADMIN, roles=("admin",))
    missing = (await _create_organization(client)).json()["id"]

    response = await client.post(
        f"/api/v1/users/{missing}/suspension",
        json={"reason": "Test suspension"},
        headers=auth_headers(subject=ADMIN),
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Moderation                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["moderator", "admin"])
async def test_moderation_ping_by_moderator_or_admin_returns_ok(
    client: httpx.AsyncClient, role: str
) -> None:
    response = await client.get(
        "/api/v1/moderation/ping", headers=auth_headers(roles=[role])
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_moderation_ping_by_citizen_returns_403_problem(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/moderation/ping", headers=auth_headers())

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_moderation_ping_anonymous_returns_401(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/moderation/ping")

    assert response.status_code == 401


def test_map_realm_roles_ignores_unknown_names() -> None:
    names = ["admin", "offline_access", "default-roles-yakhnama", "Moderator"]

    roles = map_realm_roles(names)

    assert roles == frozenset({Role.ADMIN})
