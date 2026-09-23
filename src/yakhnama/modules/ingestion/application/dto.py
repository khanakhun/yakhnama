"""Results returned by the ingestion handlers, query services and pipelines.

Every DTO is a frozen Pydantic model built from a domain entity with ``from_entity``,
so the API never sees an aggregate and no ``dict`` crosses a layer. The one
deliberate exception is ``RasterAssetSummary.to_stac_item``, a documented JSON
serialisation boundary for STAC clients (see ``RasterAsset.to_stac_item_dict``).

Patterns: DTO.
"""

from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    MAX_RASTER_ASSETS,
    MAX_RASTER_BANDS,
    AdapterName,
    CloudCover,
    DatasetCode,
    DatasetDescription,
    DatasetLicence,
    DatasetStatus,
    DatasetTitle,
    DatasetUrl,
    GridCellRef,
    IngestionReport,
    InputChecksum,
    Notes,
    Platform,
    Publisher,
    QualityFlag,
    RasterFootprint,
    RecordVersion,
    RunCounts,
    RunStatus,
    SpatialCoverage,
    StacAsset,
    StacBand,
    StacId,
    StationRef,
    TemporalCoverage,
    UpdateFrequency,
    VariableCode,
    VersionLabel,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import DateWithPrecision, Measurement

RECENT_VERSIONS_MAX: Final = 20
"""How many versions ``DatasetDetail`` lists, newest first (**proposed**)."""

LOAD_REPORT_MAX_ENTRIES: Final = 1000
"""Bound on each list of ``LoadReport``; equals ``MAX_REFERENCE_DATASETS``."""

SKIP_REASON_MAX_LENGTH: Final = 300


class DatasetSummary(BaseModel):
    """One dataset as listed in the catalog.

    Implements: DTO.

    Attributes:
        id: The dataset's id.
        code: Catalog code.
        title: Human-readable title.
        publisher: Who publishes the data.
        licence: The terms and attribution the data is ingested under.
        update_frequency: How often the publisher releases data.
        status: ``active``, ``deprecated`` or ``retired``.
        version: Optimistic-concurrency version, for ``ETag``.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: DatasetCode
    title: DatasetTitle
    publisher: Publisher
    licence: DatasetLicence
    update_frequency: UpdateFrequency
    status: DatasetStatus
    version: RecordVersion
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, dataset: Dataset) -> Self:
        """Build the summary of ``dataset``.

        Args:
            dataset: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            id=dataset.id,
            code=dataset.code,
            title=dataset.title,
            publisher=dataset.publisher,
            licence=dataset.licence,
            update_frequency=dataset.update_frequency,
            status=dataset.status,
            version=dataset.version,
            created_at=dataset.created_at,
            updated_at=dataset.updated_at,
        )


