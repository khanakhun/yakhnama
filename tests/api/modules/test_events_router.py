"""HTTP tests for the public events record: ``/api/v1/events``."""

import httpx
import pytest

from tests.api.modules.recording import (
    EVENTS,
    EXACT_LATITUDE,
    EXACT_LONGITUDE,
    MODERATION,
    PLACE_CODE,
    ROUNDED_LATITUDE,
    ROUNDED_LONGITUDE,
    create_event,
    etag_of,
    moderator_headers,
    new_client_id,
    publish_and_verify,
    recording_app,
    reporter_headers,
    submit_report,
)


async def _public_event(client: httpx.AsyncClient) -> str:
    report = await submit_report(client)
    event = await create_event(client, [report["id"]])
    await publish_and_verify(client, event["id"])
    return str(event["id"])


async def _draft_event(client: httpx.AsyncClient) -> str:
    report = await submit_report(client)
    event = await create_event(client, [report["id"]])
    return str(event["id"])


async def test_list_events_anonymous_hides_draft_events() -> None:
    api = recording_app()

    async with api.client() as client:
        await _draft_event(client)
        response = await client.get(EVENTS)

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_list_events_as_moderator_includes_draft_events() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _draft_event(client)
        response = await client.get(EVENTS, headers=moderator_headers())

    assert [item["id"] for item in response.json()["items"]] == [event_id]


async def test_list_events_anonymous_shows_published_verified_event_rounded() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _public_event(client)
        response = await client.get(EVENTS)

    items = response.json()["items"]
    assert [item["id"] for item in items] == [event_id]
    assert items[0]["verification_state"] == "verified"
    assert items[0]["centroid"] == {
        "longitude": ROUNDED_LONGITUDE,
        "latitude": ROUNDED_LATITUDE,
    }
    assert str(EXACT_LONGITUDE) not in response.text
    assert response.headers["vary"] == "Accept"


async def test_list_events_anonymous_cannot_switch_off_verified_only() -> None:
    api = recording_app()

    async with api.client() as client:
        await _draft_event(client)
        response = await client.get(EVENTS, params={"verified_only": "false"})

    assert response.json()["items"] == []


async def test_list_events_moderator_verified_only_hides_unverified() -> None:
    api = recording_app()

    async with api.client() as client:
        await _draft_event(client)
        response = await client.get(
            EVENTS, params={"verified_only": "true"}, headers=moderator_headers()
        )

    assert response.json()["items"] == []


async def test_list_events_accepting_geojson_returns_centroid_features() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _public_event(client)
        response = await client.get(EVENTS, headers={"Accept": "application/geo+json"})

    body = response.json()
    assert response.headers["content-type"] == "application/geo+json"
    assert body["type"] == "FeatureCollection"
    feature = body["features"][0]
    assert feature["id"] == event_id
    assert feature["geometry"] == {
        "type": "Point",
        "coordinates": [ROUNDED_LONGITUDE, ROUNDED_LATITUDE],
    }
    assert feature["properties"]["verification_state"] == "verified"


async def test_list_events_walk_with_link_visits_every_event_once() -> None:
    api = recording_app()

    async with api.client() as client:
        created = [await _public_event(client) for _ in range(3)]
        first = await client.get(EVENTS, params={"limit": 2, "format": "geojson"})
        link = first.headers["link"].split(";")[0].strip("<>")
        second = await client.get(link)

    seen = [feature["id"] for feature in first.json()["features"]]
    seen += [feature["id"] for feature in second.json()["features"]]
    assert sorted(seen) == sorted(created)
    assert "format=geojson" in link
    assert "link" not in second.headers


