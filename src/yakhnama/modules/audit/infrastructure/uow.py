"""The SQLAlchemy unit of work of the audit module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.audit.application.ports import AuditEntryRepository
from yakhnama.modules.audit.infrastructure.repositories import (
    SqlAlchemyAuditEntryRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyAuditUnitOfWork(SqlAlchemyUnitOfWork):
    """``AuditUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``AuditUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the audit repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._audit_entries = SqlAlchemyAuditEntryRepository(session)

    @property
    def audit_entries(self) -> AuditEntryRepository:
        """Return the audit entry repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._audit_entries
