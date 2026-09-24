"""Write requests accepted by the ingestion command handlers.

Commands acting on an existing dataset accept an optional ``expected_version``:
the API fills it from ``If-Match`` and the handler raises
``PreconditionFailedError`` (HTTP 412) when the stored version differs.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.ingestion.domain.reference import DatasetReferenceFile
from yakhnama.modules.ingestion.domain.value_objects import (
    AdapterName,
    DatasetDetails,
    DatasetVersionDetails,
    RasterAssetDescription,
    RecordVersion,
)
from yakhnama.shared_kernel.ids import EntityId


class RegisterDataset(BaseModel):
    """Register a dataset in the catalog, with its licence.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator.
        details: Code, title, publisher, licence and the optional descriptions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    details: DatasetDetails


class RecordDatasetVersion(BaseModel):
    """Record one release of an active dataset, pinned by its checksum.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator.
        dataset_id: The dataset.
        details: Label, retrieval time, checksum and notes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    dataset_id: EntityId
    details: DatasetVersionDetails


class DeprecateDataset(BaseModel):
    """Stop new data for a dataset while keeping its history.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator.
        dataset_id: The dataset.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    dataset_id: EntityId
    expected_version: RecordVersion | None = None


class RetireDataset(BaseModel):
    """Retire a dataset for good; its records stay.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator.
        dataset_id: The dataset.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    dataset_id: EntityId
    expected_version: RecordVersion | None = None


class RunIngestion(BaseModel):
    """Request an ingestion run of one dataset version through one source adapter.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator.
        dataset_id: The dataset.
        version_id: The version to ingest; it belongs to ``dataset_id``.
        adapter_name: The registered source adapter to read through.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    dataset_id: EntityId
    version_id: EntityId
    adapter_name: AdapterName


class ExecuteIngestionRun(BaseModel):
    """Execute a requested run: fetch, parse, validate, normalise, store.

    Internal: enqueued as the ``ingestion.run`` task by ``RunIngestion``; no
    endpoint accepts it and it carries no actor.

    Implements: Command.

    Attributes:
        run_id: The pending run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: EntityId


class CatalogueRasterAsset(BaseModel):
    """Add one STAC-aligned raster of a dataset version to the catalog.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator.
        dataset_id: The dataset.
        version_id: The version the raster belongs to.
        description: Footprint, acquisition time, platform, bands and assets.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    dataset_id: EntityId
    version_id: EntityId
    description: RasterAssetDescription


class LoadReferenceDatasets(BaseModel):
    """Load the dataset reference file, registering new codes idempotently.

    Implements: Command.

    Attributes:
        actor: Who asks; an administrator (the seed acts as one).
        file: The validated ``data/reference/datasets.yaml``.
        include_fixtures: Also register entries marked ``is_fixture``; only the
            development seed sets it, so synthetic data never enters a real
            catalog by accident.
        dry_run: Compute the report but roll back instead of committing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    file: DatasetReferenceFile
    include_fixtures: bool = False
    dry_run: bool = False
