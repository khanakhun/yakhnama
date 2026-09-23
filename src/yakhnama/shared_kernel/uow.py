"""The unit-of-work protocol: one transaction per use case.

A command handler opens a unit of work, loads and saves aggregates through the
repositories the module's own unit-of-work protocol exposes, records the domain events
the change produced and commits. The SQLAlchemy implementation in
``yakhnama.platform.uow`` writes the recorded events to the outbox inside the same
database transaction (ADR 0007), so a state change and its events are stored together
or not at all.

Modules extend ``UnitOfWork`` with read-only repository properties (see the
``new-module`` skill); this kernel protocol knows nothing about repositories.

Patterns: Unit of Work.
"""

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol, Self

from yakhnama.shared_kernel.events import DomainEvent, EventRecorder


class UnitOfWork(EventRecorder, Protocol):
    """A transaction boundary that also collects domain events.

    Leaving the ``async with`` block without calling ``commit`` rolls the transaction
    back, including when an exception escapes, so a failed use case never leaves a
    partial write behind.

    Implements: Unit of Work.
    """

    async def __aenter__(self) -> Self:
        """Begin the transaction.

        Returns:
            The unit of work itself.
        """
        ...

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the transaction, rolling back anything not committed.

        Args:
            error_type: Type of the exception leaving the block, if any.
            error: The exception leaving the block, if any; it is not suppressed.
            traceback: Traceback of that exception, if any.
        """
        ...

    async def commit(self) -> None:
        """Persist staged changes and the collected events atomically."""
        ...

    async def rollback(self) -> None:
        """Discard staged changes and the collected events."""
        ...

    def record_event(self, event: DomainEvent) -> None:
        """Collect ``event`` for publication with the next ``commit``.

        Args:
            event: The event to write to the outbox with this transaction.
        """
        ...

    @property
    def collected_events(self) -> Sequence[DomainEvent]:
        """Return the events recorded since the last commit or rollback, in order."""
        ...


class UnitOfWorkFactory[UnitOfWorkT: UnitOfWork](Protocol):
    """Opens a fresh unit of work per use case.

    Generic so a module can declare ``UnitOfWorkFactory[HazardsUnitOfWork]`` instead of
    repeating the protocol.

    Implements: Unit of Work (factory port).
    """

    def __call__(self) -> UnitOfWorkT:
        """Return a new, not yet entered, unit of work.

        Returns:
            A unit of work to be used as ``async with factory() as uow:``.
        """
        ...
