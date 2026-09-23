"""HTTP tests for ``/api/v1/exports`` and ``/api/v1/moderation/imports``.

The worker is played by running ``RunExportHandler`` and ``RunImportHandler``
over the same fakes the app is wired to. Every value is synthetic.
"""

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import httpx
import pytest

from tests.fakes.api import ApiHarness, auth_headers, build_test_app
from tests.fakes.exchange import (
    PRESIGN_BASE_URL,
    UPLOAD_EXPIRES_AT,
    FakeBackfillReferenceChecker,
    FakeExportRowSource,
    FakeHistoricalEventWriter,
    FakeLineageSourceRegistrar,
    csv_bytes,
)
from yakhnama.modules.events.public import EventStatus
from yakhnama.modules.exchange.api.schemas import IMPORT_UPLOAD_MAX_BYTES
from yakhnama.modules.exchange.domain.backfill import claim_column
from yakhnama.modules.exchange.public import (
    BACKFILL_COLUMNS,
    RUN_EXPORT_TASK,
    RUN_IMPORT_TASK,
    EventExportRow,
    RunExport,
    RunExportHandler,
    RunImport,
    RunImportHandler,
)
from yakhnama.platform.etag import make_etag
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

EXPORTS: Final = "/api/v1/exports"
IMPORTS: Final = "/api/v1/moderation/imports"
UPLOADS: Final = f"{IMPORTS}/uploads"
CITIZEN: Final = "exchange-citizen"
OTHER: Final = "exchange-other"
MODERATOR: Final = "exchange-moderator"
GENERATOR: Final = "yakhnama/0.0.0-test"
UNKNOWN_ID: Final = "01890000-0000-7000-8000-000000000000"
EVENTS_GEOJSON: Final = {"dataset": "events", "format": "geojson"}
UPLOAD_KEY_PATTERN: Final = re.compile(
    r"^imports/[0-9a-f-]{36}/source\.csv$",
)
UNKNOWN_HAZARD: Final = "zz_unknown"
# Synthetic backfill row: no real event, place or source.
VALID_CELLS: Final[dict[str, str]] = {
    "title": "Synthetic GLOF, upper valley",
    "hazard_type": "glof",
    "started_at": "2022-07-15T06:00:00Z",
    "started_at_precision": "day",
    "longitude": "74.5",
    "latitude": "36.5",
    "source_citation": "Synthetic source, 2022",
    claim_column(1, "metric_code"): "houses_destroyed",
    claim_column(1, "value_kind"): "count",
    claim_column(1, "value"): "12",
    claim_column(1, "confidence"): "medium",
    claim_column(1, "claimed_at"): "2022-07-16T00:00:00+05:00",
    claim_column(1, "claimed_at_precision"): "day",
}


def citizen() -> dict[str, str]:
    """Return the ``Authorization`` header of a citizen."""
    return auth_headers(subject=CITIZEN)


def other() -> dict[str, str]:
    """Return the ``Authorization`` header of another citizen."""
    return auth_headers(subject=OTHER)


def moderator() -> dict[str, str]:
    """Return the ``Authorization`` header of a moderator."""
    return auth_headers(subject=MODERATOR, roles=["moderator"])


def payloads(api: ApiHarness, task_name: str) -> list[dict[str, str]]:
    """Return the payloads of the enqueued ``task_name`` tasks, values as text."""
    return [
        {key: str(value) for key, value in task.payload.items()}
        for task in api.task_queue.of(task_name)
    ]


def etag_of(body: dict[str, Any]) -> str:
    """Return the ETag a job body should carry."""
    return make_etag(body["version"], UUID(body["id"]))


def event_row() -> EventExportRow:
    """Return a synthetic published, verified event row."""
    return EventExportRow.model_validate(
        {
            "event_id": UUID("01890000-0000-7000-8000-00000000e001"),
            "hazard_code": "glof",
            "title": "Synthetic outburst flood",
            "started_at": DateWithPrecision(
                value=datetime(2022, 7, 15, tzinfo=UTC), precision=DatePrecision.DAY
            ),
            "centroid": Coordinates(longitude=74.5, latitude=36.25),
            "place_codes": ("pk.gb.test",),
            "source_ids": (UUID("01890000-0000-7000-8000-00000000e002"),),
            "status": EventStatus.PUBLISHED,
            "verification_state": "verified",
            "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
        }
    )


