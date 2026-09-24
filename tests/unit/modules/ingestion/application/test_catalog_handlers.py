"""Unit tests for the catalog command handlers, with in-memory fakes."""

import pytest

from tests.factories.ingestion import (
    TEST_FOOTPRINT,
    DatasetDetailsTestFactory,
    StacAssetTestFactory,
)
from tests.fakes.ingestion import InMemoryIngestionUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.ingestion.application.support import (
    ADMIN,
    CITIZEN,
    EARLIER,
    MODERATOR,
    NOW,
    World,
    csv,
    make_dataset,
    make_version,
    row,
    sha256,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.ingestion.application.commands import (
    CatalogueRasterAsset,
    DeprecateDataset,
    RecordDatasetVersion,
    RegisterDataset,
    RetireDataset,
)
from yakhnama.modules.ingestion.application.handlers import (
    CatalogueRasterAssetHandler,
    DeprecateDatasetHandler,
    RecordDatasetVersionHandler,
    RegisterDatasetHandler,
    RetireDatasetHandler,
)
from yakhnama.modules.ingestion.domain.errors import (
    DatasetNotFoundError,
    DatasetStatusError,
    DatasetVersionNotFoundError,
    LicenceRequiredError,
    VersionDatasetMismatchError,
)
from yakhnama.modules.ingestion.domain.events import (
    DatasetRegistered,
    DatasetStatusChanged,
    DatasetVersionRecorded,
    RasterAssetCatalogued,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetStatus,
    DatasetVersionDetails,
    RasterAssetDescription,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    PermissionDeniedError,
    PreconditionFailedError,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

UNKNOWN_ID = make_dataset().id
CONTENT = csv(row())


def version_details(label: str = "v1") -> DatasetVersionDetails:
    return DatasetVersionDetails(
        label=label,
        retrieved_at=DateWithPrecision(value=EARLIER, precision=DatePrecision.DAY),
        input_checksum=sha256(CONTENT),
    )


def description(stac_id: str = "scene-1") -> RasterAssetDescription:
    return RasterAssetDescription(
        stac_id=stac_id,
        footprint=TEST_FOOTPRINT,
        acquired_at=DateWithPrecision(value=EARLIER, precision=DatePrecision.EXACT),
        platform="test-platform",
        assets=(StacAssetTestFactory.build(),),
    )


# ---------------------------------------------------------------- register


async def test_register_dataset_by_admin_commits_dataset_and_event() -> None:
    world = World(CONTENT)
    handler = RegisterDatasetHandler(world.uow_factory, world.clock, world.ids)
    details = DatasetDetailsTestFactory.build()

    summary = await handler(RegisterDataset(actor=ADMIN, details=details))

    stored = world.uow.datasets.committed[summary.id]
    assert stored.code == details.code
    assert summary.status is DatasetStatus.ACTIVE
    assert isinstance(world.uow.committed_events[-1], DatasetRegistered)


@pytest.mark.parametrize("actor", [CITIZEN, MODERATOR, Actor.anonymous()])
async def test_register_dataset_by_non_admin_is_denied_before_reading(
    actor: Actor,
) -> None:
    world = World(CONTENT)
    handler = RegisterDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PermissionDeniedError):
        await handler(
            RegisterDataset(actor=actor, details=DatasetDetailsTestFactory.build())
        )

    assert world.uow_factory.calls == 0


async def test_register_dataset_with_taken_code_raises_conflict() -> None:
    world = World(CONTENT)
    handler = RegisterDatasetHandler(world.uow_factory, world.clock, world.ids)
    details = DatasetDetailsTestFactory.build(code=world.dataset.code)

    with pytest.raises(ConflictError) as caught:
        await handler(RegisterDataset(actor=ADMIN, details=details))

    assert caught.value.details["reason"] == "dataset_code_taken"
    assert world.uow.commit_count == 0


async def test_register_dataset_without_licence_raises_licence_required() -> None:
    world = World(CONTENT)
    handler = RegisterDatasetHandler(world.uow_factory, world.clock, world.ids)
    details = DatasetDetailsTestFactory.build(licence=None)

    with pytest.raises(LicenceRequiredError):
        await handler(RegisterDataset(actor=ADMIN, details=details))


# ------------------------------------------------------------------ version


async def test_record_dataset_version_by_admin_commits_version() -> None:
    world = World(CONTENT)
    handler = RecordDatasetVersionHandler(world.uow_factory, world.clock, world.ids)

    summary = await handler(
        RecordDatasetVersion(
            actor=ADMIN, dataset_id=world.dataset.id, details=version_details("v9")
        )
    )

    stored = world.uow.dataset_versions.committed[summary.id]
    assert stored.label == "v9"
    assert stored.created_at == NOW
    assert isinstance(world.uow.committed_events[-1], DatasetVersionRecorded)


async def test_record_dataset_version_by_moderator_is_denied() -> None:
    world = World(CONTENT)
    handler = RecordDatasetVersionHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PermissionDeniedError):
        await handler(
            RecordDatasetVersion(
                actor=MODERATOR, dataset_id=world.dataset.id, details=version_details()
            )
        )


