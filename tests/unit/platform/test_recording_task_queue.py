"""Unit tests for the canonical ``TaskQueue`` Fake in ``tests/fakes/tasks.py``."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from tests.fakes.tasks import RecordingTaskQueue
from yakhnama.shared_kernel.tasks import TaskId, TaskQueue

REPORT_ID = UUID("01927b5e-0000-7000-8000-000000000001")


async def test_recording_task_queue_enqueue_records_task_with_sequential_ids() -> None:
    queue = RecordingTaskQueue()

    first = await queue.enqueue(
        "reports.run_triage", {"report_id": REPORT_ID}, idempotency_key="k-1"
    )
    second = await queue.enqueue("media.scan", {}, delay_seconds=5)

    assert (first, second) == (TaskId(value="task-1"), TaskId(value="task-2"))
    assert [task.task_name for task in queue.enqueued] == [
        "reports.run_triage",
        "media.scan",
    ]
    assert dict(queue.enqueued[0].payload) == {"report_id": REPORT_ID}
    assert queue.enqueued[0].idempotency_key == "k-1"
    assert queue.enqueued[1].delay_seconds == 5


async def test_recording_task_queue_of_filters_by_task_name() -> None:
    queue = RecordingTaskQueue()
    await queue.enqueue("reports.run_triage", {"report_id": REPORT_ID})
    await queue.enqueue("media.scan", {})

    triage = queue.of("reports.run_triage")

    assert [task.task_id.value for task in triage] == ["task-1"]
    assert queue.of("outbox.relay_once") == []


async def test_recording_task_queue_enqueue_contract_breach_records_nothing() -> None:
    queue = RecordingTaskQueue()

    with pytest.raises(ValidationError):
        await queue.enqueue("reports.run_triage", {"latitude": 35.9})

    assert queue.enqueued == []


async def test_recording_task_queue_failure_is_raised_after_validation() -> None:
    queue = RecordingTaskQueue()
    queue.failure = ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        await queue.enqueue("reports.run_triage", {"report_id": REPORT_ID})

    assert queue.enqueued == []


def test_recording_task_queue_satisfies_the_port() -> None:
    queue: TaskQueue = RecordingTaskQueue()

    assert callable(queue.enqueue)
