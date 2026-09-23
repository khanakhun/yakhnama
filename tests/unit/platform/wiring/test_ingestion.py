"""Unit tests for ``yakhnama.platform.wiring.ingestion`` over the ingestion fakes."""

from typing import Final

import pydantic
import pytest

from tests.fakes.api import build_test_app
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.ingestion.domain.errors import IngestionRunNotFoundError
from yakhnama.platform.wiring.ingestion import (
    ExecuteIngestionRunTaskAdapter,
    IngestionTaskPayload,
)
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId

IDS: Final = SequentialIdGenerator(seed=9601)


def _task(payload: dict[str, object]) -> ScheduledTask:
    return ScheduledTask.model_validate(
        {
            "task_id": TaskId(value="t-1"),
            "task_name": "ingestion.run",
            "payload": payload,
        }
    )


def _adapter() -> ExecuteIngestionRunTaskAdapter:
    api = build_test_app()
    return ExecuteIngestionRunTaskAdapter(
        api.ingestion_services.execute_ingestion_run_handler
    )


async def test_execute_ingestion_run_task_adapter_runs_the_named_run() -> None:
    adapter = _adapter()

    with pytest.raises(IngestionRunNotFoundError):
        await adapter(_task({"run_id": str(IDS.new_id())}))


async def test_execute_ingestion_run_task_adapter_invalid_payload_raises() -> None:
    adapter = _adapter()

    with pytest.raises(pydantic.ValidationError):
        await adapter(_task({"run_id": "not-a-uuid"}))
    with pytest.raises(pydantic.ValidationError):
        await adapter(_task({"run_id": str(IDS.new_id()), "extra": 1}))


def test_ingestion_task_payload_reads_the_run_id_string() -> None:
    run_id = IDS.new_id()

    payload = IngestionTaskPayload.model_validate({"run_id": str(run_id)})

    assert payload.run_id == run_id
