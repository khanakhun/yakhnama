"""HTTP tests for ``/api/v1/reports``: submission, privacy, paging and changes."""

from uuid import UUID, uuid4

import pytest

from tests.api.modules.recording import (
    EXACT_LATITUDE,
    EXACT_LONGITUDE,
    MODERATION,
    REPORTS,
    ROUNDED_LATITUDE,
    ROUNDED_LONGITUDE,
    etag_of,
    moderator_headers,
    new_client_id,
    other_headers,
    recording_app,
    report_body,
    reporter_headers,
    submit_report,
)
from yakhnama.modules.reports.public import RUN_TRIAGE_TASK
from yakhnama.platform.etag import make_etag

# A right-to-left override: refused by the kernel safe-text type.
UNSAFE_DESCRIPTION = "Water" + chr(0x202E) + "rising"
ECHO_PROBE = "call-me-on-0300-1234567"


async def test_submit_report_returns_201_with_exact_view_location_and_etag() -> None:
    api = recording_app()
    client_id = new_client_id()

    async with api.client() as client:
        response = await client.post(
            REPORTS,
            json=report_body(client_report_id=client_id),
            headers=reporter_headers(),
        )

    body = response.json()
    assert response.status_code == 201
    assert body["id"] == client_id
    assert response.headers["location"] == f"{REPORTS}/{client_id}"
    assert response.headers["etag"] == make_etag(body["version"], body["id"])
    assert body["coordinates_are_exact"] is True
    assert body["coordinates"] == {
        "longitude": EXACT_LONGITUDE,
        "latitude": EXACT_LATITUDE,
    }
    assert body["accuracy"]["value"] == 12.5
    assert len(api.task_queue.of(RUN_TRIAGE_TASK)) == 1


async def test_submit_report_twice_with_same_client_id_returns_201_same_report() -> (
    None
):
    api = recording_app()
    body = report_body()

    async with api.client() as client:
        first = await client.post(REPORTS, json=body, headers=reporter_headers())
        second = await client.post(REPORTS, json=body, headers=reporter_headers())

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert len(api.reports.reports.committed) == 1
    assert len(api.task_queue.of(RUN_TRIAGE_TASK)) == 1


async def test_submit_report_replayed_with_idempotency_key_marks_replay() -> None:
    api = recording_app()
    body = report_body()
    headers = reporter_headers() | {"Idempotency-Key": str(uuid4())}

    async with api.client() as client:
        first = await client.post(REPORTS, json=body, headers=headers)
        second = await client.post(REPORTS, json=body, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()


async def test_submit_report_same_client_id_other_content_returns_409() -> None:
    api = recording_app()
    client_id = new_client_id()

    async with api.client() as client:
        await client.post(
            REPORTS,
            json=report_body(client_report_id=client_id),
            headers=reporter_headers(),
        )
        response = await client.post(
            REPORTS,
            json=report_body(client_report_id=client_id, description="Other text."),
            headers=reporter_headers(),
        )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_submit_report_anonymous_returns_401_problem() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(REPORTS, json=report_body())

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert api.reports.reports.committed == {}


@pytest.mark.parametrize(
    ("member", "value"),
    [
        ("coordinates", {"longitude": 180.5, "latitude": 36.0}),
        ("coordinates", {"longitude": 74.0, "latitude": -91.0}),
        ("accuracy_metres", -1.0),
        ("description", "x" * 4001),
        ("description", UNSAFE_DESCRIPTION),
        ("observed_at", {"value": "2026-07-01T06:00:00", "precision": "hour"}),
        ("client_report_id", str(uuid4())),
        ("media_ids", [new_client_id()] * 21),
        ("unexpected", ECHO_PROBE),
    ],
)
async def test_submit_report_with_invalid_member_returns_422_without_echo(
    member: str, value: object
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            REPORTS, json=report_body(**{member: value}), headers=reporter_headers()
        )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/validation-error")
    assert ECHO_PROBE not in response.text
    assert UNSAFE_DESCRIPTION not in response.text
    assert api.reports.reports.committed == {}


async def test_submit_report_with_nul_character_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            REPORTS,
            json=report_body(description="Water\u0000rising"),
            headers=reporter_headers(),
        )

    assert response.status_code == 422
    assert api.reports.reports.committed == {}


