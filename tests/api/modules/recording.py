"""Shared arrangements for the Phase 3 recording and moderation API tests.

Every value here is synthetic: the place code, the coordinates (inside the
Gilgit-Baltistan bounding box but not a real observation) and the texts are made up.

Patterns: Factory (test data).
"""

from typing import Any, Final
from uuid import UUID

import httpx

from tests.factories.geography import PlaceTestFactory
from tests.factories.hazards import HazardTypeTestFactory
from tests.factories.impacts import ImpactMetricTestFactory
from tests.fakes.api import ApiHarness, auth_headers, build_test_app
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.impacts.public import ValueKind
from yakhnama.modules.media.public import MimeType, StoredObject, original_object_key
from yakhnama.platform.etag import make_etag

REPORTS: Final = "/api/v1/reports"
EVENTS: Final = "/api/v1/events"
MEDIA: Final = "/api/v1/media"
SOURCES: Final = "/api/v1/sources"
MODERATION: Final = "/api/v1/moderation"
CASES: Final = f"{MODERATION}/verification-cases"

REPORTER: Final = "reporter-subject"
OTHER_USER: Final = "other-subject"
MODERATOR: Final = "moderator-subject"
HAZARD_CODE: Final = "glof"
METRIC_CODE: Final = "deaths"
PLACE_CODE: Final = "test.place.hunza"
EXACT_LONGITUDE: Final = 74.654321
EXACT_LATITUDE: Final = 36.314159
ROUNDED_LONGITUDE: Final = 74.65
ROUNDED_LATITUDE: Final = 36.31
OBSERVED_AT: Final = "2026-07-01T06:00:00Z"
FAKE_SHA256: Final = "a" * 64
_VERIFY_PATH: Final = ("submitted", "under_review", "verified")

_CLIENT_IDS = SequentialIdGenerator(seed=7)


def new_client_id() -> str:
    """Return a fresh UUIDv7 a reporting client would generate.

    Returns:
        The id as text.
    """
    return str(_CLIENT_IDS.new_id())


def reporter_headers() -> dict[str, str]:
    """Return the ``Authorization`` header of the reporting citizen.

    Returns:
        The header.
    """
    return auth_headers(subject=REPORTER)


def other_headers() -> dict[str, str]:
    """Return the ``Authorization`` header of another citizen.

    Returns:
        The header.
    """
    return auth_headers(subject=OTHER_USER)


def moderator_headers() -> dict[str, str]:
    """Return the ``Authorization`` header of a moderator.

    Returns:
        The header.
    """
    return auth_headers(subject=MODERATOR, roles=["moderator"])


def recording_app() -> ApiHarness:
    """Build the test app with a GLOF hazard type, a count metric and a place.

    Returns:
        The harness.
    """
    return build_test_app(
        hazard_types=[
            HazardTypeTestFactory.build(code=HAZARD_CODE, attributes_schema=HAZARD_CODE)
        ],
        impact_metrics=[
            ImpactMetricTestFactory.build(code=METRIC_CODE, value_kind=ValueKind.COUNT)
        ],
        places=[PlaceTestFactory.build(code=PLACE_CODE)],
    )


def report_body(**overrides: object) -> dict[str, object]:
    """Return a valid ``POST /reports`` body with a fresh client id.

    Args:
        **overrides: Members to replace or add, ``client_report_id`` included.

    Returns:
        The JSON body.
    """
    body: dict[str, object] = {
        "client_report_id": new_client_id(),
        "observed_at": {"value": OBSERVED_AT, "precision": "hour"},
        "coordinates": {"longitude": EXACT_LONGITUDE, "latitude": EXACT_LATITUDE},
        "accuracy_metres": 12.5,
        "description": "Muddy water rising fast in the nala below the village.",
        "original_language": "en",
        "hazard_guess": {"hazard_code": HAZARD_CODE, "confidence": "medium"},
        "place_hint": PLACE_CODE,
        "media_ids": [],
    }
    body.update(overrides)
    return body


async def submit_report(
    client: httpx.AsyncClient, **overrides: object
) -> dict[str, Any]:
    """Submit a report as the reporter and return its detail.

    Args:
        client: The HTTP client.
        **overrides: Members of the body to replace.

    Returns:
        The response body.
    """
    response = await client.post(
        REPORTS, json=report_body(**overrides), headers=reporter_headers()
    )
    assert response.status_code == 201, response.text
    # Any: a decoded JSON response body, the external boundary of these tests.
    body: dict[str, Any] = response.json()
    return body


