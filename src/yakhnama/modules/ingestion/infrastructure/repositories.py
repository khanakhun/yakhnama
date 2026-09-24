"""SQLAlchemy adapters of the ingestion repository ports.

Writes go straight to the unit of work's transaction, so a later read in the same
unit of work sees them and a rollback discards them. Inserts of aggregates run
inside a savepoint so a unique violation becomes a ``ConflictError`` and leaves the
transaction usable. Rows are expunged as soon as they are read or written, so a
stale identity-map entry never shadows a later write.

**Optimistic concurrency.** The ports accept exactly one version step per save
(handlers save after every change), so ``save`` updates the row only ``WHERE
version = new.version - 1``. If no row matches, the id is looked up once more to
tell a missing aggregate (``...NotFoundError``) from a concurrent change
(``ConflictError``).

**Observations.** ``append_many`` inserts with ``INSERT ... ON CONFLICT DO NOTHING
RETURNING`` on the primary key (the natural key), in statements of at most
``OBSERVATION_INSERT_BATCH`` rows, and counts the returned rows: an observation
whose key is already stored, or appears earlier in the same call, is skipped and
never overwritten, exactly as the port and the Fake specify.

There is no delete anywhere: datasets are retired, runs finish, observations and
rasters are appended.

Patterns: Repository (adapter side).
"""

from collections.abc import Sequence
from typing import Final

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.ingestion.application.queries import ListRasterAssets
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.errors import (
    DatasetNotFoundError,
    IngestionRunNotFoundError,
)
from yakhnama.modules.ingestion.infrastructure.mappers import (
    dataset_to_row,
    dataset_to_values,
    observation_to_values,
    raster_to_row,
    row_to_dataset,
    row_to_raster,
    row_to_run,
    row_to_version,
    run_to_row,
    run_to_values,
    version_to_row,
)
from yakhnama.modules.ingestion.infrastructure.orm import (
    DatasetRow,
    DatasetVersionRow,
    IngestionRunRow,
    ObservationRow,
    RasterAssetRow,
)
from yakhnama.modules.ingestion.infrastructure.queries import (
    page_rasters,
    raster_search_statement,
)
from yakhnama.platform.db import Base, is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page

OBSERVATION_INSERT_BATCH: Final = 1000
"""Rows per ``INSERT`` statement. Ten bound parameters per row keeps a statement at
10,000 parameters, well under the PostgreSQL protocol limit of 32,767."""

# Names used in conflict messages and their details keys.
_DATASET: Final = "dataset"
_RUN: Final = "run"

_OBSERVATION_KEY: Final = (
    ObservationRow.observed_at,
    ObservationRow.dataset_version_id,
    ObservationRow.variable_code,
    ObservationRow.site_ref,
)


async def _insert(session: AsyncSession, row: Base, conflict: ConflictError) -> None:
    """Insert ``row`` in a savepoint; a unique violation raises ``conflict``."""
    try:
        # The savepoint keeps the transaction usable after a duplicate.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        if not is_unique_violation(error):
            raise
        raise conflict from error
    session.expunge(row)


def _stale(what: str, entity_id: EntityId, expected: int, stored: int) -> ConflictError:
    message = (
        f"{what} {entity_id} was changed concurrently (expected version {expected})"
    )
    return ConflictError(
        message,
        details={
            f"{what}_id": str(entity_id),
            "expected_version": expected,
            "stored_version": stored,
        },
    )


