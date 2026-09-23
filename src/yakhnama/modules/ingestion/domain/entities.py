"""Entities of the ``ingestion`` bounded context.

- ``Dataset`` (aggregate root): one catalog entry, always with a licence.
- ``DatasetVersion`` (entity): one release of a dataset, fixed once recorded.
- ``IngestionRun`` (aggregate root): one attempt to ingest a dataset version, with an
  explicit transition table (``RUN_TRANSITIONS``).
- ``Observation`` (entity, not an aggregate): one value of one variable at one site
  and instant, identified by its natural key; it is appended, never changed.
- ``RasterAsset`` (aggregate root): one STAC-aligned raster item; the bytes live in
  object storage, only their hrefs here.

Every model is frozen. A state-changing method validates the new state, bumps
``version`` by one, stamps ``updated_at`` from the injected ``Clock`` and returns an
``AggregateChange`` with the new instance and its events. A change already in effect
returns the aggregate unchanged with no events, so a retried command is harmless.

Patterns: Entity, Aggregate Root, State, Domain Events.
"""

from datetime import UTC, datetime
from typing import Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from yakhnama.modules.ingestion.domain.errors import (
    DatasetStatusError,
    RunOutcomeError,
    RunStateError,
)
from yakhnama.modules.ingestion.domain.events import (
    CoverageField,
    DatasetCoverageUpdated,
    DatasetStatusChanged,
    IngestionRunFinished,
    IngestionRunStarted,
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
    ObservationKey,
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
    can_move_dataset,
    can_move_run,
    is_known_variable,
    latest_instant,
    require_unique_raster_parts,
    variable_definition,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.value_objects import (
    DatePrecision,
    DateWithPrecision,
    Measurement,
)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _to_utc_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)


def _check_updated_after_created(created_at: datetime, updated_at: datetime) -> None:
    if updated_at < created_at:
        message = "updated_at must not be earlier than created_at"
        raise ValueError(message)


def _fields(model: BaseModel) -> dict[str, object]:
    # Nested value objects stay objects; model_dump would turn them into dicts.
    return {name: getattr(model, name) for name in type(model).model_fields}


# --------------------------------------------------------------------------- #
# Dataset                                                                     #
# --------------------------------------------------------------------------- #


