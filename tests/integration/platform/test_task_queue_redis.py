"""``TaskiqTaskQueue`` over ``RedisStreamBroker`` against a real Redis.

One process plays both sides: it enqueues through the adapter, then reads the stream
as a worker would and runs the message through Taskiq's receiver, so the wire format
(JSON payload, idempotency label, task id) and the acknowledgement are exercised.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from pydantic import RedisDsn
from redis.asyncio import Redis
from taskiq import AsyncBroker
from taskiq.acks import AckableMessage
from taskiq.receiver import Receiver

from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.broker import (
    TASK_CONSUMER_GROUP,
    TASK_STREAM_NAME,
    build_broker,
)
from yakhnama.platform.tasks.handlers import REPORTS_TRIAGE_TASK, TaskHandlerRegistry
from yakhnama.platform.tasks.taskiq_adapter import TaskiqTaskQueue, register_tasks
from yakhnama.shared_kernel.tasks import ScheduledTask

pytestmark = pytest.mark.integration

REPORT_ID = UUID("01927b5e-0000-7000-8000-000000000001")


@pytest.fixture
async def broker(redis_url: str, redis_client: Redis) -> AsyncIterator[AsyncBroker]:
    settings = Settings(
        _env_file=None,
        environment="test",
        task_queue_backend="redis",
        redis_url=RedisDsn(redis_url),
    )
    broker = build_broker(settings)
    # Startup declares the consumer group, which reads only entries added after it.
    await broker.startup()

    yield broker

    await broker.shutdown()


async def _next_message(broker: AsyncBroker) -> AckableMessage:
    async for message in broker.listen():
        assert isinstance(message, AckableMessage)
        return message
    failure = "the stream ended"
    raise AssertionError(failure)


async def test_enqueue_then_worker_receive_runs_handler_and_acknowledges(
    broker: AsyncBroker, redis_client: Redis
) -> None:
    received: list[ScheduledTask] = []

    async def triage(task: ScheduledTask) -> None:
        received.append(task)

    handlers = TaskHandlerRegistry()
    handlers.bind({REPORTS_TRIAGE_TASK: triage})
    queue = TaskiqTaskQueue(broker, register_tasks(broker, handlers))

    task_id = await queue.enqueue(
        REPORTS_TRIAGE_TASK,
        {"report_id": REPORT_ID, "revision": 1},
        idempotency_key=f"{REPORTS_TRIAGE_TASK}:{REPORT_ID}",
    )
    message = await _next_message(broker)
    await Receiver(broker, run_startup=False).callback(message)

    (task,) = received
    assert task.task_id == task_id
    assert dict(task.payload) == {"report_id": str(REPORT_ID), "revision": 1}
    assert task.idempotency_key == f"{REPORTS_TRIAGE_TASK}:{REPORT_ID}"
    pending = await redis_client.xpending(TASK_STREAM_NAME, TASK_CONSUMER_GROUP)
    assert pending["pending"] == 0


async def test_enqueue_stores_only_ids_and_scalars_in_the_stream(
    broker: AsyncBroker, redis_client: Redis
) -> None:
    queue = TaskiqTaskQueue(broker, register_tasks(broker, TaskHandlerRegistry()))

    await queue.enqueue(REPORTS_TRIAGE_TASK, {"report_id": REPORT_ID})

    entries = await redis_client.xrange(TASK_STREAM_NAME)

    assert entries is not None
    ((_, fields),) = entries
    assert fields is not None
    body = fields[b"data"]
    assert isinstance(body, bytes)
    assert str(REPORT_ID).encode() in body
    assert b'"schedule"' not in body
