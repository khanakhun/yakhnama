"""Unit tests for requesting, running and cancelling exports."""

import hashlib
import json
from collections.abc import AsyncIterator

import pytest

from tests.fakes.exchange import FakeExportRowSource
from tests.unit.modules.exchange.application.support import (
    ANONYMOUS,
    CITIZEN,
    GENERATOR,
    MODERATOR,
    MODERATOR_ID,
    NOW,
    OTHER,
    USER_ID,
    ExchangeWorld,
    claim_row,
    event_row,
    report_row,
)
from yakhnama.modules.exchange.application import handlers as handlers_module
from yakhnama.modules.exchange.application.commands import (
    CancelExport,
    RequestExport,
    RunExport,
)
from yakhnama.modules.exchange.application.formats import (
    EventExportRow,
    ExportRow,
    ReportExportRow,
)
from yakhnama.modules.exchange.application.handlers import (
    EXPORT_DENIED_SUMMARY,
    EXPORT_FAILED_SUMMARY,
    EXPORT_INTERNAL_SUMMARY,
    EXPORT_INVALID_SUMMARY,
    EXPORT_NOT_FOUND_SUMMARY,
    EXPORT_TOO_LARGE_SUMMARY,
)
from yakhnama.modules.exchange.application.ports import RUN_EXPORT_TASK
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    JobStateError,
    UnsupportedFormatError,
)
from yakhnama.modules.exchange.domain.events import (
    ExportCancelled,
    ExportCompleted,
    ExportFailed,
    ExportRequested,
    ExportStarted,
)
from yakhnama.modules.exchange.domain.value_objects import (
    PROPOSED_DATASET_LICENCE,
    ExportDataset,
    ExportFilters,
    ExportFormat,
    JobStatus,
)
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.shared_kernel.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from yakhnama.shared_kernel.value_objects import Coordinates

ROW_TEXT = "row text that must never reach a job"
FILTERS = ExportFilters(hazard_type="glof", place_code="pk.gb.test")


def request(
    dataset: ExportDataset = ExportDataset.EVENTS,
    actor: Actor = CITIZEN,
    export_format: ExportFormat = ExportFormat.CSV,
) -> RequestExport:
    return RequestExport(
        actor=actor, dataset=dataset, format=export_format, filters=FILTERS
    )


# --------------------------------------------------------------------------- #
# RequestExport                                                               #
# --------------------------------------------------------------------------- #


async def test_request_export_stores_queued_job_and_enqueues_its_run() -> None:
    world = ExchangeWorld()

    job_id = await world.request_export()(request())

    job = world.export_job(job_id)
    assert job.status is JobStatus.QUEUED
    assert job.requested_by == USER_ID
    assert job.filters == FILTERS
    assert [type(event) for event in world.uow.committed_events] == [ExportRequested]
    (task,) = world.tasks.of(RUN_EXPORT_TASK)
    assert dict(task.payload) == {"export_job_id": job_id}
    assert task.idempotency_key == f"{RUN_EXPORT_TASK}:{job_id}"


async def test_request_export_of_reports_by_moderator_is_queued() -> None:
    world = ExchangeWorld()

    job_id = await world.request_export()(request(ExportDataset.REPORTS, MODERATOR))

    assert world.export_job(job_id).dataset is ExportDataset.REPORTS


@pytest.mark.parametrize(
    ("dataset", "actor"),
    [
        (ExportDataset.REPORTS, CITIZEN),
        (ExportDataset.EVENTS, ANONYMOUS),
        (ExportDataset.CLAIMS, ANONYMOUS),
    ],
)
async def test_request_export_denied_actor_raises_and_stores_nothing(
    dataset: ExportDataset, actor: Actor
) -> None:
    world = ExchangeWorld()

    with pytest.raises(PermissionDeniedError):
        await world.request_export()(request(dataset, actor))

    assert world.uow.export_jobs.committed == {}
    assert world.tasks.enqueued == []


async def test_request_export_without_exporter_raises_unsupported_format() -> None:
    world = ExchangeWorld()

    with pytest.raises(UnsupportedFormatError):
        await world.request_export()(request(export_format=ExportFormat.GEOJSON))

    assert world.uow.export_jobs.committed == {}


async def test_request_export_when_queue_fails_keeps_job_queued_and_raises() -> None:
    world = ExchangeWorld()
    world.tasks.failure = ConflictError("broker unavailable")

    with pytest.raises(ConflictError):
        await world.request_export()(request())

    (job,) = world.uow.export_jobs.committed.values()
    assert job.status is JobStatus.QUEUED


# --------------------------------------------------------------------------- #
# RunExport, success                                                          #
# --------------------------------------------------------------------------- #


