"""Worker and scheduler entry points for Taskiq.

``poe worker`` runs ``taskiq worker yakhnama.platform.tasks.worker:broker`` and
``poe scheduler`` runs ``taskiq scheduler yakhnama.platform.tasks.worker:scheduler``.
Importing this module reads ``Settings`` and builds the broker, which performs no I/O.
The worker's container, and with it the database engine, is built only when the
worker starts (Taskiq's ``WORKER_STARTUP`` event) and closed when it stops; the
scheduler process never builds one, because it only enqueues.

The worker needs ``task_queue_backend=redis``: the in-memory broker has nothing to
listen to, and with it tasks already run inside the process that enqueued them.

Patterns: Composition Root.
"""

from collections.abc import Callable

from taskiq import AsyncBroker, TaskiqEvents, TaskiqState

from yakhnama.platform.container import Container, build_container, build_task_handlers
from yakhnama.platform.logging import configure_logging
from yakhnama.platform.settings import Settings, get_settings
from yakhnama.platform.tasks.broker import build_broker
from yakhnama.platform.tasks.handlers import TaskHandlerRegistry
from yakhnama.platform.tasks.scheduled import build_scheduler, task_schedules
from yakhnama.platform.tasks.taskiq_adapter import register_tasks

type ContainerFactory = Callable[[Settings], Container]


def create_worker_broker(
    settings: Settings, container_factory: ContainerFactory = build_container
) -> AsyncBroker:
    """Build the broker a worker runs, with every task and schedule registered.

    Args:
        settings: The worker's settings.
        container_factory: Builds the container on worker startup; tests pass one
            that swaps adapters for fakes.

    Returns:
        The broker; its ``WORKER_STARTUP`` handler builds the container and binds
        ``build_task_handlers(container)``, its ``WORKER_SHUTDOWN`` handler closes
        the container.
    """
    broker = build_broker(settings)
    handlers = TaskHandlerRegistry()
    register_tasks(broker, handlers, task_schedules(settings))
    started: list[Container] = []

    async def start(_state: TaskiqState) -> None:
        configure_logging(settings)
        container = container_factory(settings)
        handlers.bind(build_task_handlers(container))
        started.append(container)

    async def stop(_state: TaskiqState) -> None:
        while started:
            await started.pop().aclose()

    broker.add_event_handler(TaskiqEvents.WORKER_STARTUP, start)
    broker.add_event_handler(TaskiqEvents.WORKER_SHUTDOWN, stop)
    return broker


broker = create_worker_broker(get_settings())
scheduler = build_scheduler(broker)
