"""Ports the ingestion application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1):

- the repositories and ``RasterAssetCatalog`` behind ``IngestionUnitOfWork``, and
  ``IngestionQueryService``, by the SQL adapters in ``infrastructure``;
- ``SourceAdapter`` by one adapter per external source in
  ``infrastructure/adapters/`` (Adapter + Anti-Corruption Layer), collected in a
  ``SourceAdapterRegistry``;
- ``PipelineFactory`` by ``PipelineClassRegistry`` (``registry``), which maps each
  adapter name to its ``IngestionPipeline`` subclass.

Runs are executed by the ``ingestion.run`` task (``INGESTION_RUN_TASK``) through the
kernel's ``TaskQueue``.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side),
Factory, Value Object.
"""

import hashlib
from collections.abc import Mapping, Sequence
from typing import Final, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from yakhnama.modules.ingestion.application.dto import (
    DatasetDetail,
    DatasetSummary,
    IngestionOutcome,
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
    InputChecksum,
    MediaType,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

INGESTION_RUN_TASK: Final = "ingestion.run"
"""Task name under which ``ExecuteIngestionRunHandler`` runs; payload ``{run_id}``."""


# --------------------------------------------------------------------------- #
# Repositories and unit of work                                               #
# --------------------------------------------------------------------------- #


class DatasetRepository(Protocol):
    """Loads and stages ``Dataset`` aggregates inside one unit of work.

    There is no delete: datasets are deprecated or retired, never removed.

    Implements: Repository (port side).
    """

    async def get(self, dataset_id: EntityId) -> Dataset | None:
        """Return the dataset with ``dataset_id``.

        Args:
            dataset_id: The dataset's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def get_by_code(self, code: str) -> Dataset | None:
        """Return the dataset with catalog code ``code``.

        Args:
            code: The dataset code.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, dataset: Dataset) -> None:
        """Stage a newly registered dataset.

        Args:
            dataset: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or the code is taken, including by a
                concurrent registration.
        """
        ...

    async def save(self, dataset: Dataset) -> None:
        """Stage a changed dataset, checking optimistic concurrency.

        Args:
            dataset: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If no dataset with that id exists.
            ConflictError: If the stored version is not ``dataset.version - 1``.
        """
        ...


class DatasetVersionRepository(Protocol):
    """Loads and stages ``DatasetVersion`` entities; versions never change.

    Implements: Repository (port side).
    """

    async def get(self, dataset_version_id: EntityId) -> DatasetVersion | None:
        """Return the version with ``dataset_version_id``.

        Args:
            dataset_version_id: The version's id.

        Returns:
            The entity, or ``None``.
        """
        ...

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
        ...

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
        ...

    async def add(self, version: DatasetVersion) -> None:
        """Stage a newly recorded version.

        Args:
            version: The new entity.

        Raises:
            ConflictError: If the id or ``(dataset_id, label)`` is taken.
        """
        ...


