"""The SQLAlchemy unit of work of the identity module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.identity.application.ports import (
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from yakhnama.modules.identity.infrastructure.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyIdentityUnitOfWork(SqlAlchemyUnitOfWork):
    """``IdentityUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``IdentityUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the identity repositories on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._users = SqlAlchemyUserRepository(session)
        self._organizations = SqlAlchemyOrganizationRepository(session)
        self._memberships = SqlAlchemyMembershipRepository(session)

    @property
    def users(self) -> UserRepository:
        """Return the user repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._users

    @property
    def organizations(self) -> OrganizationRepository:
        """Return the organisation repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._organizations

    @property
    def memberships(self) -> MembershipRepository:
        """Return the membership repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._memberships
