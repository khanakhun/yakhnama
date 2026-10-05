"""The SQLAlchemy unit of work of the geography module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.geography.application.ports import (
    DistrictEdgeSetRepository,
    PlaceRepository,
)
from yakhnama.modules.geography.infrastructure.repositories import (
    SqlAlchemyDistrictEdgeSetRepository,
    SqlAlchemyPlaceRepository,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyGeographyUnitOfWork(SqlAlchemyUnitOfWork):
    """``GeographyUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Implements: Unit of Work (port ``GeographyUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the repositories on the session this unit of work opened.

        Args:
            session: The session of this unit of work.
        """
        self._places = SqlAlchemyPlaceRepository(session)
        self._district_edge_sets = SqlAlchemyDistrictEdgeSetRepository(session)

    @property
    def places(self) -> PlaceRepository:
        """Return the place repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        # Reading the session first raises the unit of work's own error when it is
        # not active, instead of an AttributeError on a missing repository.
        _ = self.session
        return self._places

    @property
    def district_edge_sets(self) -> DistrictEdgeSetRepository:
        """Return the district edge snapshot repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._district_edge_sets
