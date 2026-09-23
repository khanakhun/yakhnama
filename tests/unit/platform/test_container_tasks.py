"""Unit tests for the task queue wiring in ``yakhnama.platform.container``."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from structlog.testing import capture_logs
from taskiq import InMemoryBroker

from tests.unit.platform.events import FIXED_INSTANT, FixedClock, make_event
from tests.unit.platform.outbox_store import InMemoryOutboxStore
from tests.unit.platform.tasks_wiring import build_faked_container
from yakhnama.platform.container import Container, build_container, build_task_handlers
from yakhnama.platform.outbox.sqlalchemy_store import SqlAlchemyOutboxStore
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.handlers import (
    IDEMPOTENCY_PURGE_TASK,
    OUTBOX_PURGE_TASK,
    OUTBOX_RELAY_TASK,
)
from yakhnama.platform.tasks.taskiq_adapter import TaskiqTaskQueue
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId


def _task(task_name: str) -> ScheduledTask:
    return ScheduledTask(task_id=TaskId(value="t-1"), task_name=task_name)


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    container = build_container(settings)

    yield container

    await container.aclose()


def test_build_container_binds_taskiq_queue_on_the_configured_broker(
    container: Container,
) -> None:
    queue = container.task_queue

    assert isinstance(queue, TaskiqTaskQueue)
    assert isinstance(container.task_broker, InMemoryBroker)
    assert isinstance(container.outbox_store, SqlAlchemyOutboxStore)


def test_build_container_binds_the_platform_task_handlers(
    container: Container,
) -> None:
    bound = container.task_handlers.bound()

    assert set(bound) == {OUTBOX_RELAY_TASK, OUTBOX_PURGE_TASK, IDEMPOTENCY_PURGE_TASK}


async def test_container_aclose_shuts_the_task_broker_down(settings: Settings) -> None:
    container = build_container(settings)
    broker = container.task_broker
    assert isinstance(broker, InMemoryBroker)

    await container.aclose()

    # ThreadPoolExecutor exposes no public "is shut down" flag.
    assert broker.executor._shutdown is True


async def test_build_task_handlers_relay_runs_one_batch_and_logs_counts(
    settings: Settings,
) -> None:
    clock = FixedClock()
    store = InMemoryOutboxStore([OutboxWriter(clock).to_message(make_event())])
    container = build_faked_container(settings, store, clock)
    handlers = build_task_handlers(container)

    with capture_logs() as logs:
        await handlers[OUTBOX_RELAY_TASK](_task(OUTBOX_RELAY_TASK))
    await container.aclose()

    assert store.claims[0].batch_size == settings.outbox_batch_size
    assert logs == [
        {
            "event": "outbox_relayed",
            "log_level": "info",
            "claimed": 1,
            "published": 1,
            "failed": 0,
            "dead_lettered": 0,
        }
    ]


async def test_build_task_handlers_relay_with_nothing_pending_logs_nothing(
    settings: Settings,
) -> None:
    container = build_faked_container(settings, InMemoryOutboxStore(), FixedClock())

    with capture_logs() as logs:
        await build_task_handlers(container)[OUTBOX_RELAY_TASK](
            _task(OUTBOX_RELAY_TASK)
        )
    await container.aclose()

    assert logs == []


async def test_build_task_handlers_outbox_purge_uses_retention_days(
    settings: Settings,
) -> None:
    clock = FixedClock()
    writer = OutboxWriter(clock)
    old, recent = writer.to_message(make_event("old")), writer.to_message(make_event())
    retention = timedelta(days=settings.outbox_retention_days)
    old.published_at = FIXED_INSTANT - retention - timedelta(seconds=1)
    recent.published_at = FIXED_INSTANT - retention + timedelta(seconds=1)
    store = InMemoryOutboxStore([old, recent])
    container = build_faked_container(settings, store, clock)

    await build_task_handlers(container)[OUTBOX_PURGE_TASK](_task(OUTBOX_PURGE_TASK))
    await container.aclose()

    assert set(store.rows) == {recent.id}


async def test_build_task_handlers_idempotency_purge_logs_deleted_count(
    settings: Settings,
) -> None:
    container = build_faked_container(settings, InMemoryOutboxStore(), FixedClock())

    with capture_logs() as logs:
        await build_task_handlers(container)[IDEMPOTENCY_PURGE_TASK](
            _task(IDEMPOTENCY_PURGE_TASK)
        )
    await container.aclose()

    assert logs == [{"event": "idempotency_purged", "log_level": "info", "deleted": 0}]