async def test_run_export_streams_rows_and_stores_artifact_and_sidecar() -> None:
    rows = (event_row(), event_row())
    world = ExchangeWorld(rows=FakeExportRowSource(events=rows))
    job_id = await world.request_export()(request())

    finished = await world.run_export()(RunExport(job_id=job_id))

    job = world.export_job(job_id)
    assert finished == job
    assert job.status is JobStatus.COMPLETED
    assert job.artifact is not None
    assert job.sidecar is not None
    key = f"exports/{job_id}/events.csv"
    stored = world.artifacts.objects[key]
    assert job.artifact.object_key == key
    assert stored.media_type == "text/csv"
    lines = stored.data.decode().splitlines()
    assert lines[0] == "events"
    assert [EventExportRow.model_validate_json(line) for line in lines[1:]] == list(
        rows
    )
    assert job.artifact.sha256 == hashlib.sha256(stored.data).hexdigest()
    assert job.artifact.byte_size == len(stored.data)
    assert world.exporter.datasets == [ExportDataset.EVENTS]


async def test_run_export_sidecar_describes_licence_filters_count_and_checksum() -> (
    None
):
    world = ExchangeWorld(rows=FakeExportRowSource(events=(event_row(),)))
    job_id = await world.request_export()(request())

    await world.run_export()(RunExport(job_id=job_id))

    job = world.export_job(job_id)
    assert job.sidecar is not None
    assert job.artifact is not None
    sidecar = job.sidecar
    assert sidecar.licence == PROPOSED_DATASET_LICENCE
    assert sidecar.licence.status == "proposed"
    assert sidecar.filters == FILTERS
    assert sidecar.row_count == 1
    assert sidecar.checksum == job.artifact.sha256
    assert sidecar.generator == GENERATOR
    assert sidecar.generated_at >= NOW
    assert str(job_id) in sidecar.citation
    assert "hazard_type=glof; place_code=pk.gb.test" in sidecar.citation
    assert "CC-BY-4.0 (proposed)" in sidecar.citation
    stored = world.artifacts.objects[f"exports/{job_id}/events.sidecar.json"]
    assert stored.media_type == "application/json"
    assert json.loads(stored.data) == sidecar.to_json_dict()


async def test_run_export_reads_rows_as_the_rebuilt_requesting_actor() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request(ExportDataset.CLAIMS))

    await world.run_export()(RunExport(job_id=job_id))

    assert world.rows.calls == [(ExportDataset.CLAIMS, FILTERS, CITIZEN)]
    assert world.export_job(job_id).status is JobStatus.COMPLETED


async def test_run_export_of_claims_writes_every_claim() -> None:
    rows = (claim_row(), claim_row())
    world = ExchangeWorld(rows=FakeExportRowSource(claims=rows))
    job_id = await world.request_export()(request(ExportDataset.CLAIMS))

    await world.run_export()(RunExport(job_id=job_id))

    job = world.export_job(job_id)
    assert job.sidecar is not None
    assert job.sidecar.row_count == len(rows)


async def test_run_export_of_reports_rounds_positions_again() -> None:
    world = ExchangeWorld(
        rows=FakeExportRowSource(reports=(report_row(74.123456, 36.987654),))
    )
    job_id = await world.request_export()(request(ExportDataset.REPORTS, MODERATOR))

    await world.run_export()(RunExport(job_id=job_id))

    data = world.artifacts.objects[f"exports/{job_id}/reports.csv"].data
    (line,) = data.decode().splitlines()[1:]
    exported = ReportExportRow.model_validate_json(line)
    assert exported.coordinates == Coordinates(longitude=74.12, latitude=36.99)


async def test_run_export_records_started_then_completed_events() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())

    await world.run_export()(RunExport(job_id=job_id))

    assert [type(event) for event in world.uow.committed_events] == [
        ExportRequested,
        ExportStarted,
        ExportCompleted,
    ]


# --------------------------------------------------------------------------- #
# RunExport, repeats and missing jobs                                         #
# --------------------------------------------------------------------------- #


async def test_run_export_of_final_job_returns_none_and_changes_nothing() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())
    await world.run_export()(RunExport(job_id=job_id))
    before = world.export_job(job_id)

    repeated = await world.run_export()(RunExport(job_id=job_id))

    assert repeated is None
    assert world.export_job(job_id) == before


async def test_run_export_of_running_job_returns_none_and_writes_nothing() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())
    queued = world.export_job(job_id)
    world.uow.export_jobs.committed[job_id] = queued.start(
        clock=world.deps.clock, ids=world.deps.ids
    ).state

    repeated = await world.run_export()(RunExport(job_id=job_id))

    assert repeated is None
    assert world.artifacts.objects == {}
    assert world.export_job(job_id).status is JobStatus.RUNNING


