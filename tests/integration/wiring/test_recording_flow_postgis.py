"""The Phase 3 gate flow through the real composition root, PostGIS and MinIO.

``docs/plans/phase-3.md`` §5 as one test, over HTTP against ``create_app`` with the
container ``build_container`` makes from the test settings: SQL units of work and
query services on the migrated session database, ``S3StoragePort`` on the session
MinIO, the in-memory task broker and the development ``noop`` scanner. Only two
things are replaced, both documented here:

- the token validator, with one backed by the session's in-memory signing key, so
  locally signed tokens are accepted without an identity provider;
- the malware scanner for the explicit ``media.scan`` run, with
  ``NoOpMalwareScanner(verdict=clean)``, through ``dataclasses.replace`` and
  ``build_task_handlers`` (the scan the in-memory broker ran on completion used
  the configured scanner and recorded ``unavailable``; a later verdict replaces
  it).

The coordinates, texts and file are synthetic.
"""

import dataclasses
import re
from io import BytesIO
from typing import Final
from uuid import UUID

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from tests.fakes.identity import actor_with
from tests.integration.wiring.flow_steps import (
    API,
    MODERATION,
    Json,
    bearer_headers,
    drain_tasks,
    json_body,
    scheduled_task,
    verify_event,
)
from tests.unit.modules.media.infrastructure.adapters.images import gps_photo
from yakhnama.main import create_app
from yakhnama.modules.identity.public import Role
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    NoOpMalwareScanner,
)
from yakhnama.modules.media.public import ScanStatus
from yakhnama.platform.container import (
    Container,
    build_seed_handler,
    build_task_handlers,
)
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.tasks.handlers import MEDIA_SCAN_TASK, REPORTS_TRIAGE_TASK
from yakhnama.seed.application import SeedReferenceData
from yakhnama.shared_kernel.pagination import MAX_PAGE_LIMIT, PageRequest

pytestmark = pytest.mark.integration

REPORTER: Final = "wiring-reporter"
NEIGHBOUR: Final = "wiring-neighbour"
MODERATOR: Final = "wiring-moderator"
HAZARD_CODE: Final = "glof"
METRIC_CODE: Final = "deaths"
EXACT_LONGITUDE: Final = 74.654321
EXACT_LATITUDE: Final = 36.314159
ROUNDED: Final = [74.65, 36.31]
DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
# Ids, codes and digests only: no field of an audit entry can hold free text.
AUDIT_FIELDS: Final = {
    "id",
    "occurred_at",
    "actor_id",
    "actor_kind",
    "action",
    "target",
    "before_digest",
    "after_digest",
    "request_id",
    "event_id",
    "payload_digest",
}


REPORTER_HEADERS: Final = bearer_headers(REPORTER)
NEIGHBOUR_HEADERS: Final = bearer_headers(NEIGHBOUR)
MODERATOR_HEADERS: Final = bearer_headers(MODERATOR, ("moderator",))


def _report_body(client_report_id: str, media_ids: list[str]) -> Json:
    return {
        "client_report_id": client_report_id,
        "observed_at": {"value": "2026-07-01T06:00:00Z", "precision": "hour"},
        "coordinates": {"longitude": EXACT_LONGITUDE, "latitude": EXACT_LATITUDE},
        "accuracy_metres": 12.5,
        "description": "Muddy water rising fast in the nala below the village.",
        "original_language": "en",
        "hazard_guess": {"hazard_code": HAZARD_CODE, "confidence": "medium"},
        "media_ids": media_ids,
    }


async def _upload_photo(client: httpx.AsyncClient, body: bytes) -> str:
    grant = json_body(
        await client.post(
            f"{API}/media", json={"mime_type": "image/jpeg"}, headers=REPORTER_HEADERS
        ),
        201,
    )
    headers = {header["name"]: header["value"] for header in grant["headers"]}
    async with httpx.AsyncClient() as storage_client:
        stored = await storage_client.put(
            grant["upload_url"], content=body, headers=headers
        )
    assert stored.status_code == 200, stored.text
    asset_id = str(grant["asset_id"])
    json_body(
        await client.post(f"{API}/media/{asset_id}/complete", headers=REPORTER_HEADERS),
        200,
    )
    return asset_id


async def _relay_everything(container: Container) -> None:
    while (await container.outbox_relay.relay_once(MAX_PAGE_LIMIT)).claimed:
        pass


async def _audit_event_ids(container: Container) -> list[UUID]:
    event_ids: list[UUID] = []
    page = PageRequest(limit=MAX_PAGE_LIMIT)
    while True:
        entries = await container.audit_query_service.list_recent(page)
        for entry in entries.items:
            assert DIGEST_PATTERN.fullmatch(entry.payload_digest)
            assert set(entry.model_dump()) == AUDIT_FIELDS
            event_ids.append(entry.event_id)
        if entries.next_cursor is None:
            return event_ids
        page = PageRequest(limit=MAX_PAGE_LIMIT, cursor=entries.next_cursor)


