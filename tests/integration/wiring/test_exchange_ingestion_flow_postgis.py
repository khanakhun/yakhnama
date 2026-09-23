"""The Phase 4 gate flow through the real composition root, PostGIS and MinIO.

``docs/plans/phase-4.md`` §4 over HTTP against ``create_app`` with the container
``build_container`` makes from the wiring settings: SQL units of work and query
services on the migrated session database, ``S3ArtifactStore`` on the session MinIO
and the in-memory task broker, which runs every enqueued task in this process. Only
the token validator (locally keyed) and the rate limiter are replaced (see the
package conftest). After each request that enqueues work the broker is drained and
the task is then delivered once more through ``build_task_handlers``, as a
redelivering worker would: a finished job or run must be left as it is.

The seed runs with the synthetic fixture dataset (``environment`` is ``test``);
every text, figure and coordinate here is synthetic.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Final

import httpx
import pytest

from tests.fakes.exchange import csv_bytes
from tests.fakes.identity import actor_with
from tests.integration.conftest import MinioServer
from tests.integration.wiring.conftest import admin_client
from tests.integration.wiring.flow_steps import (
    API,
    CASES,
    MODERATION,
    Json,
    bearer_headers,
    drain_tasks,
    json_body,
    publish_verified_event_from_report,
    scheduled_task,
)
from yakhnama.main import create_app
from yakhnama.modules.events.public import EventPeriod
from yakhnama.modules.exchange.domain.backfill import (
    ImportedClaimDraft,
    ImportedSourceDraft,
)
from yakhnama.modules.exchange.public import (
    BACKFILL_COLUMNS,
    PROPOSED_DATASET_LICENCE,
    ImportedEventDraft,
)
from yakhnama.modules.identity.public import Role
from yakhnama.modules.impacts.public import CountValue
from yakhnama.platform.container import (
    Container,
    build_seed_handler,
    build_task_handlers,
)
from yakhnama.platform.tasks.handlers import (
    EXCHANGE_RUN_EXPORT_TASK,
    EXCHANGE_RUN_IMPORT_TASK,
    INGESTION_RUN_TASK,
)
from yakhnama.seed.application import SeedReferenceData
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    DatePrecision,
    DateWithPrecision,
)

pytestmark = pytest.mark.integration

ADMIN_HEADERS: Final = bearer_headers("phase4-admin", ("admin",))
MODERATOR_HEADERS: Final = bearer_headers("phase4-moderator", ("moderator",))
REPORTER_HEADERS: Final = bearer_headers("phase4-reporter")
READER_HEADERS: Final = bearer_headers("phase4-reader")

FIXTURE_DATASET: Final = "fixture.temperature_sample"
FIXTURE_ADAPTER: Final = "local_csv_temperature"
FIXTURE_FILE: Final = "temperature_sample.csv"
FIXTURE_ROWS: Final = 144
HAZARD_CODE: Final = "glof"
METRIC_CODE: Final = "deaths"
PLACE_CODE: Final = "pk.gb"
INVALID_HAZARD: Final = "not_a_hazard"


def _day(year: int, month: int, day: int) -> DateWithPrecision:
    return DateWithPrecision(
        value=datetime(year, month, day, tzinfo=UTC), precision=DatePrecision.DAY
    )


def _backfill_row(title: str, hazard_type: str, deaths: int) -> dict[str, str]:
    draft = ImportedEventDraft(
        title=title,
        hazard_type=HAZARD_CODE,
        period=EventPeriod(started_at=_day(1974, 7, 12)),
        place_codes=(PLACE_CODE,),
        source=ImportedSourceDraft(citation="District archive, flood register 1974"),
        claims=(
            ImportedClaimDraft(
                metric_code=METRIC_CODE,
                value=CountValue(count=deaths),
                confidence=Confidence.MEDIUM,
                claimed_at=_day(1974, 7, 20),
            ),
        ),
    )
    # The hazard code is set on the flat row, so an unknown one reaches the
    # reference check exactly as it would from a file.
    return draft.to_flat_row() | {"hazard_type": hazard_type}


def _backfill_file(*, is_corrected: bool) -> bytes:
    second_hazard = HAZARD_CODE if is_corrected else INVALID_HAZARD
    rows = [_backfill_row("Outburst flood of 1974", HAZARD_CODE, 3)]
    if not is_corrected:
        rows.append(_backfill_row("Second outburst of 1974", second_hazard, 1))
    return csv_bytes(rows, BACKFILL_COLUMNS)


def _report_body(container: Container) -> Json:
    return {
        "client_report_id": str(container.id_generator.new_id()),
        "observed_at": {"value": "2026-07-01T06:00:00Z", "precision": "hour"},
        "coordinates": {"longitude": 74.654321, "latitude": 36.314159},
        "accuracy_metres": 12.5,
        "description": "Muddy water rising fast in the nala below the village.",
        "original_language": "en",
        "hazard_guess": {"hazard_code": HAZARD_CODE, "confidence": "medium"},
        "media_ids": [],
    }


async def _deliver_again(container: Container, task_name: str, payload: Json) -> None:
    # A redelivery: the worker runs the task a second time.
    await build_task_handlers(container)[task_name](scheduled_task(task_name, payload))


async def _import(
    client: httpx.AsyncClient, container: Container, body: bytes, *, dry_run: bool
) -> Json:
    grant = json_body(
        await client.post(
            f"{MODERATION}/imports/uploads",
            json={"format": "csv"},
            headers=MODERATOR_HEADERS,
        ),
        201,
    )
    headers = {header["name"]: header["value"] for header in grant["headers"]}
    async with httpx.AsyncClient() as storage_client:
        stored = await storage_client.put(
            grant["upload_url"], content=body, headers=headers
        )
    assert stored.status_code == 200, stored.text
    job = json_body(
        await client.post(
            f"{MODERATION}/imports",
            json={
                "format": "csv",
                "artifact": {
                    "object_key": grant["object_key"],
                    "byte_size": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                },
                "dry_run": dry_run,
            },
            headers=MODERATOR_HEADERS,
        ),
        202,
    )
    await drain_tasks(container)
    await _deliver_again(
        container, EXCHANGE_RUN_IMPORT_TASK, {"import_job_id": job["id"]}
    )
    return json_body(
        await client.get(
            f"{MODERATION}/imports/{job['id']}", headers=MODERATOR_HEADERS
        ),
        200,
    )


async def _read_private_object(
    minio_server: MinioServer, container: Container, key: str
) -> bytes:
    async with admin_client(minio_server) as storage:
        stored = await storage.get_object(
            Bucket=container.settings.storage_private_bucket, Key=key
        )
        async with stored["Body"] as stream:
            return await stream.read()


async def _run_fixture_ingestion(
    client: httpx.AsyncClient, container: Container
) -> tuple[Json, Json, str]:
    fixture = container.settings.ingestion_fixtures_dir / FIXTURE_FILE
    checksum = hashlib.sha256(fixture.read_bytes()).hexdigest()
    version = json_body(
        await client.post(
            f"{API}/admin/datasets/{FIXTURE_DATASET}/versions",
            json={
                "details": {
                    "label": "fixture-1",
                    "retrieved_at": {
                        "value": "2026-09-23T00:00:00Z",
                        "precision": "day",
                    },
                    "input_checksum": checksum,
                }
            },
            headers=ADMIN_HEADERS,
        ),
        201,
    )
    requested = json_body(
        await client.post(
            f"{API}/admin/datasets/{FIXTURE_DATASET}/runs",
            json={"version_id": version["id"], "adapter_name": FIXTURE_ADAPTER},
            headers=ADMIN_HEADERS,
        ),
        202,
    )
    await drain_tasks(container)
    await _deliver_again(container, INGESTION_RUN_TASK, {"run_id": requested["id"]})
    run = json_body(await client.get(f"{API}/ingestion-runs/{requested['id']}"), 200)
    observations = json_body(
        await client.get(
            f"{API}/observations",
            params={
                "dataset": FIXTURE_DATASET,
                "variable": "air_temperature",
                "from": "2026-01-01T00:00:00+05:00",
                "to": "2026-01-03T00:00:00+05:00",
                "limit": 200,
            },
        ),
        200,
    )
    return run, observations, checksum


async def test_gate_flow_ingestion_export_and_import(  # noqa: PLR0915  # reason: the gate flow is one scenario, step by step
    container: Container, minio_server: MinioServer
) -> None:
    seed_report = await build_seed_handler(container)(
        SeedReferenceData(actor=actor_with({Role.ADMIN}))
    )
    app = create_app(container.settings, container)
    transport = httpx.ASGITransport(app=app)
    summaries: list[str | None] = []

    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as client:
        # (a) The fixture ingestion.
        run, observations, checksum = await _run_fixture_ingestion(client, container)

        # (b) A verified, published event, exported as GeoJSON by a plain user.
        event = await publish_verified_event_from_report(
            client,
            report=_report_body(container),
            reporter_headers=REPORTER_HEADERS,
            moderator_headers=MODERATOR_HEADERS,
            hazard_code=HAZARD_CODE,
        )
        requested = json_body(
            await client.post(
                f"{API}/exports",
                json={"dataset": "events", "format": "geojson"},
                headers=READER_HEADERS,
            ),
            202,
        )
        await drain_tasks(container)
        await _deliver_again(
            container, EXCHANGE_RUN_EXPORT_TASK, {"export_job_id": requested["id"]}
        )
        export = json_body(
            await client.get(
                f"{API}/exports/{requested['id']}", headers=READER_HEADERS
            ),
            200,
        )
        async with httpx.AsyncClient() as storage_client:
            download = await storage_client.get(export["download_url"])
        sidecar_bytes = await _read_private_object(
            minio_server,
            container,
            f"exports/{requested['id']}/events.sidecar.json",
        )

        # (c) A dry run with one bad row, then the corrected file for real.
        dry_run = await _import(
            client, container, _backfill_file(is_corrected=False), dry_run=True
        )
        events_after_dry_run = json_body(
            await client.get(f"{API}/events", headers=MODERATOR_HEADERS), 200
        )
        applied = await _import(
            client, container, _backfill_file(is_corrected=True), dry_run=False
        )
        (imported_id,) = applied["created_ids"]
        lineage = json_body(
            await client.get(
                f"{API}/sources/{applied['lineage_source_id']}",
                headers=MODERATOR_HEADERS,
            ),
            200,
        )
        impacts = json_body(
            await client.get(
                f"{API}/events/{imported_id}/impacts", headers=MODERATOR_HEADERS
            ),
            200,
        )
        public_view = await client.get(f"{API}/events/{imported_id}")
        cases = json_body(
            await client.get(
                CASES, params={"target_kind": "event"}, headers=MODERATOR_HEADERS
            ),
            200,
        )
        summaries.extend(job["error_summary"] for job in (export, dry_run, applied))

    # Seed: the fixture dataset is in the catalog outside production.
    assert seed_report.datasets is not None
    assert FIXTURE_DATASET in seed_report.datasets.created
    # (a) Every fixture row stored in kelvin, the gaps flagged, the run traced.
    items = observations["items"]
    assert run["status"] == "succeeded"
    assert run["input_checksum"] == checksum
    assert run["counts"]["persisted"] == FIXTURE_ROWS
    assert len(items) == FIXTURE_ROWS
    assert observations["next_cursor"] is None
    assert {item["value"]["unit"] for item in items if item["value"]} == {"kelvin"}
    missing = [item for item in items if item["quality"] == "missing"]
    suspect = [item for item in items if item["quality"] == "suspect"]
    assert [item["value"] for item in missing] == [None]
    assert len(suspect) == 1
    assert {item["ingested_run_id"] for item in items} == {run["id"]}
    # (b) The export holds the verified event; the sidecar matches the file.
    assert export["status"] == "completed"
    assert download.status_code == 200
    collection = download.json()
    assert collection["type"] == "FeatureCollection"
    assert [feature["id"] for feature in collection["features"]] == [event["id"]]
    file_digest = hashlib.sha256(download.content).hexdigest()
    sidecar = json.loads(sidecar_bytes)
    assert sidecar["checksum"] == file_digest == export["artifact"]["sha256"]
    assert sidecar["licence"] == PROPOSED_DATASET_LICENCE.model_dump(mode="json")
    assert export["sidecar"]["checksum"] == file_digest
    # (c) The dry run reported the one bad row and created nothing.
    assert dry_run["status"] == "completed"
    assert dry_run["created_ids"] == []
    assert dry_run["lineage_source_id"] is None
    assert [
        (issue["row_number"], issue["field"], issue["severity"])
        for issue in dry_run["report"]["issues"]
    ] == [(2, "hazard_type", "error")]
    assert INVALID_HAZARD not in json.dumps(dry_run["report"])
    assert [item["id"] for item in events_after_dry_run["items"]] == [event["id"]]
    # (c) The real import: one event, its claim, a dataset lineage source; the
    # event waits for verification and is not public.
    assert applied["status"] == "completed"
    assert lineage["source_type"] == "dataset"
    best = impacts["best_figures"][0]
    assert (best["metric"]["code"], best["value"]["count"]) == (METRIC_CODE, 3)
    assert public_view.status_code == 404
    imported_cases = [
        item for item in cases["items"] if item["target"]["target_id"] == imported_id
    ]
    assert [case["state"] for case in imported_cases] in (["draft"], ["submitted"])
    # No job carries an error summary, so no exception text can leak through one.
    assert summaries == [None, None, None]
