"""HTTP tests for the moderators' media queue, ``GET /api/v1/moderation/media``."""

from typing import Any
from uuid import UUID

import httpx
import pytest

from tests.api.modules.recording import (
    MEDIA,
    MODERATION,
    REPORTS,
    moderator_headers,
    other_headers,
    recording_app,
    reporter_headers,
    submit_report,
)
from tests.factories.media import synthetic_sha256
from tests.fakes.api import ApiHarness
from tests.fakes.media import InMemoryMediaQueryService
from yakhnama.modules.media.public import (
    MimeType,
    StoredObject,
    original_object_key,
)

QUEUE = f"{MODERATION}/media"


def _listing_reports(api: ApiHarness) -> InMemoryMediaQueryService:
    # The fake cannot see the reports store; the test arranges the newest report
    # that lists an asset, as the SQL adapter would find it.
    service = api.app.state.container.media_query_service
    assert isinstance(service, InMemoryMediaQueryService)
    return service


async def upload_media(
    api: ApiHarness, client: httpx.AsyncClient, path: str = MEDIA
) -> str:
    # Like recording.upload_media, but each file has its own digest, so the
    # uploader's identical files are not deduplicated into one asset.
    grant = await client.post(
        path,
        json={"mime_type": "image/jpeg", "byte_size": 2048},
        headers=reporter_headers(),
    )
    asset_id = str(grant.json()["asset_id"])
    key = original_object_key(UUID(asset_id))
    api.storage.objects[key] = StoredObject(sha256=synthetic_sha256(), byte_size=2048)
    api.mime_sniffer.types[key] = MimeType.JPEG
    completed = await client.post(
        f"{MEDIA}/{asset_id}/complete", headers=reporter_headers()
    )
    assert completed.status_code == 200, completed.text
    return asset_id


async def _queue(
    client: httpx.AsyncClient, **params: str | int
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    response = await client.get(QUEUE, params=params, headers=moderator_headers())
    items: list[dict[str, Any]] = response.json().get("items", [])
    return response, items


async def test_media_queue_lists_completed_uploads_oldest_first_without_links() -> None:
    api = recording_app()

    async with api.client() as client:
        first = await upload_media(api, client)
        second = await upload_media(api, client)
        await client.post(
            MEDIA,
            json={"mime_type": "image/jpeg", "byte_size": 2048},
            headers=reporter_headers(),
        )
        response, items = await _queue(client)

    assert response.status_code == 200
    assert [item["id"] for item in items] == [first, second]
    assert all(item["public_download"] is None for item in items)
    assert all(item["original_download"] is None for item in items)
    assert items[0]["moderation_status"] == "pending"
    assert response.json()["next_cursor"] is None
    assert "link" not in response.headers
    assert api.storage.presigned_gets == []


async def test_media_queue_filters_by_moderation_status() -> None:
    api = recording_app()

    async with api.client() as client:
        pending = await upload_media(api, client)
        rejected = await upload_media(api, client)
        decision = await client.post(
            f"{MODERATION}/media/{rejected}/decision",
            json={"decision": "rejected", "reason": "Not a hazard photo."},
            headers=moderator_headers(),
        )
        _, pending_items = await _queue(client, moderation_status="pending")
        _, rejected_items = await _queue(client, moderation_status="rejected")
        _, clean_items = await _queue(client, scan_status="clean")

    assert decision.status_code == 200
    assert [item["id"] for item in pending_items] == [pending]
    assert [item["id"] for item in rejected_items] == [rejected]
    assert clean_items == []


async def test_media_queue_pages_with_link_header() -> None:
    api = recording_app()

    async with api.client() as client:
        assets = [await upload_media(api, client) for _ in range(3)]
        first, first_items = await _queue(client, limit=2)
        link = first.headers["link"]
        next_url = link.split(">", 1)[0].removeprefix("<")
        second = await client.get(next_url, headers=moderator_headers())

    assert [item["id"] for item in first_items] == assets[:2]
    assert link.startswith(f"<{QUEUE}?")
    assert link.endswith('>; rel="next"')
    assert "limit=2" in link
    assert [item["id"] for item in second.json()["items"]] == assets[2:]
    assert second.json()["next_cursor"] is None


async def test_media_queue_resolves_the_report_of_an_asset_uploaded_before_it() -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        report = await submit_report(client, media_ids=[asset_id])
        _listing_reports(api).listing_reports[UUID(asset_id)] = UUID(report["id"])
        _, items = await _queue(client)
        single = await client.get(f"{MEDIA}/{asset_id}", headers=moderator_headers())

    assert items[0]["report_id"] == report["id"]
    assert single.json()["report_id"] is None


async def test_media_queue_keeps_the_report_of_an_asset_uploaded_for_it() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        asset_id = await upload_media(api, client, f"{REPORTS}/{report['id']}/media")
        _, items = await _queue(client)

    assert [(item["id"], item["report_id"]) for item in items] == [
        (asset_id, report["id"])
    ]


@pytest.mark.parametrize("headers", [reporter_headers(), other_headers()])
async def test_media_queue_for_a_citizen_returns_403(headers: dict[str, str]) -> None:
    api = recording_app()

    async with api.client() as client:
        await upload_media(api, client)
        response = await client.get(QUEUE, headers=headers)

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


async def test_media_queue_anonymous_returns_401() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(QUEUE)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"moderation_status": "published"},
        {"scan_status": "dirty"},
        {"limit": 0},
        {"limit": 201},
        {"unknown": "x"},
        {"cursor": "x" * 1025},
    ],
)
async def test_media_queue_with_invalid_parameters_returns_422(
    params: dict[str, str | int],
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(QUEUE, params=params, headers=moderator_headers())

    assert response.status_code == 422


async def test_media_queue_with_a_forged_cursor_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            QUEUE, params={"cursor": "not-a-cursor"}, headers=moderator_headers()
        )

    assert response.status_code == 422
