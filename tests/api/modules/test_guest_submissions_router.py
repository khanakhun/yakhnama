"""HTTP tests for guest submissions and the report channels (ADR 0019, 0020)."""

from datetime import timedelta
from typing import Any, Final

import httpx

from tests.api.modules.recording import (
    REPORTS,
    moderator_headers,
    report_body,
)
from tests.fakes.api import ApiHarness, auth_headers, build_test_app, harness_settings
from yakhnama.modules.media.public import (
    MimeType,
    StoredObject,
    original_object_key,
    upload_object_key,
)
from yakhnama.modules.reports.domain.guest_submissions import is_proof_of_work_valid
from yakhnama.modules.reports.public import (
    RUN_TRIAGE_TASK,
    ReportChannel,
)
from yakhnama.platform.problem_details import problem_type

GUEST: Final = "/api/v1/guest-submissions"
CAPABILITY_HEADER: Final = "Guest-Capability"
DIFFICULTY: Final = 4


def guest_app(**updates: object) -> ApiHarness:
    """Build the test app with an easy proof of work."""
    settings = harness_settings().model_copy(
        update={"guest_pow_difficulty_bits": DIFFICULTY, **updates}
    )
    return build_test_app(settings=settings)


def solve(challenge: dict[str, Any]) -> str:
    """Return the smallest nonce solving ``challenge``."""
    nonce = 0
    while not is_proof_of_work_valid(
        challenge["salt"], str(nonce), challenge["difficulty_bits"]
    ):
        nonce += 1
    return str(nonce)


async def open_submission(client: httpx.AsyncClient) -> dict[str, Any]:
    """Run the challenge and redeem it; return the submission grant."""
    challenge = (await client.post(f"{GUEST}/challenges")).json()
    response = await client.post(
        GUEST, json={"challenge": challenge["challenge"], "nonce": solve(challenge)}
    )
    assert response.status_code == 201, response.text
    # Any: a decoded JSON body, the external boundary of these tests.
    body: dict[str, Any] = response.json()
    return body


def capability(grant: dict[str, Any]) -> dict[str, str]:
    """Return the capability header of a submission grant."""
    return {CAPABILITY_HEADER: grant["capability"]}


def guest_report(**overrides: object) -> dict[str, object]:
    """Return a guest report body: a report body without account-only fields."""
    body = report_body(**overrides)
    del body["client_report_id"]
    return body


