"""The Phase 3 manual gate flow, end to end against fakes, over HTTP only.

A citizen uploads a photo and submits a report with it, attaches a second photo
to the submitted report, a moderator creates an event from the report, links a
second report, records an impact claim from a government source, publishes the
event and verifies it; the event then appears in the anonymous ``GET /events``
with a best figure and as GeoJSON, and no public payload carries the reporter's
exact position or accuracy.
"""

from tests.api.modules.recording import (
    EVENTS,
    EXACT_LATITUDE,
    EXACT_LONGITUDE,
    METRIC_CODE,
    MODERATION,
    REPORTS,
    ROUNDED_LATITUDE,
    ROUNDED_LONGITUDE,
    create_event,
    event_case_id,
    moderator_headers,
    other_headers,
    recording_app,
    register_source,
    report_body,
    submit_report,
    upload_media,
    verify,
)


async def test_gate_flow_from_report_with_media_to_public_verified_event() -> None:
    api = recording_app()

    async with api.client() as client:
        photo_id = await upload_media(api, client)
        report = await submit_report(client, media_ids=[photo_id])
        second_photo_id = await upload_media(
            api, client, path=f"{REPORTS}/{report['id']}/media"
        )
        corroborating = await client.post(
            REPORTS,
            json=report_body(description="Bridge at the nala washed away today."),
            headers=other_headers(),
        )
        event = await create_event(client, [report["id"]])
        linked = await client.post(
            f"{MODERATION}/events/{event['id']}/reports",
            json={"report_id": corroborating.json()["id"], "role": "supporting"},
            headers=moderator_headers(),
        )
        source_id = await register_source(client)
        claim = await client.post(
            f"{MODERATION}/events/{event['id']}/impact-claims",
            json={
                "metric_code": METRIC_CODE,
                "value": {"kind": "count", "count": 2},
                "confidence": "medium",
                "source_id": source_id,
                "claimed_at": {"value": "2026-07-02T00:00:00Z", "precision": "day"},
            },
            headers=moderator_headers(),
        )
        hidden = await client.get(EVENTS)
        published = await client.post(
            f"{MODERATION}/events/{event['id']}/publication",
            headers=moderator_headers(),
        )
        await verify(client, await event_case_id(client, event["id"]))
        listing = await client.get(EVENTS)
        geojson = await client.get(EVENTS, headers={"Accept": "application/geo+json"})
        impacts = await client.get(f"{EVENTS}/{event['id']}/impacts")
        detail = await client.get(f"{EVENTS}/{event['id']}")
        timeline = await client.get(f"{EVENTS}/{event['id']}/timeline")

    assert report["media_ids"] == [photo_id]
    assert second_photo_id != photo_id
    assert corroborating.status_code == 201
    assert linked.status_code == 200
    assert claim.status_code == 201
    assert hidden.json()["items"] == []
    assert published.json()["status"] == "published"
    assert [item["id"] for item in listing.json()["items"]] == [event["id"]]
    assert listing.json()["items"][0]["verification_state"] == "verified"
    feature = geojson.json()["features"][0]
    assert feature["geometry"]["coordinates"] == [ROUNDED_LONGITUDE, ROUNDED_LATITUDE]
    best = impacts.json()["best_figures"][0]
    assert best["metric"]["code"] == METRIC_CODE
    assert best["value"]["count"] == 2
    assert len(detail.json()["report_links"]) == 2
    assert timeline.status_code == 200
    for public in (listing, geojson, impacts, detail, timeline):
        assert str(EXACT_LONGITUDE) not in public.text
        assert str(EXACT_LATITUDE) not in public.text
        assert "accuracy" not in public.text
