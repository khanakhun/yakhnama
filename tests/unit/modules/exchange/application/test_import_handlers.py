"""Unit tests for requesting and running imports of historical events."""

import hashlib

import pydantic
import pytest

from tests.fakes.exchange import (
    FakeBackfillReferenceChecker,
    FakeHistoricalEventWriter,
    FakeImporter,
    csv_bytes,
)
from tests.unit.modules.exchange.application.support import (
    CITIZEN,
    IMPORT_KEY,
    MODERATOR,
    MODERATOR_ID,
    ExchangeWorld,
    artifact_of,
    cells,
    import_file,
)
from yakhnama.modules.exchange.application import handlers as handlers_module
from yakhnama.modules.exchange.application.commands import RequestImport, RunImport
from yakhnama.modules.exchange.application.handlers import (
    IMPORT_DENIED_SUMMARY,
    IMPORT_FAILED_SUMMARY,
    IMPORT_HEADER_SUMMARY,
    IMPORT_INTEGRITY_SUMMARY,
    IMPORT_INTERNAL_SUMMARY,
    IMPORT_INVALID_SUMMARY,
    IMPORT_NOT_FOUND_SUMMARY,
    IMPORT_TOO_LARGE_SUMMARY,
    IMPORT_TOO_MANY_ROWS_SUMMARY,
    batch_failure_summary,
)
from yakhnama.modules.exchange.application.ports import RUN_IMPORT_TASK
from yakhnama.modules.exchange.domain.backfill import ImportedEventDraft
from yakhnama.modules.exchange.domain.errors import (
    ImportJobNotFoundError,
    UnsupportedFormatError,
)
from yakhnama.modules.exchange.domain.events import (
    ImportCompleted,
    ImportFailed,
    ImportRequested,
    ImportStarted,
)
from yakhnama.modules.exchange.domain.value_objects import (
    NO_WRITES,
    ImportFormat,
    JobStatus,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.errors import (
    ConflictError,
    PermissionDeniedError,
)

ROW_TEXT = "cell text that must never reach a job"


def draft_of(row: dict[str, str], row_number: int = 1) -> ImportedEventDraft:
    outcome = ImportedEventDraft.from_flat_row(row_number, row)
    assert isinstance(outcome, ImportedEventDraft), outcome
    return outcome


def titled(number: int) -> dict[str, str]:
    return cells(title=f"Synthetic flood number {number}")


# --------------------------------------------------------------------------- #
# RequestImport                                                               #
# --------------------------------------------------------------------------- #


async def test_request_import_of_stored_file_queues_job_and_enqueues_run() -> None:
    world = ExchangeWorld()
    content = import_file(cells())

    job_id = await world.queued_import(content, dry_run=True)

    job = world.import_job(job_id)
    assert job.status is JobStatus.QUEUED
    assert job.dry_run is True
    assert job.requested_by == MODERATOR_ID
    assert job.source_artifact == artifact_of(content)
    assert [type(event) for event in world.uow.committed_events] == [ImportRequested]
    (task,) = world.tasks.of(RUN_IMPORT_TASK)
    assert dict(task.payload) == {"import_job_id": job_id}
    assert task.idempotency_key == f"{RUN_IMPORT_TASK}:{job_id}"


async def test_request_import_inline_csv_stores_file_under_imports() -> None:
    world = ExchangeWorld()
    content = import_file(cells())

    job_id = await world.request_import()(
        RequestImport(
            actor=MODERATOR, format=ImportFormat.CSV, inline_csv=content, dry_run=False
        )
    )

    artifact = world.import_job(job_id).source_artifact
    assert artifact.object_key.startswith("imports/")
    assert artifact.object_key.endswith("/source.csv")
    assert artifact.byte_size == len(content)
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert world.artifacts.objects[artifact.object_key].data == content


async def test_request_import_by_citizen_raises_and_stores_nothing() -> None:
    world = ExchangeWorld()

    with pytest.raises(PermissionDeniedError):
        await world.request_import()(
            RequestImport(
                actor=CITIZEN, format=ImportFormat.CSV, inline_csv=b"x", dry_run=True
            )
        )

    assert world.uow.import_jobs.committed == {}
    assert world.artifacts.objects == {}
    assert world.tasks.enqueued == []


async def test_request_import_without_importer_raises_unsupported_format() -> None:
    world = ExchangeWorld()
    content = b"{}"

    with pytest.raises(UnsupportedFormatError):
        await world.request_import()(
            RequestImport(
                actor=MODERATOR,
                format=ImportFormat.GEOJSON,
                artifact=artifact_of(content, "imports/upload-2/source.geojson"),
                dry_run=True,
            )
        )

    assert world.uow.import_jobs.committed == {}


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"inline_csv": b"a", "artifact": artifact_of(b"a")},
        {"format": ImportFormat.GEOJSON, "inline_csv": b"{}"},
        {"artifact": artifact_of(b"a", "exports/other/events.csv")},
        {"inline_csv": b""},
    ],
)
def test_request_import_with_bad_file_choice_raises_validation_error(
    fields: dict[str, object],
) -> None:
    base: dict[str, object] = {
        "actor": MODERATOR,
        "format": ImportFormat.CSV,
        "dry_run": True,
    }

    with pytest.raises(pydantic.ValidationError):
        RequestImport.model_validate(base | fields)


