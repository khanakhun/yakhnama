"""Unit tests for ``yakhnama.platform.tasks.handlers``."""

import pytest
from structlog.testing import capture_logs

from yakhnama.modules.exchange.public import RUN_EXPORT_TASK, RUN_IMPORT_TASK
from yakhnama.modules.ingestion.public import (
    INGESTION_RUN_TASK as INGESTION_MODULE_RUN_TASK,
)
from yakhnama.platform.tasks.errors import (
    TaskHandlerNotBoundError,
    TaskNotRegisteredError,
)
from yakhnama.platform.tasks.handlers import (
    EXCHANGE_RUN_EXPORT_TASK,
    EXCHANGE_RUN_IMPORT_TASK,
    IDEMPOTENCY_PURGE_TASK,
    INGESTION_RUN_TASK,
    MEDIA_SCAN_TASK,
    OUTBOX_PURGE_TASK,
    OUTBOX_RELAY_TASK,
    REPORTS_TRIAGE_TASK,
    TASK_NAMES,
    TaskHandlerRegistry,
)
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId


async def _noop(_task: ScheduledTask) -> None:
    return None


async def _other(_task: ScheduledTask) -> None:
    return None


def _task(task_name: str = OUTBOX_RELAY_TASK) -> ScheduledTask:
    return ScheduledTask(task_id=TaskId(value="abc123"), task_name=task_name)


def test_task_names_are_the_documented_contract() -> None:
    names = TASK_NAMES

    assert names == (
        "outbox.relay_once",
        "outbox.purge_published",
        "idempotency.purge_expired",
        "reports.run_triage",
        "media.scan",
        "exchange.run_export",
        "exchange.run_import",
        "ingestion.run",
    )
    assert names[:3] == (OUTBOX_RELAY_TASK, OUTBOX_PURGE_TASK, IDEMPOTENCY_PURGE_TASK)
    assert names[3:5] == (REPORTS_TRIAGE_TASK, MEDIA_SCAN_TASK)
    assert names[5:] == (
        EXCHANGE_RUN_EXPORT_TASK,
        EXCHANGE_RUN_IMPORT_TASK,
        INGESTION_RUN_TASK,
    )


def test_module_task_names_match_the_names_the_modules_enqueue() -> None:
    names = (EXCHANGE_RUN_EXPORT_TASK, EXCHANGE_RUN_IMPORT_TASK, INGESTION_RUN_TASK)

    assert names == (RUN_EXPORT_TASK, RUN_IMPORT_TASK, INGESTION_MODULE_RUN_TASK)


def test_task_handler_registry_bind_then_handler_for_returns_handler() -> None:
    registry = TaskHandlerRegistry()

    registry.bind({OUTBOX_RELAY_TASK: _noop})

    assert registry.handler_for(_task()) is _noop
    assert dict(registry.bound()) == {OUTBOX_RELAY_TASK: _noop}


def test_task_handler_registry_bind_unknown_name_binds_nothing() -> None:
    registry = TaskHandlerRegistry()

    with pytest.raises(TaskNotRegisteredError) as caught:
        registry.bind({OUTBOX_RELAY_TASK: _noop, "weather.fetch": _other})

    assert caught.value.details == {"task_names": ["weather.fetch"]}
    assert dict(registry.bound()) == {}


def test_task_handler_registry_bind_same_name_twice_raises_conflict() -> None:
    registry = TaskHandlerRegistry()
    registry.bind({OUTBOX_RELAY_TASK: _noop})

    with pytest.raises(ConflictError, match="already bound"):
        registry.bind({OUTBOX_RELAY_TASK: _other, OUTBOX_PURGE_TASK: _other})

    assert dict(registry.bound()) == {OUTBOX_RELAY_TASK: _noop}


def test_task_handler_registry_custom_names_limit_what_can_be_bound() -> None:
    registry = TaskHandlerRegistry(("testing.only",))

    with pytest.raises(TaskNotRegisteredError):
        registry.bind({OUTBOX_RELAY_TASK: _noop})


def test_task_handler_registry_handler_for_unbound_logs_and_raises() -> None:
    registry = TaskHandlerRegistry()

    with capture_logs() as logs, pytest.raises(TaskHandlerNotBoundError) as caught:
        registry.handler_for(_task(REPORTS_TRIAGE_TASK))

    assert caught.value.code == "task_handler_not_bound"
    assert caught.value.details == {"task_name": REPORTS_TRIAGE_TASK}
    assert logs == [
        {
            "event": "task_handler_not_bound",
            "log_level": "error",
            "task": REPORTS_TRIAGE_TASK,
            "task_id": "abc123",
        }
    ]
