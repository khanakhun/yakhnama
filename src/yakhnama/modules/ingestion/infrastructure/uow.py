"""The SQLAlchemy unit of work of the ingestion module.

Patterns: Unit of Work.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.ingestion.application.ports import (
    DatasetRepository,
    DatasetVersionRepository,
    IngestionRunRepository,
    ObservationRepository,
    RasterAssetCatalog,
)
from yakhnama.modules.ingestion.infrastructure.repositories import (
    SqlAlchemyDatasetRepository,
    SqlAlchemyDatasetVersionRepository,
    SqlAlchemyIngestionRunRepository,
    SqlAlchemyObservationRepository,
    SqlAlchemyRasterAssetCatalog,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWork


class SqlAlchemyIngestionUnitOfWork(SqlAlchemyUnitOfWork):
    """``IngestionUnitOfWork`` over one ``AsyncSession``, with the outbox on commit.

    Every property first reads ``session``, so using a repository outside ``async
    with`` raises the unit of work's own error instead of an ``AttributeError``.

    Implements: Unit of Work (port ``IngestionUnitOfWork``).
    """

    def _open_repositories(self, session: AsyncSession) -> None:
        """Build the five ingestion repositories on this unit of work's session.

        Args:
            session: The session of this unit of work.
        """
        self._datasets = SqlAlchemyDatasetRepository(session)
        self._dataset_versions = SqlAlchemyDatasetVersionRepository(session)
        self._ingestion_runs = SqlAlchemyIngestionRunRepository(session)
        self._observations = SqlAlchemyObservationRepository(session)
        self._raster_assets = SqlAlchemyRasterAssetCatalog(session)

    @property
    def datasets(self) -> DatasetRepository:
        """Return the dataset repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._datasets

    @property
    def dataset_versions(self) -> DatasetVersionRepository:
        """Return the dataset version repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._dataset_versions

    @property
    def ingestion_runs(self) -> IngestionRunRepository:
        """Return the run repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._ingestion_runs

    @property
    def observations(self) -> ObservationRepository:
        """Return the observation repository bound to this transaction.

        Returns:
            The repository; valid only inside ``async with``.
        """
        _ = self.session
        return self._observations

    @property
    def raster_assets(self) -> RasterAssetCatalog:
        """Return the raster asset catalog bound to this transaction.

        Returns:
            The catalog; valid only inside ``async with``.
        """
        _ = self.session
        return self._raster_assets
