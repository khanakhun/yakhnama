"""Builds the Taskiq broker from ``Settings``.

- ``redis``: a ``RedisStreamBroker`` on ``redis_url``. Messages sit in the Redis
  stream ``TASK_STREAM_NAME`` and are read by a consumer group; a worker acknowledges
  a message after the task ran (Taskiq's ``when_saved`` default), and a message left
  unacknowledged by a dead worker is claimed by another after
  ``UNACKNOWLEDGED_IDLE_MILLISECONDS``. Required in production.
- ``memory``: an ``InMemoryBroker``. Tasks run as asyncio tasks inside the process
  that enqueued them, with no worker and nothing persisted; for development and
  tests only.

Building a broker performs no I/O: the Redis pool connects on first use.

Patterns: Factory.
"""

from typing import Final

from taskiq import AsyncBroker, InMemoryBroker
from taskiq_redis import RedisStreamBroker

from yakhnama.platform.settings import Settings

TASK_STREAM_NAME: Final = "yakhnama:tasks"
TASK_CONSUMER_GROUP: Final = "yakhnama-workers"
# Proposed operational bounds, not domain facts. Acknowledged entries stay in a
# stream until trimmed, so the stream is capped. The cap is far above the backlog
# expected at the current scale; if it is ever reached, the oldest entries are
# dropped, which is harmless for relay and purge tasks (the next run re-reads the
# database) but not for triage or scan tasks (open question in the Phase 3 report).
TASK_STREAM_MAX_LENGTH: Final = 100_000
UNACKNOWLEDGED_IDLE_MILLISECONDS: Final = 10 * 60 * 1000
# Without a timeout a worker killed while holding the auto-claim lock would block
# every other worker's redelivery pass forever.
AUTOCLAIM_LOCK_TIMEOUT_SECONDS: Final = 30.0


def build_broker(settings: Settings) -> AsyncBroker:
    """Return the broker selected by ``settings.task_queue_backend``.

    Args:
        settings: Supplies ``task_queue_backend`` and ``redis_url``.

    Returns:
        A ``RedisStreamBroker`` for ``redis``, an ``InMemoryBroker`` for ``memory``.
    """
    if settings.task_queue_backend == "redis" and settings.redis_url is not None:
        return RedisStreamBroker(
            url=str(settings.redis_url),
            queue_name=TASK_STREAM_NAME,
            consumer_group_name=TASK_CONSUMER_GROUP,
            maxlen=TASK_STREAM_MAX_LENGTH,
            idle_timeout=UNACKNOWLEDGED_IDLE_MILLISECONDS,
            unacknowledged_lock_timeout=AUTOCLAIM_LOCK_TIMEOUT_SECONDS,
        )
    return InMemoryBroker()
