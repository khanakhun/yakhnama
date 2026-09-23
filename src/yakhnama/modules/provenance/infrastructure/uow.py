"""The SQLAlchemy unit of work of the provenance module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.provenance.application.ports import SourceRepository
from yakhnama.modules.provenance.infrastructure.repositories import (
    SqlAlchemySourceRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyProvenanceUnitOfWork(SqlAlchemyUnitOfWork):
    """``ProvenanceUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``ProvenanceUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the provenance repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._sources = SqlAlchemySourceRepository(session)

    @property
    def sources(self) -> SourceRepository:
        """Return the source repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._sources
