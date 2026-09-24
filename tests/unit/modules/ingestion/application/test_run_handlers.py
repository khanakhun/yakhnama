"""Unit tests for requesting and executing ingestion runs, with fakes."""

import pytest

from tests.fakes.ingestion import InMemoryIngestionUnitOfWork, RecordingPipelineFactory
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.ingestion.application.support import (
    ADAPTER,
    ADMIN,
    MODERATOR,
    NOW,
    World,
    csv,
    make_dataset,
    make_run,
    row,
    sha256,
    status_of,
)
from yakhnama.modules.ingestion.application.commands import (
    ExecuteIngestionRun,
    RunIngestion,
)
from yakhnama.modules.ingestion.application.handlers import (
    ExecuteIngestionRunHandler,
    RunIngestionHandler,
)
from yakhnama.modules.ingestion.application.ports import INGESTION_RUN_TASK
from yakhnama.modules.ingestion.domain.errors import (
    DatasetNotFoundError,
    DatasetStatusError,
    DatasetVersionNotFoundError,
    IngestionRunNotFoundError,
    RunStateError,
)
from yakhnama.modules.ingestion.domain.events import (
    IngestionRunFinished,
    IngestionRunRequested,
    IngestionRunStarted,
)
from yakhnama.modules.ingestion.domain.value_objects import RunStatus
from yakhnama.shared_kernel.errors import NotFoundError, PermissionDeniedError

UNKNOWN_ID = make_dataset().id


def request_handler(
    world: World, pipelines: RecordingPipelineFactory | None = None
) -> RunIngestionHandler:
    return RunIngestionHandler(
        uow_factory=world.uow_factory,
        adapters=world.adapters,
        pipelines=world.pipelines if pipelines is None else pipelines,
        task_queue=world.queue,
        clock=world.clock,
        ids=world.ids,
    )


def execute_handler(
    world: World, pipelines: RecordingPipelineFactory | None = None
) -> ExecuteIngestionRunHandler:
    return ExecuteIngestionRunHandler(
        uow_factory=world.uow_factory,
        adapters=world.adapters,
        pipelines=world.pipelines if pipelines is None else pipelines,
        clock=world.clock,
        ids=world.ids,
    )


def run_command(world: World, adapter_name: str = ADAPTER) -> RunIngestion:
    return RunIngestion(
        actor=ADMIN,
        dataset_id=world.dataset.id,
        version_id=world.version.id,
        adapter_name=adapter_name,
    )


# ------------------------------------------------------------------ request


async def test_run_ingestion_by_admin_commits_pending_run_and_enqueues_it() -> None:
    world = World(csv(row()))

    run_id = await request_handler(world)(run_command(world))

    stored = world.uow.ingestion_runs.committed[run_id]
    assert stored.status is RunStatus.PENDING
    assert stored.triggered_by == ADMIN.user_id
    assert isinstance(world.uow.committed_events[-1], IngestionRunRequested)
    [task] = world.queue.of(INGESTION_RUN_TASK)
    assert dict(task.payload) == {"run_id": run_id}
    assert task.idempotency_key == f"{INGESTION_RUN_TASK}:{run_id}"


async def test_run_ingestion_by_moderator_is_denied_before_reading() -> None:
    world = World(csv(row()))
    command = run_command(world).model_copy(update={"actor": MODERATOR})

    with pytest.raises(PermissionDeniedError):
        await request_handler(world)(command)

    assert world.uow_factory.calls == 0
    assert world.queue.enqueued == []


async def test_run_ingestion_with_unknown_adapter_raises_not_found() -> None:
    world = World(csv(row()))

    with pytest.raises(NotFoundError) as caught:
        await request_handler(world)(run_command(world, "unknown_adapter"))

    assert caught.value.details == {"adapter_name": "unknown_adapter"}
    assert world.queue.enqueued == []


async def test_run_ingestion_with_adapter_without_pipeline_raises_not_found() -> None:
    world = World(csv(row()))
    pipelines = RecordingPipelineFactory(
        clock=world.clock, ids=world.ids, adapter_names=()
    )

    with pytest.raises(NotFoundError, match="no pipeline"):
        await request_handler(world, pipelines)(run_command(world))

    assert world.uow_factory.calls == 0


async def test_run_ingestion_of_unknown_dataset_raises_not_found() -> None:
    world = World(csv(row()))
    command = run_command(world).model_copy(update={"dataset_id": UNKNOWN_ID})

    with pytest.raises(DatasetNotFoundError):
        await request_handler(world)(command)


async def test_run_ingestion_of_unknown_version_raises_not_found() -> None:
    world = World(csv(row()))
    command = run_command(world).model_copy(update={"version_id": UNKNOWN_ID})

    with pytest.raises(DatasetVersionNotFoundError):
        await request_handler(world)(command)


async def test_run_ingestion_of_deprecated_dataset_raises_status_error() -> None:
    world = World(csv(row()))
    world.uow.datasets.committed[world.dataset.id] = world.dataset.deprecate(
        clock=world.clock, ids=world.ids
    ).state

    with pytest.raises(DatasetStatusError):
        await request_handler(world)(run_command(world))

    assert world.queue.enqueued == []


