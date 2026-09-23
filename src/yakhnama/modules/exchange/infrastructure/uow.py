"""The SQLAlchemy unit of work of the exchange module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.exchange.application.ports import (
    ExportJobRepository,
    ImportJobRepository,
)
from yakhnama.modules.exchange.infrastructure.repositories import (
    SqlAlchemyExportJobRepository,
    SqlAlchemyImportJobRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyExchangeUnitOfWork(SqlAlchemyUnitOfWork):
    """``ExchangeUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``ExchangeUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the export and import job repositories on this session.

        Args:
            session: The session of this unit of work.
        """
        self._export_jobs = SqlAlchemyExportJobRepository(session)
        self._import_jobs = SqlAlchemyImportJobRepository(session)

    @property
    def export_jobs(self) -> ExportJobRepository:
        """Return the export job repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._export_jobs

    @property
    def import_jobs(self) -> ImportJobRepository:
        """Return the import job repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._import_jobs
