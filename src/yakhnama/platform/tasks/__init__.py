"""Background tasks: Taskiq behind the kernel's ``TaskQueue`` port (ADR 0008).

- ``handlers``: the task names, the ``TaskHandler`` contract and the registry the
  composition root binds handlers into. No Taskiq import.
- ``broker``: builds the Taskiq broker from ``Settings`` (Redis stream or in memory).
- ``taskiq_adapter``: registers one Taskiq task per task name and implements
  ``TaskQueue`` as ``TaskiqTaskQueue``.
- ``scheduled``: the periodic schedules and the ``TaskiqScheduler`` factory.
- ``worker``: the entry point ``taskiq worker yakhnama.platform.tasks.worker:broker``
  and ``taskiq scheduler yakhnama.platform.tasks.worker:scheduler``.

Patterns: Adapter, Registry, Composition Root.
"""
