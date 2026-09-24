"""Creation of datasets, dataset versions, ingestion runs and raster assets.

Every factory method stamps timestamps from the injected ``Clock``, draws ids from
the injected ``IdGenerator`` and returns an ``AggregateChange`` holding the new
instance and its creation event. Uniqueness across records (a dataset code, a
``(dataset, label)`` pair, a STAC id per version) needs a repository and belongs to
the command handler.

Patterns: Factory.
"""

from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.errors import (
    DatasetStatusError,
    LicenceRequiredError,
    VersionDatasetMismatchError,
)
from yakhnama.modules.ingestion.domain.events import (
    DatasetRegistered,
    DatasetVersionRecorded,
    IngestionRunRequested,
    RasterAssetCatalogued,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetDetails,
    DatasetVersionDetails,
    RasterAssetDescription,
    RunRequest,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator


def _require_accepts_new_data(dataset: Dataset, action: str) -> None:
    if not dataset.accepts_new_data:
        raise DatasetStatusError.refused(dataset.id, dataset.status, action)


def _require_version_of(dataset: Dataset, version: DatasetVersion) -> None:
    if version.dataset_id != dataset.id:
        raise VersionDatasetMismatchError.for_ids(dataset.id, version.id)


class DatasetFactory:
    """Register datasets in the catalog; never without a licence.

    Implements: Factory.
    """

    def register(
        self, details: DatasetDetails, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange[Dataset]:
        """Create an active dataset at version 1 and ``DatasetRegistered``.

        Args:
            details: What the curator supplied, including the licence.
            clock: Source of every timestamp.
            ids: Source of the dataset id and the event id.

        Returns:
            The new dataset and ``DatasetRegistered``.

        Raises:
            LicenceRequiredError: If ``details.licence`` is ``None``; nothing is
                ingested without recorded terms.
        """
        if details.licence is None:
            raise LicenceRequiredError.for_code(details.code)
        now = clock.now()
        dataset = Dataset.model_validate(
            {
                **details.as_fields(),
                "id": ids.new_id(),
                "created_at": now,
                "updated_at": now,
            }
        )
        event = DatasetRegistered(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=dataset.id,
            version=dataset.version,
            code=dataset.code,
        )
        return AggregateChange[Dataset](state=dataset, events=(event,))


class DatasetVersionFactory:
    """Record releases of active datasets.

    Implements: Factory.
    """

    def record(
        self,
        dataset: Dataset,
        details: DatasetVersionDetails,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[DatasetVersion]:
        """Create a dataset version and ``DatasetVersionRecorded``.

        Args:
            dataset: The dataset the release belongs to.
            details: Label, retrieval time, checksum and notes.
            clock: Source of ``created_at`` and ``occurred_at``.
            ids: Source of the version id and the event id.

        Returns:
            The new version and ``DatasetVersionRecorded``.

        Raises:
            DatasetStatusError: If the dataset is deprecated or retired.
        """
        _require_accepts_new_data(dataset, "record_version")
        now = clock.now()
        version = DatasetVersion.model_validate(
            {
                "id": ids.new_id(),
                "dataset_id": dataset.id,
                "label": details.label,
                "retrieved_at": details.retrieved_at,
                "input_checksum": details.input_checksum,
                "notes": details.notes,
                "created_at": now,
            }
        )
        event = DatasetVersionRecorded(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=version.id,
            dataset_id=dataset.id,
            label=version.label,
            input_checksum=version.input_checksum,
        )
        return AggregateChange[DatasetVersion](state=version, events=(event,))


class IngestionRunFactory:
    """Open ingestion runs for versions of active datasets.

    Implements: Factory.
    """

    def start(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        request: RunRequest,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[IngestionRun]:
        """Create a ``pending`` run and ``IngestionRunRequested``.

        The run starts its life queued; the worker moves it to ``running`` with
        ``IngestionRun.start`` when it picks the task up. Passing the dataset lets
        the factory check, at the moment the run is opened, that a licensed and
        active dataset owns the version.

        Args:
            dataset: The dataset being ingested.
            version: The version to ingest; must belong to ``dataset``.
            request: The adapter to use and who asked.
            clock: Source of ``created_at`` and ``occurred_at``.
            ids: Source of the run id and the event id.

        Returns:
            The pending run and ``IngestionRunRequested``.

        Raises:
            VersionDatasetMismatchError: If ``version`` belongs to another dataset.
            DatasetStatusError: If the dataset is deprecated or retired.
        """
        _require_version_of(dataset, version)
        _require_accepts_new_data(dataset, "start_run")
        now = clock.now()
        run = IngestionRun(
            id=ids.new_id(),
            dataset_version_id=version.id,
            adapter_name=request.adapter_name,
            triggered_by=request.triggered_by,
            created_at=now,
            updated_at=now,
        )
        event = IngestionRunRequested(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=run.id,
            version=run.version,
            dataset_version_id=version.id,
            adapter_name=run.adapter_name,
            triggered_by=run.triggered_by,
        )
        return AggregateChange[IngestionRun](state=run, events=(event,))


class RasterAssetFactory:
    """Catalogue rasters of versions of active datasets.

    Implements: Factory.
    """

    def catalogue(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        description: RasterAssetDescription,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[RasterAsset]:
        """Create a raster asset at version 1 and ``RasterAssetCatalogued``.

        Args:
            dataset: The dataset the raster belongs to.
            version: The dataset version; must belong to ``dataset``.
            description: The STAC-aligned description.
            clock: Source of every timestamp.
            ids: Source of the asset id and the event id.

        Returns:
            The new raster asset and ``RasterAssetCatalogued``.

        Raises:
            VersionDatasetMismatchError: If ``version`` belongs to another dataset.
            DatasetStatusError: If the dataset is deprecated or retired.
        """
        _require_version_of(dataset, version)
        _require_accepts_new_data(dataset, "catalogue_raster")
        now = clock.now()
        asset = RasterAsset.model_validate(
            {
                **description.as_fields(),
                "id": ids.new_id(),
                "dataset_version_id": version.id,
                "created_at": now,
                "updated_at": now,
            }
        )
        event = RasterAssetCatalogued(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=asset.id,
            version=asset.version,
            dataset_version_id=version.id,
            stac_id=asset.stac_id,
        )
        return AggregateChange[RasterAsset](state=asset, events=(event,))