async def test_record_dataset_version_of_unknown_dataset_raises_not_found() -> None:
    world = World(CONTENT)
    handler = RecordDatasetVersionHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(DatasetNotFoundError):
        await handler(
            RecordDatasetVersion(
                actor=ADMIN, dataset_id=UNKNOWN_ID, details=version_details()
            )
        )


async def test_record_dataset_version_with_taken_label_raises_conflict() -> None:
    world = World(CONTENT)
    handler = RecordDatasetVersionHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(ConflictError) as caught:
        await handler(
            RecordDatasetVersion(
                actor=ADMIN,
                dataset_id=world.dataset.id,
                details=version_details(world.version.label),
            )
        )

    assert caught.value.details["reason"] == "version_label_taken"


# ------------------------------------------------------ deprecate and retire


async def test_deprecate_dataset_by_admin_saves_status_and_event() -> None:
    world = World(CONTENT)
    handler = DeprecateDatasetHandler(world.uow_factory, world.clock, world.ids)

    summary = await handler(
        DeprecateDataset(actor=ADMIN, dataset_id=world.dataset.id, expected_version=1)
    )

    assert summary.status is DatasetStatus.DEPRECATED
    assert world.uow.datasets.committed[world.dataset.id].version == 2
    assert isinstance(world.uow.committed_events[-1], DatasetStatusChanged)


async def test_deprecate_dataset_twice_is_a_no_op() -> None:
    world = World(CONTENT)
    handler = DeprecateDatasetHandler(world.uow_factory, world.clock, world.ids)
    command = DeprecateDataset(actor=ADMIN, dataset_id=world.dataset.id)
    await handler(command)

    summary = await handler(command)

    assert summary.version == 2
    assert len(world.uow.committed_events) == 1


async def test_deprecate_dataset_with_stale_version_raises_precondition() -> None:
    world = World(CONTENT)
    handler = DeprecateDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PreconditionFailedError):
        await handler(
            DeprecateDataset(
                actor=ADMIN, dataset_id=world.dataset.id, expected_version=7
            )
        )


async def test_deprecate_dataset_by_citizen_is_denied() -> None:
    world = World(CONTENT)
    handler = DeprecateDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PermissionDeniedError):
        await handler(DeprecateDataset(actor=CITIZEN, dataset_id=world.dataset.id))


async def test_deprecate_unknown_dataset_raises_not_found() -> None:
    world = World(CONTENT)
    handler = DeprecateDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(DatasetNotFoundError):
        await handler(DeprecateDataset(actor=ADMIN, dataset_id=UNKNOWN_ID))


async def test_retire_dataset_by_admin_saves_status() -> None:
    world = World(CONTENT)
    handler = RetireDatasetHandler(world.uow_factory, world.clock, world.ids)

    summary = await handler(
        RetireDataset(actor=ADMIN, dataset_id=world.dataset.id, expected_version=1)
    )

    assert summary.status is DatasetStatus.RETIRED
    assert world.uow.datasets.committed[world.dataset.id].status is (
        DatasetStatus.RETIRED
    )