# --------------------------------------------------------------------------- #
# RunImport, outcomes without writes                                          #
# --------------------------------------------------------------------------- #


async def test_run_import_dry_run_reports_rows_and_creates_nothing() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1), titled(2)), dry_run=True)

    finished = await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert finished == job
    assert job.status is JobStatus.COMPLETED
    assert job.report is not None
    assert job.report.rows_seen == 2
    assert job.report.rows_valid == 2
    assert job.writes == NO_WRITES
    assert world.writer.batches_opened == 0
    assert world.lineage.registered == []
    assert world.references.checked == [1, 2]
    assert [type(event) for event in world.uow.committed_events] == [
        ImportRequested,
        ImportStarted,
        ImportCompleted,
    ]


async def test_run_import_with_blocking_error_creates_nothing() -> None:
    world = ExchangeWorld()
    content = import_file(titled(1), cells(started_at=ROW_TEXT))
    job_id = await world.queued_import(content, dry_run=False)

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.status is JobStatus.COMPLETED
    assert job.report is not None
    assert job.report.rows_rejected == 1
    assert [issue.row_number for issue in job.report.issues] == [2]
    assert [issue.field for issue in job.report.issues] == ["started_at"]
    assert ROW_TEXT not in job.report.model_dump_json()
    assert job.writes == NO_WRITES
    assert world.writer.committed == []
    assert world.lineage.registered == []


async def test_run_import_with_unknown_reference_creates_nothing() -> None:
    world = ExchangeWorld(
        references=FakeBackfillReferenceChecker(unknown_hazards={"landslide"})
    )
    content = import_file(titled(1), cells(hazard_type="landslide"))
    job_id = await world.queued_import(content, dry_run=False)

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.report is not None
    assert [(issue.row_number, issue.field) for issue in job.report.issues] == [
        (2, "hazard_type")
    ]
    assert job.writes == NO_WRITES
    assert world.writer.committed == []


async def test_run_import_of_file_without_rows_completes_without_writes() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(), dry_run=False)

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.status is JobStatus.COMPLETED
    assert job.report is not None
    assert job.report.rows_seen == 0
    assert job.writes == NO_WRITES
    assert world.lineage.registered == []


# --------------------------------------------------------------------------- #
# RunImport, writes                                                           #
# --------------------------------------------------------------------------- #


