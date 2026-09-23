'''In-memory unit of work and its factory, mirroring ``SqlAlchemyUnitOfWork``.

``InMemoryUnitOfWork`` implements ``yakhnama.shared_kernel.uow.UnitOfWork`` with the
same observable rules as the platform implementation in ``yakhnama.platform.uow``:

- leaving the ``async with`` block without ``commit``, or with an exception, rolls
  back and discards the collected events; the exception is never suppressed;
- ``commit`` and ``rollback`` outside the block, a second ``commit`` in the same
  transaction and ``record_event`` after ``commit`` raise ``InvariantViolationError``;
- entering a unit of work that is already active raises ``InvariantViolationError``.

One deliberate difference: the platform unit of work is single-use, but
``InMemoryUnitOfWorkFactory`` hands out the *same* instance on every call so a test can
inspect it afterwards. This fake may therefore be entered again once the previous block
has exited; each entry starts a new transaction and resets ``committed`` and
``rolled_back``. Events committed by every transaction accumulate in
``committed_events``, which plays the part of the outbox.

How a module attaches its fake repositories (task T9 follows this)::

    class InMemoryHazardsUnitOfWork(InMemoryUnitOfWork):
        """Hazards unit of work over in-memory repositories.

        Implements: Fake.
        """

        def __init__(self) -> None:
            super().__init__()
            self.hazard_types = InMemoryHazardTypeRepository()

        def _on_commit(self) -> None:
            self.hazard_types.apply_staged()

        def _on_rollback(self) -> None:
            self.hazard_types.discard_staged()


    uow = InMemoryHazardsUnitOfWork()
    factory: UnitOfWorkFactory[InMemoryHazardsUnitOfWork] = (
        InMemoryUnitOfWorkFactory(uow)
    )
    handler = CreateHazardTypeHandler(uow_factory=factory, ...)

A plain ``hazard_types`` attribute satisfies a protocol's read-only property. Staging
writes until ``_on_commit`` is what makes a failing use case leave the fake repository
unchanged, exactly as a rolled-back database transaction would.

Patterns: Fake, Unit of Work.
'''

from collections.abc import Sequence
from types import TracebackType
from typing import Self

from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.events import DomainEvent


class InMemoryUnitOfWork:
    """Unit of work that keeps events in memory and tracks what happened to them.

    Implements: Fake (of Unit of Work).

    Attributes:
        committed: Whether the current or most recent transaction committed.
        rolled_back: Whether the current or most recent transaction rolled back.
        commit_count: How many transactions committed over the fake's lifetime.
        rollback_count: How many rollbacks happened over the fake's lifetime.
    """

    def __init__(self) -> None:
        """Create an inactive unit of work with no events."""
        self._pending: list[DomainEvent] = []
        self._committed_events: list[DomainEvent] = []
        self._is_active = False
        self.committed = False
        self.rolled_back = False
        self.commit_count = 0
        self.rollback_count = 0

    @property
    def is_active(self) -> bool:
        """Return ``True`` while inside the ``async with`` block."""
        return self._is_active

    @property
    def collected_events(self) -> Sequence[DomainEvent]:
        """Return the events recorded since the last commit or rollback, in order."""
        return tuple(self._pending)

    @property
    def committed_events(self) -> Sequence[DomainEvent]:
        """Return every event committed by any transaction, in commit order."""
        return tuple(self._committed_events)

    async def __aenter__(self) -> Self:
        """Begin a transaction.

        Returns:
            The unit of work itself.

        Raises:
            InvariantViolationError: If a transaction is already active.
        """
        if self._is_active:
            message = "the unit of work is already active; nested use is not allowed"
            raise InvariantViolationError(message)
        self._is_active = True
        self.committed = False
        self.rolled_back = False
        self._on_begin()
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back anything not committed and end the transaction.

        Args:
            error_type: Type of the exception leaving the block, if any.
            error: The exception leaving the block, if any; it is not suppressed.
            traceback: Traceback of that exception, if any.
        """
        try:
            if not self.committed:
                await self.rollback()
        finally:
            self._is_active = False

    def record_event(self, event: DomainEvent) -> None:
        """Collect ``event`` for the next ``commit``.

        Args:
            event: A domain event produced by the use case.

        Raises:
            InvariantViolationError: If this transaction already committed, because
                the event could no longer be stored with the change.
        """
        if self.committed:
            message = "cannot record an event after the unit of work committed"
            raise InvariantViolationError(message)
        self._pending.append(event)

    async def commit(self) -> None:
        """Publish the collected events to ``committed_events``.

        Raises:
            InvariantViolationError: If the unit of work is not active or this
                transaction already committed.
        """
        self._require_active("commit")
        if self.committed:
            message = "the unit of work has already committed; open a new one"
            raise InvariantViolationError(message)
        self._on_commit()
        self._committed_events.extend(self._pending)
        self._pending.clear()
        self.committed = True
        self.commit_count += 1

    async def rollback(self) -> None:
        """Discard the collected events.

        Raises:
            InvariantViolationError: If the unit of work is not active.
        """
        self._require_active("roll back")
        self._on_rollback()
        self._pending.clear()
        self.rolled_back = True
        self.rollback_count += 1

    def _require_active(self, action: str) -> None:
        if not self._is_active:
            message = f"cannot {action}: the unit of work is not active"
            raise InvariantViolationError(message)

    def _on_begin(self) -> None:
        """Hook for subclasses: a transaction has just begun; the base does nothing."""

    def _on_commit(self) -> None:
        """Hook for subclasses: apply staged repository writes; the base has none."""

    def _on_rollback(self) -> None:
        """Hook for subclasses: discard staged repository writes; the base has none."""


class InMemoryUnitOfWorkFactory[UnitOfWorkT: InMemoryUnitOfWork]:
    """``UnitOfWorkFactory`` that always returns one shared in-memory unit of work.

    Returning the same instance lets a test arrange repository contents before the
    handler runs and inspect events and state afterwards.

    Implements: Fake (of the Unit of Work factory).

    Attributes:
        unit_of_work: The instance every call returns.
        calls: How many times the factory was called.
    """

    def __init__(self, unit_of_work: UnitOfWorkT) -> None:
        """Create the factory.

        Args:
            unit_of_work: The instance to hand out, usually a module's subclass of
                ``InMemoryUnitOfWork`` with fake repositories attached.
        """
        self.unit_of_work = unit_of_work
        self.calls = 0

    def __call__(self) -> UnitOfWorkT:
        """Return the shared unit of work.

        Returns:
            The instance given at construction.
        """
        self.calls += 1
        return self.unit_of_work
