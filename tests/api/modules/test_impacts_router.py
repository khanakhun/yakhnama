"""HTTP tests for ``/api/v1/impact-metrics``, event impacts and claim moderation."""

import httpx
import pytest

from tests.api.modules.recording import (
    EVENTS,
    METRIC_CODE,
    MODERATION,
    create_event,
    moderator_headers,
    new_client_id,
    publish_and_verify,
    recording_app,
    register_source,
    reporter_headers,
    submit_report,
)
from tests.factories.impacts import ImpactMetricTestFactory
from tests.fakes.api import build_test_app
from yakhnama.modules.impacts.public import MetricCategory
from yakhnama.platform.etag import make_etag

IMPACT_METRICS = "/api/v1/impact-metrics"


async def test_list_impact_metrics_anonymous_returns_metrics_by_code() -> None:
    api = build_test_app(
        impact_metrics=[
            ImpactMetricTestFactory.build(code="deaths"),
            ImpactMetricTestFactory.build(code="bridges_destroyed"),
        ]
    )

    async with api.client() as client:
        response = await client.get(IMPACT_METRICS)

    assert response.status_code == 200
    codes = [item["code"] for item in response.json()["items"]]
    assert codes == ["bridges_destroyed", "deaths"]


async def test_list_impact_metrics_by_category_returns_only_that_category() -> None:
    categories = list(MetricCategory)
    wanted, other = categories[0], categories[1]
    api = build_test_app(
        impact_metrics=[
            ImpactMetricTestFactory.build(code="metric_a", category=wanted),
            ImpactMetricTestFactory.build(code="metric_b", category=other),
        ]
    )

    async with api.client() as client:
        response = await client.get(IMPACT_METRICS, params={"category": wanted.value})

    assert [item["code"] for item in response.json()["items"]] == ["metric_a"]


async def test_list_impact_metrics_with_unknown_category_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(IMPACT_METRICS, params={"category": "nonsense"})

    assert response.status_code == 422
    assert "nonsense" not in response.text


async def test_list_impact_metrics_walk_with_cursor_follows_link() -> None:
    api = build_test_app(
        impact_metrics=[
            ImpactMetricTestFactory.build(code=f"metric_{index}") for index in range(3)
        ]
    )

    async with api.client() as client:
        first = await client.get(IMPACT_METRICS, params={"limit": 2})
        link = first.headers["link"].split(";")[0].strip("<>")
        second = await client.get(link)

    seen = [item["code"] for page in (first, second) for item in page.json()["items"]]
    assert seen == ["metric_0", "metric_1", "metric_2"]
    assert "limit=2" in link
    assert second.json()["next_cursor"] is None


async def test_get_impact_metric_anonymous_returns_detail_with_etag() -> None:
    metric = ImpactMetricTestFactory.build(code="deaths")
    api = build_test_app(impact_metrics=[metric])

    async with api.client() as client:
        response = await client.get(f"{IMPACT_METRICS}/deaths")

    assert response.status_code == 200
    assert response.json()["id"] == str(metric.id)
    assert response.headers["etag"] == make_etag(metric.version, metric.id)


async def test_get_impact_metric_when_missing_returns_404_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{IMPACT_METRICS}/unknown_metric")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


# --------------------------------------------------------------------------- #
# Impact claims, infrastructure assets and damage (Phase 3)                   #
# --------------------------------------------------------------------------- #

PRIVATE_NOTE = "Private moderator note about the caller."
RETRACTION_REASON = "The figure double-counted a neighbouring village."
PRIVATE_FIELDS = ("recorded_by", "retracted_by", "retraction_reason", "note")


def _claim_body(source_id: str, count: int = 3) -> dict[str, object]:
    return {
        "metric_code": METRIC_CODE,
        "value": {"kind": "count", "count": count},
        "confidence": "high",
        "source_id": source_id,
        "claimed_at": {"value": "2026-07-02T00:00:00Z", "precision": "day"},
        "note": PRIVATE_NOTE,
    }


