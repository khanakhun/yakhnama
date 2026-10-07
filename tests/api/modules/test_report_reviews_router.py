"""HTTP tests for the moderators' report review marks and the new report filters.

``/api/v1/moderation/reports/{id}/review`` (GET and POST),
``/api/v1/moderation/reports/review`` (bulk), the ``review_state`` and
``reporter=me`` filters of ``GET /api/v1/reports`` and the moderator-only
``review`` and ``linked_events`` members (ADR 0022).
"""

from typing import Any, Final
from uuid import UUID

import pytest

from tests.api.modules.recording import (
    MODERATION,
    REPORTS,
    moderator_headers,
    new_client_id,
    other_headers,
    recording_app,
    reporter_headers,
    submit_report,
)
from tests.fakes.api import ApiHarness, auth_headers
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.reports import InMemoryReportQueryService
from yakhnama.modules.reports.domain.value_objects import TriageFlagKind
from yakhnama.modules.reports.public import (
    LinkedEvent,
    ReportRecord,
    ReportStatus,
    TriageFlag,
    TriageResult,
)
from yakhnama.platform.etag import make_etag
from yakhnama.shared_kernel.value_objects import Confidence

REVIEWS: Final = f"{MODERATION}/reports"
ARCHIVE: Final = {"state": "archived", "reason": "Test submission by a volunteer."}
EVENT_IDS: Final = SequentialIdGenerator(seed=2401)


def review_path(report_id: str) -> str:
    """Return the review route of one report."""
    return f"{REVIEWS}/{report_id}/review"


async def test_get_review_of_unmarked_report_is_new_with_version_zero_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.get(
            review_path(report["id"]), headers=moderator_headers()
        )

    body = response.json()
    assert response.status_code == 200
    assert (body["state"], body["version"], body["history"]) == ("new", 0, [])
    assert body["lineage_id"] == report["id"]
    assert response.headers["etag"] == make_etag(0, report["id"])


async def test_mark_review_archives_and_returns_review_with_history_and_etag() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            review_path(report["id"]), json=ARCHIVE, headers=moderator_headers()
        )
        after = await client.get(
            f"{REPORTS}/{report['id']}", headers=reporter_headers()
        )

    body = response.json()
    assert response.status_code == 200
    assert (body["state"], body["version"], body["reviewed_revision"]) == (
        "archived",
        1,
        1,
    )
    assert body["history"][0]["reason"] == ARCHIVE["reason"]
    assert response.headers["etag"] == make_etag(1, report["id"])
    # The report is untouched: same version, same status, no mark for the reporter.
    assert after.json()["version"] == report["version"]
    assert after.json()["status"] == ReportStatus.SUBMITTED.value
    assert after.json()["review"] is None


async def test_mark_review_archive_without_reason_returns_422() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            review_path(report["id"]),
            json={"state": "archived"},
            headers=moderator_headers(),
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert api.reports.report_reviews.committed == {}


async def test_mark_review_with_matching_if_match_succeeds_and_stale_one_is_412() -> (
    None
):
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        first = await client.post(
            review_path(report["id"]),
            json={"state": "reviewed"},
            headers=moderator_headers() | {"If-Match": make_etag(0, report["id"])},
        )
        stale = await client.post(
            review_path(report["id"]),
            json=ARCHIVE,
            headers=moderator_headers() | {"If-Match": make_etag(0, report["id"])},
        )
        foreign = await client.post(
            review_path(report["id"]),
            json=ARCHIVE,
            headers=moderator_headers()
            | {"If-Match": make_etag(1, UUID(new_client_id()))},
        )

    assert first.status_code == 200
    assert (stale.status_code, foreign.status_code) == (412, 412)
    assert (
        api.reports.report_reviews.committed[
            next(iter(api.reports.report_reviews.committed))
        ].version
        == 1
    )


async def test_review_routes_refuse_citizens_and_anonymous_callers() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        as_reporter = await client.post(
            review_path(report["id"]), json=ARCHIVE, headers=reporter_headers()
        )
        read_as_reporter = await client.get(
            review_path(report["id"]), headers=reporter_headers()
        )
        anonymous = await client.get(review_path(report["id"]))
        bulk_as_reporter = await client.post(
            f"{REVIEWS}/review",
            json={"report_ids": [report["id"]], "state": "reviewed"},
            headers=reporter_headers(),
        )

    assert (as_reporter.status_code, read_as_reporter.status_code) == (403, 403)
    assert (anonymous.status_code, bulk_as_reporter.status_code) == (401, 403)


async def test_mark_review_of_unknown_report_returns_404() -> None:
    api = recording_app()

    async with api.client() as client:
        response = await client.post(
            review_path(new_client_id()),
            json={"state": "reviewed"},
            headers=moderator_headers(),
        )

    assert response.status_code == 404


