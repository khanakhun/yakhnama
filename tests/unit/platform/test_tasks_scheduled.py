"""Unit tests for ``yakhnama.platform.tasks.scheduled``."""

import pytest
from pydantic import ValidationError
from taskiq import InMemoryBroker
from taskiq.schedule_sources import LabelScheduleSource

from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.handlers import (
    IDEMPOTENCY_PURGE_TASK,
    OUTBOX_PURGE_TASK,
    OUTBOX_RELAY_TASK,
    TaskHandlerRegistry,
)
from yakhnama.platform.tasks.scheduled import (
    OUTBOX_PURGE_INTERVAL_SECONDS,
    TaskSchedule,
    build_scheduler,
    task_schedules,
)
from yakhnama.platform.tasks.taskiq_adapter import register_tasks


def test_task_schedules_defaults_are_five_seconds_hourly_and_daily(
    settings: Settings,
) -> None:
    schedules = task_schedules(settings)

    assert {
        schedule.task_name: schedule.interval_seconds for schedule in schedules
    } == {
        OUTBOX_RELAY_TASK: 5,
        IDEMPOTENCY_PURGE_TASK: 3600,
        OUTBOX_PURGE_TASK: OUTBOX_PURGE_INTERVAL_SECONDS,
    }
    assert OUTBOX_PURGE_INTERVAL_SECONDS == 86_400


def test_task_schedules_follow_the_interval_settings(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "outbox_relay_interval_seconds": 12,
            "idempotency_purge_interval_minutes": 5,
        }
    )

    schedules = task_schedules(configured)

    intervals = {
        schedule.task_name: schedule.interval_seconds for schedule in schedules
    }
    assert intervals[OUTBOX_RELAY_TASK] == 12
    assert intervals[IDEMPOTENCY_PURGE_TASK] == 300


def test_task_schedule_interval_below_one_second_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="interval_seconds"):
        TaskSchedule(task_name=OUTBOX_RELAY_TASK, interval_seconds=0)


async def test_build_scheduler_label_source_yields_one_schedule_per_task(
    settings: Settings,
) -> None:
    broker = InMemoryBroker()
    register_tasks(broker, TaskHandlerRegistry(), task_schedules(settings))

    scheduler = build_scheduler(broker)
    (source,) = scheduler.sources
    await source.startup()
    schedules = await source.get_schedules()

    assert isinstance(source, LabelScheduleSource)
    assert scheduler.broker is broker
    assert {
        (schedule.schedule_id, schedule.task_name, schedule.interval)
        for schedule in schedules
    } == {
        (OUTBOX_RELAY_TASK, OUTBOX_RELAY_TASK, 5),
        (IDEMPOTENCY_PURGE_TASK, IDEMPOTENCY_PURGE_TASK, 3600),
        (OUTBOX_PURGE_TASK, OUTBOX_PURGE_TASK, 86_400),
    }
    assert all(schedule.kwargs == {} for schedule in schedules)