def etag_of(body: dict[str, Any]) -> str:
    """Return the ETag of a resource from its JSON body.

    Args:
        body: A decoded body with ``id`` and ``version``.

    Returns:
        The strong tag the API sets for it.
    """
    return make_etag(int(body["version"]), UUID(str(body["id"])))


def store_upload(api: ApiHarness, asset_id: str) -> None:
    """Make the fake storage hold a JPEG at the asset's original key.

    Args:
        api: The harness.
        asset_id: The asset the upload was granted for.
    """
    key = original_object_key(UUID(asset_id))
    api.storage.objects[key] = StoredObject(sha256=FAKE_SHA256, byte_size=2048)
    api.mime_sniffer.types[key] = MimeType.JPEG


async def upload_media(
    api: ApiHarness, client: httpx.AsyncClient, path: str = MEDIA
) -> str:
    """Request an upload, store the file and complete it as the reporter.

    Args:
        api: The harness.
        client: The HTTP client.
        path: The grant route.

    Returns:
        The completed asset's id.
    """
    grant = await client.post(
        path, json={"mime_type": "image/jpeg"}, headers=reporter_headers()
    )
    assert grant.status_code == 201, grant.text
    asset_id = str(grant.json()["asset_id"])
    store_upload(api, asset_id)
    completed = await client.post(
        f"{MEDIA}/{asset_id}/complete", headers=reporter_headers()
    )
    assert completed.status_code == 200, completed.text
    return asset_id


async def create_event(
    client: httpx.AsyncClient, report_ids: list[str], **overrides: object
) -> dict[str, Any]:
    """Create an event from reports as the moderator.

    Args:
        client: The HTTP client.
        report_ids: The reports.
        **overrides: Members of the body to replace or add.

    Returns:
        The new event's detail.
    """
    body: dict[str, object] = {
        "report_ids": report_ids,
        "hazard_type": HAZARD_CODE,
        "title": "Outburst flood below the glacier",
    }
    body.update(overrides)
    response = await client.post(
        f"{MODERATION}/events", json=body, headers=moderator_headers()
    )
    assert response.status_code == 201, response.text
    # Any: a decoded JSON response body.
    created: dict[str, Any] = response.json()
    return created


async def event_case_id(client: httpx.AsyncClient, event_id: str) -> str:
    """Return the id of the verification case of an event.

    Args:
        client: The HTTP client.
        event_id: The event.

    Returns:
        The case id.
    """
    response = await client.get(
        CASES, params={"target_kind": "event"}, headers=moderator_headers()
    )
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["target"]["target_id"] == event_id:
            return str(item["id"])
    message = f"no case for event {event_id}"
    raise AssertionError(message)


async def verify(client: httpx.AsyncClient, case_id: str) -> None:
    """Move a case along the table until it is ``verified``.

    Args:
        client: The HTTP client.
        case_id: The case.
    """
    for state in _VERIFY_PATH:
        case = await client.get(f"{CASES}/{case_id}", headers=moderator_headers())
        if case.json()["state"] == state:
            continue
        response = await client.post(
            f"{MODERATION}/verification/{case_id}/transitions",
            json={"to_state": state, "reason": "Checked against the field team."},
            headers=moderator_headers(),
        )
        assert response.status_code == 200, response.text


async def publish_and_verify(client: httpx.AsyncClient, event_id: str) -> None:
    """Publish an event and verify its case, making it public.

    Args:
        client: The HTTP client.
        event_id: The event.
    """
    published = await client.post(
        f"{MODERATION}/events/{event_id}/publication", headers=moderator_headers()
    )
    assert published.status_code == 200, published.text
    await verify(client, await event_case_id(client, event_id))


async def register_source(
    client: httpx.AsyncClient, source_type: str = "government"
) -> str:
    """Register a source as the moderator.

    Args:
        client: The HTTP client.
        source_type: The kind of source.

    Returns:
        The source id.
    """
    response = await client.post(
        f"{MODERATION}/sources",
        json={
            "source_type": source_type,
            "details": {
                "title": "District situation report",
                "citation": "District Disaster Management Authority, report 7",
            },
        },
        headers=moderator_headers(),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])