class DatasetVersionSummary(BaseModel):
    """One release of a dataset.

    Implements: DTO.

    Attributes:
        id: The version's id.
        dataset_id: The dataset it belongs to.
        label: The release label.
        retrieved_at: When the input was retrieved, with precision.
        input_checksum: SHA-256 of the input bytes.
        notes: Curator remarks, if any.
        created_at: When the version was recorded, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    dataset_id: EntityId
    label: VersionLabel
    retrieved_at: DateWithPrecision
    input_checksum: InputChecksum
    notes: Notes | None = None
    created_at: AwareDatetime

    @classmethod
    def from_entity(cls, version: DatasetVersion) -> Self:
        """Build the summary of ``version``.

        Args:
            version: The entity.

        Returns:
            Its summary.
        """
        return cls(
            id=version.id,
            dataset_id=version.dataset_id,
            label=version.label,
            retrieved_at=version.retrieved_at,
            input_checksum=version.input_checksum,
            notes=version.notes,
            created_at=version.created_at,
        )


class DatasetDetail(DatasetSummary):
    """One dataset with its descriptive fields and most recent versions.

    Implements: DTO.

    Attributes:
        description: Long-form description, if any.
        homepage_url: The dataset's page, if online.
        spatial_coverage: The covered area, if known.
        temporal_coverage: The covered period, if known.
        recent_versions: Up to ``RECENT_VERSIONS_MAX`` versions, newest first.
    """

    description: DatasetDescription | None = None
    homepage_url: DatasetUrl | None = None
    spatial_coverage: SpatialCoverage | None = None
    temporal_coverage: TemporalCoverage | None = None
    recent_versions: tuple[DatasetVersionSummary, ...] = Field(
        default=(), max_length=RECENT_VERSIONS_MAX
    )

    @classmethod
    def from_entities(
        cls, dataset: Dataset, versions: tuple[DatasetVersion, ...]
    ) -> Self:
        """Build the detail of ``dataset`` with ``versions``.

        Args:
            dataset: The aggregate.
            versions: Its most recent versions, newest first, at most
                ``RECENT_VERSIONS_MAX``.

        Returns:
            Its detail.
        """
        return cls(
            **DatasetSummary.from_entity(dataset).model_dump(),
            description=dataset.description,
            homepage_url=dataset.homepage_url,
            spatial_coverage=dataset.spatial_coverage,
            temporal_coverage=dataset.temporal_coverage,
            recent_versions=tuple(
                DatasetVersionSummary.from_entity(version) for version in versions
            ),
        )


class RunSummary(BaseModel):
    """One ingestion run as listed for a dataset.

    Who requested the run is not part of the view (**proposed**): the listing is
    read by anyone, and an account id is not needed to judge the data.

    Implements: DTO.

    Attributes:
        id: The run's id.
        dataset_version_id: The version ingested.
        adapter_name: The source adapter used.
        status: Where the run is in its lifecycle.
        created_at: When the run was requested, UTC.
        started_at: When a worker started it, if it did.
        finished_at: When it finished, if it did.
        counts: Record counts per pipeline stage.
        error_count: Errors found, kept and omitted.
        warning_count: Warnings found, kept and omitted.
        input_checksum: SHA-256 of the bytes read, once known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    dataset_version_id: EntityId
    adapter_name: AdapterName
    status: RunStatus
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    counts: RunCounts
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    input_checksum: InputChecksum | None = None

    @classmethod
    def from_entity(cls, run: IngestionRun) -> Self:
        """Build the summary of ``run``.

        Args:
            run: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            id=run.id,
            dataset_version_id=run.dataset_version_id,
            adapter_name=run.adapter_name,
            status=run.status,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            counts=run.counts,
            error_count=run.report.error_count,
            warning_count=run.report.warning_count,
            input_checksum=run.input_checksum,
        )


class RunDetail(RunSummary):
    """One ingestion run with its full report inline.

    Implements: DTO.

    Attributes:
        report: Every kept issue, the omitted counts and the counts.
    """

    report: IngestionReport

    @classmethod
    def from_entity(cls, run: IngestionRun) -> Self:
        """Build the detail of ``run``.

        Args:
            run: The aggregate.

        Returns:
            Its detail, report included.
        """
        return cls(**RunSummary.from_entity(run).model_dump(), report=run.report)


class ObservationRecord(BaseModel):
    """One stored observation, as the time-series query returns it.

    Implements: DTO.

    Attributes:
        dataset_version_id: The dataset version it came from.
        site_ref: ``station:<code>`` or ``grid_cell:<id>``.
        station: The station, for station data.
        grid_cell: The grid cell, for gridded data.
        variable: The variable code.
        value: The value in the registry unit, or ``None`` when missing.
        observed_at: When it was observed, with precision.
        quality: How far the value can be trusted.
        ingested_run_id: The run that ingested it (lineage).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version_id: EntityId
    site_ref: str = Field(min_length=1)
    station: StationRef | None = None
    grid_cell: GridCellRef | None = None
    variable: VariableCode
    value: Measurement | None
    observed_at: DateWithPrecision
    quality: QualityFlag
    ingested_run_id: EntityId

    @classmethod
    def from_entity(cls, observation: Observation) -> Self:
        """Build the record of ``observation``.

        Args:
            observation: The entity.

        Returns:
            Its record.
        """
        return cls(
            dataset_version_id=observation.dataset_version_id,
            site_ref=observation.site_ref,
            station=observation.station,
            grid_cell=observation.grid_cell,
            variable=observation.variable,
            value=observation.value,
            observed_at=observation.observed_at,
            quality=observation.quality,
            ingested_run_id=observation.ingested_run_id,
        )


