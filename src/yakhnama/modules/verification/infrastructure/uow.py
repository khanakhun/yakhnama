"""The SQLAlchemy unit of work of the verification module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.verification.application.ports import (
    VerificationCaseRepository,
)
from yakhnama.modules.verification.infrastructure.repositories import (
    SqlAlchemyVerificationCaseRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyVerificationUnitOfWork(SqlAlchemyUnitOfWork):
    """``VerificationUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``VerificationUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the case repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._verification_cases = SqlAlchemyVerificationCaseRepository(session)

    @property
    def verification_cases(self) -> VerificationCaseRepository:
        """Return the case repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._verification_cases
