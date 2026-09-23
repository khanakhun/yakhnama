"""The canonical in-memory ``TaskQueue`` for application and API tests.

``RecordingTaskQueue`` validates every call into a ``ScheduledTask`` exactly as the
Taskiq adapter does, so a payload that breaks the contract (floats, long strings,
bad keys) fails in unit tests too, and records it instead of running anything. Task
ids are deterministic: ``task-1``, ``task-2`` and so on.

Patterns: Fake.
"""

from collections.abc import Mapping

from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId


class RecordingTaskQueue:
    """``TaskQueue`` that records each enqueued task.

    Implements: Fake.

    Attributes:
        enqueued: Every accepted task, in order.
        failure: Raised by ``enqueue`` (after validation, before recording) when
            set, to simulate an unavailable broker.
    """

    def __init__(self) -> None:
        """Create an empty queue."""
        self.enqueued: list[ScheduledTask] = []
        self.failure: Exception | None = None
        self._next_number = 1

    async def enqueue(
        self,
        task_name: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str | None = None,
        delay_seconds: int = 0,
    ) -> TaskId:
        """Validate and record one task; see ``TaskQueue.enqueue``.

        Args:
            task_name: A ``TaskName``.
            payload: Ids and non-personal scalars.
            idempotency_key: Recorded on the task.
            delay_seconds: Recorded on the task; nothing waits.

        Returns:
            The next deterministic id.

        Raises:
            pydantic.ValidationError: If the arguments break the contract.
        """
        task = ScheduledTask.model_validate(
            {
                "task_id": {"value": f"task-{self._next_number}"},
                "task_name": task_name,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
                "delay_seconds": delay_seconds,
            }
        )
        if self.failure is not None:
            raise self.failure
        self._next_number += 1
        self.enqueued.append(task)
        return task.task_id

    def of(self, task_name: str) -> list[ScheduledTask]:
        """Return the recorded tasks named ``task_name``, in order.

        Args:
            task_name: The task name to filter by.

        Returns:
            The matching tasks; empty if none.
        """
        return [task for task in self.enqueued if task.task_name == task_name]