async def test_gate_flow_from_uploaded_photo_to_public_verified_event(  # noqa: PLR0915  # reason: the gate flow is one scenario, step by step
    container: Container,
) -> None:
    seed = build_seed_handler(container)
    await seed(SeedReferenceData(actor=actor_with({Role.ADMIN})))
    app = create_app(container.settings, container)
    transport = httpx.ASGITransport(app=app)
    public_texts: list[str] = []

    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as client:
        photo_id = await _upload_photo(client, gps_photo())
        await drain_tasks(container)
        scan_container = dataclasses.replace(
            container, malware_scanner=NoOpMalwareScanner(verdict=ScanStatus.CLEAN)
        )
        await build_task_handlers(scan_container)[MEDIA_SCAN_TASK](
            scheduled_task(MEDIA_SCAN_TASK, {"asset_id": photo_id})
        )
        approved = json_body(
            await client.post(
                f"{MODERATION}/media/{photo_id}/decision",
                json={"decision": "approved", "sensitivity": "none"},
                headers=MODERATOR_HEADERS,
            ),
            200,
        )
        report = json_body(
            await client.post(
                f"{API}/reports",
                json=_report_body(str(container.id_generator.new_id()), [photo_id]),
                headers=REPORTER_HEADERS,
            ),
            201,
        )
        await drain_tasks(container)
        await build_task_handlers(container)[REPORTS_TRIAGE_TASK](
            scheduled_task(REPORTS_TRIAGE_TASK, {"report_id": report["id"]})
        )
        corroborating = json_body(
            await client.post(
                f"{API}/reports",
                json=_report_body(str(container.id_generator.new_id()), []),
                headers=NEIGHBOUR_HEADERS,
            ),
            201,
        )
        await drain_tasks(container)
        event = json_body(
            await client.post(
                f"{MODERATION}/events",
                json={
                    "report_ids": [report["id"]],
                    "hazard_type": HAZARD_CODE,
                    "title": "Outburst flood below the glacier",
                },
                headers=MODERATOR_HEADERS,
            ),
            201,
        )
        json_body(
            await client.post(
                f"{MODERATION}/events/{event['id']}/reports",
                json={"report_id": corroborating["id"], "role": "supporting"},
                headers=MODERATOR_HEADERS,
            ),
            200,
        )
        source = json_body(
            await client.post(
                f"{MODERATION}/sources",
                json={
                    "source_type": "government",
                    "details": {
                        "title": "District situation report",
                        "citation": "District Disaster Management Authority, report 7",
                    },
                },
                headers=MODERATOR_HEADERS,
            ),
            201,
        )
        json_body(
            await client.post(
                f"{MODERATION}/events/{event['id']}/impact-claims",
                json={
                    "metric_code": METRIC_CODE,
                    "value": {"kind": "count", "count": 2},
                    "confidence": "medium",
                    "source_id": source["id"],
                    "claimed_at": {"value": "2026-07-02T00:00:00Z", "precision": "day"},
                },
                headers=MODERATOR_HEADERS,
            ),
            201,
        )
        hidden = json_body(await client.get(f"{API}/events"), 200)
        json_body(
            await client.post(
                f"{MODERATION}/events/{event['id']}/publication",
                headers=MODERATOR_HEADERS,
            ),
            200,
        )
        await verify_event(client, event["id"], MODERATOR_HEADERS)
        responses = (
            await client.get(f"{API}/events"),
            await client.get(
                f"{API}/events", headers={"Accept": "application/geo+json"}
            ),
            await client.get(f"{API}/events/{event['id']}"),
            await client.get(f"{API}/events/{event['id']}/impacts"),
            await client.get(f"{API}/events/{event['id']}/timeline"),
            await client.get(f"{API}/media/{photo_id}"),
        )
        public_texts.extend(response.text for response in responses)
        listing, geojson, detail, impacts, timeline, media = (
            json_body(response, 200) for response in responses
        )
    async with httpx.AsyncClient() as storage_client:
        public_copy = await storage_client.get(media["public_download"]["url"])
    await _relay_everything(container)
    audited = await _audit_event_ids(container)
    async with container.session_factory() as session:
        published = set(await session.scalars(select(OutboxMessage.id)))
    stored_report = await container.report_query_service.get_report(UUID(report["id"]))

    assert approved["is_published"] is True
    assert stored_report is not None
    assert stored_report.triage is not None
    assert hidden["items"] == []
    assert [item["id"] for item in listing["items"]] == [event["id"]]
    assert listing["items"][0]["verification_state"] == "verified"
    assert geojson["features"][0]["geometry"]["coordinates"] == ROUNDED
    assert len(detail["report_links"]) == 2
    best = impacts["best_figures"][0]
    assert (best["metric"]["code"], best["value"]["count"]) == (METRIC_CODE, 2)
    kinds = {entry["kind"] for entry in timeline["entries"]}
    assert {"impact_claim", "verification_transition"} <= kinds
    assert public_copy.status_code == 200
    with Image.open(BytesIO(public_copy.content)) as image:
        assert dict(image.getexif()) == {}
        assert "exif" not in image.info
    assert published
    assert sorted(audited) == sorted(published)
    for text in public_texts:
        assert str(EXACT_LONGITUDE) not in text
        assert str(EXACT_LATITUDE) not in text
        assert "accuracy" not in text