class Dataset(BaseModel):
    """One dataset in the catalog, with the licence it is ingested under.

    ``licence`` is required by type: a dataset without recorded terms cannot exist,
    so nothing can ever be ingested without one (Phase 4 plan §1). ``code`` and
    ``licence`` are fixed at registration; different terms for new releases are a
    new dataset (**proposed**, open question).

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        code: Catalog code, unique and never reused.
        title: Human-readable title.
        publisher: Who publishes the data.
        licence: The publisher's terms and attribution.
        spatial_coverage: The covered area, if known.
        temporal_coverage: The covered period, if known.
        update_frequency: How often the publisher releases data.
        status: ``active``, ``deprecated`` or ``retired``.
        description: Long-form description, if any.
        homepage_url: The dataset's page, if online.
        version: Optimistic-concurrency version, 1 at creation.
        created_at: When it was registered, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: DatasetCode
    title: DatasetTitle
    publisher: Publisher
    licence: DatasetLicence
    spatial_coverage: SpatialCoverage | None = None
    temporal_coverage: TemporalCoverage | None = None
    update_frequency: UpdateFrequency
    status: DatasetStatus = DatasetStatus.ACTIVE
    description: DatasetDescription | None = None
    homepage_url: DatasetUrl | None = None
    version: RecordVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    _normalise_to_utc = field_validator("created_at", "updated_at", mode="after")(
        _to_utc
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        _check_updated_after_created(self.created_at, self.updated_at)
        return self

    @property
    def accepts_new_data(self) -> bool:
        """Tell whether new versions and runs may be recorded for the dataset.

        Only ``active`` datasets accept new data (**proposed**): deprecating a
        dataset is how a curator stops ingestion while keeping its history.

        Returns:
            ``True`` if the dataset is active.
        """
        return self.status is DatasetStatus.ACTIVE

    def deprecate(
        self, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Dataset"]:
        """Mark the dataset deprecated; it keeps its history but takes no new data.

        Args:
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The deprecated dataset and ``DatasetStatusChanged``, or the unchanged
            dataset and no events if it is already deprecated.

        Raises:
            DatasetStatusError: If the dataset is retired.
        """
        return self._move(DatasetStatus.DEPRECATED, "deprecate", clock, ids)

    def retire(self, *, clock: Clock, ids: IdGenerator) -> AggregateChange["Dataset"]:
        """Retire the dataset for good; its records stay, it never changes again.

        Args:
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The retired dataset and ``DatasetStatusChanged``, or the unchanged
            dataset and no events if it is already retired.
        """
        return self._move(DatasetStatus.RETIRED, "retire", clock, ids)

    def update_coverage(
        self,
        spatial_coverage: SpatialCoverage | None,
        temporal_coverage: TemporalCoverage | None,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["Dataset"]:
        """Replace both coverages; ``None`` clears one.

        Args:
            spatial_coverage: The new covered area, or ``None`` if unknown.
            temporal_coverage: The new covered period, or ``None`` if unknown.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The updated dataset and ``DatasetCoverageUpdated``, or the unchanged
            dataset and no events if both coverages are unchanged.

        Raises:
            DatasetStatusError: If the dataset is retired.
        """
        if self.status is DatasetStatus.RETIRED:
            raise DatasetStatusError.refused(self.id, self.status, "update_coverage")
        candidates: tuple[tuple[CoverageField, object], ...] = (
            ("spatial_coverage", spatial_coverage),
            ("temporal_coverage", temporal_coverage),
        )
        changed_fields = frozenset(
            name for name, value in candidates if getattr(self, name) != value
        )
        if not changed_fields:
            return AggregateChange[Dataset](state=self)
        now = clock.now()
        state = self._evolve(
            now,
            spatial_coverage=spatial_coverage,
            temporal_coverage=temporal_coverage,
        )
        event = DatasetCoverageUpdated(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            version=state.version,
            changed_fields=changed_fields,
        )
        return AggregateChange[Dataset](state=state, events=(event,))

    def _move(
        self, target: DatasetStatus, action: str, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["Dataset"]:
        if self.status is target:
            return AggregateChange[Dataset](state=self)
        if not can_move_dataset(self.status, target):
            raise DatasetStatusError.refused(self.id, self.status, action)
        now = clock.now()
        state = self._evolve(now, status=target)
        event = DatasetStatusChanged(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            version=state.version,
            from_status=self.status,
            to_status=target,
        )
        return AggregateChange[Dataset](state=state, events=(event,))

    def _evolve(self, now: datetime, **updates: object) -> Self:
        # model_validate, not model_copy: model_copy skips validation and would let a
        # change break an invariant.
        return self.model_validate(
            {
                **_fields(self),
                **updates,
                "version": self.version + 1,
                "updated_at": now,
            }
        )


# --------------------------------------------------------------------------- #
# Dataset version                                                             #
# --------------------------------------------------------------------------- #


class DatasetVersion(BaseModel):
    """One release of a dataset, pinned by the checksum of its input bytes.

    Fixed once recorded: a different file under the same label is a new version, so
    every observation's lineage points at exactly one set of bytes. ``(dataset_id,
    label)`` is unique; the repository enforces it.

    Implements: Entity.

    Attributes:
        id: Stable identity (UUIDv7).
        dataset_id: The dataset it belongs to.
        label: The release label.
        retrieved_at: When the input was retrieved, with precision; its period
            never starts after ``created_at``.
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

    _normalise_to_utc = field_validator("created_at", mode="after")(_to_utc)

    @model_validator(mode="after")
    def _check_retrieval(self) -> Self:
        # A version cannot record bytes retrieved after it was recorded; comparing
        # the start of the retrieval period keeps imprecise dates acceptable.
        if self.retrieved_at.truncate().value > self.created_at:
            message = "retrieved_at must not be later than created_at"
            raise ValueError(message)
        return self


# --------------------------------------------------------------------------- #
# Ingestion run                                                               #
# --------------------------------------------------------------------------- #


def _check_run_timestamps(run: "IngestionRun") -> None:
    # A failed run may never have started (it can fail while pending); every other
    # status fixes whether started_at is set.
    if run.status is not RunStatus.FAILED and (run.started_at is None) != (
        run.status is RunStatus.PENDING
    ):
        message = "started_at is set exactly when the run has started"
        raise ValueError(message)
    if (run.finished_at is not None) != run.is_finished:
        message = "finished_at is set exactly when the run has finished"
        raise ValueError(message)
    moments = [run.created_at, run.started_at, run.finished_at]
    present = [moment for moment in moments if moment is not None]
    if present != sorted(present):
        message = "created_at <= started_at <= finished_at is required"
        raise ValueError(message)