async def test_submit_report_with_someone_elses_media_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        grant = await client.post(
            "/api/v1/media", json={"mime_type": "image/jpeg"}, headers=other_headers()
        )
        response = await client.post(
            REPORTS,
            json=report_body(media_ids=[grant.json()["asset_id"]]),
            headers=reporter_headers(),
        )

    assert response.status_code == 403
    assert api.reports.reports.committed == {}


async def test_get_report_as_reporter_returns_exact_view_with_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.get(
            f"{REPORTS}/{created['id']}", headers=reporter_headers()
        )

    body = response.json()
    assert response.status_code == 200
    assert body["coordinates_are_exact"] is True
    assert body["coordinates"]["longitude"] == EXACT_LONGITUDE
    assert response.headers["etag"] == make_etag(body["version"], body["id"])
    assert response.headers["cache-control"] == "no-store"


async def test_get_report_as_moderator_returns_exact_view_with_triage_member() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.get(
            f"{REPORTS}/{created['id']}", headers=moderator_headers()
        )

    body = response.json()
    assert response.status_code == 200
    assert body["coordinates_are_exact"] is True
    assert "triage" in body


async def test_get_report_as_other_user_returns_404_without_coordinates() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.get(
            f"{REPORTS}/{created['id']}", headers=other_headers()
        )

    assert response.status_code == 404
    assert str(EXACT_LONGITUDE) not in response.text
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_get_report_anonymous_returns_401() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.get(f"{REPORTS}/{created['id']}")

    assert response.status_code == 401
    assert str(EXACT_LATITUDE) not in response.text


async def test_get_report_with_non_uuid7_id_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(f"{REPORTS}/{uuid4()}", headers=reporter_headers())

    assert response.status_code == 422


async def test_list_reports_as_reporter_returns_only_own_rounded_summaries() -> None:
    api = recording_app()

    async with api.client() as client:
        own = await submit_report(client)
        await client.post(REPORTS, json=report_body(), headers=other_headers())
        response = await client.get(REPORTS, headers=reporter_headers())

    items = response.json()["items"]
    assert response.status_code == 200
    assert [item["id"] for item in items] == [own["id"]]
    assert items[0]["coordinates"] == {
        "longitude": ROUNDED_LONGITUDE,
        "latitude": ROUNDED_LATITUDE,
    }
    assert "accuracy" not in items[0]
    assert str(EXACT_LONGITUDE) not in response.text
    assert response.headers["vary"] == "Accept"


async def test_list_reports_as_moderator_returns_every_report_rounded() -> None:
    api = recording_app()

    async with api.client() as client:
        await submit_report(client)
        await client.post(REPORTS, json=report_body(), headers=other_headers())
        response = await client.get(REPORTS, headers=moderator_headers())

    assert len(response.json()["items"]) == 2
    assert str(EXACT_LATITUDE) not in response.text


async def test_list_reports_walk_with_link_visits_every_report_once() -> None:
    api = recording_app()

    async with api.client() as client:
        created = [(await submit_report(client))["id"] for _ in range(3)]
        first = await client.get(
            REPORTS, params={"limit": 2}, headers=reporter_headers()
        )
        link = first.headers["link"].split(";")[0].strip("<>")
        second = await client.get(link, headers=reporter_headers())

    seen = [item["id"] for page in (first, second) for item in page.json()["items"]]
    assert sorted(seen) == sorted(created)
    assert second.json()["next_cursor"] is None
    assert "link" not in second.headers


async def test_list_reports_accepting_geojson_returns_rounded_feature_collection() -> (
    None
):
    api = recording_app()

    async with api.client() as client:
        await submit_report(client)
        response = await client.get(
            REPORTS,
            headers=reporter_headers() | {"Accept": "application/geo+json"},
        )

    body = response.json()
    assert response.headers["content-type"] == "application/geo+json"
    assert body["type"] == "FeatureCollection"
    assert body["features"][0]["geometry"]["coordinates"] == [
        ROUNDED_LONGITUDE,
        ROUNDED_LATITUDE,
    ]
    assert "accuracy" not in body["features"][0]["properties"]
    assert str(EXACT_LONGITUDE) not in response.text


async def test_list_reports_format_json_wins_over_geojson_accept() -> None:
    api = recording_app()

    async with api.client() as client:
        await submit_report(client)
        response = await client.get(
            REPORTS,
            params={"format": "json"},
            headers=reporter_headers() | {"Accept": "application/geo+json"},
        )

    assert response.headers["content-type"].startswith("application/json")
    assert "items" in response.json()