class IngestionRunRepository(Protocol):
    """Loads and stages ``IngestionRun`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, run_id: EntityId) -> IngestionRun | None:
        """Return the run with ``run_id``.

        Args:
            run_id: The run's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

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
        ...

    async def add(self, run: IngestionRun) -> None:
        """Stage a newly requested run.

        Args:
            run: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        ...

    async def save(self, run: IngestionRun) -> None:
        """Stage a changed run, checking optimistic concurrency.

        Args:
            run: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If no run with that id exists.
            ConflictError: If the stored version is not ``run.version - 1``.
        """
        ...


class ObservationRepository(Protocol):
    """Appends observations to the narrow time-series table; never updates one.

    Implements: Repository (port side).
    """

    async def append_many(self, observations: Sequence[Observation]) -> int:
        """Stage the observations whose natural key is not stored yet.

        Idempotent on ``Observation.key``: an observation whose key already exists
        (stored before, or earlier in ``observations``) is skipped, never
        overwritten, so re-running a version stores nothing twice. The SQL adapter
        uses ``INSERT ... ON CONFLICT DO NOTHING`` on the primary key.

        Args:
            observations: Observations of one run, in any order.

        Returns:
            How many were staged, between 0 and ``len(observations)``.
        """
        ...

    async def count_for_version(self, dataset_version_id: EntityId) -> int:
        """Return how many observations a dataset version has.

        Args:
            dataset_version_id: The version.

        Returns:
            The number stored, staged ones included.
        """
        ...


class RasterAssetCatalog(Protocol):
    """Loads, stages and searches ``RasterAsset`` aggregates.

    Implements: Repository (port side).
    """

    async def get(self, raster_asset_id: EntityId) -> RasterAsset | None:
        """Return the raster asset with ``raster_asset_id``.

        Args:
            raster_asset_id: The asset's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, asset: RasterAsset) -> None:
        """Stage a newly catalogued raster asset.

        Args:
            asset: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or ``(dataset_version_id, stac_id)`` is taken.
        """
        ...

    async def search(self, query: ListRasterAssets) -> Page[RasterAsset]:
        """Return one page of the rasters ``query`` selects.

        Footprints are matched with ``ST_Intersects`` against ``query.bbox``; the
        acquisition window compares the start of ``acquired_at``. Order and cursor
        as documented in ``queries``.

        Args:
            query: Filters and page request.

        Returns:
            Up to ``query.page.limit`` assets and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...


class IngestionUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the ingestion repositories.

    Implements: Unit of Work.
    """

    @property
    def datasets(self) -> DatasetRepository:
        """Return the dataset repository bound to this transaction."""
        ...

    @property
    def dataset_versions(self) -> DatasetVersionRepository:
        """Return the dataset version repository bound to this transaction."""
        ...

    @property
    def ingestion_runs(self) -> IngestionRunRepository:
        """Return the run repository bound to this transaction."""
        ...

    @property
    def observations(self) -> ObservationRepository:
        """Return the observation repository bound to this transaction."""
        ...

    @property
    def raster_assets(self) -> RasterAssetCatalog:
        """Return the raster asset catalog bound to this transaction."""
        ...


type IngestionUnitOfWorkFactory = UnitOfWorkFactory[IngestionUnitOfWork]
"""Opens a fresh ingestion unit of work per use case."""


# --------------------------------------------------------------------------- #
# Read side                                                                   #
# --------------------------------------------------------------------------- #


class IngestionQueryService(Protocol):
    """Read port for the catalog, runs, observations and rasters.

    Every result is a DTO; orders and cursors are documented in ``queries``.

    Implements: Query Service.
    """

    async def list_datasets(self, query: ListDatasets) -> Page[DatasetSummary]:
        """Return one page of the catalog, ordered by code.

        Args:
            query: Status filter and page request.

        Returns:
            Up to ``query.page.limit`` datasets and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get_dataset(self, query: GetDataset) -> DatasetDetail | None:
        """Return one dataset with its most recent versions.

        Args:
            query: The dataset's id or code.

        Returns:
            The detail, or ``None``.
        """
        ...

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
        ...

    async def get_run(self, query: GetRun) -> RunDetail | None:
        """Return one run with its report.

        Args:
            query: The run.

        Returns:
            The detail, or ``None``.
        """
        ...

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
        ...

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
        ...


# --------------------------------------------------------------------------- #
# Sources and pipelines                                                       #
# --------------------------------------------------------------------------- #


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RawPayload(BaseModel):
    """The exact bytes a source adapter fetched, with their fingerprint.

    ``checksum`` is always the SHA-256 of ``content``: the adapter may supply it
    (for example from a streamed download), and it is then verified; if omitted it
    is computed here, so a payload can never carry a checksum of other bytes.

    Implements: Value Object.

    Attributes:
        content: The bytes, exactly as received.
        media_type: Their media type, lower case.
        retrieved_at: When they were retrieved, UTC, from the adapter's clock.
        checksum: SHA-256 of ``content``, 64 lower-case hex digits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: bytes
    media_type: MediaType
    retrieved_at: AwareDatetime
    checksum: InputChecksum

    @model_validator(mode="before")
    @classmethod
    def _fingerprint(cls, data: object) -> object:
        if not isinstance(data, Mapping) or not isinstance(data.get("content"), bytes):
            # Leave malformed input to the field validators and their errors.
            return data
        actual = _sha256(data["content"])
        given = data.get("checksum")
        if given is not None and str(given).lower() != actual:
            message = "checksum does not match the SHA-256 of content"
            raise ValueError(message)
        return {**data, "checksum": actual}

    @classmethod
    def of(
        cls, content: bytes, *, media_type: str, retrieved_at: AwareDatetime
    ) -> Self:
        """Build a payload and compute its checksum.

        Args:
            content: The bytes.
            media_type: Their media type.
            retrieved_at: When they were retrieved.

        Returns:
            The payload.
        """
        return cls.model_validate(
            {
                "content": content,
                "media_type": media_type,
                "retrieved_at": retrieved_at,
            }
        )