def outcome_problem(status: RunStatus, report: IngestionReport) -> str | None:
    """Return why ``report`` cannot finish a run with ``status``, or ``None``.

    The outcome rules (**proposed**):

    - ``succeeded``: no errors and no invalid records; warnings are allowed;
    - ``partially_succeeded``: at least one record persisted, and at least one
      error or invalid record (otherwise it is a success);
    - ``failed``: at least one error, so a failure always says why.

    Args:
        status: The terminal status the run would take.
        report: The report it would finish with.

    Returns:
        A fixed sentence naming the broken rule, or ``None`` if consistent.
    """
    has_problems = report.has_errors or report.counts.invalid > 0
    if status is RunStatus.SUCCEEDED and has_problems:
        return "a succeeded run has no errors and no invalid records"
    if status is RunStatus.PARTIALLY_SUCCEEDED and not (
        has_problems and report.counts.persisted > 0
    ):
        return "a partially succeeded run persisted records and has problems"
    if status is RunStatus.FAILED and not report.has_errors:
        return "a failed run reports at least one error"
    return None


class IngestionRun(BaseModel):
    """One attempt to ingest one dataset version through one source adapter.

    Lifecycle (``RUN_TRANSITIONS``)::

        pending -> running | failed
        running -> succeeded | partially_succeeded | failed

    Finished runs are terminal; a retry is a new run. ``counts`` always equals
    ``report.counts``, and the report must fit the outcome (``outcome_problem``).

    Implements: Entity / Aggregate Root, State.

    Attributes:
        id: Stable identity (UUIDv7).
        dataset_version_id: The version being ingested.
        adapter_name: The source adapter used.
        status: Where the run is in its lifecycle.
        created_at: When the run was requested, UTC.
        started_at: When a worker started it; ``None`` while pending and for a run
            that failed before starting.
        finished_at: When it reached a terminal status.
        counts: The counts reached, equal to ``report.counts``.
        report: Issues found and counts.
        input_checksum: SHA-256 of the bytes the run read, once known.
        triggered_by: The requesting user, or ``None`` for the system.
        version: Optimistic-concurrency version, 1 at creation.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    dataset_version_id: EntityId
    adapter_name: AdapterName
    status: RunStatus = RunStatus.PENDING
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    counts: RunCounts = RunCounts()
    report: IngestionReport = IngestionReport()
    input_checksum: InputChecksum | None = None
    triggered_by: EntityId | None = None
    version: RecordVersion = 1
    updated_at: AwareDatetime

    _normalise_to_utc = field_validator("created_at", "updated_at", mode="after")(
        _to_utc
    )
    _normalise_optional_to_utc = field_validator(
        "started_at", "finished_at", mode="after"
    )(_to_utc_or_none)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        _check_updated_after_created(self.created_at, self.updated_at)
        _check_run_timestamps(self)
        if self.counts != self.report.counts:
            message = "counts must equal report.counts"
            raise ValueError(message)
        problem = (
            outcome_problem(self.status, self.report) if self.is_finished else None
        )
        if problem is not None:
            raise ValueError(problem)
        return self

    @property
    def is_finished(self) -> bool:
        """Tell whether the run reached a terminal status.

        Returns:
            ``True`` for ``succeeded``, ``partially_succeeded`` and ``failed``.
        """
        return self.status not in {RunStatus.PENDING, RunStatus.RUNNING}

    def start(
        self, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["IngestionRun"]:
        """Move a pending run to ``running``.

        Not idempotent: a second worker picking up a running run is a bug the task
        queue must surface, so it is refused.

        Args:
            clock: Source of ``started_at``, ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The running run and ``IngestionRunStarted``.

        Raises:
            RunStateError: If the run is not pending.
        """
        self._require_move(RunStatus.RUNNING)
        now = clock.now()
        state = self._evolve(now, status=RunStatus.RUNNING, started_at=now)
        event = IngestionRunStarted(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            version=state.version,
            dataset_version_id=self.dataset_version_id,
        )
        return AggregateChange[IngestionRun](state=state, events=(event,))

    def succeed(
        self,
        report: IngestionReport,
        *,
        input_checksum: InputChecksum,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["IngestionRun"]:
        """Finish a running run as ``succeeded``.

        Args:
            report: The report; its counts become the run's counts.
            input_checksum: SHA-256 of the bytes read.
            clock: Source of ``finished_at``, ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The finished run and ``IngestionRunFinished``.

        Raises:
            RunStateError: If the run is not running.
            RunOutcomeError: If the report has errors or invalid records.
        """
        return self._finish(RunStatus.SUCCEEDED, report, input_checksum, clock, ids)

    def partially_succeed(
        self,
        report: IngestionReport,
        *,
        input_checksum: InputChecksum,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["IngestionRun"]:
        """Finish a running run as ``partially_succeeded``.

        Args:
            report: The report; its counts become the run's counts.
            input_checksum: SHA-256 of the bytes read.
            clock: Source of ``finished_at``, ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The finished run and ``IngestionRunFinished``.

        Raises:
            RunStateError: If the run is not running.
            RunOutcomeError: If nothing was persisted or the report has no
                problems.
        """
        return self._finish(
            RunStatus.PARTIALLY_SUCCEEDED, report, input_checksum, clock, ids
        )

    def fail(
        self,
        report: IngestionReport,
        *,
        input_checksum: InputChecksum | None = None,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["IngestionRun"]:
        """Finish a pending or running run as ``failed``.

        Args:
            report: The report, with at least one error saying why.
            input_checksum: SHA-256 of the bytes read, or ``None`` if the fetch
                itself failed.
            clock: Source of ``finished_at``, ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The failed run and ``IngestionRunFinished``.

        Raises:
            RunStateError: If the run has already finished.
            RunOutcomeError: If the report has no error.
        """
        return self._finish(RunStatus.FAILED, report, input_checksum, clock, ids)

    def _require_move(self, target: RunStatus) -> None:
        if not can_move_run(self.status, target):
            raise RunStateError.for_move(self.id, self.status, target)

    def _finish(
        self,
        target: RunStatus,
        report: IngestionReport,
        input_checksum: InputChecksum | None,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["IngestionRun"]:
        self._require_move(target)
        problem = outcome_problem(target, report)
        if problem is not None:
            raise RunOutcomeError.because(self.id, problem)
        now = clock.now()
        state = self._evolve(
            now,
            status=target,
            finished_at=now,
            counts=report.counts,
            report=report,
            input_checksum=input_checksum,
        )
        event = IngestionRunFinished(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=self.id,
            version=state.version,
            dataset_version_id=self.dataset_version_id,
            status=target,
            counts=report.counts,
            error_count=report.error_count,
            input_checksum=input_checksum,
        )
        return AggregateChange[IngestionRun](state=state, events=(event,))

    def _evolve(self, now: datetime, **updates: object) -> Self:
        return self.model_validate(
            {
                **_fields(self),
                **updates,
                "version": self.version + 1,
                "updated_at": now,
            }
        )


# --------------------------------------------------------------------------- #
# Observation                                                                 #
# --------------------------------------------------------------------------- #


class Observation(BaseModel):
    """One value of one variable at one site and instant, from one dataset version.

    Observations are appended, never changed; a correction arrives as a new dataset
    version. The site is exactly one of ``station`` and ``grid_cell``. ``value`` is
    in the registry unit of ``variable`` (``VARIABLES``) and is ``None`` exactly when
    ``quality`` is ``missing``, so a publisher's fill value (for example ``-9999``)
    is never stored as if it were measured (**proposed**).

    Construction raises ``pydantic.ValidationError`` for an unknown variable or a
    wrong unit; code that wants the domain errors (``UnknownVariableError``,
    ``ObservationUnitMismatchError``) calls ``require_variable_unit`` first.

    Implements: Entity (identified by ``key``; not an aggregate).

    Attributes:
        dataset_version_id: The dataset version it came from.
        station: The station, for station data.
        grid_cell: The grid cell, for gridded data.
        variable: The variable code.
        value: The value in the registry unit, or ``None`` when missing.
        observed_at: When the value was observed, with precision.
        quality: How far the value can be trusted.
        ingested_run_id: The run that ingested it (lineage).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version_id: EntityId
    station: StationRef | None = None
    grid_cell: GridCellRef | None = None
    variable: VariableCode
    value: Measurement | None
    observed_at: DateWithPrecision
    quality: QualityFlag
    ingested_run_id: EntityId

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if (self.station is None) == (self.grid_cell is None):
            message = "an observation has exactly one of station and grid_cell"
            raise ValueError(message)
        if not is_known_variable(self.variable):
            message = f"unknown variable {self.variable!r}"
            raise ValueError(message)
        if (self.value is None) != (self.quality is QualityFlag.MISSING):
            message = "value is absent exactly when quality is 'missing'"
            raise ValueError(message)
        expected_unit = variable_definition(self.variable).unit
        if self.value is not None and self.value.unit != expected_unit:
            message = f"{self.variable} is stored in {expected_unit}"
            raise ValueError(message)
        return self

    @property
    def site_ref(self) -> str:
        """Return ``station:<code>`` or ``grid_cell:<id>``.

        Returns:
            The site part of the natural key.
        """
        # Exactly one site is set (checked on construction), so ``next`` always
        # finds it.
        sites: tuple[StationRef | GridCellRef | None, ...] = (
            self.station,
            self.grid_cell,
        )
        return next(site.site_ref for site in sites if site is not None)

    @property
    def key(self) -> ObservationKey:
        """Return the natural key ``(observed_at, dataset_version_id, variable, site)``.

        Returns:
            The key; two observations with equal keys are the same observation.
        """
        return ObservationKey(
            observed_at=self.observed_at.value,
            dataset_version_id=self.dataset_version_id,
            variable=self.variable,
            site_ref=self.site_ref,
        )


