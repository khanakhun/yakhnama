"""Unit tests for ``yakhnama.modules.exchange.domain.entities``."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.base import FACTORY_IDS
from tests.factories.exchange import (
    ExportJobTestFactory,
    ImportJobTestFactory,
    artifact_for,
    artifact_ref,
    sidecar_for,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.exchange.domain.entities import (
    EXPORT_TRANSITIONS,
    IMPORT_TRANSITIONS,
    ExportJob,
    ImportJob,
    can_transition,
)
from yakhnama.modules.exchange.domain.errors import JobStateError
from yakhnama.modules.exchange.domain.events import (
    ExportCancelled,
    ExportCompleted,
    ExportFailed,
    ExportStarted,
    ImportCompleted,
    ImportFailed,
    ImportStarted,
)
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFilters,
    ExportFormat,
    ImportWrites,
    JobStatus,
    RowIssue,
    ValidationReport,
)
from yakhnama.shared_kernel.events import AggregateChange

REQUESTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 9, 2, tzinfo=UTC)
FAILURE = "The worker lost its connection to storage."


def _clock() -> SteppingClock:
    return SteppingClock(CHANGED_AT, timedelta(seconds=1))


def _ids() -> SequentialIdGenerator:
    return SequentialIdGenerator(seed=17)


def _export(**fields: object) -> ExportJob:
    return ExportJobTestFactory.build(
        factory_use_construct=False, **{"requested_at": REQUESTED_AT, **fields}
    )


def _import(**fields: object) -> ImportJob:
    return ImportJobTestFactory.build(
        factory_use_construct=False, **{"requested_at": REQUESTED_AT, **fields}
    )


def _running_export(**fields: object) -> ExportJob:
    return _export(**fields).start(clock=_clock(), ids=_ids()).state


def _running_import(**fields: object) -> ImportJob:
    return _import(**fields).start(clock=_clock(), ids=_ids()).state


def _clean_report(rows: int = 2) -> ValidationReport:
    return ValidationReport.from_issues(rows, [])


def _blocking_report() -> ValidationReport:
    return ValidationReport.from_issues(
        2, [RowIssue(row_number=2, field="title", message="a value is required")]
    )


def _writes(count: int = 2) -> ImportWrites:
    return ImportWrites(
        created_ids=tuple(FACTORY_IDS.new_id() for _ in range(count)),
        lineage_source_id=FACTORY_IDS.new_id(),
        batches_applied=1,
    )


# --------------------------------------------------------------------------- #
# Transition tables                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("table", "current", "target", "expected"),
    [
        (EXPORT_TRANSITIONS, JobStatus.QUEUED, JobStatus.CANCELLED, True),
        (EXPORT_TRANSITIONS, JobStatus.RUNNING, JobStatus.CANCELLED, False),
        (EXPORT_TRANSITIONS, JobStatus.COMPLETED, JobStatus.FAILED, False),
        (IMPORT_TRANSITIONS, JobStatus.QUEUED, JobStatus.CANCELLED, False),
        (IMPORT_TRANSITIONS, JobStatus.RUNNING, JobStatus.COMPLETED, True),
        (IMPORT_TRANSITIONS, JobStatus.FAILED, JobStatus.RUNNING, False),
    ],
)
def test_can_transition_follows_the_table(
    table: dict[JobStatus, frozenset[JobStatus]],
    current: JobStatus,
    target: JobStatus,
    expected: object,
) -> None:
    assert can_transition(table, current, target) is expected


def test_transition_tables_never_leave_a_final_status() -> None:
    for table in (EXPORT_TRANSITIONS, IMPORT_TRANSITIONS):
        assert not any(status.is_final for status in table)


# --------------------------------------------------------------------------- #
# ExportJob                                                                   #
# --------------------------------------------------------------------------- #


def test_export_job_start_records_start_and_event() -> None:
    job = _export()

    change = job.start(clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.RUNNING
    assert change.state.started_at == CHANGED_AT
    assert change.state.version == 2  # reason: one change after creation
    (event,) = change.events
    assert isinstance(event, ExportStarted)
    assert event.event_type == "exchange.export_started"
    assert (event.aggregate_id, event.version) == (job.id, 2)


def test_export_job_complete_records_artifact_sidecar_and_counts() -> None:
    job = _running_export()
    artifact = artifact_for(job)
    sidecar = sidecar_for(job, artifact, row_count=7)

    change = job.complete(artifact, sidecar, clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.COMPLETED
    assert change.state.artifact == artifact
    assert change.state.sidecar == sidecar
    assert change.state.finished_at == CHANGED_AT
    (event,) = change.events
    assert isinstance(event, ExportCompleted)
    assert (event.row_count, event.byte_size) == (7, artifact.byte_size)


def test_export_job_fail_from_running_records_summary() -> None:
    job = _running_export()

    change = job.fail(FAILURE, clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.FAILED
    assert change.state.error_summary == FAILURE
    (event,) = change.events
    assert isinstance(event, ExportFailed)
    assert "error_summary" not in event.model_dump()


def test_export_job_fail_from_queued_needs_no_start() -> None:
    change = _export().fail(FAILURE, clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.FAILED
    assert change.state.started_at is None


def test_export_job_cancel_from_queued_records_finish() -> None:
    change = _export().cancel(clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.CANCELLED
    assert change.state.finished_at == CHANGED_AT
    assert isinstance(change.events[0], ExportCancelled)


def test_export_job_cancel_when_running_raises_job_state_error() -> None:
    job = _running_export()

    with pytest.raises(JobStateError) as caught:
        job.cancel(clock=_clock(), ids=_ids())

    assert caught.value.details["status"] == "running"
    assert caught.value.details["action"] == "cancel"


def test_export_job_complete_when_queued_raises_job_state_error() -> None:
    job = _export()
    artifact = artifact_for(job)

    with pytest.raises(JobStateError, match="does not allow"):
        job.complete(artifact, sidecar_for(job, artifact), clock=_clock(), ids=_ids())


def test_export_job_start_when_completed_raises_job_state_error() -> None:
    job = _running_export()
    artifact = artifact_for(job)
    done = job.complete(
        artifact, sidecar_for(job, artifact), clock=_clock(), ids=_ids()
    ).state

    with pytest.raises(JobStateError):
        done.start(clock=_clock(), ids=_ids())


def test_export_job_fail_with_unsafe_summary_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        _running_export().fail("line one\nline two", clock=_clock(), ids=_ids())


@pytest.mark.parametrize(
    ("sidecar_change", "match"),
    [
        ({"dataset": ExportDataset.CLAIMS}, "describe this job"),
        ({"format": ExportFormat.CSV}, "describe this job"),
        ({"filters": ExportFilters(hazard_type="glof")}, "describe this job"),
        ({"checksum": "0" * 64}, "checksum"),
    ],
)
def test_export_job_complete_with_mismatched_sidecar_raises_validation_error(
    sidecar_change: dict[str, object], match: str
) -> None:
    job = _running_export()
    artifact = artifact_for(job)
    sidecar = sidecar_for(job, artifact).model_copy(update=sidecar_change)

    with pytest.raises(PydanticValidationError, match=match):
        job.complete(artifact, sidecar, clock=_clock(), ids=_ids())


def test_export_job_complete_with_wrong_media_type_raises_validation_error() -> None:
    job = _running_export()
    artifact = artifact_ref(media_type="text/csv")

    with pytest.raises(PydanticValidationError, match="media type"):
        job.complete(artifact, sidecar_for(job, artifact), clock=_clock(), ids=_ids())


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"status": JobStatus.RUNNING}, "running job needs"),
        ({"status": JobStatus.QUEUED, "error_summary": FAILURE}, "queued job"),
        ({"status": JobStatus.FAILED, "finished_at": CHANGED_AT}, "failed job"),
        ({"status": JobStatus.CANCELLED}, "cancelled job"),
        (
            {"status": JobStatus.RUNNING, "started_at": REQUESTED_AT - timedelta(1)},
            "requested_at <= started_at",
        ),
    ],
)
def test_export_job_with_inconsistent_state_raises_validation_error(
    fields: dict[str, object], match: str
) -> None:
    with pytest.raises(PydanticValidationError, match=match):
        _export(**fields)


def test_export_job_normalises_timestamps_to_utc() -> None:
    local = datetime(2026, 9, 1, 5, tzinfo=timezone(timedelta(hours=5)))

    job = _export(requested_at=local)

    assert job.requested_at == REQUESTED_AT
    assert job.requested_at.tzinfo is UTC


_EXPORT_ACTIONS: dict[
    str, tuple[JobStatus, Callable[[ExportJob], AggregateChange[ExportJob]]]
] = {
    "start": (JobStatus.RUNNING, lambda job: job.start(clock=_clock(), ids=_ids())),
    "complete": (
        JobStatus.COMPLETED,
        lambda job: job.complete(
            artifact_for(job),
            sidecar_for(job, artifact_for(job)).model_copy(
                update={"checksum": "b" * 64}
            ),
            clock=_clock(),
            ids=_ids(),
        ),
    ),
    "fail": (
        JobStatus.FAILED,
        lambda job: job.fail(FAILURE, clock=_clock(), ids=_ids()),
    ),
    "cancel": (JobStatus.CANCELLED, lambda job: job.cancel(clock=_clock(), ids=_ids())),
}


@given(actions=st.lists(st.sampled_from(sorted(_EXPORT_ACTIONS)), max_size=6))
def test_export_job_any_action_sequence_follows_transition_table(
    actions: list[str],
) -> None:
    job = _export()

    for action in actions:
        target, perform = _EXPORT_ACTIONS[action]
        allowed = can_transition(EXPORT_TRANSITIONS, job.status, target)
        if not allowed:
            with pytest.raises(JobStateError):
                perform(job)
            continue
        try:
            change = perform(job)
        except PydanticValidationError:
            # Only the arranged completion with a foreign checksum is refused.
            assert action == "complete"
            continue
        assert change.state.status is target
        assert change.state.version == job.version + 1
        job = change.state

    assert ExportJob.model_validate(job.model_dump()) == job


# --------------------------------------------------------------------------- #
# ImportJob                                                                   #
# --------------------------------------------------------------------------- #


def test_import_job_start_records_start_and_event() -> None:
    change = _import().start(clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.RUNNING
    assert isinstance(change.events[0], ImportStarted)
    assert change.events[0].event_type == "exchange.import_started"


def test_import_job_dry_run_complete_records_report_and_nothing_created() -> None:
    job = _running_import(dry_run=True)

    change = job.complete(_blocking_report(), clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.COMPLETED
    assert change.state.created_ids == ()
    (event,) = change.events
    assert isinstance(event, ImportCompleted)
    assert (event.dry_run, event.rows_seen, event.rows_rejected) == (True, 2, 1)
    assert event.created_count == 0


def test_import_job_real_complete_records_created_ids_and_lineage() -> None:
    job = _running_import()
    writes = _writes()

    change = job.complete(_clean_report(), writes, clock=_clock(), ids=_ids())

    assert change.state.created_ids == writes.created_ids
    assert change.state.created_count == 2  # reason: arranged writes
    assert change.state.writes.lineage_source_id == writes.lineage_source_id
    event = change.events[0]
    assert isinstance(event, ImportCompleted)
    assert (event.created_count, event.batches_applied) == (2, 1)


def test_import_job_real_complete_with_blocking_errors_and_nothing_created() -> None:
    change = _running_import().complete(_blocking_report(), clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.COMPLETED
    assert change.state.created_count == 0


def test_import_job_real_complete_with_blocking_errors_and_ids_raises() -> None:
    job = _running_import()

    with pytest.raises(PydanticValidationError, match="blocking errors"):
        job.complete(_blocking_report(), _writes(1), clock=_clock(), ids=_ids())


def test_import_job_complete_with_more_ids_than_valid_rows_raises() -> None:
    job = _running_import()

    with pytest.raises(PydanticValidationError, match="one event per valid row"):
        job.complete(_clean_report(rows=1), _writes(2), clock=_clock(), ids=_ids())


def test_import_job_dry_run_with_writes_raises_validation_error() -> None:
    job = _running_import(dry_run=True)

    with pytest.raises(PydanticValidationError, match="dry run"):
        job.complete(_clean_report(), _writes(), clock=_clock(), ids=_ids())


def test_import_job_fail_keeps_committed_batches() -> None:
    job = _running_import()
    writes = _writes(1)

    change = job.fail(FAILURE, _clean_report(), writes, clock=_clock(), ids=_ids())

    assert change.state.status is JobStatus.FAILED
    assert change.state.created_ids == writes.created_ids
    event = change.events[0]
    assert isinstance(event, ImportFailed)
    assert event.created_count == 1


def test_import_job_fail_from_queued_without_report_counts_zero_rows() -> None:
    change = _import().fail(FAILURE, clock=_clock(), ids=_ids())

    event = change.events[0]
    assert isinstance(event, ImportFailed)
    assert (event.rows_seen, event.rows_rejected, event.created_count) == (0, 0, 0)


def test_import_job_complete_when_queued_raises_job_state_error() -> None:
    with pytest.raises(JobStateError) as caught:
        _import().complete(_clean_report(), clock=_clock(), ids=_ids())

    assert caught.value.details["job_kind"] == "import_job"


def test_import_job_fail_when_failed_raises_job_state_error() -> None:
    failed = _import().fail(FAILURE, clock=_clock(), ids=_ids()).state

    with pytest.raises(JobStateError):
        failed.fail(FAILURE, clock=_clock(), ids=_ids())


def test_import_job_stored_as_cancelled_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="never cancelled"):
        _import(status=JobStatus.CANCELLED, finished_at=CHANGED_AT)


def test_import_job_completed_without_report_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="completed job needs"):
        _import(
            status=JobStatus.COMPLETED,
            started_at=REQUESTED_AT,
            finished_at=CHANGED_AT,
        )