@pytest.mark.parametrize(
    ("parameter", "value", "expected_count"),
    [
        ("hazard_type", "glof", 1),
        ("hazard_type", "landslide", 0),
        ("bbox", "74,36,75,37", 1),
        ("bbox", "10,10,11,11", 0),
        ("status", "published", 1),
        ("status", "draft", 0),
        ("from", "2026-06-01T00:00:00Z", 1),
        ("to", "2026-06-01T00:00:00Z", 0),
        ("place_code", PLACE_CODE, 0),
    ],
)
async def test_list_events_with_filter_returns_matching_events(
    parameter: str, value: str, expected_count: int
) -> None:
    api = recording_app()

    async with api.client() as client:
        await _public_event(client)
        response = await client.get(EVENTS, params={parameter: value})

    assert response.status_code == 200
    assert len(response.json()["items"]) == expected_count


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("limit", "0"),
        ("limit", "201"),
        ("bbox", "75,36,74,37"),
        ("hazard_type", "Not A Code"),
        ("status", "nonsense"),
        ("format", "xml"),
    ],
)
async def test_list_events_with_invalid_parameter_returns_422(
    parameter: str, value: str
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(EVENTS, params={parameter: value})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_list_events_with_reversed_period_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            EVENTS,
            params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        )

    assert response.status_code == 422


async def test_list_events_with_rejected_token_returns_401() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            EVENTS, headers={"Authorization": "Bearer not-a-token"}
        )

    assert response.status_code == 401


async def test_get_event_anonymous_returns_public_event_with_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _public_event(client)
        response = await client.get(f"{EVENTS}/{event_id}")

    body = response.json()
    assert response.status_code == 200
    assert body["id"] == event_id
    assert response.headers["etag"] == etag_of(body)
    assert "created_by" not in body
    assert all("linked_by" not in link for link in body["report_links"])
    assert str(EXACT_LATITUDE) not in response.text


async def test_get_event_anonymous_when_draft_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _draft_event(client)
        response = await client.get(f"{EVENTS}/{event_id}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_get_event_as_moderator_when_draft_returns_detail() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _draft_event(client)
        response = await client.get(f"{EVENTS}/{event_id}", headers=moderator_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "draft"


async def test_get_event_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(f"{EVENTS}/{new_client_id()}")

    assert response.status_code == 404


async def test_get_event_accepting_geojson_returns_feature_at_centroid() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _public_event(client)
        response = await client.get(
            f"{EVENTS}/{event_id}", headers={"Accept": "application/geo+json"}
        )

    body = response.json()
    assert response.headers["content-type"] == "application/geo+json"
    assert body["type"] == "Feature"
    assert body["geometry"]["coordinates"] == [ROUNDED_LONGITUDE, ROUNDED_LATITUDE]
    assert body["properties"]["id"] == event_id
    assert response.headers["etag"]


async def test_get_event_geojson_uses_event_geometry_when_mapped() -> None:
    api = recording_app()
    polygon = [[[74.0, 36.0], [74.1, 36.0], [74.1, 36.1], [74.0, 36.0]]]

    async with api.client() as client:
        event_id = await _public_event(client)
        await client.patch(
            f"{MODERATION}/events/{event_id}",
            json={"geometry": {"type": "Polygon", "coordinates": polygon}},
            headers=moderator_headers(),
        )
        response = await client.get(
            f"{EVENTS}/{event_id}", params={"format": "geojson"}
        )

    assert response.json()["geometry"]["type"] == "Polygon"


async def test_get_event_timeline_anonymous_returns_ordered_entries() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _public_event(client)
        response = await client.get(f"{EVENTS}/{event_id}/timeline")

    body = response.json()
    kinds = [entry["kind"] for entry in body["entries"]]
    assert response.status_code == 200
    assert body["event_id"] == event_id
    assert kinds[:2] == ["report_observed", "event_started"]
    assert "verification_transition" in kinds
    assert "verified" in [entry["label"] for entry in body["entries"]]


async def test_get_event_timeline_anonymous_when_draft_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _draft_event(client)
        response = await client.get(f"{EVENTS}/{event_id}/timeline")

    assert response.status_code == 404


async def test_get_event_as_reporter_when_draft_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _draft_event(client)
        response = await client.get(f"{EVENTS}/{event_id}", headers=reporter_headers())

    assert response.status_code == 404