async def test_run_import_creates_one_event_per_row_with_one_lineage_source() -> None:
    world = ExchangeWorld()
    rows = (titled(1), titled(2), titled(3))
    job_id = await world.queued_import(import_file(*rows), dry_run=False)

    await world.run_import(batch_size=2)(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.status is JobStatus.COMPLETED
    assert world.writer.drafts == [
        draft_of(row, number) for number, row in enumerate(rows, 1)
    ]
    assert job.writes.created_ids == tuple(
        event_id for event_id, _ in world.writer.committed
    )
    assert job.writes.batches_applied == 2
    ((source, actor, source_id),) = world.lineage.registered
    assert job.writes.lineage_source_id == source_id
    assert world.writer.lineage_ids == {source_id}
    assert world.writer.actors == {MODERATOR}
    assert actor == MODERATOR
    assert source.import_job_id == job_id
    assert str(job_id) in source.details.citation
    assert "csv" in source.details.citation


async def test_run_import_keeps_draft_claims_for_the_writer() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(cells()), dry_run=False)

    await world.run_import()(RunImport(job_id=job_id))

    (draft,) = world.writer.drafts
    (claim,) = draft.claims
    assert claim.metric_code == "houses_destroyed"


async def test_run_import_with_only_warnings_writes_the_rows() -> None:
    world = ExchangeWorld(
        references=FakeBackfillReferenceChecker(warn_hazards={"glof"})
    )
    job_id = await world.queued_import(import_file(titled(1)), dry_run=False)

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.report is not None
    assert job.report.warning_count == 1
    assert job.created_count == 1


async def test_run_import_batch_two_failure_keeps_batch_one_and_fails_job() -> None:
    world = ExchangeWorld(writer=FakeHistoricalEventWriter(fail_on_batch=2))
    rows = tuple(titled(number) for number in range(1, 6))
    job_id = await world.queued_import(import_file(*rows), dry_run=False)

    finished = await world.run_import(batch_size=2)(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert finished == job
    assert job.status is JobStatus.FAILED
    assert job.error_summary == batch_failure_summary(2)
    assert len(world.writer.committed) == 2
    assert job.writes.created_ids == tuple(
        event_id for event_id, _ in world.writer.committed
    )
    assert job.writes.batches_applied == 1
    assert job.writes.lineage_source_id is not None
    assert job.report is not None
    assert job.report.rows_seen == len(rows)
    assert world.writer.rolled_back == 1
    assert world.writer.batches_opened == 2
    assert isinstance(world.uow.committed_events[-1], ImportFailed)


async def test_run_import_first_batch_failure_records_lineage_without_ids() -> None:
    world = ExchangeWorld(writer=FakeHistoricalEventWriter(fail_on_batch=1))
    job_id = await world.queued_import(import_file(titled(1), titled(2)), dry_run=False)

    await world.run_import(batch_size=2)(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.writes.created_ids == ()
    assert job.writes.batches_applied == 0
    assert job.writes.lineage_source_id is not None


async def test_run_import_unexpected_batch_error_records_writes_and_reraises() -> None:
    world = ExchangeWorld(
        writer=FakeHistoricalEventWriter(
            fail_on_batch=2, failure=RuntimeError(ROW_TEXT)
        )
    )
    rows = tuple(titled(number) for number in range(1, 5))
    job_id = await world.queued_import(import_file(*rows), dry_run=False)

    with pytest.raises(RuntimeError):
        await world.run_import(batch_size=2)(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_summary == IMPORT_INTERNAL_SUMMARY
    assert job.writes.batches_applied == 1
    assert len(job.writes.created_ids) == 2


# --------------------------------------------------------------------------- #
# RunImport, failures before writing                                          #
# --------------------------------------------------------------------------- #


async def test_run_import_with_wrong_header_fails_without_report() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(
        csv_bytes([{"title": "x"}], ("title",)), dry_run=True
    )

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_summary == IMPORT_HEADER_SUMMARY
    assert job.report is None


async def test_run_import_over_row_limit_fails_as_too_many_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handlers_module, "IMPORT_MAX_ROWS", 1)
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1), titled(2)), dry_run=True)

    await world.run_import()(RunImport(job_id=job_id))

    assert world.import_job(job_id).error_summary == IMPORT_TOO_MANY_ROWS_SUMMARY