@pytest.mark.parametrize(
    ("parameter", "value", "expected_count"),
    [
        ("bbox", "74.6,36.3,74.7,36.4", 1),
        ("bbox", "10,10,11,11", 0),
        ("status", "submitted", 1),
        ("status", "withdrawn", 0),
        ("hazard_code", "glof", 1),
        ("hazard_code", "landslide", 0),
        ("from", "2026-06-01T00:00:00Z", 1),
        ("to", "2026-06-01T00:00:00Z", 0),
    ],
)
async def test_list_reports_with_filter_returns_matching_reports(
    parameter: str, value: str, expected_count: int
) -> None:
    api = recording_app()

    async with api.client() as client:
        await submit_report(client)
        response = await client.get(
            REPORTS, params={parameter: value}, headers=reporter_headers()
        )

    assert response.status_code == 200
    assert len(response.json()["items"]) == expected_count


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("limit", "201"),
        ("bbox", "74.7,36.3,74.6,36.4"),
        ("bbox", "200,0,201,1"),
        ("bbox", "not-a-box"),
        ("status", "nonsense"),
        ("cursor", "x" * 1025),
    ],
)
async def test_list_reports_with_invalid_parameter_returns_422(
    parameter: str, value: str
) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            REPORTS, params={parameter: value}, headers=reporter_headers()
        )

    assert response.status_code == 422
    assert value not in response.text


async def test_list_reports_with_reversed_time_range_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            REPORTS,
            params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
            headers=reporter_headers(),
        )

    assert response.status_code == 422


async def test_list_reports_with_forged_cursor_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(
            REPORTS, params={"cursor": "bm90LWEtY3Vyc29y"}, headers=reporter_headers()
        )

    assert response.status_code == 422


def _revision_body() -> dict[str, object]:
    body = report_body(description="Corrected text.")
    del body["client_report_id"]
    return body


async def test_revise_report_with_current_etag_returns_201_new_revision() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/revisions",
            json=_revision_body(),
            headers=reporter_headers() | {"If-Match": etag_of(created)},
        )

    body = response.json()
    assert response.status_code == 201
    assert body["id"] != created["id"]
    assert body["supersedes_id"] == created["id"]
    assert body["revision"] == 2
    assert response.headers["location"] == f"{REPORTS}/{body['id']}"


async def test_revise_report_rejects_client_report_id_member() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/revisions",
            json=report_body(),
            headers=reporter_headers() | {"If-Match": etag_of(created)},
        )

    assert response.status_code == 422


async def test_revise_report_without_if_match_returns_428() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/revisions",
            json=_revision_body(),
            headers=reporter_headers(),
        )

    assert response.status_code == 428
    assert len(api.reports.reports.committed) == 1


async def test_revise_report_with_stale_etag_returns_412() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/revisions",
            json=_revision_body(),
            headers=reporter_headers()
            | {"If-Match": make_etag(99, UUID(created["id"]))},
        )

    assert response.status_code == 412


async def test_revise_report_by_other_user_returns_403() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/revisions",
            json=_revision_body(),
            headers=other_headers() | {"If-Match": etag_of(created)},
        )

    assert response.status_code == 403


async def test_withdraw_report_with_reason_returns_withdrawn_detail() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/withdrawal",
            json={"reason": "Sent by mistake."},
            headers=reporter_headers() | {"If-Match": etag_of(created)},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "withdrawn"
    assert body["withdrawal_reason"] == "Sent by mistake."
    assert response.headers["etag"] == make_etag(body["version"], body["id"])


async def test_withdraw_report_when_missing_returns_404() -> None:
    api = recording_app()
    missing = new_client_id()

    async with api.client() as client:
        response = await client.post(
            f"{REPORTS}/{missing}/withdrawal",
            json={"reason": "Sent by mistake."},
            headers=reporter_headers() | {"If-Match": make_etag(1, UUID(missing))},
        )

    assert response.status_code == 404


async def test_withdraw_report_without_reason_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        created = await submit_report(client)
        response = await client.post(
            f"{REPORTS}/{created['id']}/withdrawal",
            json={},
            headers=reporter_headers() | {"If-Match": etag_of(created)},
        )

    assert response.status_code == 422


async def test_moderation_ping_still_answers_for_moderators() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(f"{MODERATION}/ping", headers=moderator_headers())

    assert response.status_code == 200
