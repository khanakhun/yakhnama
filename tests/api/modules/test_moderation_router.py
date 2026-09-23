"""HTTP tests for ``/api/v1/moderation/events/...``."""

from typing import Any
from uuid import UUID

import httpx
import pytest

from tests.api.modules.recording import (
    EVENTS,
    MODERATION,
    PLACE_CODE,
    create_event,
    etag_of,
    moderator_headers,
    new_client_id,
    recording_app,
    reporter_headers,
    submit_report,
)
from yakhnama.platform.etag import make_etag

MODERATION_EVENTS = f"{MODERATION}/events"
REASON = {"reason": "The report describes another valley."}
POLYGON = {
    "type": "Polygon",
    "coordinates": [[[74.0, 36.0], [74.1, 36.0], [74.1, 36.1], [74.0, 36.0]]],
}


async def _event_with_report(client: httpx.AsyncClient) -> tuple[dict[str, Any], str]:
    report = await submit_report(client)
    return await create_event(client, [report["id"]]), str(report["id"])


async def _post(
    client: httpx.AsyncClient, path: str, body: object | None = None
) -> httpx.Response:
    return await client.post(path, json=body, headers=moderator_headers())


async def test_create_event_returns_201_with_location_etag_and_derived_values() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            MODERATION_EVENTS,
            json={
                "report_ids": [report["id"]],
                "hazard_type": "glof",
                "title": "Outburst flood below the glacier",
                "summary": "Two reports of rising water.",
            },
            headers=moderator_headers(),
        )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"] == f"{EVENTS}/{body['id']}"
    assert response.headers["etag"] == etag_of(body)
    assert body["status"] == "draft"
    assert body["report_links"][0]["role"] == "primary"
    assert body["source_ids"] == [report["source_id"]]
    assert len(api.verification.verification_cases.committed) == 1


async def test_create_event_with_matching_attributes_stores_them() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        event = await create_event(
            client,
            [report["id"]],
            attributes={"hazard_type": "glof", "mechanism": "moraine_dam_breach"},
        )

    assert event["attributes"]["mechanism"] == "moraine_dam_breach"


@pytest.mark.parametrize(
    "attributes",
    [
        {"hazard_type": "landslide"},
        {"hazard_type": "glof", "mechanism": "made-up"},
        {"hazard_type": "unregistered"},
        {"mechanism": "moraine_dam_breach"},
    ],
)
async def test_create_event_with_attributes_not_of_its_hazard_type_returns_422(
    attributes: dict[str, object],
) -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await _post(
            client,
            MODERATION_EVENTS,
            {
                "report_ids": [report["id"]],
                "hazard_type": "glof",
                "title": "Outburst flood",
                "attributes": attributes,
            },
        )

    assert response.status_code == 422
    assert "made-up" not in response.text
    assert api.events.events.committed == {}


@pytest.mark.parametrize(
    ("member", "value"),
    [
        ("report_ids", []),
        ("title", "ab"),
        ("title", "x" * 201),
        ("hazard_type", "Not A Code"),
        ("unexpected", 1),
    ],
)
async def test_create_event_with_invalid_member_returns_422(
    member: str, value: object
) -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        body = {"report_ids": [report["id"]], "hazard_type": "glof", "title": "Flood"}
        response = await _post(client, MODERATION_EVENTS, body | {member: value})

    assert response.status_code == 422


async def test_create_event_with_unknown_hazard_type_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await _post(
            client,
            MODERATION_EVENTS,
            {"report_ids": [report["id"]], "hazard_type": "avalanche", "title": "Snow"},
        )

    assert response.status_code == 422


async def test_create_event_with_unknown_report_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await _post(
            client,
            MODERATION_EVENTS,
            {"report_ids": [new_client_id()], "hazard_type": "glof", "title": "Flood"},
        )

    assert response.status_code == 404


async def test_create_event_as_citizen_returns_403_before_reading() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            MODERATION_EVENTS,
            json={"report_ids": [report["id"]], "hazard_type": "glof", "title": "Flo"},
            headers=reporter_headers(),
        )

    assert response.status_code == 403
    assert api.events.events.committed == {}


async def test_create_event_anonymous_returns_401() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(MODERATION_EVENTS, json={})

    assert response.status_code == 401


