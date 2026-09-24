"""Fixtures and arrangement helpers of the ingestion infrastructure tests."""

from collections.abc import Iterable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.infrastructure.queries import (
    SqlAlchemyIngestionQueryService,
)
from yakhnama.modules.ingestion.infrastructure.uow import (
    SqlAlchemyIngestionUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory

type IngestionFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyIngestionUnitOfWork]


@pytest.fixture
def ingestion_queries(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyIngestionQueryService:
    """Return the SQL ingestion query service on the test database."""
    return SqlAlchemyIngestionQueryService(session_factory)


async def store(  # noqa: PLR0913  # reason: one optional collection per table
    factory: IngestionFactory,
    *,
    datasets: Iterable[Dataset] = (),
    versions: Iterable[DatasetVersion] = (),
    runs: Iterable[IngestionRun] = (),
    observations: Iterable[Observation] = (),
    assets: Iterable[RasterAsset] = (),
) -> None:
    """Commit the given records in foreign-key order in one unit of work.

    Args:
        factory: Opens the unit of work.
        datasets: Datasets to add.
        versions: Versions to add; their datasets must be stored or given.
        runs: Runs to add; their versions must be stored or given.
        observations: Observations to append.
        assets: Raster assets to add; their versions must be stored or given.
    """
    async with factory() as uow:
        for dataset in datasets:
            await uow.datasets.add(dataset)
        for version in versions:
            await uow.dataset_versions.add(version)
        for run in runs:
            await uow.ingestion_runs.add(run)
        await uow.observations.append_many(list(observations))
        for asset in assets:
            await uow.raster_assets.add(asset)
        await uow.commit()
