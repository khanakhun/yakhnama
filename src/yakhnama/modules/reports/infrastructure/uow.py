"""The SQLAlchemy unit of work of the reports module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.reports.application.ports import ReportRepository
from yakhnama.modules.reports.infrastructure.repositories import (
    SqlAlchemyReportRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyReportsUnitOfWork(SqlAlchemyUnitOfWork):
    """``ReportsUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``ReportsUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the report repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._reports = SqlAlchemyReportRepository(session)

    @property
    def reports(self) -> ReportRepository:
        """Return the report repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._reports
