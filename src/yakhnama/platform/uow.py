"""The SQLAlchemy unit of work: one ``AsyncSession``, one transaction, one outbox write.

``SqlAlchemyUnitOfWork`` implements ``yakhnama.shared_kernel.uow.UnitOfWork``. On
``commit`` it stages the collected domain events as outbox rows in the same session and
then commits, so the aggregate change and its events are stored atomically (ADR 0007).

How a module adds its repositories (tasks T9 and T10 follow this):

1. ``application/ports.py`` declares the module's protocol, for example
   ``class HazardsUnitOfWork(UnitOfWork, Protocol)`` with a read-only
   ``hazard_types: HazardTypeRepository`` property.
2. ``infrastructure/`` subclasses ``SqlAlchemyUnitOfWork`` and overrides
   ``_open_repositories`` to build its SQLAlchemy repositories on the session the unit
   of work just opened; the properties return those instances::

       class SqlAlchemyHazardsUnitOfWork(SqlAlchemyUnitOfWork):
           def _open_repositories(self, session: AsyncSession) -> None:
               self._hazard_types = SqlAlchemyHazardTypeRepository(session)

           @property
           def hazard_types(self) -> HazardTypeRepository:
               return self._hazard_types

   Repositories receive the session (composition); they never commit it.
3. ``platform/container.py`` (or the module's registration function called from it)
   binds ``SqlAlchemyUnitOfWorkFactory(SqlAlchemyHazardsUnitOfWork, ...)`` to the
   module's ``UnitOfWorkFactory[HazardsUnitOfWork]`` port.

Patterns: Unit of Work.
"""

from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.events import DomainEvent

type SessionFactory = Callable[[], AsyncSession]


class SqlAlchemyUnitOfWork:
    """Unit of work over one ``AsyncSession`` that writes events to the outbox.

    A unit of work is single-use: enter it once, commit at most once. Leaving the
    ``async with`` block without a commit, or with an exception, rolls back and
    discards the collected events.

    Implements: Unit of Work.
    """

    def __init__(
        self, session_factory: SessionFactory, outbox_writer: OutboxWriter
    ) -> None:
        """Create the unit of work; no session is opened until it is entered.

        Args:
            session_factory: Opens the session for this unit of work.
            outbox_writer: Stages the collected events as outbox rows on commit.
        """
        self._session_factory = session_factory
        self._outbox_writer = outbox_writer
        self._session: AsyncSession | None = None
        self._events: list[DomainEvent] = []
        self._is_entered = False
        self._is_committed = False

    @property
    def session(self) -> AsyncSession:
        """Return the open session, for repositories built by subclasses.

        Returns:
            The session of the current transaction.

        Raises:
            InvariantViolationError: If the unit of work is not inside ``async with``.
        """
        if self._session is None:
            message = "the unit of work is not active; use it in 'async with'"
            raise InvariantViolationError(message)
        return self._session

    @property
    def collected_events(self) -> Sequence[DomainEvent]:
        """Return the events recorded since the last commit or rollback, in order."""
        return tuple(self._events)

    async def __aenter__(self) -> Self:
        """Open the session and let subclasses build their repositories on it.

        Returns:
            The unit of work itself.

        Raises:
            InvariantViolationError: If this unit of work was entered before.
        """
        if self._is_entered:
            message = "a unit of work can be entered only once; open a new one"
            raise InvariantViolationError(message)
        self._is_entered = True
        self._session = self._session_factory()
        self._open_repositories(self._session)
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back anything not committed, then close the session."""
        session = self.session
        try:
            if not self._is_committed:
                await self.rollback()
        finally:
            await session.close()
            self._session = None

    def record_event(self, event: DomainEvent) -> None:
        """Collect ``event`` for the outbox write in the next ``commit``.

        Args:
            event: A domain event produced by the use case.

        Raises:
            InvariantViolationError: If the unit of work already committed, because
                the event could no longer be stored with the change.
        """
        if self._is_committed:
            message = "cannot record an event after the unit of work committed"
            raise InvariantViolationError(message)
        self._events.append(event)

    async def commit(self) -> None:
        """Stage the collected events in the outbox and commit the transaction.

        Raises:
            InvariantViolationError: If the unit of work is not active or already
                committed.
            sqlalchemy.exc.SQLAlchemyError: If the database rejects the transaction;
                the unit of work then rolls back on exit.
        """
        session = self.session
        if self._is_committed:
            message = "the unit of work has already committed; open a new one"
            raise InvariantViolationError(message)
        self._outbox_writer.write(session, self._events)
        await session.commit()
        self._is_committed = True
        self._events.clear()

    async def rollback(self) -> None:
        """Discard staged changes and the collected events.

        Raises:
            InvariantViolationError: If the unit of work is not active.
        """
        await self.session.rollback()
        self._events.clear()

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the module's repositories on ``session``; the base has none.

        Args:
            session: The session this unit of work just opened.
        """


class SqlAlchemyUnitOfWorkFactory[UnitOfWorkT: SqlAlchemyUnitOfWork]:
    """Opens a fresh SQLAlchemy unit of work of one concrete class per use case.

    Satisfies ``yakhnama.shared_kernel.uow.UnitOfWorkFactory`` for that class.

    Implements: Unit of Work (factory).
    """

    def __init__(
        self,
        unit_of_work_class: type[UnitOfWorkT],
        *,
        session_factory: SessionFactory,
        outbox_writer: OutboxWriter,
    ) -> None:
        """Create the factory.

        Args:
            unit_of_work_class: ``SqlAlchemyUnitOfWork`` or a module's subclass.
            session_factory: Opens one session per unit of work.
            outbox_writer: Shared by every unit of work the factory opens.
        """
        self._unit_of_work_class = unit_of_work_class
        self._session_factory = session_factory
        self._outbox_writer = outbox_writer

    def __call__(self) -> UnitOfWorkT:
        """Return a new, not yet entered, unit of work.

        Returns:
            An instance of the configured unit-of-work class.
        """
        return self._unit_of_work_class(self._session_factory, self._outbox_writer)
