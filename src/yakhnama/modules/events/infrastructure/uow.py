"""The SQLAlchemy unit of work of the events module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.events.application.ports import (
    EventRelationRepository,
    EventRepository,
)
from yakhnama.modules.events.infrastructure.repositories import (
    SqlAlchemyEventRelationRepository,
    SqlAlchemyEventRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyEventsUnitOfWork(SqlAlchemyUnitOfWork):
    """``EventsUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``EventsUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the event and relation repositories on this unit of work's session.

        Args:
            session: The session of this unit of work.
        """
        # Not ``_events``: the base class keeps its collected domain events there.
        self._event_repository = SqlAlchemyEventRepository(session)
        self._event_relations = SqlAlchemyEventRelationRepository(session)

    @property
    def events(self) -> EventRepository:
        """Return the event repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._event_repository

    @property
    def event_relations(self) -> EventRelationRepository:
        """Return the relation repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._event_relations
