"""Unit tests for ``yakhnama.modules.ingestion.domain.factories``."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.ingestion import (
    TEST_FOOTPRINT,
    DatasetDetailsTestFactory,
    DatasetTestFactory,
    DatasetVersionTestFactory,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.ingestion.domain.entities import Dataset, DatasetVersion
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
from yakhnama.modules.ingestion.domain.factories import (
    DatasetFactory,
    DatasetVersionFactory,
    IngestionRunFactory,
    RasterAssetFactory,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    COG_MEDIA_TYPE,
    DatasetStatus,
    DatasetVersionDetails,
    RasterAssetDescription,
    RunRequest,
    RunStatus,
    StacAsset,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
SHA256 = "c" * 64


def _dataset(status: DatasetStatus = DatasetStatus.ACTIVE) -> Dataset:
    earlier = NOW - timedelta(days=1)
    return DatasetTestFactory.build(
        factory_use_construct=False,
        status=status,
        created_at=earlier,
        updated_at=earlier,
    )


def _version(dataset: Dataset) -> DatasetVersion:
    return DatasetVersionTestFactory.build(
        factory_use_construct=False,
        dataset_id=dataset.id,
        created_at=NOW - timedelta(hours=1),
    )


def _description() -> RasterAssetDescription:
    return RasterAssetDescription(
        stac_id="scene-1",
        footprint=TEST_FOOTPRINT,
        acquired_at=DateWithPrecision(value=NOW, precision=DatePrecision.DAY),
        platform="test-platform",
        assets=(
            StacAsset(key="data", href="rasters/x.tif", media_type=COG_MEDIA_TYPE),
        ),
    )


# --------------------------------------------------------------------------- #
# DatasetFactory                                                              #
# --------------------------------------------------------------------------- #


def test_dataset_factory_register_creates_active_dataset_and_event() -> None:
    details = DatasetDetailsTestFactory.build(factory_use_construct=False)
    ids = SequentialIdGenerator()

    change = DatasetFactory().register(details, clock=FrozenClock(NOW), ids=ids)

    dataset = change.state
    assert dataset.id == ids.issued[0]
    assert dataset.code == details.code
    assert dataset.licence == details.licence
    assert dataset.status is DatasetStatus.ACTIVE
    assert dataset.version == 1
    assert dataset.created_at == dataset.updated_at == NOW
    (event,) = change.events
    assert isinstance(event, DatasetRegistered)
    assert event.code == details.code
    assert event.aggregate_id == dataset.id
    assert event.event_id == ids.issued[1]


def test_dataset_factory_register_without_licence_raises_licence_required() -> None:
    details = DatasetDetailsTestFactory.build(factory_use_construct=False, licence=None)
    ids = SequentialIdGenerator()

    with pytest.raises(LicenceRequiredError) as caught:
        DatasetFactory().register(details, clock=FrozenClock(NOW), ids=ids)

    assert caught.value.details == {"code": details.code}
    assert ids.issued == []


# --------------------------------------------------------------------------- #
# DatasetVersionFactory                                                       #
# --------------------------------------------------------------------------- #


def test_dataset_version_factory_record_creates_version_and_event() -> None:
    dataset = _dataset()
    details = DatasetVersionDetails(
        label="2024-08-01",
        retrieved_at=DateWithPrecision(value=NOW, precision=DatePrecision.DAY),
        input_checksum=SHA256,
        notes="Synthetic",
    )

    change = DatasetVersionFactory().record(
        dataset, details, clock=FrozenClock(NOW), ids=SequentialIdGenerator()
    )

    version = change.state
    assert version.dataset_id == dataset.id
    assert version.label == "2024-08-01"
    assert version.input_checksum == SHA256
    assert version.notes == "Synthetic"
    assert version.created_at == NOW
    (event,) = change.events
    assert isinstance(event, DatasetVersionRecorded)
    assert event.dataset_id == dataset.id
    assert event.aggregate_type == "dataset_version"


@pytest.mark.parametrize("status", [DatasetStatus.DEPRECATED, DatasetStatus.RETIRED])
def test_dataset_version_factory_record_for_closed_dataset_is_refused(
    status: DatasetStatus,
) -> None:
    details = DatasetVersionDetails(
        label="v2",
        retrieved_at=DateWithPrecision(value=NOW, precision=DatePrecision.EXACT),
        input_checksum=SHA256,
    )

    with pytest.raises(DatasetStatusError):
        DatasetVersionFactory().record(
            _dataset(status),
            details,
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(),
        )


# --------------------------------------------------------------------------- #
# IngestionRunFactory                                                         #
# --------------------------------------------------------------------------- #


def test_ingestion_run_factory_start_creates_pending_run_and_event() -> None:
    dataset = _dataset()
    version = _version(dataset)
    actor_id = SequentialIdGenerator(seed=3).new_id()
    request = RunRequest(adapter_name="local_csv_temperature", triggered_by=actor_id)

    change = IngestionRunFactory().start(
        dataset, version, request, clock=FrozenClock(NOW), ids=SequentialIdGenerator()
    )

    run = change.state
    assert run.status is RunStatus.PENDING
    assert run.dataset_version_id == version.id
    assert run.adapter_name == "local_csv_temperature"
    assert run.triggered_by == actor_id
    assert run.started_at is None
    (event,) = change.events
    assert isinstance(event, IngestionRunRequested)
    assert event.triggered_by == actor_id
    assert event.dataset_version_id == version.id


def test_ingestion_run_factory_start_with_foreign_version_is_refused() -> None:
    dataset = _dataset()
    foreign = _version(_dataset())

    with pytest.raises(VersionDatasetMismatchError):
        IngestionRunFactory().start(
            dataset,
            foreign,
            RunRequest(adapter_name="local_csv_temperature"),
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(),
        )


def test_ingestion_run_factory_start_for_retired_dataset_is_refused() -> None:
    dataset = _dataset(DatasetStatus.RETIRED)

    with pytest.raises(DatasetStatusError) as caught:
        IngestionRunFactory().start(
            dataset,
            _version(dataset),
            RunRequest(adapter_name="local_csv_temperature"),
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(),
        )

    assert caught.value.details["action"] == "start_run"


# --------------------------------------------------------------------------- #
# RasterAssetFactory                                                          #
# --------------------------------------------------------------------------- #


def test_raster_asset_factory_catalogue_creates_asset_and_event() -> None:
    dataset = _dataset()
    version = _version(dataset)

    change = RasterAssetFactory().catalogue(
        dataset,
        version,
        _description(),
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(),
    )

    asset = change.state
    assert asset.dataset_version_id == version.id
    assert asset.stac_id == "scene-1"
    assert asset.footprint == TEST_FOOTPRINT
    (event,) = change.events
    assert isinstance(event, RasterAssetCatalogued)
    assert event.stac_id == "scene-1"


def test_raster_asset_factory_catalogue_with_foreign_version_is_refused() -> None:
    with pytest.raises(VersionDatasetMismatchError):
        RasterAssetFactory().catalogue(
            _dataset(),
            _version(_dataset()),
            _description(),
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(),
        )


def test_raster_asset_factory_catalogue_for_deprecated_dataset_is_refused() -> None:
    dataset = _dataset(DatasetStatus.DEPRECATED)

    with pytest.raises(DatasetStatusError):
        RasterAssetFactory().catalogue(
            dataset,
            _version(dataset),
            _description(),
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(),
        )