async def request_export(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Request an export and return the job body."""
    response = await client.post(
        EXPORTS, json=EVENTS_GEOJSON if body is None else body, headers=headers
    )
    assert response.status_code == 202, response.text
    job: dict[str, Any] = response.json()
    return job


async def run_export(api: ApiHarness, job_id: str) -> None:
    """Play the worker: run the export over the fakes."""
    handler = RunExportHandler(
        api.exchange_services.dependencies,
        rows=FakeExportRowSource(events=[event_row()]),
        coordinates=PublicCoordinatePolicy(decimals=2),
        generator=GENERATOR,
    )
    await handler(RunExport(job_id=UUID(job_id)))


# --------------------------------------------------------------------------- #
# Exports                                                                     #
# --------------------------------------------------------------------------- #


async def test_request_export_anonymous_returns_401() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(EXPORTS, json=EVENTS_GEOJSON)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert api.exchange.export_jobs.committed == {}


async def test_request_export_as_citizen_returns_202_with_location_etag_and_task() -> (
    None
):
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(EXPORTS, json=EVENTS_GEOJSON, headers=citizen())

    body = response.json()
    assert response.status_code == 202
    assert response.headers["location"] == f"{EXPORTS}/{body['id']}"
    assert response.headers["etag"] == etag_of(body)
    assert body["status"] == "queued"
    assert body["download_url"] is None
    assert body["sidecar"] is None
    assert payloads(api, RUN_EXPORT_TASK) == [{"export_job_id": body["id"]}]


async def test_request_reports_export_as_citizen_returns_403() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(
            EXPORTS, json={"dataset": "reports", "format": "csv"}, headers=citizen()
        )

    assert response.status_code == 403
    assert api.exchange.export_jobs.committed == {}


async def test_request_reports_export_as_moderator_returns_202() -> None:
    api = build_test_app()

    async with api.client() as client:
        job = await request_export(
            client, moderator(), {"dataset": "reports", "format": "csv"}
        )

    assert job["dataset"] == "reports"


async def test_request_export_in_format_without_exporter_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(
            EXPORTS,
            json={"dataset": "events", "format": "geoparquet"},
            headers=citizen(),
        )

    assert response.status_code == 422
    assert api.task_queue.of(RUN_EXPORT_TASK) == []


@pytest.mark.parametrize(
    "body",
    [
        {"dataset": "secrets", "format": "csv"},
        {"dataset": "events", "format": "xlsx"},
        {"dataset": "events", "format": "csv", "owner": "someone"},
        {"dataset": "events", "format": "csv", "filters": {"hazard_type": "<b>x"}},
        {
            "dataset": "events",
            "format": "csv",
            "filters": {
                "bbox": {
                    "min_longitude": 200,
                    "min_latitude": 0,
                    "max_longitude": 1,
                    "max_latitude": 1,
                }
            },
        },
        {"format": "csv"},
    ],
)
async def test_request_export_with_invalid_body_returns_422_without_echo(
    body: dict[str, Any],
) -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(EXPORTS, json=body, headers=citizen())

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    for rejected in ("secrets", "xlsx", "someone", "<b>x"):
        assert rejected not in response.text


async def test_export_worker_run_makes_completed_job_with_download_and_sidecar() -> (
    None
):
    api = build_test_app()

    async with api.client() as client:
        queued = await request_export(client, citizen())
        await run_export(api, queued["id"])
        response = await client.get(f"{EXPORTS}/{queued['id']}", headers=citizen())

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "completed"
    assert body["download_url"] == f"{PRESIGN_BASE_URL}{body['artifact']['object_key']}"
    assert body["artifact"]["object_key"].startswith(f"exports/{queued['id']}/")
    assert body["sidecar"]["row_count"] == 1
    assert body["sidecar"]["licence"]["status"] == "proposed"
    assert body["sidecar"]["checksum"] == body["artifact"]["sha256"]
    assert body["version"] > queued["version"]
    assert response.headers["etag"] == etag_of(body)
    assert response.headers["cache-control"] == "no-store"


async def test_get_export_of_another_user_returns_404() -> None:
    api = build_test_app()

    async with api.client() as client:
        job = await request_export(client, citizen())
        response = await client.get(f"{EXPORTS}/{job['id']}", headers=other())

    assert response.status_code == 404
    assert job["id"] not in response.text


async def test_get_export_as_moderator_returns_the_job() -> None:
    api = build_test_app()

    async with api.client() as client:
        job = await request_export(client, citizen())
        response = await client.get(f"{EXPORTS}/{job['id']}", headers=moderator())

    assert response.status_code == 200
    assert response.json()["id"] == job["id"]


async def test_get_export_anonymous_returns_401() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{EXPORTS}/{UNKNOWN_ID}")

    assert response.status_code == 401


async def test_get_export_with_malformed_id_returns_422_without_echo() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{EXPORTS}/not-a-uuid-xyz", headers=citizen())

    assert response.status_code == 422
    assert "not-a-uuid-xyz" not in response.text


async def test_cancel_export_by_owner_returns_204_and_job_is_cancelled() -> None:
    api = build_test_app()

    async with api.client() as client:
        job = await request_export(client, citizen())
        cancelled = await client.delete(f"{EXPORTS}/{job['id']}", headers=citizen())
        response = await client.get(f"{EXPORTS}/{job['id']}", headers=citizen())

    assert cancelled.status_code == 204
    assert cancelled.content == b""
    assert response.json()["status"] == "cancelled"


async def test_cancel_export_twice_returns_409() -> None:
    api = build_test_app()

    async with api.client() as client:
        job = await request_export(client, citizen())
        await client.delete(f"{EXPORTS}/{job['id']}", headers=citizen())
        response = await client.delete(f"{EXPORTS}/{job['id']}", headers=citizen())

    assert response.status_code == 409
    assert response.json()["type"].endswith("/invalid-transition")


async def test_cancel_export_of_another_user_returns_404() -> None:
    api = build_test_app()

    async with api.client() as client:
        job = await request_export(client, citizen())
        response = await client.delete(f"{EXPORTS}/{job['id']}", headers=other())

    assert response.status_code == 404


async def test_list_exports_shows_own_jobs_to_a_citizen_and_all_to_a_moderator() -> (
    None
):
    api = build_test_app()

    async with api.client() as client:
        mine = await request_export(client, citizen())
        theirs = await request_export(client, other())
        own = await client.get(EXPORTS, headers=citizen())
        every = await client.get(EXPORTS, headers=moderator())

    assert [item["id"] for item in own.json()["items"]] == [mine["id"]]
    assert {item["id"] for item in every.json()["items"]} == {
        mine["id"],
        theirs["id"],
    }


async def test_list_exports_walks_every_page_through_link_headers() -> None:
    api = build_test_app()
    seen: list[str] = []

    async with api.client() as client:
        created = [(await request_export(client, citizen()))["id"] for _ in range(3)]
        url: str | None = f"{EXPORTS}?limit=2"
        while url is not None:
            response = await client.get(url, headers=citizen())
            seen.extend(item["id"] for item in response.json()["items"])
            link = response.headers.get("link")
            url = None if link is None else link.split(">")[0].lstrip("<")

    assert sorted(seen) == sorted(created)
    assert len(seen) == len(set(seen))


async def test_list_exports_with_limit_over_200_returns_422() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{EXPORTS}?limit=201", headers=citizen())

    assert response.status_code == 422


async def test_request_export_replayed_with_idempotency_key_returns_same_job() -> None:
    api = build_test_app()
    headers = citizen() | {"Idempotency-Key": "0b0c1f5e-0000-4000-8000-000000000001"}

    async with api.client() as client:
        first = await client.post(EXPORTS, json=EVENTS_GEOJSON, headers=headers)
        second = await client.post(EXPORTS, json=EVENTS_GEOJSON, headers=headers)

    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert second.headers["idempotent-replayed"] == "true"
    assert len(api.task_queue.of(RUN_EXPORT_TASK)) == 1


# --------------------------------------------------------------------------- #
# Imports                                                                     #
# --------------------------------------------------------------------------- #


async def grant_upload(client: httpx.AsyncClient) -> dict[str, Any]:
    """Ask for a CSV upload grant as a moderator."""
    response = await client.post(UPLOADS, json={"format": "csv"}, headers=moderator())
    assert response.status_code == 201, response.text
    grant: dict[str, Any] = response.json()
    return grant


def upload(api: ApiHarness, key: str, content: bytes) -> dict[str, Any]:
    """Play the client's presigned PUT and return the artifact it declares."""
    api.artifacts.put(key, content)
    return {
        "object_key": key,
        "byte_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


async def test_import_upload_grant_as_moderator_returns_201_with_key_url_and_cap() -> (
    None
):
    api = build_test_app()

    async with api.client() as client:
        grant = await grant_upload(client)

    assert UPLOAD_KEY_PATTERN.fullmatch(grant["object_key"])
    assert grant["upload_url"].startswith(f"{PRESIGN_BASE_URL}{grant['object_key']}")
    assert grant["media_type"] == "text/csv"
    assert grant["max_bytes"] == IMPORT_UPLOAD_MAX_BYTES
    assert grant["expires_at"] == UPLOAD_EXPIRES_AT.isoformat().replace("+00:00", "Z")
    assert {"name": "Content-Type", "value": "text/csv"} in grant["headers"]
    assert api.artifacts.upload_grants == [
        (grant["object_key"], "text/csv", IMPORT_UPLOAD_MAX_BYTES)
    ]


async def test_import_upload_grant_as_citizen_returns_403() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(UPLOADS, json={"format": "csv"}, headers=citizen())

    assert response.status_code == 403
    assert api.artifacts.upload_grants == []


async def test_import_upload_grant_anonymous_returns_401() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.post(UPLOADS, json={"format": "csv"})

    assert response.status_code == 401


async def test_dry_run_import_flow_reports_the_row_with_an_error() -> None:
    api = build_test_app()
    content = csv_bytes(
        [VALID_CELLS, VALID_CELLS | {"hazard_type": UNKNOWN_HAZARD}],
        BACKFILL_COLUMNS,
    )
    writer = FakeHistoricalEventWriter()

    async with api.client() as client:
        grant = await grant_upload(client)
        artifact = upload(api, grant["object_key"], content)
        requested = await client.post(
            IMPORTS,
            json={"format": "csv", "artifact": artifact, "dry_run": True},
            headers=moderator(),
        )
        job_id = requested.json()["id"]
        await RunImportHandler(
            api.exchange_services.dependencies,
            references=FakeBackfillReferenceChecker(unknown_hazards=[UNKNOWN_HAZARD]),
            lineage=FakeLineageSourceRegistrar(),
            writer=writer,
        )(RunImport(job_id=UUID(job_id)))
        response = await client.get(f"{IMPORTS}/{job_id}", headers=moderator())

    queued = requested.json()
    body = response.json()
    assert requested.status_code == 202
    assert requested.headers["location"] == f"{IMPORTS}/{job_id}"
    assert requested.headers["etag"] == etag_of(queued)
    assert queued["status"] == "queued"
    assert queued["dry_run"] is True
    assert queued["source_artifact"]["media_type"] == "text/csv"
    assert payloads(api, RUN_IMPORT_TASK) == [{"import_job_id": job_id}]
    assert response.status_code == 200
    assert response.headers["etag"] == etag_of(body)
    assert body["status"] == "completed"
    assert body["report"]["rows_seen"] == 2
    assert body["report"]["rows_rejected"] == 1
    assert [
        (issue["row_number"], issue["field"]) for issue in body["report"]["issues"]
    ] == [(2, "hazard_type")]
    assert body["created_ids"] == []
    assert writer.committed == []


@pytest.mark.parametrize(
    "artifact_changes",
    [
        {"object_key": "exports/01890000-0000-7000-8000-000000000000/events.csv"},
        {"object_key": "imports/../media/upload/source.csv"},
        {"object_key": "imports/01890000-0000-7000-8000-000000000000/source.geojson"},
        {"byte_size": 0},
        {"byte_size": IMPORT_UPLOAD_MAX_BYTES + 1},
        {"sha256": "Z" * 64},
    ],
)
async def test_request_import_with_invalid_artifact_returns_422(
    artifact_changes: dict[str, Any],
) -> None:
    api = build_test_app()
    artifact = {
        "object_key": "imports/01890000-0000-7000-8000-000000000000/source.csv",
        "byte_size": 10,
        "sha256": "a" * 64,
    } | artifact_changes

    async with api.client() as client:
        response = await client.post(
            IMPORTS,
            json={"format": "csv", "artifact": artifact, "dry_run": True},
            headers=moderator(),
        )

    assert response.status_code == 422
    assert "exports/" not in response.text
    assert "media/upload" not in response.text
    assert api.exchange.import_jobs.committed == {}


async def test_request_import_without_dry_run_returns_422() -> None:
    api = build_test_app()
    artifact = {
        "object_key": "imports/01890000-0000-7000-8000-000000000000/source.csv",
        "byte_size": 10,
        "sha256": "a" * 64,
    }

    async with api.client() as client:
        response = await client.post(
            IMPORTS, json={"format": "csv", "artifact": artifact}, headers=moderator()
        )

    assert response.status_code == 422


async def test_request_import_as_citizen_returns_403() -> None:
    api = build_test_app()
    artifact = {
        "object_key": "imports/01890000-0000-7000-8000-000000000000/source.csv",
        "byte_size": 10,
        "sha256": "a" * 64,
    }

    async with api.client() as client:
        response = await client.post(
            IMPORTS,
            json={"format": "csv", "artifact": artifact, "dry_run": True},
            headers=citizen(),
        )

    assert response.status_code == 403


async def test_get_unknown_import_as_moderator_returns_404() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{IMPORTS}/{UNKNOWN_ID}", headers=moderator())

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_get_import_as_citizen_returns_403() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(f"{IMPORTS}/{UNKNOWN_ID}", headers=citizen())

    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Download links follow the reader's current roles                            #
# --------------------------------------------------------------------------- #

OTHER_MODERATOR: Final = "exchange-moderator-2"


ADMIN: Final = "exchange-admin"


async def demote_moderator(client: httpx.AsyncClient) -> None:
    """Have an administrator revoke the moderator's role through the API."""
    admin = auth_headers(subject=ADMIN, roles=["admin"])
    assert (await client.get("/api/v1/me", headers=admin)).status_code == 200
    me = await client.get("/api/v1/me", headers=moderator())
    revoked = await client.delete(
        f"/api/v1/users/{me.json()['id']}/roles/moderator",
        headers=admin | {"If-Match": me.headers["etag"]},
    )
    assert revoked.status_code == 200, revoked.text
    assert "moderator" not in revoked.json()["roles"]


@pytest.mark.parametrize(
    "body",
    [{"dataset": "reports", "format": "geojson"}, EVENTS_GEOJSON],
    ids=["reports", "events"],
)
async def test_get_export_for_demoted_owner_returns_job_without_download_url(
    body: dict[str, str],
) -> None:
    api = build_test_app()

    async with api.client() as client:
        queued = await request_export(client, moderator(), body)
        await run_export(api, queued["id"])
        await demote_moderator(client)
        demoted = await client.get(f"{EXPORTS}/{queued['id']}", headers=moderator())
        current = await client.get(
            f"{EXPORTS}/{queued['id']}",
            headers=auth_headers(subject=OTHER_MODERATOR, roles=["moderator"]),
        )

    assert demoted.status_code == 200
    assert demoted.json()["status"] == "completed"
    assert demoted.json()["visibility"] == "moderation"
    assert demoted.json()["download_url"] is None
    assert current.status_code == 200
    assert current.json()["download_url"] is not None


async def test_get_public_export_by_owner_keeps_download_url() -> None:
    api = build_test_app()

    async with api.client() as client:
        queued = await request_export(client, citizen())
        await run_export(api, queued["id"])
        response = await client.get(f"{EXPORTS}/{queued['id']}", headers=citizen())

    assert response.json()["visibility"] == "public"
    assert response.json()["download_url"] is not None
