"""Unit tests for ``yakhnama.platform.tasks.broker``; no broker connects."""

from pydantic import RedisDsn
from taskiq import InMemoryBroker
from taskiq_redis import RedisStreamBroker

from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.broker import (
    AUTOCLAIM_LOCK_TIMEOUT_SECONDS,
    TASK_CONSUMER_GROUP,
    TASK_STREAM_MAX_LENGTH,
    TASK_STREAM_NAME,
    UNACKNOWLEDGED_IDLE_MILLISECONDS,
    build_broker,
)


def test_build_broker_memory_backend_returns_in_memory_broker(
    settings: Settings,
) -> None:
    broker = build_broker(settings)

    assert type(broker) is InMemoryBroker


async def test_build_broker_redis_backend_returns_configured_stream_broker(
    settings: Settings,
) -> None:
    redis_settings = settings.model_copy(
        update={
            "task_queue_backend": "redis",
            "redis_url": RedisDsn("redis://cache.internal:6379/2"),
        }
    )

    broker = build_broker(redis_settings)

    assert isinstance(broker, RedisStreamBroker)
    assert broker.queue_name == TASK_STREAM_NAME
    assert broker.consumer_group_name == TASK_CONSUMER_GROUP
    assert broker.maxlen == TASK_STREAM_MAX_LENGTH
    assert broker.idle_timeout == UNACKNOWLEDGED_IDLE_MILLISECONDS
    assert broker.unacknowledged_lock_timeout == AUTOCLAIM_LOCK_TIMEOUT_SECONDS
    assert broker.connection_pool.connection_kwargs["host"] == "cache.internal"
    assert broker.connection_pool.connection_kwargs["db"] == 2
    await broker.connection_pool.disconnect()
