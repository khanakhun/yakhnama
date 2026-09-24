"""Periodic tasks and the Taskiq scheduler that enqueues them.

The schedules are attached to the registered tasks as Taskiq ``schedule`` labels and
read by a ``LabelScheduleSource``, so they are defined once, in code, and the
scheduler process (``poe scheduler``) needs no storage of its own. Run exactly one
scheduler per deployment: two would enqueue every periodic task twice. That is safe
(every periodic task is idempotent and the relay is lease-based) but wasteful.

Default intervals are proposed operational values, not domain facts:

- ``outbox.relay_once`` every ``outbox_relay_interval_seconds`` (5 s);
- ``idempotency.purge_expired`` every ``idempotency_purge_interval_minutes`` (1 h);
- ``outbox.purge_published`` once a day.

Patterns: Factory, DTO.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from taskiq import AsyncBroker, TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.handlers import (
    IDEMPOTENCY_PURGE_TASK,
    OUTBOX_PURGE_TASK,
    OUTBOX_RELAY_TASK,
)
from yakhnama.shared_kernel.tasks import TaskName

SECONDS_PER_MINUTE: Final = 60
OUTBOX_PURGE_INTERVAL_SECONDS: Final = 24 * 60 * 60


class TaskSchedule(BaseModel):
    """One periodic task: which task, how often.

    Implements: DTO (proposed in ADR 0012).

    Attributes:
        task_name: The registered task to enqueue.
        interval_seconds: Seconds between two enqueues, at least 1 (Taskiq's
            scheduler has one-second resolution).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_name: TaskName
    interval_seconds: int = Field(ge=1)


def task_schedules(settings: Settings) -> tuple[TaskSchedule, ...]:
    """Return the periodic schedules configured by ``settings``.

    Args:
        settings: Supplies the relay and purge intervals.

    Returns:
        One schedule per periodic task.
    """
    return (
        TaskSchedule(
            task_name=OUTBOX_RELAY_TASK,
            interval_seconds=settings.outbox_relay_interval_seconds,
        ),
        TaskSchedule(
            task_name=IDEMPOTENCY_PURGE_TASK,
            interval_seconds=settings.idempotency_purge_interval_minutes
            * SECONDS_PER_MINUTE,
        ),
        TaskSchedule(
            task_name=OUTBOX_PURGE_TASK,
            interval_seconds=OUTBOX_PURGE_INTERVAL_SECONDS,
        ),
    )


def build_scheduler(broker: AsyncBroker) -> TaskiqScheduler:
    """Return a scheduler that enqueues the schedules labelled on ``broker``'s tasks.

    Args:
        broker: A broker whose tasks were registered by ``register_tasks`` with the
            schedules from ``task_schedules``.

    Returns:
        The scheduler object ``taskiq scheduler`` runs.
    """
    return TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