async def test_run_export_of_unknown_job_raises_not_found() -> None:
    world = ExchangeWorld()

    with pytest.raises(ExportJobNotFoundError):
        await world.run_export()(RunExport(job_id=MODERATOR_ID))


# --------------------------------------------------------------------------- #
# RunExport, failures                                                         #
# --------------------------------------------------------------------------- #


async def test_run_export_for_vanished_user_fails_without_reading_rows() -> None:
    world = ExchangeWorld(actors=(OTHER,))
    job_id = await world.request_export()(request())

    finished = await world.run_export()(RunExport(job_id=job_id))

    assert finished is not None
    assert finished.status is JobStatus.FAILED
    assert finished.error_summary == EXPORT_DENIED_SUMMARY
    assert world.rows.calls == []
    assert world.artifacts.objects == {}
    assert isinstance(world.uow.committed_events[-1], ExportFailed)


async def test_run_export_of_reports_after_role_loss_fails_as_denied() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request(ExportDataset.REPORTS, MODERATOR))
    world.actors.actors[MODERATOR_ID] = Actor(user_id=MODERATOR_ID)

    await world.run_export()(RunExport(job_id=job_id))

    assert world.export_job(job_id).error_summary == EXPORT_DENIED_SUMMARY


@pytest.mark.parametrize(
    ("failure", "summary"),
    [
        (ValidationError(ROW_TEXT), EXPORT_INVALID_SUMMARY),
        (NotFoundError(ROW_TEXT), EXPORT_NOT_FOUND_SUMMARY),
        (ConflictError(ROW_TEXT), EXPORT_FAILED_SUMMARY),
    ],
)
async def test_run_export_failing_rows_records_fixed_summary_and_no_artifact(
    failure: Exception, summary: str
) -> None:
    rows = FakeExportRowSource(events=(event_row(), event_row()))
    rows.failure = failure
    rows.fail_after = 1
    world = ExchangeWorld(rows=rows)
    job_id = await world.request_export()(request())

    await world.run_export()(RunExport(job_id=job_id))

    job = world.export_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_summary == summary
    assert ROW_TEXT not in (job.error_summary or "")
    assert job.artifact is None
    assert world.artifacts.objects == {}


async def test_run_export_unexpected_error_records_failure_and_reraises() -> None:
    world = ExchangeWorld()
    world.exporter.failure = RuntimeError(ROW_TEXT)
    job_id = await world.request_export()(request())

    with pytest.raises(RuntimeError):
        await world.run_export()(RunExport(job_id=job_id))

    job = world.export_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_summary == EXPORT_INTERNAL_SUMMARY


async def test_run_export_over_size_limit_fails_as_too_large() -> None:
    world = ExchangeWorld(rows=FakeExportRowSource(events=(event_row(),)))
    job_id = await world.request_export()(request())

    await world.run_export(max_bytes=10)(RunExport(job_id=job_id))

    assert world.export_job(job_id).error_summary == EXPORT_TOO_LARGE_SUMMARY


async def test_run_export_over_row_limit_fails_as_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handlers_module, "EXPORT_MAX_ROWS", 1)
    world = ExchangeWorld(rows=FakeExportRowSource(events=(event_row(), event_row())))
    job_id = await world.request_export()(request())

    await world.run_export()(RunExport(job_id=job_id))

    assert world.export_job(job_id).error_summary == EXPORT_TOO_LARGE_SUMMARY


async def test_run_export_with_row_of_other_dataset_fails_as_invalid() -> None:
    world = ExchangeWorld(rows=FakeExportRowSource(mixed=(claim_row(),)))
    job_id = await world.request_export()(request())

    await world.run_export()(RunExport(job_id=job_id))

    assert world.export_job(job_id).error_summary == EXPORT_INVALID_SUMMARY


async def test_run_export_when_sidecar_cannot_be_stored_fails_the_job() -> None:
    world = ExchangeWorld()
    world.artifacts.failure = ConflictError(ROW_TEXT)
    job_id = await world.request_export()(request())

    await world.run_export()(RunExport(job_id=job_id))

    assert world.export_job(job_id).error_summary == EXPORT_FAILED_SUMMARY