async def test_link_report_returns_event_with_new_link_and_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        other = await submit_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/reports",
            {"report_id": other["id"], "role": "contradicting"},
        )

    body = response.json()
    assert response.status_code == 200
    assert {link["role"] for link in body["report_links"]} == {
        "primary",
        "contradicting",
    }
    assert response.headers["etag"] == etag_of(body)
    assert body["version"] > event["version"]


async def test_link_report_twice_returns_409() -> None:
    api = recording_app()

    async with api.client() as client:
        event, report_id = await _event_with_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/reports",
            {"report_id": report_id},
        )

    assert response.status_code == 409


async def test_link_report_with_stale_if_match_returns_412() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        other = await submit_report(client)
        response = await client.post(
            f"{MODERATION_EVENTS}/{event['id']}/reports",
            json={"report_id": other["id"]},
            headers=moderator_headers()
            | {"If-Match": make_etag(99, UUID(event["id"]))},
        )

    assert response.status_code == 412
    assert response.json()["type"].endswith("/precondition-failed")


async def test_link_report_with_current_if_match_returns_200() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        other = await submit_report(client)
        response = await client.post(
            f"{MODERATION_EVENTS}/{event['id']}/reports",
            json={"report_id": other["id"]},
            headers=moderator_headers() | {"If-Match": etag_of(event)},
        )

    assert response.status_code == 200


async def test_unlink_report_with_reason_removes_link() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        other = await submit_report(client)
        await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/reports",
            {"report_id": other["id"]},
        )
        response = await client.request(
            "DELETE",
            f"{MODERATION_EVENTS}/{event['id']}/reports/{other['id']}",
            json=REASON,
            headers=moderator_headers(),
        )

    assert response.status_code == 200
    linked = [link["report_id"] for link in response.json()["report_links"]]
    assert other["id"] not in linked


async def test_unlink_report_not_linked_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await client.request(
            "DELETE",
            f"{MODERATION_EVENTS}/{event['id']}/reports/{new_client_id()}",
            json=REASON,
            headers=moderator_headers(),
        )

    assert response.status_code == 404


async def test_unlink_report_without_reason_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        event, report_id = await _event_with_report(client)
        response = await client.request(
            "DELETE",
            f"{MODERATION_EVENTS}/{event['id']}/reports/{report_id}",
            json={},
            headers=moderator_headers(),
        )

    assert response.status_code == 422


async def test_relate_events_returns_event_with_relation() -> None:
    api = recording_app()

    async with api.client() as client:
        first, _ = await _event_with_report(client)
        second, _ = await _event_with_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{first['id']}/relations",
            {"to_event_id": second["id"], "kind": "triggered_by", "note": "Upstream."},
        )

    relations = response.json()["relations"]
    assert response.status_code == 200
    assert relations[0]["to_event_id"] == second["id"]
    assert "related_by" not in relations[0]


async def test_relate_event_to_itself_returns_409() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/relations",
            {"to_event_id": event["id"], "kind": "part_of"},
        )

    assert response.status_code == 409


async def test_update_event_geometry_and_period_returns_new_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await client.patch(
            f"{MODERATION_EVENTS}/{event['id']}",
            json={
                "geometry": POLYGON,
                "period": {
                    "started_at": {"value": "2026-06-30T00:00:00Z", "precision": "day"}
                },
            },
            headers=moderator_headers() | {"If-Match": etag_of(event)},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["geometry"]["type"] == "Polygon"
    assert body["period"]["started_at"]["precision"] == "day"
    assert response.headers["etag"] == etag_of(body)
    assert response.headers["etag"] != etag_of(event)


async def test_update_event_with_null_geometry_removes_it() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        path = f"{MODERATION_EVENTS}/{event['id']}"
        await client.patch(
            path, json={"geometry": POLYGON}, headers=moderator_headers()
        )
        response = await client.patch(
            path, json={"geometry": None}, headers=moderator_headers()
        )

    assert response.status_code == 200
    assert response.json()["geometry"] is None


async def test_update_event_attributes_sets_and_clears_them() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        path = f"{MODERATION_EVENTS}/{event['id']}"
        set_response = await client.patch(
            path,
            json={"attributes": {"hazard_type": "glof", "mechanism": "ice_dam_breach"}},
            headers=moderator_headers(),
        )
        cleared = await client.patch(
            path, json={"attributes": None}, headers=moderator_headers()
        )

    assert set_response.json()["attributes"]["mechanism"] == "ice_dam_breach"
    assert cleared.json()["attributes"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"period": None},
        {"attributes": {"hazard_type": "landslide"}},
        {"geometry": {"type": "Point", "coordinates": [190.0, 36.0]}},
        {"geometry": {"type": "Point", "coordinates": [74.0, 36.0, 10.0]}},
        {"geometry": {"type": "LineString", "coordinates": [[74, 36], [75, 37]]}},
        {"title": "Titles have no command yet"},
    ],
)
async def test_update_event_with_invalid_body_returns_422(
    body: dict[str, object],
) -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await client.patch(
            f"{MODERATION_EVENTS}/{event['id']}", json=body, headers=moderator_headers()
        )

    assert response.status_code == 422


