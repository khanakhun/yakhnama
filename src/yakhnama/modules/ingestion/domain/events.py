"""Domain events of the ``ingestion`` bounded context.

Event types are ``ingestion.<snake_case_name>``. Payloads carry ids, codes, statuses,
checksums and counts only: never descriptions, licence texts, issue messages or
observed values, because events are relayed to subscribers and kept in the outbox,
and third-party text is untrusted.

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from pydantic import Field

from yakhnama.modules.ingestion.domain.value_objects import (
    AdapterName,
    DatasetCode,
    DatasetStatus,
    InputChecksum,
    RecordVersion,
    RunCounts,
    RunStatus,
    StacId,
    VersionLabel,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

DATASET_AGGREGATE_TYPE: Final = "dataset"
DATASET_VERSION_AGGREGATE_TYPE: Final = "dataset_version"
INGESTION_RUN_AGGREGATE_TYPE: Final = "ingestion_run"
RASTER_ASSET_AGGREGATE_TYPE: Final = "raster_asset"
COVERAGE_FIELD_COUNT: Final = 2
"""Number of coverage fields on a dataset; bounds ``changed_fields``."""

CoverageField = Literal["spatial_coverage", "temporal_coverage"]
"""Name of a dataset coverage field."""


class DatasetEvent(DomainEvent):
    """Fields shared by every event about a dataset; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"dataset"``.
        version: The dataset's version after the change.
    """

    aggregate_type: Literal["dataset"] = DATASET_AGGREGATE_TYPE
    version: RecordVersion


class DatasetRegistered(DatasetEvent):
    """A dataset was added to the catalog with its licence.

    Implements: Domain Events.

    Attributes:
        code: The dataset's catalog code.
    """

    event_type: ClassVar[str] = "ingestion.dataset_registered"

    code: DatasetCode


class DatasetStatusChanged(DatasetEvent):
    """A dataset was deprecated or retired.

    Implements: Domain Events.

    Attributes:
        from_status: The status before the change.
        to_status: The status after the change.
    """

    event_type: ClassVar[str] = "ingestion.dataset_status_changed"

    from_status: DatasetStatus
    to_status: DatasetStatus


class DatasetCoverageUpdated(DatasetEvent):
    """A dataset's spatial or temporal coverage changed.

    Implements: Domain Events.

    Attributes:
        changed_fields: ``spatial_coverage``, ``temporal_coverage`` or both.
    """

    event_type: ClassVar[str] = "ingestion.dataset_coverage_updated"

    changed_fields: frozenset[CoverageField] = Field(
        min_length=1, max_length=COVERAGE_FIELD_COUNT
    )


class DatasetVersionRecorded(DomainEvent):
    """A new release of a dataset was recorded with the checksum of its inputs.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"dataset_version"``.
        dataset_id: The dataset the version belongs to.
        label: The version label.
        input_checksum: SHA-256 of the version's input bytes.
    """

    event_type: ClassVar[str] = "ingestion.dataset_version_recorded"

    aggregate_type: Literal["dataset_version"] = DATASET_VERSION_AGGREGATE_TYPE
    dataset_id: EntityId
    label: VersionLabel
    input_checksum: InputChecksum


class IngestionRunEvent(DomainEvent):
    """Fields shared by every event about a run; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"ingestion_run"``.
        version: The run's version after the change.
        dataset_version_id: The dataset version the run ingests.
    """

    aggregate_type: Literal["ingestion_run"] = INGESTION_RUN_AGGREGATE_TYPE
    version: RecordVersion
    dataset_version_id: EntityId


class IngestionRunRequested(IngestionRunEvent):
    """A run was created in ``pending`` and waits for a worker.

    Implements: Domain Events.

    Attributes:
        adapter_name: The source adapter the run will use.
        triggered_by: The requesting user, or ``None`` for the system.
    """

    event_type: ClassVar[str] = "ingestion.ingestion_run_requested"

    adapter_name: AdapterName
    triggered_by: EntityId | None


class IngestionRunStarted(IngestionRunEvent):
    """A worker started processing the run.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "ingestion.ingestion_run_started"


class IngestionRunFinished(IngestionRunEvent):
    """A run reached a terminal status.

    Implements: Domain Events.

    Attributes:
        status: ``succeeded``, ``partially_succeeded`` or ``failed``.
        counts: The counts the run reached.
        error_count: Errors in the run's report.
        input_checksum: SHA-256 of the bytes the run read, if it got that far.
    """

    event_type: ClassVar[str] = "ingestion.ingestion_run_finished"

    status: RunStatus
    counts: RunCounts
    error_count: int = Field(ge=0)
    input_checksum: InputChecksum | None


class RasterAssetCatalogued(DomainEvent):
    """A raster was added to the STAC-aligned catalog.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"raster_asset"``.
        version: The raster asset's version, 1 at creation.
        dataset_version_id: The dataset version it belongs to.
        stac_id: Its STAC item id.
    """

    event_type: ClassVar[str] = "ingestion.raster_asset_catalogued"

    aggregate_type: Literal["raster_asset"] = RASTER_ASSET_AGGREGATE_TYPE
    version: RecordVersion
    dataset_version_id: EntityId
    stac_id: StacId
