"""HTTP tests for ``/api/v1/sources`` and ``/api/v1/moderation/sources``."""

import pytest

from tests.api.modules.recording import (
    MODERATION,
    SOURCES,
    etag_of,
    moderator_headers,
    new_client_id,
    recording_app,
    register_source,
    reporter_headers,
    submit_report,
)

DETAILS = {
    "title": "District situation report",
    "citation": "District Disaster Management Authority, report 7",
}


async def test_register_source_returns_201_with_location_and_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            f"{MODERATION}/sources",
            json={"source_type": "news", "details": DETAILS},
            headers=moderator_headers(),
        )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"] == f"{SOURCES}/{body['id']}"
    assert response.headers["etag"] == etag_of(body)
    assert body["source_type"] == "news"
    assert "owner_actor_id" not in body


async def test_register_source_as_citizen_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            f"{MODERATION}/sources",
            json={"source_type": "citizen", "details": DETAILS},
            headers=reporter_headers(),
        )

    assert response.status_code == 403
    assert api.provenance.sources.committed == {}


@pytest.mark.parametrize(
    "body",
    [
        {"source_type": "rumour", "details": DETAILS},
        {"source_type": "news", "details": DETAILS | {"url": "javascript:alert(1)"}},
        {"source_type": "news", "details": DETAILS | {"title": ""}},
        {"source_type": "news", "details": DETAILS | {"extra": 1}},
        {"source_type": "news"},
    ],
)
async def test_register_source_with_invalid_body_returns_422(
    body: dict[str, object],
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            f"{MODERATION}/sources", json=body, headers=moderator_headers()
        )

    assert response.status_code == 422
    assert "javascript:" not in response.text


async def test_get_source_anonymous_returns_detail_with_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        source_id = await register_source(client)
        response = await client.get(f"{SOURCES}/{source_id}")

    assert response.status_code == 200
    assert response.json()["id"] == source_id
    assert response.headers["etag"] == etag_of(response.json())


async def test_get_citizen_source_of_a_report_anonymous_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.get(f"{SOURCES}/{report['source_id']}")

    assert response.status_code == 404
    assert report["id"] not in response.text


async def test_get_citizen_source_of_a_report_as_its_reporter_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.get(
            f"{SOURCES}/{report['source_id']}", headers=reporter_headers()
        )

    assert response.status_code == 404


async def test_get_citizen_source_as_moderator_names_neither_reporter_nor_report() -> (
    None
):
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.get(
            f"{SOURCES}/{report['source_id']}", headers=moderator_headers()
        )

    body = response.json()
    assert response.status_code == 200
    assert body["source_type"] == "citizen"
    assert report["reporter_id"] not in response.text
    assert report["id"] not in response.text


async def test_list_sources_anonymous_omits_citizen_sources_of_reports() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        news = await register_source(client, "news")
        response = await client.get(SOURCES)

    ids = [item["id"] for item in response.json()["items"]]
    assert response.status_code == 200
    assert news in ids
    assert report["source_id"] not in ids


async def test_get_source_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(f"{SOURCES}/{new_client_id()}")

    assert response.status_code == 404


async def test_list_sources_filtered_by_type_returns_only_that_type() -> None:
    api = recording_app()

    async with api.client() as client:
        government = await register_source(client, "government")
        await register_source(client, "news")
        response = await client.get(SOURCES, params={"source_type": "government"})

    assert [item["id"] for item in response.json()["items"]] == [government]


async def test_list_sources_walk_with_link_visits_every_source_once() -> None:
    api = recording_app()

    async with api.client() as client:
        created = [await register_source(client) for _ in range(3)]
        first = await client.get(SOURCES, params={"limit": 2})
        link = first.headers["link"].split(";")[0].strip("<>")
        second = await client.get(link)

    seen = [item["id"] for page in (first, second) for item in page.json()["items"]]
    assert sorted(seen) == sorted(created)
    assert second.json()["next_cursor"] is None


async def test_list_sources_with_invalid_type_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(SOURCES, params={"source_type": "rumour"})

    assert response.status_code == 422


async def test_list_sources_as_authenticated_citizen_returns_sources() -> None:
    api = recording_app()

    async with api.client() as client:
        source_id = await register_source(client)
        response = await client.get(SOURCES, headers=reporter_headers())

    assert [item["id"] for item in response.json()["items"]] == [source_id]