async def test_run_import_with_misnumbered_rows_fails_as_unreadable() -> None:
    world = ExchangeWorld(importer=FakeImporter(row_numbers=(1, 3)))
    job_id = await world.queued_import(import_file(titled(1), titled(2)), dry_run=True)

    await world.run_import()(RunImport(job_id=job_id))

    assert world.import_job(job_id).error_summary == IMPORT_INVALID_SUMMARY


async def test_run_import_of_missing_file_fails_as_not_found() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1)), dry_run=True)
    del world.artifacts.objects[IMPORT_KEY]

    await world.run_import()(RunImport(job_id=job_id))

    assert world.import_job(job_id).error_summary == IMPORT_NOT_FOUND_SUMMARY


async def test_run_import_of_replaced_file_fails_integrity_check() -> None:
    world = ExchangeWorld()
    content = import_file(titled(1))
    job_id = await world.queued_import(content, dry_run=False)
    world.artifacts.put(IMPORT_KEY, content.replace(b"number 1", b"number 9"))

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.error_summary == IMPORT_INTEGRITY_SUMMARY
    assert world.writer.committed == []


async def test_run_import_of_file_larger_than_declared_fails_as_too_large() -> None:
    world = ExchangeWorld()
    content = import_file(titled(1))
    job_id = await world.queued_import(content, dry_run=True)
    world.artifacts.put(IMPORT_KEY, content + import_file(titled(2)))

    await world.run_import()(RunImport(job_id=job_id))

    assert world.import_job(job_id).error_summary == IMPORT_TOO_LARGE_SUMMARY


async def test_run_import_for_moderator_who_lost_role_fails_as_denied() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1)), dry_run=True)
    world.actors.actors[MODERATOR_ID] = Actor(user_id=MODERATOR_ID)

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.error_summary == IMPORT_DENIED_SUMMARY
    assert world.references.checked == []


async def test_run_import_when_lineage_is_refused_fails_with_generic_summary() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1)), dry_run=False)
    world.lineage.failure = ConflictError(ROW_TEXT)

    await world.run_import()(RunImport(job_id=job_id))

    job = world.import_job(job_id)
    assert job.error_summary == IMPORT_FAILED_SUMMARY
    assert world.writer.committed == []


async def test_run_import_unexpected_read_error_records_failure_and_reraises() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(b"\xff\xfe not utf-8", dry_run=True)

    with pytest.raises(UnicodeDecodeError):
        await world.run_import()(RunImport(job_id=job_id))

    assert world.import_job(job_id).error_summary == IMPORT_INTERNAL_SUMMARY


# --------------------------------------------------------------------------- #
# RunImport, repeats and missing jobs                                         #
# --------------------------------------------------------------------------- #


async def test_run_import_of_final_job_returns_none() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1)), dry_run=False)
    await world.run_import()(RunImport(job_id=job_id))

    repeated = await world.run_import()(RunImport(job_id=job_id))

    assert repeated is None
    assert len(world.writer.committed) == 1


async def test_run_import_of_running_job_returns_none_and_writes_nothing() -> None:
    world = ExchangeWorld()
    job_id = await world.queued_import(import_file(titled(1)), dry_run=False)
    queued = world.import_job(job_id)
    world.uow.import_jobs.committed[job_id] = queued.start(
        clock=world.deps.clock, ids=world.deps.ids
    ).state

    repeated = await world.run_import()(RunImport(job_id=job_id))

    assert repeated is None
    assert world.writer.batches_opened == 0


async def test_run_import_of_unknown_job_raises_not_found() -> None:
    world = ExchangeWorld()

    with pytest.raises(ImportJobNotFoundError):
        await world.run_import()(RunImport(job_id=MODERATOR_ID))
