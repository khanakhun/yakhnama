"""The SQLAlchemy unit of work of the hazards module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.hazards.application.ports import HazardTypeRepository
from yakhnama.modules.hazards.infrastructure.repositories import (
    SqlAlchemyHazardTypeRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyHazardsUnitOfWork(SqlAlchemyUnitOfWork):
    """``HazardsUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``HazardsUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the hazard type repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._hazard_types = SqlAlchemyHazardTypeRepository(session)

    @property
    def hazard_types(self) -> HazardTypeRepository:
        """Return the hazard type repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of handing out a repository on a closed session.
        _ = self.session
        return self._hazard_types
