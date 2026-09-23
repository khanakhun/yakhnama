"""Unit tests for ``yakhnama.platform.tasks.taskiq_adapter``.

They run over Taskiq's ``InMemoryBroker``, which executes each task in-process
through the real receiver, dependency injection included.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError
from structlog.testing import capture_logs
from taskiq import InMemoryBroker
from taskiq.message import BrokerMessage, TaskiqMessage

from yakhnama.platform.logging import is_sensitive_key
from yakhnama.platform.tasks.errors import (
    TaskDelayUnsupportedError,
    TaskFailedError,
    TaskHandlerNotBoundError,
    TaskNotRegisteredError,
    TaskQueueUnavailableError,
)
from yakhnama.platform.tasks.handlers import (
    OUTBOX_RELAY_TASK,
    REPORTS_TRIAGE_TASK,
    TaskHandlerRegistry,
)
from yakhnama.platform.tasks.scheduled import TaskSchedule
from yakhnama.platform.tasks.taskiq_adapter import (
    IDEMPOTENCY_KEY_LABEL,
    SCHEDULE_LABEL,
    TaskiqTaskQueue,
    TaskRegistry,
    execute_task,
    register_tasks,
    task_from_message,
)
from yakhnama.shared_kernel.tasks import ScheduledTask

REPORT_ID = UUID("01927b5e-0000-7000-8000-000000000001")


class RecordingHandler:
    """Task handler that records the tasks it receives and optionally fails.

    Implements: Fake.

    Attributes:
        received: Tasks received, in order.
        error: Raised on every call when set.
    """

    def __init__(self, error: Exception | None = None) -> None:
        """Create the handler."""
        self.received: list[ScheduledTask] = []
        self.error = error

    async def __call__(self, task: ScheduledTask) -> None:
        """Record ``task``, then raise ``error`` if set."""
        self.received.append(task)
        if self.error is not None:
            raise self.error


class RefusingBroker(InMemoryBroker):
    """An in-memory broker whose ``kick`` always fails, like an unreachable Redis.

    Implements: Fake.
    """

    async def kick(self, message: BrokerMessage) -> None:
        """Refuse every message."""
        refusal = "connection refused"
        raise ConnectionError(refusal)


@pytest.fixture
async def broker() -> AsyncIterator[InMemoryBroker]:
    broker = InMemoryBroker()
    await broker.startup()

    yield broker

    await broker.shutdown()


def _queue(
    broker: InMemoryBroker, handlers: TaskHandlerRegistry
) -> tuple[TaskiqTaskQueue, TaskRegistry]:
    registry = register_tasks(broker, handlers)
    return TaskiqTaskQueue(broker, registry), registry


def _message(
    task_name: str = REPORTS_TRIAGE_TASK, labels: dict[str, object] | None = None
) -> TaskiqMessage:
    return TaskiqMessage(
        task_id="abc123",
        task_name=task_name,
        labels=labels or {},
        labels_types=None,
        args=[],
        kwargs={},
    )


# --------------------------------------------------------------------------- #
# Enqueue and run round-trip                                                  #
# --------------------------------------------------------------------------- #


async def test_taskiq_task_queue_enqueue_runs_bound_handler_with_the_task(
    broker: InMemoryBroker,
) -> None:
    handler = RecordingHandler()
    handlers = TaskHandlerRegistry()
    handlers.bind({REPORTS_TRIAGE_TASK: handler})
    queue, _ = _queue(broker, handlers)

    task_id = await queue.enqueue(
        REPORTS_TRIAGE_TASK,
        {"report_id": REPORT_ID, "revision": 2, "is_retry": False, "note": None},
        idempotency_key=f"reports.run_triage:{REPORT_ID}",
    )
    await broker.wait_all()

    (received,) = handler.received
    assert received.task_id == task_id
    assert received.task_name == REPORTS_TRIAGE_TASK
    # UUIDs cross the broker as JSON strings; the handler validates them back.
    assert dict(received.payload) == {
        "report_id": str(REPORT_ID),
        "revision": 2,
        "is_retry": False,
        "note": None,
    }
    assert received.idempotency_key == f"reports.run_triage:{REPORT_ID}"
    assert received.delay_seconds == 0


async def test_taskiq_task_queue_enqueue_returns_distinct_broker_ids(
    broker: InMemoryBroker,
) -> None:
    queue, _ = _queue(broker, TaskHandlerRegistry())

    first = await queue.enqueue(OUTBOX_RELAY_TASK, {})
    second = await queue.enqueue(OUTBOX_RELAY_TASK, {})
    await broker.wait_all()

    assert first != second
    assert len(first.value) == 32


async def test_taskiq_task_queue_enqueue_without_key_sends_no_key_label(
    broker: InMemoryBroker,
) -> None:
    handler = RecordingHandler()
    handlers = TaskHandlerRegistry()
    handlers.bind({OUTBOX_RELAY_TASK: handler})
    queue, _ = _queue(broker, handlers)

    await queue.enqueue(OUTBOX_RELAY_TASK, {})
    await broker.wait_all()

    assert handler.received[0].idempotency_key is None


async def test_taskiq_task_queue_enqueue_omits_schedule_label_from_messages() -> None:
    sent: list[BrokerMessage] = []

    class CapturingBroker(InMemoryBroker):
        """Records messages instead of running them.

        Implements: Fake.
        """

        async def kick(self, message: BrokerMessage) -> None:
            """Record ``message``."""
            sent.append(message)

    broker = CapturingBroker()
    registry = register_tasks(
        broker,
        TaskHandlerRegistry(),
        [TaskSchedule(task_name=OUTBOX_RELAY_TASK, interval_seconds=5)],
    )
    queue = TaskiqTaskQueue(broker, registry)

    await queue.enqueue(OUTBOX_RELAY_TASK, {}, idempotency_key="k-1")

    (message,) = sent
    assert message.labels == {IDEMPOTENCY_KEY_LABEL: "k-1"}
    assert SCHEDULE_LABEL in registry.find(OUTBOX_RELAY_TASK).labels


# --------------------------------------------------------------------------- #
# Enqueue refusals                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("payload", "task_name"),
    [
        ({"latitude": 35.92}, REPORTS_TRIAGE_TASK),
        ({"note": "x" * 201}, REPORTS_TRIAGE_TASK),
        ({"ReportId": "a"}, REPORTS_TRIAGE_TASK),
        ({}, "Reports.Triage"),
    ],
)
async def test_taskiq_task_queue_enqueue_contract_breach_raises_before_sending(
    broker: InMemoryBroker, payload: dict[str, object], task_name: str
) -> None:
    handler = RecordingHandler()
    handlers = TaskHandlerRegistry()
    handlers.bind({REPORTS_TRIAGE_TASK: handler})
    queue, _ = _queue(broker, handlers)

    with pytest.raises(PydanticValidationError):
        await queue.enqueue(task_name, payload)
    await broker.wait_all()

    assert handler.received == []


async def test_taskiq_task_queue_enqueue_unregistered_name_raises(
    broker: InMemoryBroker,
) -> None:
    queue, _ = _queue(broker, TaskHandlerRegistry())

    with pytest.raises(TaskNotRegisteredError) as caught:
        await queue.enqueue("weather.fetch_forecast", {})

    assert caught.value.details == {"task_name": "weather.fetch_forecast"}


async def test_taskiq_task_queue_enqueue_positive_delay_is_refused(
    broker: InMemoryBroker,
) -> None:
    handler = RecordingHandler()
    handlers = TaskHandlerRegistry()
    handlers.bind({OUTBOX_RELAY_TASK: handler})
    queue, _ = _queue(broker, handlers)

    with pytest.raises(TaskDelayUnsupportedError, match="cannot delay"):
        await queue.enqueue(OUTBOX_RELAY_TASK, {}, delay_seconds=30)
    await broker.wait_all()

    assert handler.received == []


async def test_taskiq_task_queue_enqueue_broker_failure_raises_unavailable() -> None:
    broker = RefusingBroker()
    queue = TaskiqTaskQueue(broker, register_tasks(broker, TaskHandlerRegistry()))

    with capture_logs() as logs, pytest.raises(TaskQueueUnavailableError) as caught:
        await queue.enqueue(OUTBOX_RELAY_TASK, {"report_id": REPORT_ID})

    assert caught.value.details == {"task_name": OUTBOX_RELAY_TASK}
    assert logs[0]["event"] == "task_enqueue_failed"
    assert set(logs[0]) == {"event", "log_level", "task", "task_id"}


# --------------------------------------------------------------------------- #
# Running a received message                                                  #
# --------------------------------------------------------------------------- #


def test_task_from_message_reads_id_name_payload_and_key() -> None:
    message = _message(labels={IDEMPOTENCY_KEY_LABEL: "k-9"})

    task = task_from_message(message, {"report_id": str(REPORT_ID)})

    assert task.task_id.value == "abc123"
    assert task.task_name == REPORTS_TRIAGE_TASK
    assert dict(task.payload) == {"report_id": str(REPORT_ID)}
    assert task.idempotency_key == "k-9"


async def test_execute_task_invalid_payload_raises_sanitised_failure() -> None:
    handler = RecordingHandler()
    handlers = TaskHandlerRegistry()
    handlers.bind({REPORTS_TRIAGE_TASK: handler})

    with capture_logs() as logs, pytest.raises(TaskFailedError) as caught:
        await execute_task(_message(), {"latitude": 35.92}, handlers)

    assert "35.92" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert logs[0]["error_type"] == "ValidationError"
    assert handler.received == []


async def test_execute_task_handler_error_is_logged_by_type_and_not_chained() -> None:
    marker = "reporter phone 0300-0000000"
    handlers = TaskHandlerRegistry()
    handlers.bind({REPORTS_TRIAGE_TASK: RecordingHandler(RuntimeError(marker))})

    with capture_logs() as logs, pytest.raises(TaskFailedError) as caught:
        await execute_task(_message(), {}, handlers)

    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert caught.value.details == {
        "task_name": REPORTS_TRIAGE_TASK,
        "error_type": "RuntimeError",
    }
    assert logs == [
        {
            "event": "task_failed",
            "log_level": "error",
            "task": REPORTS_TRIAGE_TASK,
            "task_id": "abc123",
            "error_type": "RuntimeError",
        }
    ]


async def test_execute_task_unbound_handler_raises_not_bound() -> None:
    with pytest.raises(TaskHandlerNotBoundError):
        await execute_task(_message(), {}, TaskHandlerRegistry())


async def test_registered_task_failure_is_recorded_as_sanitised_result(
    broker: InMemoryBroker,
) -> None:
    handlers = TaskHandlerRegistry()
    handlers.bind({OUTBOX_RELAY_TASK: RecordingHandler(RuntimeError("secret"))})
    queue, _ = _queue(broker, handlers)

    task_id = await queue.enqueue(OUTBOX_RELAY_TASK, {})
    await broker.wait_all()

    result = await broker.result_backend.get_result(task_id.value)
    assert result.is_err is True
    assert isinstance(result.error, TaskFailedError)
    assert "secret" not in str(result.error)


# --------------------------------------------------------------------------- #
# TaskRegistry and register_tasks                                             #
# --------------------------------------------------------------------------- #


def test_register_tasks_registers_every_name_with_schedule_labels() -> None:
    broker = InMemoryBroker()

    registry = register_tasks(
        broker,
        TaskHandlerRegistry(),
        [TaskSchedule(task_name=OUTBOX_RELAY_TASK, interval_seconds=7)],
    )

    assert set(registry.names()) == set(broker.get_all_tasks())
    assert registry.find(OUTBOX_RELAY_TASK).labels == {
        SCHEDULE_LABEL: [{"interval": 7, "schedule_id": OUTBOX_RELAY_TASK}]
    }
    assert registry.find(REPORTS_TRIAGE_TASK).labels == {}


def test_register_tasks_custom_names_registers_only_those() -> None:
    broker = InMemoryBroker()

    registry = register_tasks(
        broker, TaskHandlerRegistry(), task_names=(OUTBOX_RELAY_TASK,)
    )

    assert registry.names() == (OUTBOX_RELAY_TASK,)


def test_task_registry_find_unknown_name_raises_not_registered() -> None:
    registry = TaskRegistry()

    with pytest.raises(TaskNotRegisteredError, match=r"outbox\.relay_once"):
        registry.find(OUTBOX_RELAY_TASK)


def test_task_log_key_survives_personal_data_redaction() -> None:
    # The task logs use "task" because "task_name" would be redacted as a name.
    keys = ("task", "task_id", "error_type")

    sensitive = [key for key in keys if is_sensitive_key(key)]

    assert sensitive == []
    assert is_sensitive_key("task_name") is True