async def _event(client: httpx.AsyncClient, *, is_public: bool) -> str:
    report = await submit_report(client)
    event = await create_event(client, [report["id"]])
    if is_public:
        await publish_and_verify(client, event["id"])
    return str(event["id"])


async def _record(
    client: httpx.AsyncClient, event_id: str, source_id: str, count: int = 3
) -> httpx.Response:
    return await client.post(
        f"{MODERATION}/events/{event_id}/impact-claims",
        json=_claim_body(source_id, count),
        headers=moderator_headers(),
    )


async def test_record_impact_claim_returns_201_public_view() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=False)
        source_id = await register_source(client)
        response = await _record(client, event_id, source_id)

    body = response.json()
    assert response.status_code == 201
    assert body["event_id"] == event_id
    assert body["source_type"] == "government"
    assert body["value"] == {"kind": "count", "count": 3}
    assert not set(PRIVATE_FIELDS) & set(body)
    assert PRIVATE_NOTE not in response.text


async def test_get_event_impacts_anonymous_hides_private_claim_fields() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=True)
        source_id = await register_source(client)
        await _record(client, event_id, source_id)
        response = await client.get(f"{EVENTS}/{event_id}/impacts")

    body = response.json()
    assert response.status_code == 200
    assert body["best_figures"][0]["metric"]["code"] == METRIC_CODE
    assert body["best_figures"][0]["value"]["count"] == 3
    for claim in body["claims"]:
        assert not set(PRIVATE_FIELDS) & set(claim)
    assert PRIVATE_NOTE not in response.text


async def test_get_event_impacts_anonymous_when_event_hidden_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=False)
        response = await client.get(f"{EVENTS}/{event_id}/impacts")

    assert response.status_code == 404


async def test_retract_impact_claim_returns_204_and_hides_the_reason() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=True)
        source_id = await register_source(client)
        claim = (await _record(client, event_id, source_id)).json()
        response = await client.post(
            f"{MODERATION}/impact-claims/{claim['id']}/retraction",
            json={"reason": RETRACTION_REASON},
            headers=moderator_headers(),
        )
        impacts = await client.get(f"{EVENTS}/{event_id}/impacts")

    assert response.status_code == 204
    assert impacts.json()["claims"][0]["status"] == "retracted"
    assert impacts.json()["best_figures"][0]["value"] is None
    assert RETRACTION_REASON not in impacts.text


async def test_retract_impact_claim_twice_returns_409() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=False)
        source_id = await register_source(client)
        claim = (await _record(client, event_id, source_id)).json()
        path = f"{MODERATION}/impact-claims/{claim['id']}/retraction"
        await client.post(
            path, json={"reason": RETRACTION_REASON}, headers=moderator_headers()
        )
        response = await client.post(
            path, json={"reason": RETRACTION_REASON}, headers=moderator_headers()
        )

    assert response.status_code == 409


async def test_correct_impact_claim_returns_201_new_claim_superseding_old() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=True)
        source_id = await register_source(client)
        claim = (await _record(client, event_id, source_id)).json()
        response = await client.post(
            f"{MODERATION}/impact-claims/{claim['id']}/correction",
            json={"value": {"kind": "count", "count": 4}, "reason": RETRACTION_REASON},
            headers=moderator_headers(),
        )
        impacts = await client.get(f"{EVENTS}/{event_id}/impacts")

    new_id = response.json()["id"]
    claims = {item["id"]: item for item in impacts.json()["claims"]}
    assert response.status_code == 201
    assert claims[new_id]["supersedes_id"] == claim["id"]
    assert claims[claim["id"]]["status"] == "retracted"
    assert impacts.json()["best_figures"][0]["value"]["count"] == 4


async def test_record_impact_claim_with_unknown_source_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=False)
        response = await _record(client, event_id, new_client_id())

    assert response.status_code == 404