class SourceAdapter(Protocol):
    """Fetches the raw bytes of one dataset version from one external source.

    Each adapter lives in ``infrastructure/adapters/`` and is registered under its
    ``name`` in a ``SourceAdapterRegistry``. Timeouts, size caps and retries are the
    adapter's and the composition root's (``retry`` Decorator) concern; the pipeline
    only sees the payload or an exception.

    Implements: Adapter (port side).
    """

    @property
    def name(self) -> str:
        """Return the adapter's registry name, an ``AdapterName``."""
        ...

    async def fetch(self, dataset: Dataset, version: DatasetVersion) -> RawPayload:
        """Fetch the bytes of ``version``.

        Args:
            dataset: The dataset being ingested (code, licence, homepage).
            version: The version to fetch; its ``input_checksum`` is what the
                pipeline expects the bytes to hash to.

        Returns:
            The bytes with their media type, retrieval time and checksum.

        Raises:
            Exception: Any transport or storage failure; the run is marked
                ``failed`` with the stage and the exception type.
        """
        ...


class RunnablePipeline(Protocol):
    """What the run handler needs from a pipeline, whatever its row type.

    ``IngestionPipeline`` (``pipeline``) implements it; the protocol exists so a
    ``PipelineFactory`` can return pipelines of different row types.

    Implements: Adapter (port side).
    """

    @property
    def input_checksum(self) -> InputChecksum | None:
        """Return the checksum of the fetched bytes, once fetched."""
        ...

    async def run(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        run: IngestionRun,
        uow: IngestionUnitOfWork,
    ) -> IngestionOutcome:
        """Run every stage and finish the run inside ``uow``; see the pipeline.

        Args:
            dataset: The dataset.
            version: The version; it belongs to ``dataset``.
            run: The run, ``running``, for ``version``.
            uow: The open unit of work; the caller commits it.

        Returns:
            The finished run.
        """
        ...

    def failure_report(self, error: Exception) -> IngestionReport:
        """Return the report of a run that ``error`` aborted.

        Args:
            error: The exception that escaped ``run``.

        Returns:
            The issues found so far plus one error naming the stage.
        """
        ...


class PipelineFactory(Protocol):
    """Builds a fresh pipeline for a source adapter, keyed by the adapter's name.

    Implements: Factory.
    """

    def supports(self, adapter_name: str) -> bool:
        """Tell whether a pipeline is registered for ``adapter_name``.

        Args:
            adapter_name: A source adapter's name.

        Returns:
            ``True`` if ``create`` can build one.
        """
        ...

    def create(self, adapter: SourceAdapter) -> RunnablePipeline:
        """Build a single-use pipeline reading through ``adapter``.

        Args:
            adapter: The resolved source adapter.

        Returns:
            A pipeline that has not run yet.

        Raises:
            NotFoundError: If no pipeline is registered for ``adapter.name``.
        """
        ...