class _ConcurrentlyFailingRows(FakeExportRowSource):
    """Fails the job through the unit of work while rows are read.

    Implements: Fake.
    """

    def __init__(self, world_ref: list[ExchangeWorld]) -> None:
        super().__init__()
        self._world_ref = world_ref

    def events(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[EventExportRow]:
        return self._fail_then_yield()

    async def _fail_then_yield(self) -> AsyncIterator[EventExportRow]:
        world = self._world_ref[0]
        async with world.uow as uow:
            (job,) = uow.export_jobs.committed.values()
            failed = job.fail(
                "stopped elsewhere", clock=world.deps.clock, ids=world.deps.ids
            )
            await uow.export_jobs.save(failed.state)
            await uow.commit()
        yield event_row()


async def test_run_export_of_job_failed_meanwhile_keeps_the_other_failure() -> None:
    world_ref: list[ExchangeWorld] = []
    world = ExchangeWorld(rows=_ConcurrentlyFailingRows(world_ref))
    world_ref.append(world)
    job_id = await world.request_export()(request())

    finished = await world.run_export()(RunExport(job_id=job_id))

    assert finished is not None
    assert finished.error_summary == "stopped elsewhere"
    assert world.export_job(job_id).error_summary == "stopped elsewhere"


# --------------------------------------------------------------------------- #
# CancelExport                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("actor", [CITIZEN, MODERATOR])
async def test_cancel_export_by_owner_or_moderator_cancels_queued_job(
    actor: Actor,
) -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())

    await world.cancel_export()(CancelExport(actor=actor, job_id=job_id))

    assert world.export_job(job_id).status is JobStatus.CANCELLED
    assert isinstance(world.uow.committed_events[-1], ExportCancelled)


async def test_cancel_export_by_other_user_raises_not_found() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())

    with pytest.raises(ExportJobNotFoundError):
        await world.cancel_export()(CancelExport(actor=OTHER, job_id=job_id))

    assert world.export_job(job_id).status is JobStatus.QUEUED


async def test_cancel_export_by_anonymous_raises_permission_denied() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())

    with pytest.raises(PermissionDeniedError):
        await world.cancel_export()(CancelExport(actor=ANONYMOUS, job_id=job_id))


async def test_cancel_export_of_finished_job_raises_job_state_error() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())
    await world.run_export()(RunExport(job_id=job_id))

    with pytest.raises(JobStateError):
        await world.cancel_export()(CancelExport(actor=CITIZEN, job_id=job_id))


async def test_cancel_export_of_unknown_job_raises_not_found() -> None:
    world = ExchangeWorld()
    unknown = USER_ID

    with pytest.raises(ExportJobNotFoundError):
        await world.cancel_export()(CancelExport(actor=CITIZEN, job_id=unknown))


def test_export_row_union_names_each_dataset() -> None:
    rows: tuple[ExportRow, ...] = (event_row(), claim_row(), report_row())

    datasets = [row.dataset for row in rows]

    assert datasets == [
        ExportDataset.EVENTS,
        ExportDataset.CLAIMS,
        ExportDataset.REPORTS,
    ]


# --------------------------------------------------------------------------- #
# Visibility                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("actor", "expected"), [(CITIZEN, "public"), (MODERATOR, "moderation")]
)
async def test_request_export_fixes_visibility_from_requesting_actor(
    actor: Actor, expected: str
) -> None:
    world = ExchangeWorld()

    job_id = await world.request_export()(request(actor=actor))

    assert world.export_job(job_id).visibility == expected
    (requested,) = world.uow.committed_events
    assert isinstance(requested, ExportRequested)
    assert requested.visibility == expected


async def test_run_export_writes_visibility_into_the_sidecar() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request(ExportDataset.REPORTS, MODERATOR))

    await world.run_export()(RunExport(job_id=job_id))

    job = world.export_job(job_id)
    assert job.sidecar is not None
    assert job.sidecar.visibility == "moderation"
    stored = world.artifacts.objects[f"exports/{job_id}/reports.sidecar.json"]
    assert json.loads(stored.data)["visibility"] == "moderation"


async def test_run_export_of_public_job_reads_without_roles_gained_since() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request())
    promoted = Actor(
        user_id=USER_ID, roles=frozenset({Role.CITIZEN, Role.MODERATOR, Role.ADMIN})
    )
    world.actors.actors[USER_ID] = promoted

    await world.run_export()(RunExport(job_id=job_id))

    ((_, _, reader),) = world.rows.calls
    assert reader == Actor(user_id=USER_ID, roles=frozenset({Role.CITIZEN}))


async def test_run_export_of_moderation_job_reads_as_the_current_moderator() -> None:
    world = ExchangeWorld()
    job_id = await world.request_export()(request(actor=MODERATOR))

    await world.run_export()(RunExport(job_id=job_id))

    ((_, _, reader),) = world.rows.calls
    assert reader == MODERATOR