async def test_update_event_with_stale_if_match_returns_412_and_changes_nothing() -> (
    None
):
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await client.patch(
            f"{MODERATION_EVENTS}/{event['id']}",
            json={"geometry": POLYGON},
            headers=moderator_headers()
            | {"If-Match": make_etag(event["version"] + 1, UUID(event["id"]))},
        )
        current = await client.get(
            f"{EVENTS}/{event['id']}", headers=moderator_headers()
        )

    assert response.status_code == 412
    assert current.json()["geometry"] is None


async def test_update_event_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.patch(
            f"{MODERATION_EVENTS}/{new_client_id()}",
            json={"geometry": POLYGON},
            headers=moderator_headers(),
        )

    assert response.status_code == 404


async def test_add_affected_place_with_known_code_lists_it() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/places",
            {"place_code": PLACE_CODE, "kind": "impacted"},
        )
        listed = await client.get(
            EVENTS, params={"place_code": PLACE_CODE}, headers=moderator_headers()
        )

    assert response.status_code == 200
    assert response.json()["affected_places"] == [
        {"place_code": PLACE_CODE, "kind": "impacted"}
    ]
    assert [item["id"] for item in listed.json()["items"]] == [event["id"]]


async def test_add_affected_place_with_unknown_code_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/places",
            {"place_code": "test.place.nowhere", "kind": "origin"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_publish_event_returns_published_event() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await _post(client, f"{MODERATION_EVENTS}/{event['id']}/publication")

    assert response.status_code == 200
    assert response.json()["status"] == "published"


async def test_retract_event_keeps_reason_and_refuses_further_changes() -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await _post(
            client, f"{MODERATION_EVENTS}/{event['id']}/retraction", REASON
        )
        again = await _post(
            client,
            f"{MODERATION_EVENTS}/{event['id']}/places",
            {"place_code": PLACE_CODE, "kind": "origin"},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "retracted"
    assert body["status_reason"] == REASON["reason"]
    assert again.status_code == 409


async def test_merge_events_points_the_merged_event_at_the_survivor() -> None:
    api = recording_app()

    async with api.client() as client:
        merged, _ = await _event_with_report(client)
        survivor, _ = await _event_with_report(client)
        response = await _post(
            client,
            f"{MODERATION_EVENTS}/{merged['id']}/merge",
            {"into_event_id": survivor["id"], "reason": "Same outburst."},
        )

    assert response.status_code == 200
    assert response.json()["merged_into"] == survivor["id"]


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "/reports", {"report_id": "00000000-0000-7000-8000-000000000000"}),
        ("POST", "/relations", {"to_event_id": new_client_id(), "kind": "part_of"}),
        ("PATCH", "", {"geometry": None}),
        ("POST", "/places", {"place_code": PLACE_CODE, "kind": "origin"}),
        ("POST", "/publication", None),
        ("POST", "/retraction", REASON),
        ("POST", "/merge", {"into_event_id": new_client_id(), "reason": "Same."}),
    ],
)
async def test_event_moderation_routes_as_citizen_return_403(
    method: str, suffix: str, body: object
) -> None:
    api = recording_app()

    async with api.client() as client:
        event, _ = await _event_with_report(client)
        response = await client.request(
            method,
            f"{MODERATION_EVENTS}/{event['id']}{suffix}",
            json=body,
            headers=reporter_headers(),
        )

    assert response.status_code == 403
    assert response.json()["type"].endswith("/permission-denied")
