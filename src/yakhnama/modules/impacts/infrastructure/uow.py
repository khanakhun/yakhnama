"""The SQLAlchemy unit of work of the impacts module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.impacts.application.ports import ImpactMetricRepository
from yakhnama.modules.impacts.infrastructure.repositories import (
    SqlAlchemyImpactMetricRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyImpactsUnitOfWork(SqlAlchemyUnitOfWork):
    """``ImpactsUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``ImpactsUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the impact metric repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._impact_metrics = SqlAlchemyImpactMetricRepository(session)

    @property
    def impact_metrics(self) -> ImpactMetricRepository:
        """Return the impact metric repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of handing out a repository on a closed session.
        _ = self.session
        return self._impact_metrics
