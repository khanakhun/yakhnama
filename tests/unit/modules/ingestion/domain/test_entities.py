"""Unit tests for ``yakhnama.modules.ingestion.domain.entities``."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.factories.ingestion import (
    TEST_FOOTPRINT,
    DatasetTestFactory,
    DatasetVersionTestFactory,
    IngestionRunTestFactory,
    ObservationTestFactory,
    RasterAssetTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.ingestion.domain.entities import (
    DATETIME_PRECISION_PROPERTY,
    EO_EXTENSION,
    STAC_VERSION,
    Dataset,
    IngestionRun,
    Observation,
    RasterAsset,
    outcome_problem,
)
from yakhnama.modules.ingestion.domain.errors import (
    DatasetStatusError,
    RunOutcomeError,
    RunStateError,
)
from yakhnama.modules.ingestion.domain.events import (
    DatasetCoverageUpdated,
    DatasetStatusChanged,
    IngestionRunFinished,
    IngestionRunStarted,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    COG_MEDIA_TYPE,
    RUN_TRANSITIONS,
    VARIABLES,
    DatasetStatus,
    GridCellRef,
    IngestionIssue,
    IngestionReport,
    IssueSeverity,
    QualityFlag,
    RunCounts,
    RunStatus,
    SpatialCoverage,
    StacAsset,
    StacBand,
    StationRef,
    TemporalCoverage,
    latest_instant,
)
from yakhnama.shared_kernel.value_objects import (
    KNOWN_UNITS,
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 9, 2, tzinfo=UTC)
SHA256 = "b" * 64

CLEAN = IngestionReport(
    counts=RunCounts(fetched=3, parsed=3, valid=3, deduplicated=1, persisted=2)
)
PARTIAL = IngestionReport(
    issues=(IngestionIssue(stage="validate", reference="row 2", message="bad"),),
    counts=RunCounts(fetched=3, parsed=3, valid=2, invalid=1, persisted=2),
)
BROKEN = IngestionReport(
    issues=(IngestionIssue(stage="fetch", message="source unreachable"),)
)
WARNED = IngestionReport(
    issues=(
        IngestionIssue(
            stage="normalise", message="rounded", severity=IssueSeverity.WARNING
        ),
    ),
    counts=RunCounts(fetched=1, parsed=1, valid=1, persisted=1),
)


def _clock() -> SteppingClock:
    return SteppingClock(CHANGED_AT, timedelta(seconds=1))


def _dataset(**fields: object) -> Dataset:
    return DatasetTestFactory.build(
        factory_use_construct=False,
        **{"created_at": CREATED_AT, "updated_at": CREATED_AT, **fields},
    )


def _run(**fields: object) -> IngestionRun:
    return IngestionRunTestFactory.build(
        factory_use_construct=False,
        **{"created_at": CREATED_AT, "updated_at": CREATED_AT, **fields},
    )


def _running() -> IngestionRun:
    return _run().start(clock=_clock(), ids=SequentialIdGenerator()).state


# --------------------------------------------------------------------------- #
# Dataset                                                                     #
# --------------------------------------------------------------------------- #


def test_dataset_timestamps_in_other_offset_are_normalised_to_utc() -> None:
    local = datetime(2026, 9, 1, 5, tzinfo=timezone(timedelta(hours=5)))

    dataset = _dataset(created_at=local, updated_at=local)

    assert dataset.created_at == CREATED_AT
    assert dataset.updated_at.tzinfo is UTC


def test_dataset_updated_before_created_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="updated_at"):
        _dataset(updated_at=CREATED_AT - timedelta(seconds=1))


def test_dataset_without_licence_cannot_exist() -> None:
    with pytest.raises(PydanticValidationError, match="licence"):
        _dataset(licence=None)


def test_dataset_attribute_assignment_is_refused() -> None:
    dataset = _dataset()

    with pytest.raises(PydanticValidationError):
        dataset.status = DatasetStatus.RETIRED  # type: ignore[misc]  # reason: asserting frozen


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (DatasetStatus.ACTIVE, True),
        (DatasetStatus.DEPRECATED, False),
        (DatasetStatus.RETIRED, False),
    ],
)
def test_dataset_accepts_new_data_only_while_active(
    status: DatasetStatus,
    expected: bool,  # noqa: FBT001  # reason: parametrised expectation
) -> None:
    dataset = _dataset(status=status)

    assert dataset.accepts_new_data is expected


def test_dataset_deprecate_active_returns_new_state_and_event() -> None:
    dataset = _dataset()
    ids = SequentialIdGenerator()

    change = dataset.deprecate(clock=_clock(), ids=ids)

    assert change.state.status is DatasetStatus.DEPRECATED
    assert change.state.version == 2
    assert change.state.updated_at == CHANGED_AT
    assert dataset.status is DatasetStatus.ACTIVE
    (event,) = change.events
    assert isinstance(event, DatasetStatusChanged)
    assert event.from_status is DatasetStatus.ACTIVE
    assert event.to_status is DatasetStatus.DEPRECATED
    assert event.version == 2
    assert event.aggregate_id == dataset.id
    assert event.event_id == ids.issued[0]


def test_dataset_deprecate_twice_is_a_no_op() -> None:
    dataset = _dataset(status=DatasetStatus.DEPRECATED)

    change = dataset.deprecate(clock=_clock(), ids=SequentialIdGenerator())

    assert change.state is dataset
    assert change.events == ()


def test_dataset_deprecate_retired_raises_dataset_status_error() -> None:
    dataset = _dataset(status=DatasetStatus.RETIRED)

    with pytest.raises(DatasetStatusError) as caught:
        dataset.deprecate(clock=_clock(), ids=SequentialIdGenerator())

    assert caught.value.details["action"] == "deprecate"
    assert caught.value.details["status"] == "retired"


@pytest.mark.parametrize("status", [DatasetStatus.ACTIVE, DatasetStatus.DEPRECATED])
def test_dataset_retire_from_open_status_emits_status_change(
    status: DatasetStatus,
) -> None:
    dataset = _dataset(status=status)

    change = dataset.retire(clock=_clock(), ids=SequentialIdGenerator())

    assert change.state.status is DatasetStatus.RETIRED
    (event,) = change.events
    assert isinstance(event, DatasetStatusChanged)
    assert event.from_status is status


def test_dataset_retire_twice_is_a_no_op() -> None:
    dataset = _dataset(status=DatasetStatus.RETIRED)

    change = dataset.retire(clock=_clock(), ids=SequentialIdGenerator())

    assert change.state is dataset
    assert change.events == ()


SPATIAL = SpatialCoverage(
    bbox=BoundingBox(
        min_longitude=72.0, min_latitude=34.0, max_longitude=78.0, max_latitude=37.5
    )
)
TEMPORAL = TemporalCoverage(
    start=DateWithPrecision(value=CREATED_AT, precision=DatePrecision.YEAR)
)


@pytest.mark.parametrize(
    ("spatial", "temporal", "expected"),
    [
        (SPATIAL, None, {"spatial_coverage"}),
        (None, TEMPORAL, {"temporal_coverage"}),
        (SPATIAL, TEMPORAL, {"spatial_coverage", "temporal_coverage"}),
    ],
)
def test_dataset_update_coverage_names_the_changed_fields(
    spatial: SpatialCoverage | None,
    temporal: TemporalCoverage | None,
    expected: set[str],
) -> None:
    dataset = _dataset()

    change = dataset.update_coverage(
        spatial, temporal, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.spatial_coverage == spatial
    assert change.state.temporal_coverage == temporal
    (event,) = change.events
    assert isinstance(event, DatasetCoverageUpdated)
    assert event.changed_fields == expected


def test_dataset_update_coverage_unchanged_is_a_no_op() -> None:
    dataset = _dataset(spatial_coverage=SPATIAL)

    change = dataset.update_coverage(
        SPATIAL, None, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state is dataset
    assert change.events == ()


def test_dataset_update_coverage_of_retired_raises_dataset_status_error() -> None:
    dataset = _dataset(status=DatasetStatus.RETIRED)

    with pytest.raises(DatasetStatusError):
        dataset.update_coverage(
            SPATIAL, None, clock=_clock(), ids=SequentialIdGenerator()
        )


def test_dataset_change_with_clock_behind_creation_is_rejected() -> None:
    later = CHANGED_AT + timedelta(days=1)
    dataset = _dataset(created_at=later, updated_at=later)

    with pytest.raises(PydanticValidationError, match="updated_at"):
        dataset.deprecate(clock=_clock(), ids=SequentialIdGenerator())


# --------------------------------------------------------------------------- #
# Dataset version                                                             #
# --------------------------------------------------------------------------- #


def test_dataset_version_retrieved_after_recording_is_rejected() -> None:
    later = DateWithPrecision(
        value=CREATED_AT + timedelta(days=1), precision=DatePrecision.EXACT
    )

    with pytest.raises(PydanticValidationError, match="retrieved_at"):
        DatasetVersionTestFactory.build(
            factory_use_construct=False, created_at=CREATED_AT, retrieved_at=later
        )


def test_dataset_version_retrieved_in_period_containing_recording_is_accepted() -> None:
    same_month = DateWithPrecision(value=CREATED_AT, precision=DatePrecision.MONTH)

    version = DatasetVersionTestFactory.build(
        factory_use_construct=False,
        created_at=CREATED_AT + timedelta(days=3),
        retrieved_at=same_month,
    )

    assert version.retrieved_at == same_month


def test_dataset_version_checksum_is_normalised_to_lower_case() -> None:
    version = DatasetVersionTestFactory.build(
        factory_use_construct=False, input_checksum="C" * 64
    )

    assert version.input_checksum == "c" * 64


# --------------------------------------------------------------------------- #
# Ingestion run: lifecycle                                                    #
# --------------------------------------------------------------------------- #


def test_ingestion_run_new_is_pending_with_empty_report() -> None:
    run = _run()

    assert run.status is RunStatus.PENDING
    assert run.counts == RunCounts()
    assert run.report == IngestionReport()
    assert run.is_finished is False


def test_ingestion_run_start_sets_running_and_started_at() -> None:
    run = _run()
    clock = _clock()

    change = run.start(clock=clock, ids=SequentialIdGenerator())

    assert change.state.status is RunStatus.RUNNING
    assert change.state.started_at == CHANGED_AT
    assert change.state.version == 2
    (event,) = change.events
    assert isinstance(event, IngestionRunStarted)
    assert event.dataset_version_id == run.dataset_version_id


def test_ingestion_run_start_twice_raises_run_state_error() -> None:
    running = _running()

    with pytest.raises(RunStateError) as caught:
        running.start(clock=_clock(), ids=SequentialIdGenerator())

    assert caught.value.details["from_status"] == "running"
    assert caught.value.details["to_status"] == "running"


@pytest.mark.parametrize(
    ("method", "report", "expected"),
    [
        ("succeed", CLEAN, RunStatus.SUCCEEDED),
        ("succeed", WARNED, RunStatus.SUCCEEDED),
        ("partially_succeed", PARTIAL, RunStatus.PARTIALLY_SUCCEEDED),
        ("fail", BROKEN, RunStatus.FAILED),
    ],
)
def test_ingestion_run_finish_records_report_counts_and_event(
    method: Literal["succeed", "partially_succeed", "fail"],
    report: IngestionReport,
    expected: RunStatus,
) -> None:
    running = _running()

    change = getattr(running, method)(
        report, input_checksum=SHA256, clock=_clock(), ids=SequentialIdGenerator()
    )

    finished = change.state
    assert finished.status is expected
    assert finished.report == report
    assert finished.counts == report.counts
    assert finished.input_checksum == SHA256
    assert finished.finished_at == CHANGED_AT
    assert finished.is_finished is True
    (event,) = change.events
    assert isinstance(event, IngestionRunFinished)
    assert event.status is expected
    assert event.counts == report.counts
    assert event.error_count == report.error_count


def test_ingestion_run_fail_while_pending_keeps_started_at_empty() -> None:
    run = _run()

    change = run.fail(BROKEN, clock=_clock(), ids=SequentialIdGenerator())

    assert change.state.status is RunStatus.FAILED
    assert change.state.started_at is None
    assert change.state.input_checksum is None


@pytest.mark.parametrize("method", ["succeed", "partially_succeed"])
def test_ingestion_run_succeed_while_pending_raises_run_state_error(
    method: str,
) -> None:
    run = _run()

    with pytest.raises(RunStateError):
        getattr(run, method)(
            PARTIAL, input_checksum=SHA256, clock=_clock(), ids=SequentialIdGenerator()
        )


def test_ingestion_run_finished_cannot_fail_again() -> None:
    failed = _run().fail(BROKEN, clock=_clock(), ids=SequentialIdGenerator()).state

    with pytest.raises(RunStateError):
        failed.fail(BROKEN, clock=_clock(), ids=SequentialIdGenerator())


_OPERATIONS = ("start", "succeed", "partially_succeed", "fail")
_TARGETS = {
    "start": RunStatus.RUNNING,
    "succeed": RunStatus.SUCCEEDED,
    "partially_succeed": RunStatus.PARTIALLY_SUCCEEDED,
    "fail": RunStatus.FAILED,
}
_REPORTS = {"succeed": CLEAN, "partially_succeed": PARTIAL, "fail": BROKEN}


@given(st.lists(st.sampled_from(_OPERATIONS), max_size=6))
def test_ingestion_run_any_operation_sequence_follows_transition_table(
    operations: list[str],
) -> None:
    run = _run()
    clock = _clock()
    ids = SequentialIdGenerator()

    for operation in operations:
        target = _TARGETS[operation]
        is_allowed = target in RUN_TRANSITIONS[run.status]
        if not is_allowed:
            with pytest.raises(RunStateError):
                _apply(run, operation, clock, ids)
            continue
        next_run = _apply(run, operation, clock, ids)
        assert next_run.status is target
        assert next_run.version == run.version + 1
        run = next_run

    assert run.status in RUN_TRANSITIONS


def _apply(
    run: IngestionRun, operation: str, clock: SteppingClock, ids: SequentialIdGenerator
) -> IngestionRun:
    if operation == "start":
        return run.start(clock=clock, ids=ids).state
    method = getattr(run, operation)
    change = method(_REPORTS[operation], input_checksum=SHA256, clock=clock, ids=ids)
    return change.state  # type: ignore[no-any-return]  # reason: getattr erases the method's return type


# --------------------------------------------------------------------------- #
# Ingestion run: outcome rules and invariants                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "report", "reason"),
    [
        ("succeed", PARTIAL, "no errors and no invalid records"),
        ("partially_succeed", CLEAN, "persisted records and has problems"),
        ("partially_succeed", BROKEN, "persisted records and has problems"),
        ("fail", CLEAN, "at least one error"),
    ],
)
def test_ingestion_run_finish_with_inconsistent_report_raises_outcome_error(
    method: str, report: IngestionReport, reason: str
) -> None:
    running = _running()

    with pytest.raises(RunOutcomeError) as caught:
        getattr(running, method)(
            report, input_checksum=SHA256, clock=_clock(), ids=SequentialIdGenerator()
        )

    assert reason in str(caught.value.details["reason"])


@given(
    st.sampled_from(sorted(RunStatus)),
    st.sampled_from([CLEAN, PARTIAL, BROKEN, WARNED]),
)
def test_outcome_problem_accepts_non_terminal_statuses_and_fitting_reports(
    status: RunStatus, report: IngestionReport
) -> None:
    has_problems = report.has_errors or report.counts.invalid > 0
    expected_ok = {
        RunStatus.PENDING: True,
        RunStatus.RUNNING: True,
        RunStatus.SUCCEEDED: not has_problems,
        RunStatus.PARTIALLY_SUCCEEDED: has_problems and report.counts.persisted > 0,
        RunStatus.FAILED: report.has_errors,
    }[status]

    result = outcome_problem(status, report)

    assert (result is None) is expected_ok


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"status": RunStatus.RUNNING}, "started_at"),
        ({"started_at": CREATED_AT}, "started_at"),
        (
            {
                "status": RunStatus.RUNNING,
                "started_at": CREATED_AT,
                "finished_at": CREATED_AT,
            },
            "finished_at",
        ),
        (
            {
                "status": RunStatus.RUNNING,
                "started_at": CREATED_AT - timedelta(seconds=1),
            },
            "created_at <= started_at",
        ),
        (
            {
                "status": RunStatus.FAILED,
                "report": BROKEN,
                "finished_at": CREATED_AT - timedelta(seconds=1),
            },
            "created_at <= started_at",
        ),
        ({"counts": RunCounts(fetched=1)}, "counts must equal report.counts"),
        (
            {
                "status": RunStatus.FAILED,
                "report": CLEAN,
                "counts": CLEAN.counts,
                "finished_at": CREATED_AT,
            },
            "at least one error",
        ),
    ],
)
def test_ingestion_run_inconsistent_state_is_rejected(
    fields: dict[str, object], reason: str
) -> None:
    with pytest.raises(PydanticValidationError, match=reason):
        _run(**fields)


def test_ingestion_run_optional_timestamps_are_normalised_to_utc() -> None:
    local = datetime(2026, 9, 1, 6, tzinfo=timezone(timedelta(hours=5)))

    run = _run(status=RunStatus.RUNNING, started_at=local)

    assert run.started_at == datetime(2026, 9, 1, 1, tzinfo=UTC)
    assert run.started_at.tzinfo is UTC


# --------------------------------------------------------------------------- #
# Observation                                                                 #
# --------------------------------------------------------------------------- #

CELL = GridCellRef(
    cell_id="r1c1",
    centroid=Coordinates(longitude=74.0, latitude=36.0),
    resolution=Measurement(value=1000.0, unit=SiUnit.METRE),
)


def _observation(**fields: object) -> Observation:
    return ObservationTestFactory.build(factory_use_construct=False, **fields)


@pytest.mark.parametrize(
    "fields", [{"station": None}, {"grid_cell": CELL}], ids=["no site", "two sites"]
)
def test_observation_without_exactly_one_site_is_rejected(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError, match="exactly one of station"):
        _observation(**fields)


def test_observation_unknown_variable_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="unknown variable"):
        _observation(variable="wind_speed")


@given(st.sampled_from(sorted(VARIABLES)), st.sampled_from(sorted(KNOWN_UNITS)))
def test_observation_accepts_only_the_variable_registry_unit(
    variable: str, unit: str
) -> None:
    value = Measurement(value=1.0, unit=unit)
    is_registry_unit = unit == VARIABLES[variable].unit

    if is_registry_unit:
        observation = _observation(variable=variable, value=value)
        assert observation.value == value
    else:
        with pytest.raises(PydanticValidationError, match="is stored in"):
            _observation(variable=variable, value=value)


@pytest.mark.parametrize(
    ("value", "quality"),
    [
        (None, QualityFlag.GOOD),
        (Measurement(value=-9999.0, unit=SiUnit.KELVIN), QualityFlag.MISSING),
    ],
)
def test_observation_value_presence_must_match_missing_flag(
    value: Measurement | None, quality: QualityFlag
) -> None:
    with pytest.raises(PydanticValidationError, match="missing"):
        _observation(value=value, quality=quality)


def test_observation_missing_without_value_is_accepted() -> None:
    observation = _observation(value=None, quality=QualityFlag.MISSING)

    assert observation.value is None


def test_observation_key_at_station_uses_station_site_ref() -> None:
    observation = _observation(station=StationRef(code="41530"))

    key = observation.key

    assert key.site_ref == "station:41530"
    assert key.observed_at == observation.observed_at.value
    assert key.dataset_version_id == observation.dataset_version_id
    assert key.variable == observation.variable


def test_observation_key_at_grid_cell_uses_cell_site_ref() -> None:
    observation = _observation(station=None, grid_cell=CELL)

    assert observation.site_ref == "grid_cell:r1c1"


@given(
    st.floats(min_value=150.0, max_value=350.0),
    st.sampled_from([QualityFlag.GOOD, QualityFlag.SUSPECT, QualityFlag.ESTIMATED]),
    st.sampled_from(DatePrecision),
)
def test_observation_key_ignores_value_quality_precision_and_run(
    kelvin: float, quality: QualityFlag, precision: DatePrecision
) -> None:
    original = _observation()
    other = original.model_copy(
        update={
            "value": Measurement(value=kelvin, unit=SiUnit.KELVIN),
            "quality": quality,
            "observed_at": DateWithPrecision(
                value=original.observed_at.value, precision=precision
            ),
            "ingested_run_id": SequentialIdGenerator(seed=7).new_id(),
        }
    )

    assert Observation.model_validate(other.model_dump()).key == original.key


def test_observation_key_station_and_cell_with_same_code_differ() -> None:
    at_station = _observation(station=StationRef(code="r1c1"))
    at_cell = at_station.model_copy(update={"station": None, "grid_cell": CELL})

    assert at_station.key != at_cell.key


# --------------------------------------------------------------------------- #
# Raster asset                                                                #
# --------------------------------------------------------------------------- #


def _raster(**fields: object) -> RasterAsset:
    return RasterAssetTestFactory.build(
        factory_use_construct=False,
        **{"created_at": CREATED_AT, "updated_at": CREATED_AT, **fields},
    )


def test_raster_asset_duplicate_asset_keys_are_rejected() -> None:
    asset = StacAsset(key="data", href="rasters/x.tif", media_type=COG_MEDIA_TYPE)

    with pytest.raises(PydanticValidationError, match="asset keys"):
        _raster(assets=(asset, asset))


def test_raster_asset_without_assets_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _raster(assets=())


def test_raster_asset_updated_before_created_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="updated_at"):
        _raster(updated_at=CREATED_AT - timedelta(seconds=1))


STAC_REQUIRED_KEYS = {
    "type",
    "stac_version",
    "stac_extensions",
    "id",
    "geometry",
    "bbox",
    "properties",
    "links",
    "assets",
}

bands = st.lists(
    st.builds(
        StacBand,
        name=st.sampled_from(["B02", "B03", "B04", "B08"]),
        common_name=st.sampled_from([None, "blue", "red", "nir"]),
        description=st.sampled_from([None, "Test band"]),
    ),
    max_size=4,
    unique_by=lambda band: band.name,
)


@given(
    precision=st.sampled_from(DatePrecision),
    cloud_cover=st.none() | st.floats(min_value=0.0, max_value=100.0),
    raster_bands=bands,
)
def test_raster_asset_stac_item_dict_has_required_stac_fields(
    precision: DatePrecision,
    cloud_cover: float | None,
    raster_bands: list[StacBand],
) -> None:
    acquired_at = DateWithPrecision(
        value=datetime(2024, 7, 15, 5, 30, tzinfo=UTC), precision=precision
    )
    raster = _raster(
        acquired_at=acquired_at, cloud_cover=cloud_cover, bands=tuple(raster_bands)
    )

    item = raster.to_stac_item_dict()

    assert set(item) >= STAC_REQUIRED_KEYS
    assert item["type"] == "Feature"
    assert item["stac_version"] == STAC_VERSION
    assert item["id"] == raster.stac_id
    assert item["bbox"] == [74.0, 36.0, 75.0, 37.0]
    assert item["geometry"] == {
        "type": "Polygon",
        "coordinates": [
            [[74.0, 36.0], [75.0, 36.0], [75.0, 37.0], [74.0, 37.0], [74.0, 36.0]]
        ],
    }
    properties = item["properties"]
    assert isinstance(properties, dict)
    start = acquired_at.truncate().value.isoformat().replace("+00:00", "Z")
    assert properties["datetime"] == start
    assert properties[DATETIME_PRECISION_PROPERTY] == precision.value
    assert properties["platform"] == raster.platform
    is_exact = precision is DatePrecision.EXACT
    assert ("start_datetime" in properties) is not is_exact
    if not is_exact:
        assert properties["start_datetime"] == start
        assert properties["end_datetime"] == latest_instant(
            acquired_at
        ).isoformat().replace("+00:00", "Z")
    assert properties.get("eo:cloud_cover") == cloud_cover
    assert ("bands" in properties) is bool(raster_bands)
    uses_eo = cloud_cover is not None or any(
        band.common_name is not None for band in raster_bands
    )
    assert item["stac_extensions"] == ([EO_EXTENSION] if uses_eo else [])


def test_raster_asset_stac_item_dict_describes_bands_and_assets() -> None:
    raster = _raster(
        bands=(
            StacBand(name="B04", common_name="red", description="Red band"),
            StacBand(name="QA"),
        ),
        assets=(
            StacAsset(
                key="data",
                href="rasters/x.tif",
                media_type=COG_MEDIA_TYPE,
                roles=("data",),
            ),
            StacAsset(
                key="thumbnail",
                href="https://data.example.test/x.png",
                media_type="image/png",
                roles=("thumbnail",),
            ),
        ),
    )

    item = raster.to_stac_item_dict()

    properties = item["properties"]
    assert isinstance(properties, dict)
    assert properties["bands"] == [
        {"name": "B04", "description": "Red band", "eo:common_name": "red"},
        {"name": "QA"},
    ]
    assert item["assets"] == {
        "data": {"href": "rasters/x.tif", "type": COG_MEDIA_TYPE, "roles": ["data"]},
        "thumbnail": {
            "href": "https://data.example.test/x.png",
            "type": "image/png",
            "roles": ["thumbnail"],
        },
    }
    assert item["links"] == []
    assert "collection" not in item


def test_raster_asset_footprint_is_the_factory_square() -> None:
    raster = _raster()

    assert raster.footprint == TEST_FOOTPRINT