def assert_problem(response: httpx.Response, status: int, slug: str) -> None:
    """Assert a Problem Details response with ``status`` and ``slug``."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem_type(slug)


async def test_guest_challenge_is_anonymous_signed_and_not_cacheable() -> None:
    api = guest_app()

    async with api.client() as client:
        response = await client.post(f"{GUEST}/challenges")

    body = response.json()
    assert response.status_code == 201
    assert body["algorithm"] == "SHA-256"
    assert body["difficulty_bits"] == DIFFICULTY
    assert body["challenge"].startswith(f"v1.{body['salt']}.")
    assert response.headers["cache-control"] == "no-store"


async def test_guest_full_flow_files_a_guest_report_with_a_photo() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        upload = await client.post(
            f"{GUEST}/{grant['submission_id']}/media",
            json={"mime_type": "image/jpeg"},
            headers=capability(grant),
        )
        asset_id = upload.json()["asset_id"]
        api.storage.objects[upload_object_key(asset_id)] = StoredObject(
            sha256="b" * 64, byte_size=2048, content_type="image/jpeg"
        )
        api.mime_sniffer.types[original_object_key(asset_id)] = MimeType.JPEG
        completed = await client.post(
            f"{GUEST}/{grant['submission_id']}/media/{asset_id}/complete",
            headers=capability(grant),
        )
        receipt = await client.post(
            f"{GUEST}/{grant['submission_id']}/report",
            json=guest_report(media_ids=[asset_id]),
            headers=capability(grant),
        )
        queue = await client.get(
            REPORTS, params={"channel": "guest"}, headers=moderator_headers()
        )

    [report] = api.reports.reports.committed.values()
    assert grant["max_media"] == 3
    assert upload.status_code == 201
    assert completed.status_code == 200
    assert completed.json()["upload_status"] == "completed"
    assert "original_download" not in completed.json()
    assert receipt.status_code == 201
    assert receipt.json()["reference"].startswith("YK-")
    assert report.channel is ReportChannel.GUEST
    assert report.media_ids == (report.media_ids[0],)
    assert [item["id"] for item in queue.json()["items"]] == [str(report.id)]
    assert queue.json()["items"][0]["channel"] == "guest"
    assert len(api.task_queue.of(RUN_TRIAGE_TASK)) == 1


async def test_guest_report_detail_hides_the_reporter_from_moderators() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        await client.post(
            f"{GUEST}/{grant['submission_id']}/report",
            json=guest_report(),
            headers=capability(grant),
        )
        [report] = api.reports.reports.committed.values()
        detail = await client.get(f"{REPORTS}/{report.id}", headers=moderator_headers())

    assert detail.status_code == 200
    assert detail.json()["reporter_id"] is None
    assert detail.json()["channel"] == "guest"
    assert detail.json()["assisted"] is None


async def test_guest_report_retry_returns_same_receipt_and_other_content_409() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        path = f"{GUEST}/{grant['submission_id']}/report"
        body = guest_report()
        first = await client.post(path, json=body, headers=capability(grant))
        again = await client.post(path, json=body, headers=capability(grant))
        other = await client.post(
            path,
            json=guest_report(description="Something else entirely."),
            headers=capability(grant),
        )
        late_upload = await client.post(
            f"{GUEST}/{grant['submission_id']}/media",
            json={"mime_type": "image/png"},
            headers=capability(grant),
        )

    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json() == again.json()
    assert_problem(other, 409, "guest-submission-closed")
    assert_problem(late_upload, 409, "guest-submission-closed")
    assert len(api.reports.reports.committed) == 1


async def test_guest_fourth_photo_is_refused() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        path = f"{GUEST}/{grant['submission_id']}/media"
        statuses = [
            (
                await client.post(
                    path, json={"mime_type": "image/webp"}, headers=capability(grant)
                )
            ).status_code
            for _ in range(3)
        ]
        fourth = await client.post(
            path, json={"mime_type": "image/webp"}, headers=capability(grant)
        )

    assert statuses == [201, 201, 201]
    assert_problem(fourth, 409, "guest-media-limit")


async def test_guest_non_image_upload_is_422() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        response = await client.post(
            f"{GUEST}/{grant['submission_id']}/media",
            json={"mime_type": "application/pdf"},
            headers=capability(grant),
        )

    assert_problem(response, 422, "validation-error")


async def test_guest_challenge_replay_is_409_spent() -> None:
    api = guest_app()

    async with api.client() as client:
        challenge = (await client.post(f"{GUEST}/challenges")).json()
        body = {"challenge": challenge["challenge"], "nonce": solve(challenge)}
        first = await client.post(GUEST, json=body)
        replay = await client.post(GUEST, json=body)

    assert first.status_code == 201
    assert_problem(replay, 409, "guest-challenge-spent")


async def test_guest_forged_expired_and_unsolved_challenges_are_422() -> None:
    api = guest_app()

    async with api.client() as client:
        challenge = (await client.post(f"{GUEST}/challenges")).json()
        nonce = solve(challenge)
        forged = await client.post(
            GUEST, json={"challenge": challenge["challenge"] + "x", "nonce": nonce}
        )
        wrong = await client.post(
            GUEST,
            json={
                "challenge": challenge["challenge"],
                "nonce": next(
                    str(candidate)
                    for candidate in range(1000)
                    if not is_proof_of_work_valid(
                        challenge["salt"], str(candidate), DIFFICULTY
                    )
                ),
            },
        )
        api.clock.advance(timedelta(minutes=11))
        expired = await client.post(
            GUEST, json={"challenge": challenge["challenge"], "nonce": nonce}
        )
        malformed_nonce = await client.post(
            GUEST, json={"challenge": challenge["challenge"], "nonce": "-1"}
        )

    assert_problem(forged, 422, "guest-challenge-invalid")
    assert_problem(wrong, 422, "guest-proof-invalid")
    assert_problem(expired, 422, "guest-challenge-expired")
    assert_problem(malformed_nonce, 422, "validation-error")


async def test_guest_capability_missing_wrong_and_expired_are_403() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        path = f"{GUEST}/{grant['submission_id']}/report"
        missing = await client.post(path, json=guest_report())
        wrong = await client.post(
            path, json=guest_report(), headers={CAPABILITY_HEADER: "w" * 43}
        )
        api.clock.advance(timedelta(minutes=31))
        expired = await client.post(
            path, json=guest_report(), headers=capability(grant)
        )

    assert_problem(missing, 403, "guest-capability-invalid")
    assert_problem(wrong, 403, "guest-capability-invalid")
    assert_problem(expired, 403, "guest-capability-expired")
    assert "www-authenticate" not in missing.headers
    assert api.reports.reports.committed == {}


async def test_guest_hourly_cap_is_429_with_retry_after() -> None:
    api = guest_app(guest_submissions_per_hour=1)

    async with api.client() as client:
        await open_submission(client)
        challenge = (await client.post(f"{GUEST}/challenges")).json()
        refused = await client.post(
            GUEST, json={"challenge": challenge["challenge"], "nonce": solve(challenge)}
        )

    assert_problem(refused, 429, "rate-limited")
    assert int(refused.headers["retry-after"]) == 3601


async def test_guest_report_with_account_only_fields_is_422() -> None:
    api = guest_app()

    async with api.client() as client:
        grant = await open_submission(client)
        response = await client.post(
            f"{GUEST}/{grant['submission_id']}/report",
            json=guest_report(organization_id=grant["submission_id"]),
            headers=capability(grant),
        )

    assert_problem(response, 422, "validation-error")


# --------------------------------------------------------------------------- #
# Assisted reports and the channel filter over /reports                       #
# --------------------------------------------------------------------------- #

ASSISTED: Final = {
    "consent_method": "verbal",
    "consent_statement_version": "2026-10-05",
    "note": "Entered for a neighbour.",
}
TRUSTED_SUBJECT: Final = "trusted-subject"


async def test_assisted_report_by_trusted_reporter_shows_consent_to_reporter() -> None:
    api = guest_app()
    headers = auth_headers(subject=TRUSTED_SUBJECT, roles=["trusted_reporter"])

    async with api.client() as client:
        created = await client.post(
            REPORTS, json=report_body(assisted=ASSISTED), headers=headers
        )
        listing = await client.get(
            REPORTS, params={"channel": "assisted"}, headers=moderator_headers()
        )

    body = created.json()
    assert created.status_code == 201
    assert body["channel"] == "assisted"
    assert body["assisted"] == ASSISTED
    assert body["reporter_id"] is not None
    assert [item["channel"] for item in listing.json()["items"]] == ["assisted"]


async def test_assisted_report_by_citizen_is_403() -> None:
    api = guest_app()

    async with api.client() as client:
        response = await client.post(
            REPORTS,
            json=report_body(assisted=ASSISTED),
            headers=auth_headers(subject="plain-citizen"),
        )

    assert_problem(response, 403, "permission-denied")
    assert api.reports.reports.committed == {}


async def test_assisted_report_without_consent_fields_is_422() -> None:
    api = guest_app()
    headers = auth_headers(subject=TRUSTED_SUBJECT, roles=["trusted_reporter"])

    async with api.client() as client:
        response = await client.post(
            REPORTS,
            json=report_body(assisted={"note": "No consent recorded."}),
            headers=headers,
        )

    assert_problem(response, 422, "validation-error")


async def test_revision_of_assisted_report_keeps_consent_and_refuses_new_one() -> None:
    api = guest_app()
    headers = auth_headers(subject=TRUSTED_SUBJECT, roles=["trusted_reporter"])

    async with api.client() as client:
        created = (
            await client.post(
                REPORTS, json=report_body(assisted=ASSISTED), headers=headers
            )
        ).json()
        revision_body = report_body(description="Corrected after a second visit.")
        del revision_body["client_report_id"]
        etag = f'"{created["id"]}:{created["version"]}"'
        revised = await client.post(
            f"{REPORTS}/{created['id']}/revisions",
            json=revision_body,
            headers=headers | {"If-Match": etag},
        )
        with_consent = await client.post(
            f"{REPORTS}/{revised.json()['id']}/revisions",
            json=revision_body | {"assisted": ASSISTED},
            headers=headers | {"If-Match": revised.headers["etag"]},
        )

    assert revised.status_code == 201, revised.text
    assert revised.json()["channel"] == "assisted"
    assert revised.json()["assisted"] == ASSISTED
    assert_problem(with_consent, 422, "validation-error")


async def test_guest_challenge_with_rejected_bearer_token_is_401() -> None:
    api = guest_app()

    async with api.client() as client:
        response = await client.post(
            f"{GUEST}/challenges", headers={"Authorization": "Bearer not-a-token"}
        )

    assert_problem(response, 401, "authentication-failed")