async def test_bulk_review_reports_per_item_outcomes() -> None:
    api = recording_app()
    missing = new_client_id()

    async with api.client() as client:
        first = await submit_report(client)
        second = await submit_report(client)
        response = await client.post(
            f"{REVIEWS}/review",
            json={
                "report_ids": [first["id"], missing, second["id"]],
                "state": "archived",
                "reason": "Spam wave.",
            },
            headers=moderator_headers(),
        )

    body = response.json()
    assert response.status_code == 200
    assert [(item["report_id"], item["outcome"]) for item in body["items"]] == [
        (first["id"], "marked"),
        (missing, "not_found"),
        (second["id"], "marked"),
    ]


async def test_bulk_review_refuses_repeated_or_too_many_ids() -> None:
    api = recording_app()
    report_id = new_client_id()

    async with api.client() as client:
        repeated = await client.post(
            f"{REVIEWS}/review",
            json={"report_ids": [report_id, report_id], "state": "reviewed"},
            headers=moderator_headers(),
        )
        too_many = await client.post(
            f"{REVIEWS}/review",
            json={
                "report_ids": [new_client_id() for _ in range(101)],
                "state": "reviewed",
            },
            headers=moderator_headers(),
        )

    assert (repeated.status_code, too_many.status_code) == (422, 422)


async def test_list_reports_review_state_filter_for_moderator_and_403_otherwise() -> (
    None
):
    api = recording_app()

    async with api.client() as client:
        archived = await submit_report(client)
        fresh = await submit_report(client)
        await client.post(
            review_path(archived["id"]), json=ARCHIVE, headers=moderator_headers()
        )
        archived_page = await client.get(
            REPORTS, params={"review_state": "archived"}, headers=moderator_headers()
        )
        new_page = await client.get(
            REPORTS, params={"review_state": "new"}, headers=moderator_headers()
        )
        as_reporter = await client.get(
            REPORTS, params={"review_state": "new"}, headers=reporter_headers()
        )

    archived_items = archived_page.json()["items"]
    assert [item["id"] for item in archived_items] == [archived["id"]]
    assert archived_items[0]["review"]["state"] == "archived"
    assert [item["id"] for item in new_page.json()["items"]] == [fresh["id"]]
    assert as_reporter.status_code == 403


async def test_list_reports_hides_review_from_reporter() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        await client.post(
            review_path(report["id"]), json=ARCHIVE, headers=moderator_headers()
        )
        page = await client.get(REPORTS, headers=reporter_headers())

    assert page.json()["items"][0]["review"] is None


async def test_list_reports_reporter_me_narrows_moderator_to_own_reports() -> None:
    api = recording_app()
    own_body: dict[str, Any] = {
        "client_report_id": new_client_id(),
        "observed_at": {"value": "2026-07-01T06:00:00Z", "precision": "hour"},
        "coordinates": {"longitude": 74.6, "latitude": 36.3},
        "description": "Seen from the road.",
        "original_language": "en",
    }

    async with api.client() as client:
        await submit_report(client)
        own = await client.post(REPORTS, json=own_body, headers=moderator_headers())
        everything = await client.get(REPORTS, headers=moderator_headers())
        mine = await client.get(
            REPORTS, params={"reporter": "me"}, headers=moderator_headers()
        )
        invalid = await client.get(
            REPORTS, params={"reporter": "someone"}, headers=moderator_headers()
        )

    assert own.status_code == 201
    assert len(everything.json()["items"]) == 2
    assert [item["id"] for item in mine.json()["items"]] == [own.json()["id"]]
    assert invalid.status_code == 422


async def test_list_reports_reporter_me_next_link_keeps_the_filter() -> None:
    api = recording_app()

    async with api.client() as client:
        await submit_report(client)
        await submit_report(client)
        page = await client.get(
            REPORTS, params={"reporter": "me", "limit": 1}, headers=reporter_headers()
        )

    assert "reporter=me" in page.headers["link"]


async def test_get_report_shows_review_and_linked_events_to_moderator_only() -> None:
    api = recording_app()
    reads = api.app.state.container.report_query_service
    assert isinstance(reads, InMemoryReportQueryService)

    async with api.client() as client:
        report = await submit_report(client)
        event_id = EVENT_IDS.new_id()
        reads.linked_events[UUID(report["id"])] = (
            LinkedEvent(
                event_id=event_id, report_id=UUID(report["id"]), role="supporting"
            ),
        )
        as_moderator = await client.get(
            f"{REPORTS}/{report['id']}", headers=moderator_headers()
        )
        as_reporter = await client.get(
            f"{REPORTS}/{report['id']}", headers=reporter_headers()
        )

    moderator_body = as_moderator.json()
    assert moderator_body["review"] == {
        "state": "new",
        "updated_at": None,
        "updated_by": None,
        "reviewed_revision": None,
        "revised_since": False,
    }
    assert moderator_body["linked_events"] == [
        {"event_id": str(event_id), "report_id": report["id"], "role": "supporting"}
    ]
    assert as_reporter.json()["review"] is None
    assert as_reporter.json()["linked_events"] is None


