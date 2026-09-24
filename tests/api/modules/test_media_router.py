"""HTTP tests for ``/api/v1/media``, report uploads and media moderation."""

from uuid import UUID

import httpx
import pytest

from tests.api.modules.recording import (
    MEDIA,
    MODERATION,
    REPORTS,
    etag_of,
    moderator_headers,
    new_client_id,
    other_headers,
    recording_app,
    reporter_headers,
    store_upload,
    submit_report,
    upload_media,
)
from tests.fakes.api import ApiHarness
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.media.api.schemas import MODERATION_STATUS_NAMES
from yakhnama.modules.media.public import (
    SCAN_TASK,
    ModerationStatus,
    RecordScanResult,
    RecordScanResultHandler,
    ScanStatus,
)

JPEG = {"mime_type": "image/jpeg"}


async def _scan_clean(api: ApiHarness, asset_id: str) -> None:
    # The scan runs in a background task in production; the test runs the same
    # handler directly over the harness's store.
    handler = RecordScanResultHandler(
        InMemoryUnitOfWorkFactory(api.media), api.clock, SequentialIdGenerator(seed=3)
    )
    await handler(RecordScanResult(asset_id=UUID(asset_id), verdict=ScanStatus.CLEAN))


async def _approve(client: httpx.AsyncClient, asset_id: str) -> httpx.Response:
    return await client.post(
        f"{MODERATION}/media/{asset_id}/decision",
        json={"decision": "approved", "sensitivity": "none"},
        headers=moderator_headers(),
    )


async def test_request_upload_returns_201_grant_with_location() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(MEDIA, json=JPEG, headers=reporter_headers())

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"] == f"{MEDIA}/{body['asset_id']}"
    assert body["upload_url"].startswith("https://storage.example.test/")
    assert body["max_bytes"] > 0
    assert len(api.storage.presigned_puts) == 1


async def test_request_upload_anonymous_returns_401() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(MEDIA, json=JPEG)

    assert response.status_code == 401
    assert api.storage.presigned_puts == []


@pytest.mark.parametrize("body", [{"mime_type": "text/html"}, {}, JPEG | {"x": 1}])
async def test_request_upload_with_invalid_body_returns_422(
    body: dict[str, object],
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(MEDIA, json=body, headers=reporter_headers())

    assert response.status_code == 422
    assert "text/html" not in response.text


async def test_request_report_upload_for_own_report_returns_201() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{report['id']}/media", json=JPEG, headers=reporter_headers()
        )
        asset = await client.get(
            f"{MEDIA}/{response.json()['asset_id']}", headers=reporter_headers()
        )

    assert response.status_code == 201
    assert asset.json()["report_id"] == report["id"]


async def test_request_report_upload_for_someone_elses_report_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{report['id']}/media", json=JPEG, headers=other_headers()
        )

    assert response.status_code == 403
    assert api.media.media_assets.committed == {}


async def test_complete_upload_after_storing_returns_completed_asset_and_scan() -> None:
    api = recording_app()

    async with api.client() as client:
        grant = await client.post(MEDIA, json=JPEG, headers=reporter_headers())
        asset_id = grant.json()["asset_id"]
        store_upload(api, asset_id)
        response = await client.post(
            f"{MEDIA}/{asset_id}/complete", headers=reporter_headers()
        )

    body = response.json()
    assert response.status_code == 200
    assert body["upload_status"] == "completed"
    assert response.headers["etag"] == etag_of(body)
    assert len(api.task_queue.of(SCAN_TASK)) == 1


async def test_complete_upload_before_the_file_arrives_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        grant = await client.post(MEDIA, json=JPEG, headers=reporter_headers())
        response = await client.post(
            f"{MEDIA}/{grant.json()['asset_id']}/complete", headers=reporter_headers()
        )

    assert response.status_code == 422
    assert api.task_queue.of(SCAN_TASK) == []


async def test_complete_upload_by_other_user_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        grant = await client.post(MEDIA, json=JPEG, headers=reporter_headers())
        asset_id = grant.json()["asset_id"]
        store_upload(api, asset_id)
        response = await client.post(
            f"{MEDIA}/{asset_id}/complete", headers=other_headers()
        )

    assert response.status_code == 403


async def test_complete_upload_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            f"{MEDIA}/{new_client_id()}/complete", headers=reporter_headers()
        )

    assert response.status_code == 404


async def test_get_media_as_uploader_returns_original_link() -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        response = await client.get(f"{MEDIA}/{asset_id}", headers=reporter_headers())

    body = response.json()
    assert response.status_code == 200
    assert body["original_download"] is not None
    assert body["public_download"] is None
    assert "owner_id" not in body
    assert response.headers["etag"] == etag_of(body)


@pytest.mark.parametrize("is_anonymous", [True, False])
async def test_get_unpublished_media_as_anyone_else_returns_404(
    *, is_anonymous: bool
) -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        response = await client.get(
            f"{MEDIA}/{asset_id}", headers={} if is_anonymous else other_headers()
        )

    assert response.status_code == 404
    assert "original" not in response.text


async def test_get_published_media_anonymous_returns_public_copy_only() -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        await _scan_clean(api, asset_id)
        approved = await _approve(client, asset_id)
        response = await client.get(f"{MEDIA}/{asset_id}")

    body = response.json()
    assert approved.status_code == 200
    assert approved.json()["is_published"] is True
    assert response.status_code == 200
    assert body["public_download"]["url"].startswith("https://storage.example.test/")
    assert body["original_download"] is None
    assert "media/original" not in response.text


async def test_moderate_media_as_citizen_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        response = await client.post(
            f"{MODERATION}/media/{asset_id}/decision",
            json={"decision": "approved"},
            headers=reporter_headers(),
        )

    assert response.status_code == 403
    assert response.json()["type"].endswith("/permission-denied")


async def test_moderate_media_anonymous_returns_401() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            f"{MODERATION}/media/{new_client_id()}/decision",
            json={"decision": "approved"},
        )

    assert response.status_code == 401


async def test_moderate_media_rejection_without_reason_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        response = await client.post(
            f"{MODERATION}/media/{asset_id}/decision",
            json={"decision": "rejected"},
            headers=moderator_headers(),
        )

    assert response.status_code == 422


async def test_moderate_media_rejection_with_reason_keeps_asset_private() -> None:
    api = recording_app()

    async with api.client() as client:
        asset_id = await upload_media(api, client)
        response = await client.post(
            f"{MODERATION}/media/{asset_id}/decision",
            json={
                "decision": "rejected",
                "sensitivity": "injured_or_deceased",
                "reason": "Shows an injured person.",
            },
            headers=moderator_headers(),
        )
        anonymous = await client.get(f"{MEDIA}/{asset_id}")

    assert response.status_code == 200
    assert response.json()["moderation_status"] == "rejected"
    assert anonymous.status_code == 404


async def test_moderate_media_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await _approve(client, new_client_id())

    assert response.status_code == 404


def test_moderation_status_names_match_the_media_enum_values() -> None:
    enum_values = {status.value for status in ModerationStatus}

    names = set(MODERATION_STATUS_NAMES)

    assert names == enum_values