class RasterAssetSummary(BaseModel):
    """One catalogued raster, with everything needed to render its STAC item.

    Implements: DTO.

    Attributes:
        id: The asset's id.
        dataset_version_id: The dataset version it belongs to.
        stac_id: The STAC item id.
        footprint: The area covered.
        acquired_at: When the scene was acquired, with precision.
        platform: The satellite or instrument platform.
        cloud_cover: Percentage of cloud, if known.
        bands: The bands.
        assets: The files; bytes live in object storage.
        version: Optimistic-concurrency version.
        created_at: When it was catalogued, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    dataset_version_id: EntityId
    stac_id: StacId
    footprint: RasterFootprint
    acquired_at: DateWithPrecision
    platform: Platform
    cloud_cover: CloudCover | None = None
    bands: tuple[StacBand, ...] = Field(default=(), max_length=MAX_RASTER_BANDS)
    assets: tuple[StacAsset, ...] = Field(min_length=1, max_length=MAX_RASTER_ASSETS)
    version: RecordVersion
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, asset: RasterAsset) -> Self:
        """Build the summary of ``asset``.

        Args:
            asset: The aggregate.

        Returns:
            Its summary.
        """
        return cls.model_validate(
            {name: getattr(asset, name) for name in type(asset).model_fields}
        )

    def to_stac_item(self) -> dict[str, JsonValue]:
        """Return the raster as a STAC Item document.

        The rendering is the entity's, so the API and any exporter produce the same
        item; see ``RasterAsset.to_stac_item_dict`` for what is and is not included.

        Returns:
            A JSON-compatible STAC Item (GeoJSON Feature).
        """
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return RasterAsset.model_validate(fields).to_stac_item_dict()


class IngestionOutcome(BaseModel):
    """What one pipeline run produced: the finished run.

    Implements: DTO.

    Attributes:
        run: The run in its terminal status, with its report and counts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: IngestionRun

    @property
    def status(self) -> RunStatus:
        """Return the terminal status the run reached.

        Returns:
            ``run.status``.
        """
        return self.run.status

    @property
    def report(self) -> IngestionReport:
        """Return the run's report.

        Returns:
            ``run.report``.
        """
        return self.run.report


class SkippedChange(BaseModel):
    """A difference between the reference file and the catalog left unapplied.

    Implements: DTO.

    Attributes:
        code: The dataset code the difference concerns.
        reason: Why the loader did not apply it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DatasetCode
    reason: str = Field(min_length=1, max_length=SKIP_REASON_MAX_LENGTH)


class LoadReport(BaseModel):
    """What loading the dataset reference file did.

    Every code of the file appears in exactly one of ``created``, ``updated``,
    ``unchanged`` and ``excluded``. ``skipped_with_reason`` lists differences the
    loader refused to apply; their codes also appear in ``updated`` or
    ``unchanged``.

    Implements: DTO.

    Attributes:
        dry_run: Whether the changes were rolled back instead of committed.
        created: Codes registered by this load, in file order.
        updated: Codes whose status or coverage this load changed.
        unchanged: Codes already matching the file.
        excluded: Fixture codes left out because fixtures were not included.
        skipped_with_reason: Differences left unapplied, with the reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dry_run: bool
    created: tuple[DatasetCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    updated: tuple[DatasetCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    unchanged: tuple[DatasetCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    excluded: tuple[DatasetCode, ...] = Field(max_length=LOAD_REPORT_MAX_ENTRIES)
    skipped_with_reason: tuple[SkippedChange, ...] = Field(
        max_length=LOAD_REPORT_MAX_ENTRIES
    )

    @property
    def is_unchanged(self) -> bool:
        """Tell whether the load created and updated nothing.

        Returns:
            ``True`` if ``created`` and ``updated`` are empty.
        """
        return not self.created and not self.updated