async def test_run_ingestion_with_broker_down_keeps_the_pending_run() -> None:
    world = World(csv(row()))
    world.queue.failure = ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        await request_handler(world)(run_command(world))

    pending = [
        run
        for run in world.uow.ingestion_runs.committed.values()
        if run.id != world.run.id
    ]
    assert [run.status for run in pending] == [RunStatus.PENDING]


# ------------------------------------------------------------------ execute


async def test_execute_ingestion_run_succeeds_and_stores_observations() -> None:
    world = World(csv(row()))

    detail = await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    assert detail.status is RunStatus.SUCCEEDED
    assert detail.counts.persisted == 1
    assert detail.input_checksum == sha256(world.content)
    assert detail.started_at == NOW
    assert world.stored_run().status is RunStatus.SUCCEEDED
    assert len(world.uow.observations.committed) == 1
    event_types = [type(event) for event in world.uow.committed_events]
    assert event_types == [IngestionRunStarted, IngestionRunFinished]


async def test_execute_finished_run_again_returns_it_without_running() -> None:
    world = World(csv(row()))
    handler = execute_handler(world)
    first = await handler(ExecuteIngestionRun(run_id=world.run.id))

    again = await handler(ExecuteIngestionRun(run_id=world.run.id))

    assert again == first
    assert len(world.pipelines.created) == 1


async def test_execute_unknown_run_raises_not_found() -> None:
    world = World(csv(row()))

    with pytest.raises(IngestionRunNotFoundError):
        await execute_handler(world)(ExecuteIngestionRun(run_id=UNKNOWN_ID))


async def test_execute_running_run_is_refused() -> None:
    world = World(csv(row()))
    world.uow.ingestion_runs.committed[world.run.id] = world.running()

    with pytest.raises(RunStateError):
        await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    assert world.pipelines.created == []


async def test_execute_run_whose_version_is_gone_raises_not_found() -> None:
    world = World(csv(row()))
    uow = InMemoryIngestionUnitOfWork(datasets=[world.dataset], runs=[world.run])
    handler = ExecuteIngestionRunHandler(
        uow_factory=InMemoryUnitOfWorkFactory(uow),
        adapters=world.adapters,
        pipelines=world.pipelines,
        clock=world.clock,
        ids=world.ids,
    )

    with pytest.raises(DatasetVersionNotFoundError):
        await handler(ExecuteIngestionRun(run_id=world.run.id))


async def test_execute_run_of_deprecated_dataset_fails_it_while_pending() -> None:
    world = World(csv(row()))
    world.uow.datasets.committed[world.dataset.id] = world.dataset.deprecate(
        clock=world.clock, ids=world.ids
    ).state

    detail = await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    assert detail.status is RunStatus.FAILED
    assert detail.started_at is None
    assert "deprecated" in detail.report.issues[0].message
    assert world.pipelines.created == []


async def test_execute_run_of_unregistered_adapter_fails_it() -> None:
    world = World(csv(row()))
    world.run = make_run(world.version, adapter_name="gone_adapter")
    world.uow.ingestion_runs.committed[world.run.id] = world.run

    detail = await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    assert detail.status is RunStatus.FAILED
    assert "no longer registered" in detail.report.issues[0].message


async def test_execute_run_without_pipeline_fails_it() -> None:
    world = World(csv(row()))
    pipelines = RecordingPipelineFactory(
        clock=world.clock, ids=world.ids, adapter_names=()
    )

    detail = await execute_handler(world, pipelines)(
        ExecuteIngestionRun(run_id=world.run.id)
    )

    assert detail.status is RunStatus.FAILED
    assert "no pipeline" in detail.report.issues[0].message


async def test_execute_run_with_unreachable_source_marks_failed_and_reraises() -> None:
    world = World(csv(row()))
    world.adapter.failure = OSError("connection refused")

    with pytest.raises(OSError, match="connection refused"):
        await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    stored = world.stored_run()
    assert stored.status is RunStatus.FAILED
    assert stored.input_checksum is None
    assert stored.report.issues[-1].message == "the fetch stage failed with OSError"
    assert isinstance(world.uow.committed_events[-1], IngestionRunFinished)


async def test_execute_run_with_storage_outage_rolls_back_and_marks_failed() -> None:
    world = World(csv(row()))
    world.uow.observations.failure = RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    stored = world.stored_run()
    assert stored.status is RunStatus.FAILED
    assert stored.input_checksum == sha256(world.content)
    assert stored.counts.persisted == 0
    assert stored.report.issues[-1].stage == "persist"
    assert world.uow.observations.committed == {}


async def test_execute_run_with_checksum_mismatch_fails_without_raising() -> None:
    world = World(csv(row()))
    world.adapter.payloads[world.version.id] = csv(row(value="1.0"))

    detail = await execute_handler(world)(ExecuteIngestionRun(run_id=world.run.id))

    assert detail.status is RunStatus.FAILED
    assert status_of(world) is RunStatus.FAILED
    assert detail.report.issues[0].stage == "fetch"
