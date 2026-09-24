"""Task names and the handler-binding contract between modules and the worker.

A task handler is an async callable taking the ``ScheduledTask`` the worker rebuilt
from the broker message and returning nothing. The composition root
(``yakhnama.platform.container.build_task_handlers``) returns one handler per task
name it can serve; ``TaskHandlerRegistry.bind`` checks the names and the worker looks
handlers up at run time.

Contract every handler follows:

- **Payload types.** The payload crossed the broker as JSON, so a UUID arrives as its
  canonical string; integers, booleans and ``None`` keep their type. Validate the
  payload into the module's command model (``RunTriage.model_validate(...)``) rather
  than reading keys.
- **Idempotent.** Delivery is at least once; ``task.idempotency_key`` and
  ``task.task_id`` identify a repeat. Reload state in the handler's own unit of work.
- **Raise, never swallow.** A raised error marks the task failed; its type (never its
  message) is logged as ``task_failed``. The broker does not retry a failed task;
  work that must eventually happen is driven by the outbox, whose relay retries.
- **Short.** A handler that runs longer than the broker's redelivery idle time (ten
  minutes on the Redis stream) may be delivered to a second worker meanwhile.

Patterns: Registry.
"""

from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Final

import structlog

from yakhnama.platform.tasks.errors import (
    TaskHandlerNotBoundError,
    TaskNotRegisteredError,
)
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.tasks import ScheduledTask

type TaskHandler = Callable[[ScheduledTask], Awaitable[None]]

OUTBOX_RELAY_TASK: Final = "outbox.relay_once"
"""Claim and deliver one batch of outbox messages. Payload: none."""
OUTBOX_PURGE_TASK: Final = "outbox.purge_published"
"""Delete published outbox messages past ``outbox_retention_days``. Payload: none."""
IDEMPOTENCY_PURGE_TASK: Final = "idempotency.purge_expired"
"""Delete lapsed ``Idempotency-Key`` records (ADR 0016). Payload: none."""
REPORTS_TRIAGE_TASK: Final = "reports.run_triage"
"""Run the triage chain on one report. Payload: ``report_id`` (bound by reports)."""
MEDIA_SCAN_TASK: Final = "media.scan"
"""Scan and process one uploaded asset. Payload: ``asset_id`` (bound by media)."""

TASK_NAMES: Final = (
    OUTBOX_RELAY_TASK,
    OUTBOX_PURGE_TASK,
    IDEMPOTENCY_PURGE_TASK,
    REPORTS_TRIAGE_TASK,
    MEDIA_SCAN_TASK,
)
"""Every task the worker registers; enqueueing any other name is refused."""


class TaskHandlerRegistry:
    """Maps each task name to the handler the composition root bound for it.

    Implements: Registry.
    """

    def __init__(self, task_names: tuple[str, ...] = TASK_NAMES) -> None:
        """Create an empty registry for ``task_names``.

        Args:
            task_names: The names handlers may be bound for.
        """
        self._task_names = frozenset(task_names)
        self._handlers: dict[str, TaskHandler] = {}

    def bind(self, handlers: Mapping[str, TaskHandler]) -> None:
        """Bind ``handlers`` by task name.

        Args:
            handlers: Task name to handler, typically from ``build_task_handlers``.

        Raises:
            TaskNotRegisteredError: If a name is not one of the registry's names;
                nothing is bound.
            ConflictError: If a name already has a handler; nothing is bound.
        """
        unknown = sorted(set(handlers) - self._task_names)
        if unknown:
            message = f"no task is registered for {unknown}"
            raise TaskNotRegisteredError(message, details={"task_names": unknown})
        already_bound = sorted(set(handlers) & set(self._handlers))
        if already_bound:
            message = f"handlers are already bound for {already_bound}"
            raise ConflictError(message, details={"task_names": already_bound})
        self._handlers.update(handlers)

    def bound(self) -> Mapping[str, TaskHandler]:
        """Return the bound handlers.

        Returns:
            A read-only view, task name to handler.
        """
        return MappingProxyType(self._handlers)

    def handler_for(self, task: ScheduledTask) -> TaskHandler:
        """Return the handler for ``task``.

        Args:
            task: The task about to run.

        Returns:
            Its bound handler.

        Raises:
            TaskHandlerNotBoundError: If none is bound; ``task_handler_not_bound``
                is logged with the task name and id only.
        """
        handler = self._handlers.get(task.task_name)
        if handler is None:
            structlog.get_logger(__name__).error(
                "task_handler_not_bound",
                # "task", not "task_name": the log redaction treats keys ending in
                # "name" as personal data.
                task=task.task_name,
                task_id=task.task_id.value,
            )
            message = f"no handler is bound for {task.task_name}"
            raise TaskHandlerNotBoundError(
                message, details={"task_name": task.task_name}
            )
        return handler
