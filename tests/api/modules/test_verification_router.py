"""HTTP tests for ``/api/v1/moderation/verification...``."""

from uuid import UUID

import httpx
import pytest

from tests.api.modules.recording import (
    CASES,
    MODERATION,
    create_event,
    etag_of,
    event_case_id,
    moderator_headers,
    new_client_id,
    recording_app,
    reporter_headers,
    submit_report,
    verify,
)
from yakhnama.platform.etag import make_etag

REASON = "Matches the district report."


async def _case(client: httpx.AsyncClient) -> str:
    report = await submit_report(client)
    event = await create_event(client, [report["id"]])
    return await event_case_id(client, str(event["id"]))


def _transitions(case_id: str) -> str:
    return f"{MODERATION}/verification/{case_id}/transitions"


async def _moderator_id(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/me", headers=moderator_headers())
    return str(response.json()["id"])


async def test_get_case_returns_history_next_states_and_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        response = await client.get(f"{CASES}/{case_id}", headers=moderator_headers())

    body = response.json()
    assert response.status_code == 200
    assert body["target"]["kind"] == "event"
    assert body["next_states"]
    assert response.headers["etag"] == etag_of(body)


async def test_get_case_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            f"{CASES}/{new_client_id()}", headers=moderator_headers()
        )

    assert response.status_code == 404


@pytest.mark.parametrize("path", [CASES, f"{CASES}/{new_client_id()}"])
async def test_case_reads_as_citizen_return_403(path: str) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(path, headers=reporter_headers())

    assert response.status_code == 403


async def test_case_reads_anonymous_return_401() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(CASES)

    assert response.status_code == 401


async def test_list_cases_walk_with_link_visits_every_case_once() -> None:
    api = recording_app()

    async with api.client() as client:
        created = [await _case(client) for _ in range(3)]
        first = await client.get(
            CASES, params={"limit": 2}, headers=moderator_headers()
        )
        link = first.headers["link"].split(";")[0].strip("<>")
        second = await client.get(link, headers=moderator_headers())

    seen = [item["id"] for page in (first, second) for item in page.json()["items"]]
    assert sorted(seen) == sorted(created)
    assert second.json()["next_cursor"] is None


async def test_list_cases_filtered_by_state_returns_matching_cases() -> None:
    api = recording_app()

    async with api.client() as client:
        verified = await _case(client)
        await _case(client)
        await verify(client, verified)
        response = await client.get(
            CASES, params={"state": "verified"}, headers=moderator_headers()
        )

    assert [item["id"] for item in response.json()["items"]] == [verified]


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("state", "nonsense"), ("target_kind", "place"), ("limit", "500")],
)
async def test_list_cases_with_invalid_parameter_returns_422(
    parameter: str, value: str
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            CASES, params={parameter: value}, headers=moderator_headers()
        )

    assert response.status_code == 422


async def test_transition_through_the_table_reaches_verified_as_a_human() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        await verify(client, case_id)
        response = await client.get(f"{CASES}/{case_id}", headers=moderator_headers())

    body = response.json()
    assert body["state"] == "verified"
    assert all(transition["is_human"] for transition in body["history"])


async def test_transition_not_in_the_table_returns_409_invalid_transition() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        response = await client.post(
            _transitions(case_id),
            json={"to_state": "retracted", "reason": REASON},
            headers=moderator_headers(),
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/invalid-transition")


async def test_transition_without_required_reason_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        await verify(client, case_id)
        response = await client.post(
            _transitions(case_id),
            json={"to_state": "disputed"},
            headers=moderator_headers(),
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"to_state": "verified", "reason": REASON, "is_human": False},
        {"to_state": "unknown"},
        {"to_state": "verified", "reason": "x" * 2001},
    ],
)
async def test_transition_with_invalid_body_returns_422(
    body: dict[str, object],
) -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        response = await client.post(
            _transitions(case_id), json=body, headers=moderator_headers()
        )

    assert response.status_code == 422


async def test_transition_as_citizen_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        response = await client.post(
            _transitions(case_id),
            json={"to_state": "verified", "reason": REASON},
            headers=reporter_headers(),
        )

    assert response.status_code == 403


async def test_transition_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            _transitions(new_client_id()),
            json={"to_state": "submitted"},
            headers=moderator_headers(),
        )

    assert response.status_code == 404


async def test_transition_with_stale_if_match_returns_412() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        response = await client.post(
            _transitions(case_id),
            json={"to_state": "under_review", "reason": REASON},
            headers=moderator_headers() | {"If-Match": make_etag(42, UUID(case_id))},
        )

    assert response.status_code == 412


async def test_transition_with_current_if_match_returns_new_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        case = await client.get(f"{CASES}/{case_id}", headers=moderator_headers())
        next_state = case.json()["next_states"][0]
        response = await client.post(
            _transitions(case_id),
            json={"to_state": next_state, "reason": REASON},
            headers=moderator_headers() | {"If-Match": case.headers["etag"]},
        )

    assert response.status_code == 200
    assert response.headers["etag"] != case.headers["etag"]
    assert response.json()["state"] == next_state


async def test_assign_case_to_a_moderator_returns_assigned_case() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        reviewer_id = await _moderator_id(client)
        response = await client.post(
            f"{MODERATION}/verification/{case_id}/assignment",
            json={"reviewer_id": reviewer_id},
            headers=moderator_headers(),
        )
        listed = await client.get(
            CASES, params={"assigned_to": reviewer_id}, headers=moderator_headers()
        )

    assert response.status_code == 200
    assert response.json()["assigned_to"] == reviewer_id
    assert [item["id"] for item in listed.json()["items"]] == [case_id]


async def test_assign_case_to_a_citizen_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        citizen = await client.get("/api/v1/me", headers=reporter_headers())
        response = await client.post(
            f"{MODERATION}/verification/{case_id}/assignment",
            json={"reviewer_id": citizen.json()["id"]},
            headers=moderator_headers(),
        )

    assert response.status_code == 422


async def test_assign_case_with_stale_if_match_returns_412() -> None:
    api = recording_app()

    async with api.client() as client:
        case_id = await _case(client)
        reviewer_id = await _moderator_id(client)
        response = await client.post(
            f"{MODERATION}/verification/{case_id}/assignment",
            json={"reviewer_id": reviewer_id},
            headers=moderator_headers() | {"If-Match": make_etag(9, UUID(case_id))},
        )

    assert response.status_code == 412
