"""SQL implementation of the ``IngestionQueryService`` port, and the raster search.

Orders and cursors are exactly those documented in
``yakhnama.modules.ingestion.application.queries``, so the in-memory Fake and this
adapter page identically:

- datasets: ``code`` then id ascending; ``code`` has the binary collation, so the
  database order is Python's code-point order;
- runs: ``created_at`` then id descending, over every version of the dataset;
- observations: ``(observed_at, site_ref, dataset_version_id)`` ascending by keyset
  with one row-value comparison, inside the half-open window ``[from, to)``; the
  cursor is built and read by ``observation_cursor`` and
  ``decode_observation_position``;
- raster assets: ``acquired_start`` (the start of the acquisition period) then id
  descending; the box filter is ``ST_Intersects(footprint, ST_MakeEnvelope(...))``
  (edges included) and the window compares ``acquired_start``.

``raster_search_statement`` and ``page_rasters`` are shared with
``SqlAlchemyRasterAssetCatalog.search``, which runs the same query inside a unit of
work, so the two can never order or filter differently.

Patterns: Query Service (adapter side).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    Uuid,
    and_,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from yakhnama.modules.ingestion.application.dto import (
    RECENT_VERSIONS_MAX,
    DatasetDetail,
    DatasetSummary,
    ObservationRecord,
    RasterAssetSummary,
    RunDetail,
    RunSummary,
)
from yakhnama.modules.ingestion.application.queries import (
    GetDataset,
    GetRun,
    ListDatasets,
    ListRasterAssets,
    ListRuns,
    QueryObservations,
    decode_observation_position,
    observation_cursor,
)
from yakhnama.modules.ingestion.domain.entities import RasterAsset
from yakhnama.modules.ingestion.infrastructure.mappers import (
    row_to_dataset,
    row_to_observation,
    row_to_raster,
    row_to_run,
    row_to_version,
)
from yakhnama.modules.ingestion.infrastructure.orm import (
    WGS84_SRID,
    DatasetRow,
    DatasetVersionRow,
    IngestionRunRow,
    ObservationRow,
    RasterAssetRow,
)
from yakhnama.platform.db import UtcDateTime
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)


def decode_instant(sort_key: str) -> datetime:
    """Parse the ISO 8601 instant a run or raster cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``, untrusted client input.

    Returns:
        The timezone-aware instant.

    Raises:
        ValidationError: If ``sort_key`` is not an ISO 8601 instant with an offset.
    """
    try:
        instant = datetime.fromisoformat(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    # Comparing a naive instant with timestamptz would assume the session zone.
    if instant.utcoffset() is None:
        raise _invalid_cursor()
    return instant


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


def _before(
    instant_column: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[EntityId],
    cursor: CursorPayload,
) -> ColumnElement[bool]:
    # Keyset condition of a descending (instant, id) order: strictly after the cursor
    # in reading order, so equal instants are split by id.
    instant = decode_instant(cursor.sort_key)
    return or_(
        instant_column < instant,
        and_(instant_column == instant, id_column < cursor.last_id),
    )


def _instant_cursor(instant: datetime, last_id: EntityId) -> str:
    return encode_cursor(CursorPayload(sort_key=instant.isoformat(), last_id=last_id))


def _versions_of(dataset_id: EntityId) -> Select[tuple[EntityId]]:
    return select(DatasetVersionRow.id).where(
        DatasetVersionRow.dataset_id == dataset_id
    )


# --------------------------------------------------------------------------- #
# Raster search, shared with the catalog repository                           #
# --------------------------------------------------------------------------- #


def raster_search_statement(query: ListRasterAssets) -> Select[tuple[RasterAssetRow]]:
    """Build the raster search for one page, with one extra row to detect more.

    Args:
        query: Box, window, dataset and page request.

    Returns:
        The ``SELECT`` of matching rows, newest acquisition start first.

    Raises:
        ValidationError: If the cursor is invalid.
    """
    cursor = query.page.decode_cursor()
    statement = select(RasterAssetRow)
    if query.dataset_id is not None:
        statement = statement.where(
            RasterAssetRow.dataset_version_id.in_(_versions_of(query.dataset_id))
        )
    if query.acquired_from is not None:
        statement = statement.where(
            RasterAssetRow.acquired_start >= query.acquired_from
        )
    if query.acquired_to is not None:
        statement = statement.where(RasterAssetRow.acquired_start < query.acquired_to)
    if query.bbox is not None:
        bbox = query.bbox
        envelope = func.ST_MakeEnvelope(
            bbox.min_longitude,
            bbox.min_latitude,
            bbox.max_longitude,
            bbox.max_latitude,
            WGS84_SRID,
        )
        statement = statement.where(
            func.ST_Intersects(RasterAssetRow.footprint, envelope)
        )
    if cursor is not None:
        statement = statement.where(
            _before(RasterAssetRow.acquired_start, RasterAssetRow.id, cursor)
        )
    return statement.order_by(
        RasterAssetRow.acquired_start.desc(), RasterAssetRow.id.desc()
    ).limit(query.page.limit + 1)


def page_rasters(
    rows: Sequence[RasterAssetRow], page: PageRequest
) -> Page[RasterAsset]:
    """Turn the rows of ``raster_search_statement`` into one page.

    Args:
        rows: Up to ``page.limit + 1`` rows in listing order.
        page: The page request the rows were selected for.

    Returns:
        Up to ``page.limit`` assets and the next cursor, if any row was left over.
    """
    window = rows[: page.limit]
    next_cursor = None
    if len(rows) > page.limit:
        last = window[-1]
        next_cursor = _instant_cursor(last.acquired_start, last.id)
    return Page[RasterAsset](
        items=tuple(row_to_raster(row) for row in window), next_cursor=next_cursor
    )


# --------------------------------------------------------------------------- #
# Query service                                                               #
# --------------------------------------------------------------------------- #


class SqlAlchemyIngestionQueryService:
    """PostgreSQL-backed ``IngestionQueryService``.

    Opens one read session per query and returns DTOs only.

    Implements: Query Service (port ``IngestionQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def list_datasets(self, query: ListDatasets) -> Page[DatasetSummary]:
        """Return one page of the catalog, ordered by code then id.

        Args:
            query: Status filter and page request.

        Returns:
            Up to ``query.page.limit`` datasets and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = (
            select(DatasetRow)
            .order_by(DatasetRow.code, DatasetRow.id)
            .limit(query.page.limit + 1)
        )
        if query.status is not None:
            statement = statement.where(DatasetRow.status == query.status.value)
        if cursor is not None:
            statement = statement.where(
                tuple_(DatasetRow.code, DatasetRow.id)
                > tuple_(
                    literal(cursor.sort_key, String()), literal(cursor.last_id, Uuid())
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        window = rows[: query.page.limit]
        next_cursor = None
        if len(rows) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        return Page[DatasetSummary](
            items=tuple(
                DatasetSummary.from_entity(row_to_dataset(row)) for row in window
            ),
            next_cursor=next_cursor,
        )

    async def get_dataset(self, query: GetDataset) -> DatasetDetail | None:
        """Return one dataset with its most recent versions.

        Args:
            query: The dataset's id or code.

        Returns:
            The detail, or ``None``.
        """
        condition = (
            DatasetRow.id == query.dataset_id
            if query.dataset_id is not None
            else DatasetRow.code == query.code
        )
        async with self._session_factory() as session:
            row = (
                await session.execute(select(DatasetRow).where(condition))
            ).scalar_one_or_none()
            if row is None:
                return None
            version_rows = (
                (
                    await session.execute(
                        select(DatasetVersionRow)
                        .where(DatasetVersionRow.dataset_id == row.id)
                        .order_by(
                            DatasetVersionRow.created_at.desc(),
                            DatasetVersionRow.id.desc(),
                        )
                        .limit(RECENT_VERSIONS_MAX)
                    )
                )
                .scalars()
                .all()
            )
        return DatasetDetail.from_entities(
            row_to_dataset(row), tuple(row_to_version(item) for item in version_rows)
        )

    async def list_runs(self, query: ListRuns) -> Page[RunSummary]:
        """Return one page of a dataset's runs, newest first.

        Args:
            query: The dataset and page request.

        Returns:
            Up to ``query.page.limit`` runs and the next cursor, if any; an empty
            page for an unknown dataset.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        statement = (
            select(IngestionRunRow)
            .where(
                IngestionRunRow.dataset_version_id.in_(_versions_of(query.dataset_id))
            )
            .order_by(IngestionRunRow.created_at.desc(), IngestionRunRow.id.desc())
            .limit(query.page.limit + 1)
        )
        if cursor is not None:
            statement = statement.where(
                _before(IngestionRunRow.created_at, IngestionRunRow.id, cursor)
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        window = rows[: query.page.limit]
        next_cursor = None
        if len(rows) > query.page.limit:
            last = window[-1]
            next_cursor = _instant_cursor(last.created_at, last.id)
        return Page[RunSummary](
            items=tuple(RunSummary.from_entity(row_to_run(row)) for row in window),
            next_cursor=next_cursor,
        )

    async def get_run(self, query: GetRun) -> RunDetail | None:
        """Return one run with its report.

        Args:
            query: The run.

        Returns:
            The detail, or ``None``.
        """
        async with self._session_factory() as session:
            row = await session.get(IngestionRunRow, query.run_id)
        return None if row is None else RunDetail.from_entity(row_to_run(row))

    async def query_observations(
        self, query: QueryObservations
    ) -> Page[ObservationRecord]:
        """Return one page of a variable's time series.

        Args:
            query: Dataset, variable, half-open window, optional site and version,
                page request.

        Returns:
            Up to ``query.page.limit`` records ordered by ``(observed_at,
            site_ref, dataset_version_id)`` and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        versions = _versions_of(query.dataset_id)
        if query.dataset_version_id is not None:
            versions = versions.where(DatasetVersionRow.id == query.dataset_version_id)
        statement = (
            select(ObservationRow)
            .where(
                ObservationRow.dataset_version_id.in_(versions),
                ObservationRow.variable_code == query.variable,
                ObservationRow.observed_at >= query.observed_from,
                ObservationRow.observed_at < query.observed_to,
            )
            .order_by(
                ObservationRow.observed_at,
                ObservationRow.site_ref,
                ObservationRow.dataset_version_id,
            )
            .limit(query.page.limit + 1)
        )
        if query.site_ref is not None:
            statement = statement.where(ObservationRow.site_ref == query.site_ref)
        if cursor is not None:
            after = decode_observation_position(cursor)
            # One row-value comparison; site_ref compares in the column's binary
            # collation, matching the ORDER BY and Python's string order.
            statement = statement.where(
                tuple_(
                    ObservationRow.observed_at,
                    ObservationRow.site_ref,
                    ObservationRow.dataset_version_id,
                )
                > tuple_(
                    literal(after.observed_at, UtcDateTime),
                    literal(after.site_ref, String()),
                    literal(after.dataset_version_id, Uuid()),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        records = [
            ObservationRecord.from_entity(row_to_observation(row))
            for row in rows[: query.page.limit]
        ]
        next_cursor = (
            observation_cursor(records[-1]) if len(rows) > query.page.limit else None
        )
        return Page[ObservationRecord](items=tuple(records), next_cursor=next_cursor)

    async def list_raster_assets(
        self, query: ListRasterAssets
    ) -> Page[RasterAssetSummary]:
        """Return one page of catalogued rasters, newest acquisition first.

        Args:
            query: Box, window, dataset and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        statement = raster_search_statement(query)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        page = page_rasters(rows, query.page)
        return Page[RasterAssetSummary](
            items=tuple(RasterAssetSummary.from_entity(item) for item in page.items),
            next_cursor=page.next_cursor,
        )