async def test_revised_report_shows_revised_since_review() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        await client.post(
            review_path(report["id"]),
            json={"state": "reviewed", "reason": "Plausible."},
            headers=moderator_headers(),
        )
        revision = await client.post(
            f"{REPORTS}/{report['id']}/revisions",
            json={
                key: value
                for key, value in report.items()
                if key
                in {"observed_at", "coordinates", "original_language", "media_ids"}
            }
            | {"description": "Correction: it reached the bridge."},
            headers=reporter_headers()
            | {"If-Match": make_etag(report["version"], report["id"])},
        )
        review = await client.get(
            review_path(revision.json()["id"]), headers=moderator_headers()
        )

    assert revision.status_code == 201, revision.text
    body = review.json()
    assert (body["state"], body["reviewed_revision"], body["revised_since"]) == (
        "reviewed",
        1,
        True,
    )
    assert body["lineage_id"] == report["id"]


async def test_admin_may_mark_reviews() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        response = await client.post(
            review_path(report["id"]),
            json={"state": "reviewed"},
            headers=auth_headers(subject="admin-subject", roles=["admin"]),
        )
        other = await client.get(review_path(report["id"]), headers=other_headers())

    assert response.status_code == 200
    assert other.status_code == 403


def test_mark_review_route_does_not_declare_428_because_if_match_is_optional() -> None:
    api = recording_app()

    responses = api.app.openapi()["paths"][f"{REVIEWS}/{{report_id}}/review"]["post"][
        "responses"
    ]

    assert "428" not in responses
    assert "412" in responses


async def test_mark_review_etag_names_the_version_the_mark_produced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = recording_app()
    reads = api.app.state.container.report_query_service
    assert isinstance(reads, InMemoryReportQueryService)
    read_report = reads.get_report

    async def racing_read(report_id: UUID) -> ReportRecord | None:
        # Another moderator's mark lands between this mark's commit and its read.
        record = await read_report(report_id)
        if record is None or record.review is None:
            return record
        raced = record.review.model_copy(update={"version": record.review.version + 1})
        return record.model_copy(update={"review": raced})

    async with api.client() as client:
        report = await submit_report(client)
        monkeypatch.setattr(reads, "get_report", racing_read)
        response = await client.post(
            review_path(report["id"]), json=ARCHIVE, headers=moderator_headers()
        )

    assert response.status_code == 200
    assert response.headers["etag"] == make_etag(1, report["id"])


async def test_bulk_review_marks_a_lineage_once_on_its_newest_revision() -> None:
    api = recording_app()

    async with api.client() as client:
        report = await submit_report(client)
        revision = await client.post(
            f"{REPORTS}/{report['id']}/revisions",
            json={
                key: value
                for key, value in report.items()
                if key
                in {"observed_at", "coordinates", "original_language", "media_ids"}
            }
            | {"description": "Correction: it reached the bridge."},
            headers=reporter_headers()
            | {"If-Match": make_etag(report["version"], report["id"])},
        )
        response = await client.post(
            f"{REVIEWS}/review",
            json={
                "report_ids": [report["id"], revision.json()["id"]],
                "state": "reviewed",
            },
            headers=moderator_headers(),
        )
        review = await client.get(
            review_path(report["id"]), headers=moderator_headers()
        )

    items = response.json()["items"]
    assert [item["outcome"] for item in items] == ["marked", "marked"]
    assert {item["version"] for item in items} == {1}
    assert (review.json()["reviewed_revision"], len(review.json()["history"])) == (2, 1)


def _flag_report(api: ApiHarness, report_id: str, *kinds: TriageFlagKind) -> None:
    # Triage runs as a background task in production; the test stores its result.
    stored = api.reports.reports.committed[UUID(report_id)]
    flags = tuple(
        TriageFlag(kind=kind, detail="Found by a rule.", confidence=Confidence.LOW)
        for kind in kinds
    )
    api.reports.reports.committed[stored.id] = stored.model_copy(
        update={"triage": TriageResult(flags=flags, evaluated_at=stored.created_at)}
    )


async def test_list_reports_triage_flags_and_filter_for_moderators_only() -> None:
    api = recording_app()

    async with api.client() as client:
        spam = await submit_report(client)
        plain = await submit_report(client)
        _flag_report(api, spam["id"], "spam_suspected", "pii_detected")
        everything = await client.get(REPORTS, headers=moderator_headers())
        flagged = await client.get(
            REPORTS,
            params={"triage_flag": "spam_suspected", "limit": 1},
            headers=moderator_headers(),
        )
        as_reporter = await client.get(REPORTS, headers=reporter_headers())
        filter_as_reporter = await client.get(
            REPORTS,
            params={"triage_flag": "spam_suspected"},
            headers=reporter_headers(),
        )
        unknown = await client.get(
            REPORTS, params={"triage_flag": "boring"}, headers=moderator_headers()
        )

    kinds = {item["id"]: item["triage_flags"] for item in everything.json()["items"]}
    assert kinds == {
        spam["id"]: ["spam_suspected", "pii_detected"],
        plain["id"]: [],
    }
    assert [item["id"] for item in flagged.json()["items"]] == [spam["id"]]
    assert "detail" not in str(flagged.json()["items"][0]["triage_flags"])
    assert {item["triage_flags"] for item in as_reporter.json()["items"]} == {None}
    assert (filter_as_reporter.status_code, unknown.status_code) == (403, 422)
