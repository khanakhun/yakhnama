"""Unit tests for ``yakhnama.platform.tasks.worker`` on the in-memory broker.

``InMemoryBroker.startup`` fires the ``WORKER_STARTUP`` handlers too, so the worker's
whole lifecycle runs in-process: build the container, bind the handlers, run tasks,
close the container.
"""

from uuid import UUID

from structlog.testing import capture_logs
from taskiq import InMemoryBroker

from tests.unit.platform.events import FixedClock, make_event
from tests.unit.platform.outbox_store import InMemoryOutboxStore
from tests.unit.platform.tasks_wiring import build_faked_container
from yakhnama.platform.container import Container
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks import worker
from yakhnama.platform.tasks.handlers import (
    OUTBOX_RELAY_TASK,
    REPORTS_TRIAGE_TASK,
    TASK_NAMES,
)
from yakhnama.platform.tasks.worker import create_worker_broker

REPORT_ID = UUID("01927b5e-0000-7000-8000-000000000001")


class ContainerRecorder:
    """Builds faked containers and remembers them.

    Implements: Fake.

    Attributes:
        built: Every container built, in order.
        store: The outbox store every container's relay uses.
    """

    def __init__(self) -> None:
        """Create the recorder with one pending outbox message."""
        clock = FixedClock()
        self.clock = clock
        self.store = InMemoryOutboxStore([OutboxWriter(clock).to_message(make_event())])
        self.built: list[Container] = []

    def __call__(self, settings: Settings) -> Container:
        """Build and remember one container."""
        container = build_faked_container(settings, self.store, self.clock)
        self.built.append(container)
        return container


def test_worker_module_exposes_broker_and_scheduler_for_the_taskiq_cli() -> None:
    broker = worker.broker

    assert worker.scheduler.broker is broker
    assert set(TASK_NAMES) <= set(broker.get_all_tasks())


def test_create_worker_broker_registers_schedules_as_labels(
    settings: Settings,
) -> None:
    broker = create_worker_broker(settings, ContainerRecorder())

    relay = broker.find_task(OUTBOX_RELAY_TASK)

    assert relay is not None
    assert relay.labels["schedule"] == [
        {"interval": 5, "schedule_id": OUTBOX_RELAY_TASK}
    ]


async def test_create_worker_broker_startup_binds_handlers_and_runs_the_relay(
    settings: Settings,
) -> None:
    recorder = ContainerRecorder()
    broker = create_worker_broker(settings, recorder)
    assert isinstance(broker, InMemoryBroker)

    await broker.startup()
    relay = broker.find_task(OUTBOX_RELAY_TASK)
    assert relay is not None
    await relay.kiq()
    await broker.wait_all()
    await broker.shutdown()

    (container,) = recorder.built
    (row,) = recorder.store.rows.values()
    assert row.published_at is not None
    container_broker = container.task_broker
    assert isinstance(container_broker, InMemoryBroker)
    # WORKER_SHUTDOWN closed the container, which shut its own broker down.
    # ThreadPoolExecutor exposes no public "is shut down" flag.
    assert container_broker.executor._shutdown is True


async def test_create_worker_broker_triage_task_runs_the_bound_reports_handler(
    settings: Settings,
) -> None:
    broker = create_worker_broker(settings, ContainerRecorder())
    assert isinstance(broker, InMemoryBroker)
    await broker.startup()
    triage = broker.find_task(REPORTS_TRIAGE_TASK)
    assert triage is not None

    with capture_logs() as logs:
        await triage.kiq(report_id=str(REPORT_ID))
        await broker.wait_all()
    await broker.shutdown()

    # The faked reports store is empty, so the bound RunTriageHandler itself
    # answers: the task reached the reports use case, not "no handler bound".
    assert {
        "event": "task_failed",
        "task": REPORTS_TRIAGE_TASK,
        "error_type": "ReportNotFoundError",
    }.items() <= logs[-1].items()


async def test_create_worker_broker_shutdown_without_startup_is_a_no_op(
    settings: Settings,
) -> None:
    recorder = ContainerRecorder()
    broker = create_worker_broker(settings, recorder)

    await broker.shutdown()

    assert recorder.built == []