async def test_retire_dataset_twice_is_a_no_op() -> None:
    world = World(CONTENT)
    handler = RetireDatasetHandler(world.uow_factory, world.clock, world.ids)
    command = RetireDataset(actor=ADMIN, dataset_id=world.dataset.id)
    await handler(command)

    summary = await handler(command)

    assert summary.version == 2


async def test_retire_dataset_by_moderator_is_denied() -> None:
    world = World(CONTENT)
    handler = RetireDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PermissionDeniedError):
        await handler(RetireDataset(actor=MODERATOR, dataset_id=world.dataset.id))


async def test_retire_unknown_dataset_raises_not_found() -> None:
    world = World(CONTENT)
    handler = RetireDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(DatasetNotFoundError):
        await handler(RetireDataset(actor=ADMIN, dataset_id=UNKNOWN_ID))


async def test_retire_dataset_with_stale_version_raises_precondition() -> None:
    world = World(CONTENT)
    handler = RetireDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PreconditionFailedError):
        await handler(
            RetireDataset(actor=ADMIN, dataset_id=world.dataset.id, expected_version=2)
        )


async def test_deprecate_retired_dataset_raises_status_error() -> None:
    world = World(CONTENT)
    await RetireDatasetHandler(world.uow_factory, world.clock, world.ids)(
        RetireDataset(actor=ADMIN, dataset_id=world.dataset.id)
    )
    handler = DeprecateDatasetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(DatasetStatusError):
        await handler(DeprecateDataset(actor=ADMIN, dataset_id=world.dataset.id))


# ------------------------------------------------------------------- raster


async def test_catalogue_raster_asset_by_admin_commits_asset() -> None:
    world = World(CONTENT)
    handler = CatalogueRasterAssetHandler(world.uow_factory, world.clock, world.ids)

    summary = await handler(
        CatalogueRasterAsset(
            actor=ADMIN,
            dataset_id=world.dataset.id,
            version_id=world.version.id,
            description=description(),
        )
    )

    assert world.uow.raster_assets.committed[summary.id].stac_id == "scene-1"
    assert isinstance(world.uow.committed_events[-1], RasterAssetCatalogued)


async def test_catalogue_raster_asset_by_moderator_is_denied() -> None:
    world = World(CONTENT)
    handler = CatalogueRasterAssetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(PermissionDeniedError):
        await handler(
            CatalogueRasterAsset(
                actor=MODERATOR,
                dataset_id=world.dataset.id,
                version_id=world.version.id,
                description=description(),
            )
        )


async def test_catalogue_raster_asset_of_unknown_version_raises_not_found() -> None:
    world = World(CONTENT)
    handler = CatalogueRasterAssetHandler(world.uow_factory, world.clock, world.ids)

    with pytest.raises(DatasetVersionNotFoundError):
        await handler(
            CatalogueRasterAsset(
                actor=ADMIN,
                dataset_id=world.dataset.id,
                version_id=UNKNOWN_ID,
                description=description(),
            )
        )


async def test_catalogue_raster_asset_of_other_datasets_version_is_refused() -> None:
    world = World(CONTENT)
    other = make_dataset()
    other_version = make_version(other, CONTENT)
    uow = InMemoryIngestionUnitOfWork(
        datasets=[world.dataset, other], versions=[world.version, other_version]
    )
    handler = CatalogueRasterAssetHandler(
        InMemoryUnitOfWorkFactory(uow), world.clock, world.ids
    )

    with pytest.raises(VersionDatasetMismatchError):
        await handler(
            CatalogueRasterAsset(
                actor=ADMIN,
                dataset_id=world.dataset.id,
                version_id=other_version.id,
                description=description(),
            )
        )


async def test_catalogue_raster_asset_with_taken_stac_id_raises_conflict() -> None:
    world = World(CONTENT)
    handler = CatalogueRasterAssetHandler(world.uow_factory, world.clock, world.ids)
    command = CatalogueRasterAsset(
        actor=ADMIN,
        dataset_id=world.dataset.id,
        version_id=world.version.id,
        description=description(),
    )
    await handler(command)

    with pytest.raises(ConflictError):
        await handler(command)
