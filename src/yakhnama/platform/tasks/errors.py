"""Errors raised by the task queue adapter and the worker.

None of them is a client's fault: an unknown task name, an unsupported delay or an
unbound handler is a wiring bug, a broker outage is an infrastructure fault, so none
is mapped to a 4xx status and a request that meets one answers 500.

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import ClassVar

from yakhnama.shared_kernel.errors import YakhnamaError


class TaskNotRegisteredError(YakhnamaError):
    """A task name that no Taskiq task is registered for.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"task_not_registered"``.
    """

    code: ClassVar[str] = "task_not_registered"


class TaskDelayUnsupportedError(YakhnamaError):
    """``delay_seconds > 0`` on a broker that cannot delay delivery.

    Neither the Redis stream broker nor the in-memory broker can hold a message back;
    see ``TaskiqTaskQueue``.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"task_delay_unsupported"``.
    """

    code: ClassVar[str] = "task_delay_unsupported"


class TaskQueueUnavailableError(YakhnamaError):
    """The broker refused or could not receive the task.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"task_queue_unavailable"``.
    """

    code: ClassVar[str] = "task_queue_unavailable"


class TaskHandlerNotBoundError(YakhnamaError):
    """A known task ran, but the composition root bound no handler for it.

    Raised in the worker instead of running anything, so the task is recorded as
    failed and logged as ``task_handler_not_bound`` rather than silently dropped.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"task_handler_not_bound"``.
    """

    code: ClassVar[str] = "task_handler_not_bound"


class TaskFailedError(YakhnamaError):
    """A task handler raised; carries the task name and the error type only.

    The worker raises this in place of the handler's error, without chaining it, so
    Taskiq's own failure log cannot print an error message that quotes report
    content (``AGENTS.md`` §5). The ``task_failed`` log line identifies the task.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"task_failed"``.
    """

    code: ClassVar[str] = "task_failed"