async def test_record_impact_claim_for_unknown_event_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        source_id = await register_source(client)
        response = await _record(client, new_client_id(), source_id)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("member", "value"),
    [
        ("value", {"kind": "count", "count": -1}),
        ("value", {"kind": "monetary", "amount": "10", "currency": "PKR"}),
        ("metric_code", "Not A Code"),
        ("confidence", "certain"),
        ("claimed_at", {"value": "2026-07-02T00:00:00", "precision": "day"}),
        ("recorded_by", new_client_id()),
    ],
)
async def test_record_impact_claim_with_invalid_member_returns_422(
    member: str, value: object
) -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=False)
        source_id = await register_source(client)
        response = await client.post(
            f"{MODERATION}/events/{event_id}/impact-claims",
            json=_claim_body(source_id) | {member: value},
            headers=moderator_headers(),
        )

    assert response.status_code == 422
    assert PRIVATE_NOTE not in response.text


@pytest.mark.parametrize(
    "path",
    [
        f"{MODERATION}/events/{new_client_id()}/impact-claims",
        f"{MODERATION}/impact-claims/{new_client_id()}/retraction",
        f"{MODERATION}/impact-claims/{new_client_id()}/correction",
        f"{MODERATION}/infrastructure-assets",
        f"{MODERATION}/events/{new_client_id()}/damage-records",
    ],
)
async def test_claim_moderation_routes_as_citizen_return_403(path: str) -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(path, json={}, headers=reporter_headers())

    # The moderator check runs before the body is validated, so nothing leaks.
    assert response.status_code == 403
    assert api.impacts.impact_claims.committed == {}


async def test_register_infrastructure_asset_returns_201_readable_publicly() -> None:
    api = recording_app()

    async with api.client() as client:
        source_id = await register_source(client)
        response = await client.post(
            f"{MODERATION}/infrastructure-assets",
            json={
                "kind": "bridge",
                "name": "Suspension bridge at the nala",
                "source_id": source_id,
                "osm_id": "way/123456",
                "location": {"longitude": 74.6, "latitude": 36.3},
            },
            headers=moderator_headers(),
        )
        public = await client.get(response.headers["location"])

    assert response.status_code == 201
    assert public.status_code == 200
    assert public.json()["osm_id"] == "way/123456"
    assert public.headers["etag"] == response.headers["etag"]


async def test_get_infrastructure_asset_when_missing_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.get(f"/api/v1/infrastructure-assets/{new_client_id()}")

    assert response.status_code == 404


async def test_record_damage_returns_201_with_record_id() -> None:
    api = recording_app()

    async with api.client() as client:
        event_id = await _event(client, is_public=False)
        source_id = await register_source(client)
        asset = await client.post(
            f"{MODERATION}/infrastructure-assets",
            json={"kind": "bridge", "name": "Footbridge", "source_id": source_id},
            headers=moderator_headers(),
        )
        response = await client.post(
            f"{MODERATION}/events/{event_id}/damage-records",
            json={
                "asset_id": asset.json()["id"],
                "level": "destroyed",
                "confidence": "medium",
                "source_id": source_id,
                "recorded_at": {"value": "2026-07-02T00:00:00Z", "precision": "day"},
            },
            headers=moderator_headers(),
        )

    assert response.status_code == 201
    assert set(response.json()) == {"id"}


async def test_get_event_impacts_as_citizen_follows_the_public_rule() -> None:
    api = recording_app()

    async with api.client() as client:
        hidden = await _event(client, is_public=False)
        public = await _event(client, is_public=True)
        hidden_response = await client.get(
            f"{EVENTS}/{hidden}/impacts", headers=reporter_headers()
        )
        public_response = await client.get(
            f"{EVENTS}/{public}/impacts", headers=reporter_headers()
        )

    assert hidden_response.status_code == 404
    assert public_response.status_code == 200
