"""The SQLAlchemy unit of work of the media module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.media.application.ports import MediaAssetRepository
from yakhnama.modules.media.infrastructure.repositories import (
    SqlAlchemyMediaAssetRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyMediaUnitOfWork(SqlAlchemyUnitOfWork):
    """``MediaUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``MediaUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the media repository on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._media_assets = SqlAlchemyMediaAssetRepository(session)

    @property
    def media_assets(self) -> MediaAssetRepository:
        """Return the media asset repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._media_assets
