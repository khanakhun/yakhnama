"""In-memory fakes of the ingestion ports, and a minimal pipeline for tests.

The repositories stage writes until the unit of work commits and enforce the
uniqueness and optimistic-concurrency rules of the SQL adapters; the observation
repository is idempotent on the natural key like ``ON CONFLICT DO NOTHING``. The
query service serves committed rows with the orders and cursors documented in
``yakhnama.modules.ingestion.application.queries``.

``MinimalTestPipeline`` reads a tiny CSV layout (``MINIMAL_HEADER``) with values
already in SI units, and records the order its hooks are called in.

Tests use the canonical ``tests.fakes.tasks.RecordingTaskQueue`` for the
``TaskQueue`` port.

Patterns: Fake.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.ingestion.application.dto import (
    RECENT_VERSIONS_MAX,
    DatasetDetail,
    DatasetSummary,
    ObservationRecord,
    RasterAssetSummary,
    RunDetail,
    RunSummary,
)
from yakhnama.modules.ingestion.application.pipeline import (
    IngestionPipeline,
    ObservationDraft,
    ValidationCollector,
)
from yakhnama.modules.ingestion.application.ports import (
    IngestionUnitOfWork,
    RawPayload,
    RunnablePipeline,
    SourceAdapter,
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
    observation_position,
)
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    IngestionReport,
    ObservationKey,
    QualityFlag,
    StationRef,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    DatePrecision,
    DateWithPrecision,
    Measurement,
)

MINIMAL_HEADER = "station,variable,value,unit,observed_at,quality"
"""Header of the CSV layout ``MinimalTestPipeline`` reads."""


class _Staging(Protocol):
    """What the unit of work does with every store on commit and rollback."""

    def apply(self) -> None: ...

    def discard(self) -> None: ...


class _StagedStore[KeyT, ValueT]:
    """Committed and staged rows of one fake table."""

    def __init__(self, rows: Iterable[tuple[KeyT, ValueT]] = ()) -> None:
        self.committed: dict[KeyT, ValueT] = dict(rows)
        self.staged: dict[KeyT, ValueT] = {}

    def current(self) -> dict[KeyT, ValueT]:
        return {**self.committed, **self.staged}

    def apply(self) -> None:
        self.committed.update(self.staged)
        self.staged.clear()

    def discard(self) -> None:
        self.staged.clear()


def _require_next_version(stored: int | None, new: int, what: str) -> None:
    if stored is None:
        message = f"{what} is not stored"
        raise NotFoundError(message)
    if new != stored + 1:
        message = f"{what} was changed concurrently"
        raise ConflictError(message)


class InMemoryDatasetRepository:
    """``DatasetRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).
    """

    def __init__(self, datasets: Iterable[Dataset] = ()) -> None:
        """Create the repository.

        Args:
            datasets: Datasets that exist before the test acts.
        """
        self.store = _StagedStore((dataset.id, dataset) for dataset in datasets)

    @property
    def committed(self) -> dict[EntityId, Dataset]:
        """Return the committed datasets by id."""
        return self.store.committed

    async def get(self, dataset_id: EntityId) -> Dataset | None:
        """Return the dataset, staged changes included.

        Args:
            dataset_id: The id.

        Returns:
            The dataset, or ``None``.
        """
        return self.store.current().get(dataset_id)

    async def get_by_code(self, code: str) -> Dataset | None:
        """Return the dataset with ``code``, staged changes included.

        Args:
            code: The code.

        Returns:
            The dataset, or ``None``.
        """
        return next(
            (item for item in self.store.current().values() if item.code == code),
            None,
        )

    async def add(self, dataset: Dataset) -> None:
        """Stage a new dataset.

        Args:
            dataset: The dataset.

        Raises:
            ConflictError: If the id or the code is taken.
        """
        if dataset.id in self.store.current() or await self.get_by_code(dataset.code):
            message = "the dataset id or code is taken"
            raise ConflictError(message)
        self.store.staged[dataset.id] = dataset

    async def save(self, dataset: Dataset) -> None:
        """Stage a changed dataset.

        Args:
            dataset: The new state.
        """
        stored = self.store.current().get(dataset.id)
        _require_next_version(
            None if stored is None else stored.version, dataset.version, "dataset"
        )
        self.store.staged[dataset.id] = dataset


def _newest_first(item: DatasetVersion | IngestionRun) -> tuple[datetime, EntityId]:
    return item.created_at, item.id


class InMemoryDatasetVersionRepository:
    """``DatasetVersionRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).
    """

    def __init__(self, versions: Iterable[DatasetVersion] = ()) -> None:
        """Create the repository.

        Args:
            versions: Versions that exist before the test acts.
        """
        self.store = _StagedStore((version.id, version) for version in versions)

    @property
    def committed(self) -> dict[EntityId, DatasetVersion]:
        """Return the committed versions by id."""
        return self.store.committed

    async def get(self, dataset_version_id: EntityId) -> DatasetVersion | None:
        """Return the version.

        Args:
            dataset_version_id: The id.

        Returns:
            The version, or ``None``.
        """
        return self.store.current().get(dataset_version_id)

    async def get_by_label(
        self, dataset_id: EntityId, label: str
    ) -> DatasetVersion | None:
        """Return the version of ``dataset_id`` labelled ``label``.

        Args:
            dataset_id: The dataset.
            label: The label.

        Returns:
            The version, or ``None``.
        """
        return next(
            (
                item
                for item in self.store.current().values()
                if item.dataset_id == dataset_id and item.label == label
            ),
            None,
        )

    async def list_for_dataset(
        self, dataset_id: EntityId, *, limit: int
    ) -> tuple[DatasetVersion, ...]:
        """Return the newest versions of a dataset.

        Args:
            dataset_id: The dataset.
            limit: At most this many.

        Returns:
            Versions, newest first.
        """
        return self.newest(self.store.current().values(), dataset_id, limit)

    @staticmethod
    def newest(
        versions: Iterable[DatasetVersion], dataset_id: EntityId, limit: int
    ) -> tuple[DatasetVersion, ...]:
        """Return the newest ``limit`` of ``versions`` belonging to ``dataset_id``.

        Args:
            versions: Candidate versions.
            dataset_id: The dataset.
            limit: At most this many.

        Returns:
            Versions, newest first.
        """
        mine = [item for item in versions if item.dataset_id == dataset_id]
        return tuple(sorted(mine, key=_newest_first, reverse=True)[:limit])

    async def add(self, version: DatasetVersion) -> None:
        """Stage a new version.

        Args:
            version: The version.

        Raises:
            ConflictError: If the id or ``(dataset_id, label)`` is taken.
        """
        if version.id in self.store.current() or await self.get_by_label(
            version.dataset_id, version.label
        ):
            message = "the version id or label is taken"
            raise ConflictError(message)
        self.store.staged[version.id] = version


class InMemoryIngestionRunRepository:
    """``IngestionRunRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).
    """

    def __init__(
        self,
        versions: InMemoryDatasetVersionRepository,
        runs: Iterable[IngestionRun] = (),
    ) -> None:
        """Create the repository.

        Args:
            versions: The versions, to find a dataset's runs.
            runs: Runs that exist before the test acts.
        """
        self._versions = versions
        self.store = _StagedStore((run.id, run) for run in runs)

    @property
    def committed(self) -> dict[EntityId, IngestionRun]:
        """Return the committed runs by id."""
        return self.store.committed

    async def get(self, run_id: EntityId) -> IngestionRun | None:
        """Return the run.

        Args:
            run_id: The id.

        Returns:
            The run, or ``None``.
        """
        return self.store.current().get(run_id)

    async def list_for_dataset(
        self, dataset_id: EntityId, *, limit: int
    ) -> tuple[IngestionRun, ...]:
        """Return the newest runs over any version of a dataset.

        Args:
            dataset_id: The dataset.
            limit: At most this many.

        Returns:
            Runs, newest first.
        """
        version_ids = {
            item.id
            for item in self._versions.store.current().values()
            if item.dataset_id == dataset_id
        }
        mine = [
            run
            for run in self.store.current().values()
            if run.dataset_version_id in version_ids
        ]
        return tuple(sorted(mine, key=_newest_first, reverse=True)[:limit])

    async def add(self, run: IngestionRun) -> None:
        """Stage a new run.

        Args:
            run: The run.

        Raises:
            ConflictError: If the id is taken.
        """
        if run.id in self.store.current():
            message = "the run id is taken"
            raise ConflictError(message)
        self.store.staged[run.id] = run

    async def save(self, run: IngestionRun) -> None:
        """Stage a changed run.

        Args:
            run: The new state.
        """
        stored = self.store.current().get(run.id)
        _require_next_version(
            None if stored is None else stored.version, run.version, "run"
        )
        self.store.staged[run.id] = run


class InMemoryObservationRepository:
    """``ObservationRepository`` keyed by the natural key, append-only.

    Implements: Fake (of Repository).

    Attributes:
        failure: Raised by ``append_many`` when set, to simulate a storage outage.
        batch_sizes: The size of every ``append_many`` call, in order.
    """

    def __init__(self, observations: Iterable[Observation] = ()) -> None:
        """Create the repository.

        Args:
            observations: Observations stored before the test acts.
        """
        self.store = _StagedStore((item.key, item) for item in observations)
        self.failure: Exception | None = None
        self.batch_sizes: list[int] = []

    @property
    def committed(self) -> dict[ObservationKey, Observation]:
        """Return the committed observations by key."""
        return self.store.committed

    async def append_many(self, observations: Sequence[Observation]) -> int:
        """Stage the observations whose key is new; skip the rest.

        Args:
            observations: The observations.

        Returns:
            How many were staged.
        """
        if self.failure is not None:
            raise self.failure
        self.batch_sizes.append(len(observations))
        stored = 0
        for observation in observations:
            if observation.key not in self.store.current():
                self.store.staged[observation.key] = observation
                stored += 1
        return stored

    async def count_for_version(self, dataset_version_id: EntityId) -> int:
        """Return how many observations a version has, staged included.

        Args:
            dataset_version_id: The version.

        Returns:
            The count.
        """
        return sum(
            1
            for item in self.store.current().values()
            if item.dataset_version_id == dataset_version_id
        )


def _raster_order(asset: RasterAsset) -> tuple[datetime, EntityId]:
    return asset.acquired_at.truncate().value, asset.id


def _overlaps(first: BoundingBox, second: BoundingBox) -> bool:
    return (
        first.min_longitude <= second.max_longitude
        and second.min_longitude <= first.max_longitude
        and first.min_latitude <= second.max_latitude
        and second.min_latitude <= first.max_latitude
    )


def _page_before[ItemT](
    items: Sequence[ItemT],
    order: Callable[[ItemT], tuple[datetime, EntityId]],
    page: PageRequest,
) -> Page[ItemT]:
    """Page ``items`` by ``order`` descending, continuing before the cursor."""
    cursor = page.decode_cursor()
    ordered = sorted(items, key=order, reverse=True)
    if cursor is not None:
        before = (datetime.fromisoformat(cursor.sort_key), cursor.last_id)
        ordered = [item for item in ordered if order(item) < before]
    window = ordered[: page.limit]
    next_cursor = None
    if len(ordered) > page.limit:
        instant, last_id = order(window[-1])
        next_cursor = encode_cursor(
            CursorPayload(sort_key=instant.isoformat(), last_id=last_id)
        )
    return Page[ItemT](items=tuple(window), next_cursor=next_cursor)


class InMemoryRasterAssetCatalog:
    """``RasterAssetCatalog`` over a dictionary keyed by id.

    Footprints are matched by bounding-box overlap, a superset of
    ``ST_Intersects`` that is exact for the rectangular test footprints.

    Implements: Fake (of Repository).
    """

    def __init__(
        self,
        versions: InMemoryDatasetVersionRepository,
        assets: Iterable[RasterAsset] = (),
    ) -> None:
        """Create the catalog.

        Args:
            versions: The versions, to filter by dataset.
            assets: Assets catalogued before the test acts.
        """
        self._versions = versions
        self.store = _StagedStore((asset.id, asset) for asset in assets)

    @property
    def committed(self) -> dict[EntityId, RasterAsset]:
        """Return the committed assets by id."""
        return self.store.committed

    async def get(self, raster_asset_id: EntityId) -> RasterAsset | None:
        """Return the asset.

        Args:
            raster_asset_id: The id.

        Returns:
            The asset, or ``None``.
        """
        return self.store.current().get(raster_asset_id)

    async def add(self, asset: RasterAsset) -> None:
        """Stage a new asset.

        Args:
            asset: The asset.

        Raises:
            ConflictError: If the id or ``(version, stac_id)`` is taken.
        """
        current = self.store.current()
        if asset.id in current or any(
            item.dataset_version_id == asset.dataset_version_id
            and item.stac_id == asset.stac_id
            for item in current.values()
        ):
            message = "the raster id or STAC id is taken"
            raise ConflictError(message)
        self.store.staged[asset.id] = asset

    async def search(self, query: ListRasterAssets) -> Page[RasterAsset]:
        """Return one page of matching assets, staged included.

        Args:
            query: Filters and page.

        Returns:
            The page.
        """
        return self.search_in(
            self.store.current().values(),
            self._versions.store.current().values(),
            query,
        )

    @staticmethod
    def search_in(
        assets: Iterable[RasterAsset],
        versions: Iterable[DatasetVersion],
        query: ListRasterAssets,
    ) -> Page[RasterAsset]:
        """Filter and page ``assets``.

        Args:
            assets: Candidate assets.
            versions: Every version, to filter by dataset.
            query: Filters and page.

        Returns:
            The page, newest acquisition first.
        """
        dataset_of = {version.id: version.dataset_id for version in versions}

        def matches(asset: RasterAsset) -> bool:
            start = asset.acquired_at.truncate().value
            return (
                (
                    query.dataset_id is None
                    or dataset_of.get(asset.dataset_version_id) == query.dataset_id
                )
                and (query.acquired_from is None or start >= query.acquired_from)
                and (query.acquired_to is None or start < query.acquired_to)
                and (
                    query.bbox is None
                    or _overlaps(query.bbox, asset.footprint.bounding_box())
                )
            )

        return _page_before(
            [asset for asset in assets if matches(asset)], _raster_order, query.page
        )


class InMemoryIngestionUnitOfWork(InMemoryUnitOfWork):
    """``IngestionUnitOfWork`` over in-memory repositories.

    Implements: Fake (of Unit of Work).

    Attributes:
        datasets: The dataset repository.
        dataset_versions: The version repository.
        ingestion_runs: The run repository.
        observations: The observation repository.
        raster_assets: The raster catalog.
    """

    def __init__(
        self,
        *,
        datasets: Iterable[Dataset] = (),
        versions: Iterable[DatasetVersion] = (),
        runs: Iterable[IngestionRun] = (),
        observations: Iterable[Observation] = (),
        assets: Iterable[RasterAsset] = (),
    ) -> None:
        """Create the unit of work.

        Args:
            datasets: Datasets stored before the test acts.
            versions: Versions stored before the test acts.
            runs: Runs stored before the test acts.
            observations: Observations stored before the test acts.
            assets: Raster assets stored before the test acts.
        """
        super().__init__()
        self.datasets = InMemoryDatasetRepository(datasets)
        self.dataset_versions = InMemoryDatasetVersionRepository(versions)
        self.ingestion_runs = InMemoryIngestionRunRepository(
            self.dataset_versions, runs
        )
        self.observations = InMemoryObservationRepository(observations)
        self.raster_assets = InMemoryRasterAssetCatalog(self.dataset_versions, assets)

    def _stores(self) -> tuple[_Staging, ...]:
        return (
            self.datasets.store,
            self.dataset_versions.store,
            self.ingestion_runs.store,
            self.observations.store,
            self.raster_assets.store,
        )

    def _on_commit(self) -> None:
        for store in self._stores():
            store.apply()

    def _on_rollback(self) -> None:
        for store in self._stores():
            store.discard()


def _observation_order(record: ObservationRecord) -> tuple[datetime, str, EntityId]:
    return observation_position(record).as_tuple()


class InMemoryIngestionQueryService:
    """``IngestionQueryService`` reading a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryIngestionUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def list_datasets(self, query: ListDatasets) -> Page[DatasetSummary]:
        """Page datasets by ``(code, id)`` ascending.

        Args:
            query: Filter and page.

        Returns:
            The page.
        """
        cursor = query.page.decode_cursor()
        ordered = sorted(
            (
                dataset
                for dataset in self._uow.datasets.committed.values()
                if query.status is None or dataset.status is query.status
            ),
            key=lambda dataset: (dataset.code, dataset.id),
        )
        if cursor is not None:
            after = (cursor.sort_key, cursor.last_id)
            ordered = [item for item in ordered if (item.code, item.id) > after]
        window = ordered[: query.page.limit]
        next_cursor = None
        if len(ordered) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        return Page[DatasetSummary](
            items=tuple(DatasetSummary.from_entity(item) for item in window),
            next_cursor=next_cursor,
        )

    async def get_dataset(self, query: GetDataset) -> DatasetDetail | None:
        """Return one committed dataset with its newest versions.

        Args:
            query: Id or code.

        Returns:
            The detail, or ``None``.
        """
        dataset = next(
            (
                item
                for item in self._uow.datasets.committed.values()
                if item.id == query.dataset_id or item.code == query.code
            ),
            None,
        )
        if dataset is None:
            return None
        versions = InMemoryDatasetVersionRepository.newest(
            self._uow.dataset_versions.committed.values(),
            dataset.id,
            RECENT_VERSIONS_MAX,
        )
        return DatasetDetail.from_entities(dataset, versions)

    async def list_runs(self, query: ListRuns) -> Page[RunSummary]:
        """Page a dataset's committed runs, newest first.

        Args:
            query: Dataset and page.

        Returns:
            The page.
        """
        version_ids = {
            version.id
            for version in self._uow.dataset_versions.committed.values()
            if version.dataset_id == query.dataset_id
        }
        runs = [
            run
            for run in self._uow.ingestion_runs.committed.values()
            if run.dataset_version_id in version_ids
        ]
        page = _page_before(runs, _newest_first, query.page)
        return Page[RunSummary](
            items=tuple(RunSummary.from_entity(run) for run in page.items),
            next_cursor=page.next_cursor,
        )

    async def get_run(self, query: GetRun) -> RunDetail | None:
        """Return one committed run.

        Args:
            query: The run.

        Returns:
            The detail, or ``None``.
        """
        run = self._uow.ingestion_runs.committed.get(query.run_id)
        return None if run is None else RunDetail.from_entity(run)

    async def query_observations(
        self, query: QueryObservations
    ) -> Page[ObservationRecord]:
        """Page committed observations by ``(observed_at, site_ref, version)``.

        Args:
            query: Filters and page.

        Returns:
            The page.
        """
        cursor = query.page.decode_cursor()
        version_ids = {
            version.id
            for version in self._uow.dataset_versions.committed.values()
            if version.dataset_id == query.dataset_id
            and query.dataset_version_id in {None, version.id}
        }
        records = sorted(
            (
                ObservationRecord.from_entity(item)
                for item in self._uow.observations.committed.values()
                if item.dataset_version_id in version_ids
                and item.variable == query.variable
                and query.observed_from <= item.observed_at.value < query.observed_to
                and query.site_ref in {None, item.site_ref}
            ),
            key=_observation_order,
        )
        if cursor is not None:
            after = decode_observation_position(cursor).as_tuple()
            records = [item for item in records if _observation_order(item) > after]
        window = records[: query.page.limit]
        next_cursor = (
            observation_cursor(window[-1]) if len(records) > query.page.limit else None
        )
        return Page[ObservationRecord](items=tuple(window), next_cursor=next_cursor)

    async def list_raster_assets(
        self, query: ListRasterAssets
    ) -> Page[RasterAssetSummary]:
        """Page committed raster assets, newest acquisition first.

        Args:
            query: Filters and page.

        Returns:
            The page.
        """
        page = InMemoryRasterAssetCatalog.search_in(
            self._uow.raster_assets.committed.values(),
            self._uow.dataset_versions.committed.values(),
            query,
        )
        return Page[RasterAssetSummary](
            items=tuple(RasterAssetSummary.from_entity(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class FakeSourceAdapter:
    """``SourceAdapter`` answering from bytes arranged per dataset version.

    Implements: Fake (of Adapter).

    Attributes:
        name: The registry name.
        payloads: Bytes to return, by dataset version id.
        failure: Raised by ``fetch`` when set, to simulate an unreachable source.
        fetched: The version ids fetched, in order.
    """

    def __init__(
        self,
        payloads: Mapping[EntityId, bytes],
        *,
        clock: Clock,
        name: str = "test_adapter",
        media_type: str = "text/csv",
    ) -> None:
        """Create the adapter.

        Args:
            payloads: Bytes by dataset version id.
            clock: Source of ``retrieved_at``.
            name: The registry name.
            media_type: The media type of every payload.
        """
        self.name = name
        self.payloads = dict(payloads)
        self.failure: Exception | None = None
        self.fetched: list[EntityId] = []
        self._clock = clock
        self._media_type = media_type

    async def fetch(self, dataset: Dataset, version: DatasetVersion) -> RawPayload:
        """Return the arranged bytes of ``version``.

        Args:
            dataset: Ignored beyond the interface.
            version: The version.

        Returns:
            The payload.

        Raises:
            KeyError: If no bytes were arranged for the version.
        """
        if self.failure is not None:
            raise self.failure
        self.fetched.append(version.id)
        return RawPayload.of(
            self.payloads[version.id],
            media_type=self._media_type,
            retrieved_at=self._clock.now(),
        )


class MinimalTestRow(BaseModel):
    """One row of the minimal test layout, as text.

    Implements: Anti-Corruption Layer (source-shaped model).

    Attributes:
        line: 1-based data line number.
        station: Station code.
        variable: Variable code.
        value: Value text; empty when missing.
        unit: Unit name.
        observed_at: ISO 8601 instant.
        quality: Quality flag text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    line: int
    station: str
    variable: str
    value: str
    unit: str
    observed_at: str
    quality: str


class MinimalTestPipeline(IngestionPipeline[MinimalTestRow]):
    """Reads ``MINIMAL_HEADER`` CSV with SI values; records every hook call.

    Implements: Fake (of Template Method).

    Attributes:
        calls: Hook names in call order, once per call.
    """

    def __init__(
        self, adapter: SourceAdapter, *, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the pipeline.

        Args:
            adapter: The source adapter.
            clock: Source of timestamps.
            ids: Source of event ids.
        """
        super().__init__(adapter, clock=clock, ids=ids)
        self.calls: list[str] = []

    def _called(self, hook: str) -> None:
        if not self.calls or self.calls[-1] != hook:
            self.calls.append(hook)

    async def fetch(self, dataset: Dataset, version: DatasetVersion) -> RawPayload:
        """Record the call and fetch through the adapter.

        Args:
            dataset: The dataset.
            version: The version.

        Returns:
            The payload.
        """
        self._called("fetch")
        return await super().fetch(dataset, version)

    def parse(
        self, payload: RawPayload, collector: ValidationCollector
    ) -> Sequence[MinimalTestRow]:
        """Split the CSV; lines with the wrong field count are rejected.

        Args:
            payload: The bytes.
            collector: Where rejected lines go.

        Returns:
            The rows.

        Raises:
            ValueError: If the header is not ``MINIMAL_HEADER``.
        """
        self._called("parse")
        header, *lines = payload.content.decode("utf-8").splitlines()
        if header != MINIMAL_HEADER:
            message = "unexpected header"
            raise ValueError(message)
        rows: list[MinimalTestRow] = []
        fields = MINIMAL_HEADER.split(",")
        for number, line in enumerate(lines, start=1):
            values = line.split(",")
            if len(values) != len(fields):
                collector.reject("parse", f"line {number}", ["wrong field count"])
                continue
            rows.append(
                MinimalTestRow.model_validate(
                    {"line": number, **dict(zip(fields, values, strict=True))}
                )
            )
        return rows

    def validate(self, row: MinimalTestRow) -> Sequence[str]:
        """Record the call and apply the default checks.

        Args:
            row: The row.

        Returns:
            The problems.
        """
        self._called("validate")
        return super().validate(row)

    def normalise(self, row: MinimalTestRow) -> ObservationDraft:
        """Build the draft; values are already in SI units.

        Args:
            row: The row.

        Returns:
            The draft.
        """
        self._called("normalise")
        return ObservationDraft(
            station=StationRef(code=row.station),
            variable=row.variable,
            value=(
                None
                if row.value == ""
                else Measurement(value=float(row.value), unit=row.unit)
            ),
            observed_at=DateWithPrecision(
                value=datetime.fromisoformat(row.observed_at),
                precision=DatePrecision.HOUR,
            ),
            quality=QualityFlag(row.quality),
        )

    def reference_for(self, row: MinimalTestRow, index: int) -> str:
        """Name the row by its line.

        Args:
            row: The row.
            index: Ignored.

        Returns:
            ``line <n>``.
        """
        return f"line {row.line}"

    def deduplicate(
        self, observations: Sequence[Observation], collector: ValidationCollector
    ) -> Sequence[Observation]:
        """Record the call and apply the default.

        Args:
            observations: The observations.
            collector: The collector.

        Returns:
            The unique observations.
        """
        self._called("deduplicate")
        return super().deduplicate(observations, collector)

    async def persist(
        self, observations: Sequence[Observation], uow: IngestionUnitOfWork
    ) -> int:
        """Record the call and apply the default.

        Args:
            observations: The observations.
            uow: The unit of work.

        Returns:
            How many were stored.
        """
        self._called("persist")
        return await super().persist(observations, uow)

    async def record_lineage(
        self,
        run: IngestionRun,
        report: IngestionReport,
        payload: RawPayload,
        uow: IngestionUnitOfWork,
    ) -> IngestionRun:
        """Record the call and apply the default.

        Args:
            run: The run.
            report: The report.
            payload: The payload.
            uow: The unit of work.

        Returns:
            The finished run.
        """
        self._called("record_lineage")
        return await super().record_lineage(run, report, payload, uow)


class RecordingPipelineFactory:
    """``PipelineFactory`` building ``MinimalTestPipeline``s and keeping them.

    Implements: Fake (of Factory).

    Attributes:
        created: Every pipeline built, in order.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        adapter_names: Iterable[str] = ("test_adapter",),
    ) -> None:
        """Create the factory.

        Args:
            clock: Handed to every pipeline.
            ids: Handed to every pipeline.
            adapter_names: The adapters a pipeline exists for.
        """
        self._clock = clock
        self._ids = ids
        self._names = frozenset(adapter_names)
        self.created: list[MinimalTestPipeline] = []

    def supports(self, adapter_name: str) -> bool:
        """Tell whether a pipeline exists for ``adapter_name``.

        Args:
            adapter_name: The name.

        Returns:
            ``True`` if it is one of ``adapter_names``.
        """
        return adapter_name in self._names

    def create(self, adapter: SourceAdapter) -> RunnablePipeline:
        """Build a ``MinimalTestPipeline``.

        Args:
            adapter: The adapter.

        Returns:
            The pipeline.

        Raises:
            NotFoundError: If the adapter is not supported.
        """
        if not self.supports(adapter.name):
            message = "no pipeline for this adapter"
            raise NotFoundError(message)
        pipeline = MinimalTestPipeline(adapter, clock=self._clock, ids=self._ids)
        self.created.append(pipeline)
        return pipeline