# --------------------------------------------------------------------------- #
# Raster asset                                                                #
# --------------------------------------------------------------------------- #

STAC_VERSION: Final = "1.1.0"
"""STAC specification version the items align to (**proposed**)."""

EO_EXTENSION: Final = "https://stac-extensions.github.io/eo/v2.0.0/schema.json"
"""STAC electro-optical extension, for ``eo:cloud_cover`` and ``eo:common_name``."""

DATETIME_PRECISION_PROPERTY: Final = "yakhnama:datetime_precision"


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RasterAsset(BaseModel):
    """One raster item in the STAC-aligned catalog.

    Raster bytes are never stored in PostgreSQL (Phase 4 plan §1): each asset holds
    an href to object storage or a URL. Fields are explicit rather than a free
    ``properties`` mapping, so every stored property is validated and documented.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        dataset_version_id: The dataset version it belongs to.
        stac_id: The STAC item id.
        footprint: The area covered.
        acquired_at: When the scene was acquired, with precision.
        platform: The satellite or instrument platform.
        cloud_cover: Percentage of cloud, if known.
        bands: The bands, unique by name.
        assets: The files, unique by key, at least one.
        version: Optimistic-concurrency version, 1 at creation.
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
    version: RecordVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    _normalise_to_utc = field_validator("created_at", "updated_at", mode="after")(
        _to_utc
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        _check_updated_after_created(self.created_at, self.updated_at)
        require_unique_raster_parts(self.bands, self.assets)
        return self

    def to_stac_item_dict(self) -> dict[str, JsonValue]:
        """Return the raster as a STAC Item (GeoJSON Feature) document.

        This is a documented serialisation boundary, not a ``dict`` crossing a layer:
        the result is JSON for a STAC client and is never read back into the
        domain. ``properties.datetime`` is the start of the acquisition period; for
        a precision coarser than ``exact`` the period is also given as
        ``start_datetime`` and ``end_datetime``, and the precision itself as
        ``yakhnama:datetime_precision``. ``links`` is empty and ``collection`` is
        absent: both depend on where the catalog is served, so the API adds them.

        Returns:
            A JSON-compatible STAC Item with ``type``, ``stac_version``,
            ``stac_extensions``, ``id``, ``geometry``, ``bbox``, ``properties``,
            ``links`` and ``assets``.
        """
        box = self.footprint.bounding_box()
        return {
            "type": "Feature",
            "stac_version": STAC_VERSION,
            "stac_extensions": [EO_EXTENSION] if self._uses_eo() else [],
            "id": self.stac_id,
            "geometry": self.footprint.geojson.model_dump(
                mode="json", exclude_none=True
            ),
            "bbox": [
                box.min_longitude,
                box.min_latitude,
                box.max_longitude,
                box.max_latitude,
            ],
            "properties": self._stac_properties(),
            "links": [],
            "assets": {asset.key: _stac_asset(asset) for asset in self.assets},
        }

    def _uses_eo(self) -> bool:
        return self.cloud_cover is not None or any(
            band.common_name is not None for band in self.bands
        )

    def _stac_properties(self) -> dict[str, JsonValue]:
        start = self.acquired_at.truncate().value
        properties: dict[str, JsonValue] = {
            "datetime": _rfc3339(start),
            "platform": self.platform,
            DATETIME_PRECISION_PROPERTY: self.acquired_at.precision.value,
        }
        if self.acquired_at.precision is not DatePrecision.EXACT:
            properties["start_datetime"] = _rfc3339(start)
            properties["end_datetime"] = _rfc3339(latest_instant(self.acquired_at))
        if self.cloud_cover is not None:
            properties["eo:cloud_cover"] = self.cloud_cover
        if self.bands:
            properties["bands"] = [_stac_band(band) for band in self.bands]
        return properties


def _stac_band(band: StacBand) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {"name": band.name}
    if band.description is not None:
        document["description"] = band.description
    if band.common_name is not None:
        document["eo:common_name"] = band.common_name
    return document


def _stac_asset(asset: StacAsset) -> dict[str, JsonValue]:
    return {"href": asset.href, "type": asset.media_type, "roles": list(asset.roles)}