class SqlAlchemyDatasetRepository:
    """PostgreSQL-backed implementation of ``DatasetRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``DatasetRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, dataset_id: EntityId) -> Dataset | None:
        """Return the dataset with ``dataset_id``.

        Args:
            dataset_id: The dataset's id.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._one(select(DatasetRow).where(DatasetRow.id == dataset_id))

    async def get_by_code(self, code: str) -> Dataset | None:
        """Return the dataset with catalog code ``code``.

        Args:
            code: The dataset code.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._one(select(DatasetRow).where(DatasetRow.code == code))

    async def _one(self, statement: Select[tuple[DatasetRow]]) -> Dataset | None:
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_dataset(row)

    async def add(self, dataset: Dataset) -> None:
        """Insert a newly registered dataset.

        Args:
            dataset: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or the code is taken, including by a
                concurrent registration.
        """
        message = f"a dataset with id {dataset.id} or code {dataset.code!r} exists"
        await _insert(
            self._session,
            dataset_to_row(dataset),
            ConflictError(message, details={"code": dataset.code}),
        )

    async def save(self, dataset: Dataset) -> None:
        """Update a stored dataset, checking optimistic concurrency.

        Args:
            dataset: The new state; its ``version`` is one more than the stored one.

        Raises:
            DatasetNotFoundError: If no dataset with that id exists.
            ConflictError: If the stored version is not ``dataset.version - 1``.
        """
        expected = dataset.version - 1
        statement = (
            update(DatasetRow)
            .where(DatasetRow.id == dataset.id, DatasetRow.version == expected)
            .values(dataset_to_values(dataset))
            .returning(DatasetRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is not None:
            return
        stored = await self._session.scalar(
            select(DatasetRow.version).where(DatasetRow.id == dataset.id)
        )
        if stored is None:
            raise DatasetNotFoundError.for_id(dataset.id)
        raise _stale(_DATASET, dataset.id, expected, stored)


class SqlAlchemyDatasetVersionRepository:
    """PostgreSQL-backed implementation of ``DatasetVersionRepository``.

    Versions never change, so there is no ``save``.

    Implements: Repository (port ``DatasetVersionRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, dataset_version_id: EntityId) -> DatasetVersion | None:
        """Return the version with ``dataset_version_id``.

        Args:
            dataset_version_id: The version's id.

        Returns:
            The entity, or ``None``.
        """
        return await self._one(
            select(DatasetVersionRow).where(DatasetVersionRow.id == dataset_version_id)
        )

    async def get_by_label(
        self, dataset_id: EntityId, label: str
    ) -> DatasetVersion | None:
        """Return the version of ``dataset_id`` labelled ``label``.

        Args:
            dataset_id: The dataset.
            label: The release label.

        Returns:
            The entity, or ``None``.
        """
        return await self._one(
            select(DatasetVersionRow).where(
                DatasetVersionRow.dataset_id == dataset_id,
                DatasetVersionRow.label == label,
            )
        )

    async def _one(
        self, statement: Select[tuple[DatasetVersionRow]]
    ) -> DatasetVersion | None:
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_version(row)

    async def list_for_dataset(
        self, dataset_id: EntityId, *, limit: int
    ) -> tuple[DatasetVersion, ...]:
        """Return the most recent versions of a dataset.

        Args:
            dataset_id: The dataset.
            limit: At most this many, 1 or more.

        Returns:
            Versions ordered by ``created_at`` then id, newest first.
        """
        statement = (
            select(DatasetVersionRow)
            .where(DatasetVersionRow.dataset_id == dataset_id)
            .order_by(DatasetVersionRow.created_at.desc(), DatasetVersionRow.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        versions = tuple(row_to_version(row) for row in rows)
        for row in rows:
            self._session.expunge(row)
        return versions

    async def add(self, version: DatasetVersion) -> None:
        """Insert a newly recorded version.

        Args:
            version: The new entity.

        Raises:
            ConflictError: If the id or ``(dataset_id, label)`` is taken.
            sqlalchemy.exc.IntegrityError: If the dataset is not stored, which the
                handlers rule out by loading it first.
        """
        message = (
            f"dataset {version.dataset_id} already has a version with id "
            f"{version.id} or label {version.label!r}"
        )
        await _insert(
            self._session,
            version_to_row(version),
            ConflictError(
                message,
                details={
                    "dataset_id": str(version.dataset_id),
                    "label": version.label,
                },
            ),
        )


class SqlAlchemyIngestionRunRepository:
    """PostgreSQL-backed implementation of ``IngestionRunRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``IngestionRunRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, run_id: EntityId) -> IngestionRun | None:
        """Return the run with ``run_id``.

        Args:
            run_id: The run's id.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(IngestionRunRow).where(IngestionRunRow.id == run_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_run(row)

    async def list_for_dataset(
        self, dataset_id: EntityId, *, limit: int
    ) -> tuple[IngestionRun, ...]:
        """Return the most recent runs over any version of a dataset.

        Args:
            dataset_id: The dataset.
            limit: At most this many, 1 or more.

        Returns:
            Runs ordered by ``created_at`` then id, newest first.
        """
        versions = select(DatasetVersionRow.id).where(
            DatasetVersionRow.dataset_id == dataset_id
        )
        statement = (
            select(IngestionRunRow)
            .where(IngestionRunRow.dataset_version_id.in_(versions))
            .order_by(IngestionRunRow.created_at.desc(), IngestionRunRow.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        runs = tuple(row_to_run(row) for row in rows)
        for row in rows:
            self._session.expunge(row)
        return runs

    async def add(self, run: IngestionRun) -> None:
        """Insert a newly requested run.

        Args:
            run: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
            sqlalchemy.exc.IntegrityError: If the version is not stored, which the
                handlers rule out by loading it first.
        """
        message = f"ingestion run {run.id} already exists"
        await _insert(
            self._session,
            run_to_row(run),
            ConflictError(message, details={"run_id": str(run.id)}),
        )

    async def save(self, run: IngestionRun) -> None:
        """Update a stored run, checking optimistic concurrency.

        Args:
            run: The new state; its ``version`` is one more than the stored one.

        Raises:
            IngestionRunNotFoundError: If no run with that id exists.
            ConflictError: If the stored version is not ``run.version - 1``.
        """
        expected = run.version - 1
        statement = (
            update(IngestionRunRow)
            .where(IngestionRunRow.id == run.id, IngestionRunRow.version == expected)
            .values(run_to_values(run))
            .returning(IngestionRunRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is not None:
            return
        stored = await self._session.scalar(
            select(IngestionRunRow.version).where(IngestionRunRow.id == run.id)
        )
        if stored is None:
            raise IngestionRunNotFoundError.for_id(run.id)
        raise _stale(_RUN, run.id, expected, stored)


class SqlAlchemyObservationRepository:
    """PostgreSQL-backed implementation of ``ObservationRepository``.

    Append-only: there is no update or delete path.

    Implements: Repository (port ``ObservationRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def append_many(self, observations: Sequence[Observation]) -> int:
        """Insert the observations whose natural key is not stored yet.

        Args:
            observations: Observations of one run, in any order.

        Returns:
            How many rows were inserted, between 0 and ``len(observations)``.
        """
        stored = 0
        for start in range(0, len(observations), OBSERVATION_INSERT_BATCH):
            batch = observations[start : start + OBSERVATION_INSERT_BATCH]
            statement = (
                insert(ObservationRow)
                .values([observation_to_values(item) for item in batch])
                .on_conflict_do_nothing(index_elements=list(_OBSERVATION_KEY))
                # Only inserted rows are returned, so their number is the count.
                .returning(ObservationRow.observed_at)
            )
            result = await self._session.execute(statement)
            stored += len(result.all())
        return stored

    async def count_for_version(self, dataset_version_id: EntityId) -> int:
        """Return how many observations a dataset version has.

        Args:
            dataset_version_id: The version.

        Returns:
            The number stored, rows written earlier in this transaction included.
        """
        count = await self._session.scalar(
            select(func.count()).where(
                ObservationRow.dataset_version_id == dataset_version_id
            )
        )
        return int(count or 0)


class SqlAlchemyRasterAssetCatalog:
    """PostgreSQL-backed implementation of ``RasterAssetCatalog``.

    Implements: Repository (port ``RasterAssetCatalog``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the catalog.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, raster_asset_id: EntityId) -> RasterAsset | None:
        """Return the raster asset with ``raster_asset_id``.

        Args:
            raster_asset_id: The asset's id.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(RasterAssetRow).where(RasterAssetRow.id == raster_asset_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_raster(row)

    async def add(self, asset: RasterAsset) -> None:
        """Insert a newly catalogued raster asset.

        Args:
            asset: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or ``(dataset_version_id, stac_id)`` is taken.
            sqlalchemy.exc.IntegrityError: If the version is not stored, which the
                handlers rule out by loading it first.
        """
        message = (
            f"raster asset {asset.id} or STAC item {asset.stac_id!r} of version "
            f"{asset.dataset_version_id} already exists"
        )
        await _insert(
            self._session,
            raster_to_row(asset),
            ConflictError(
                message,
                details={
                    "dataset_version_id": str(asset.dataset_version_id),
                    "stac_id": asset.stac_id,
                },
            ),
        )

    async def search(self, query: ListRasterAssets) -> Page[RasterAsset]:
        """Return one page of the rasters ``query`` selects.

        Args:
            query: Filters and page request.

        Returns:
            Up to ``query.page.limit`` assets and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        rows = (
            (await self._session.execute(raster_search_statement(query)))
            .scalars()
            .all()
        )
        page = page_rasters(rows, query.page)
        for row in rows:
            self._session.expunge(row)
        return page
