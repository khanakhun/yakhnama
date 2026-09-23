"""Unit tests for loading the dataset reference file, with in-memory fakes."""

import pytest

from tests.fakes.ingestion import InMemoryIngestionUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.ingestion.application.support import (
    ADMIN,
    MODERATOR,
    NOW,
    World,
)
from yakhnama.modules.ingestion.application.commands import LoadReferenceDatasets
from yakhnama.modules.ingestion.application.handlers import (
    LoadReferenceDatasetsHandler,
)
from yakhnama.modules.ingestion.domain.reference import (
    DatasetReferenceEntry,
    DatasetReferenceFile,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetLicence,
    DatasetStatus,
    SpatialCoverage,
    UpdateFrequency,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.value_objects import BoundingBox

LICENCE = DatasetLicence(spdx_id="CC-BY-4.0", attribution="Test attribution")
COVERAGE = SpatialCoverage(
    bbox=BoundingBox(
        min_longitude=74.0, min_latitude=35.0, max_longitude=75.0, max_latitude=36.0
    )
)


def entry(code: str, **overrides: object) -> DatasetReferenceEntry:
    return DatasetReferenceEntry.model_validate(
        {
            "code": code,
            "title": f"Test dataset {code}",
            "publisher": "Test publisher",
            "licence": LICENCE,
            "update_frequency": UpdateFrequency.DAILY,
            "source": "synthetic fixture",
            **overrides,
        }
    )


def reference_file(*entries: DatasetReferenceEntry) -> DatasetReferenceFile:
    return DatasetReferenceFile(
        schema_version=1,
        data_version="test",
        source="unit test",
        licence="CC0-1.0",
        datasets=entries,
    )


def loader() -> tuple[LoadReferenceDatasetsHandler, InMemoryIngestionUnitOfWork]:
    world = World(b"")
    uow = InMemoryIngestionUnitOfWork()
    handler = LoadReferenceDatasetsHandler(
        InMemoryUnitOfWorkFactory(uow), world.clock, world.ids
    )
    return handler, uow


def statuses(uow: InMemoryIngestionUnitOfWork) -> dict[str, DatasetStatus]:
    return {item.code: item.status for item in uow.datasets.committed.values()}


async def test_load_reference_datasets_registers_new_codes_with_their_status() -> None:
    handler, uow = loader()
    file = reference_file(
        entry("test_active"),
        entry("test_deprecated", status=DatasetStatus.DEPRECATED),
        entry("test_retired", status=DatasetStatus.RETIRED),
    )

    report = await handler(LoadReferenceDatasets(actor=ADMIN, file=file))

    assert report.created == ("test_active", "test_deprecated", "test_retired")
    assert statuses(uow) == {
        "test_active": DatasetStatus.ACTIVE,
        "test_deprecated": DatasetStatus.DEPRECATED,
        "test_retired": DatasetStatus.RETIRED,
    }


async def test_load_reference_datasets_twice_changes_nothing_the_second_time() -> None:
    handler, uow = loader()
    file = reference_file(
        entry("test_active", spatial_coverage=COVERAGE),
        entry("test_retired", status=DatasetStatus.RETIRED),
    )
    await handler(LoadReferenceDatasets(actor=ADMIN, file=file))
    events = len(uow.committed_events)

    report = await handler(LoadReferenceDatasets(actor=ADMIN, file=file))

    assert report.is_unchanged
    assert report.unchanged == ("test_active", "test_retired")
    assert report.skipped_with_reason == ()
    assert len(uow.committed_events) == events


async def test_load_reference_datasets_excludes_fixtures_unless_included() -> None:
    handler, uow = loader()
    file = reference_file(entry("test_fixture", is_fixture=True))

    excluded = await handler(LoadReferenceDatasets(actor=ADMIN, file=file))
    included = await handler(
        LoadReferenceDatasets(actor=ADMIN, file=file, include_fixtures=True)
    )

    assert excluded.excluded == ("test_fixture",)
    assert included.created == ("test_fixture",)
    assert list(statuses(uow)) == ["test_fixture"]


async def test_load_reference_datasets_dry_run_commits_nothing() -> None:
    handler, uow = loader()

    report = await handler(
        LoadReferenceDatasets(
            actor=ADMIN, file=reference_file(entry("test_active")), dry_run=True
        )
    )

    assert report.dry_run is True
    assert report.created == ("test_active",)
    assert uow.datasets.committed == {}
    assert uow.commit_count == 0


async def test_load_reference_datasets_by_moderator_is_denied() -> None:
    handler, uow = loader()

    with pytest.raises(PermissionDeniedError):
        await handler(
            LoadReferenceDatasets(
                actor=MODERATOR, file=reference_file(entry("test_active"))
            )
        )

    assert uow.commit_count == 0


async def test_load_reference_datasets_never_changes_registration_in_place() -> None:
    handler, uow = loader()
    await handler(
        LoadReferenceDatasets(actor=ADMIN, file=reference_file(entry("test_active")))
    )

    report = await handler(
        LoadReferenceDatasets(
            actor=ADMIN,
            file=reference_file(entry("test_active", title="A new title")),
        )
    )

    assert report.unchanged == ("test_active",)
    [skipped] = report.skipped_with_reason
    assert skipped.reason.startswith("title differs")
    [stored] = uow.datasets.committed.values()
    assert stored.title == "Test dataset test_active"


async def test_load_reference_datasets_applies_coverage_and_forward_status() -> None:
    handler, uow = loader()
    await handler(
        LoadReferenceDatasets(actor=ADMIN, file=reference_file(entry("test_active")))
    )

    report = await handler(
        LoadReferenceDatasets(
            actor=ADMIN,
            file=reference_file(
                entry(
                    "test_active",
                    spatial_coverage=COVERAGE,
                    status=DatasetStatus.DEPRECATED,
                )
            ),
        )
    )

    assert report.updated == ("test_active",)
    [stored] = uow.datasets.committed.values()
    assert stored.spatial_coverage == COVERAGE
    assert stored.status is DatasetStatus.DEPRECATED
    assert stored.version == 3
    assert stored.updated_at == NOW


async def test_load_reference_datasets_never_undoes_a_status_move() -> None:
    handler, uow = loader()
    await handler(
        LoadReferenceDatasets(
            actor=ADMIN,
            file=reference_file(entry("test_code", status=DatasetStatus.DEPRECATED)),
        )
    )

    report = await handler(
        LoadReferenceDatasets(actor=ADMIN, file=reference_file(entry("test_code")))
    )

    assert report.unchanged == ("test_code",)
    [skipped] = report.skipped_with_reason
    assert "never undone" in skipped.reason
    assert statuses(uow) == {"test_code": DatasetStatus.DEPRECATED}


async def test_load_reference_datasets_with_coverage_and_undone_move_updates() -> None:
    handler, uow = loader()
    await handler(
        LoadReferenceDatasets(
            actor=ADMIN,
            file=reference_file(entry("test_code", status=DatasetStatus.DEPRECATED)),
        )
    )

    report = await handler(
        LoadReferenceDatasets(
            actor=ADMIN,
            file=reference_file(entry("test_code", spatial_coverage=COVERAGE)),
        )
    )

    assert report.updated == ("test_code",)
    assert len(report.skipped_with_reason) == 1
    [stored] = uow.datasets.committed.values()
    assert stored.spatial_coverage == COVERAGE


async def test_load_reference_datasets_leaves_retired_coverage_alone() -> None:
    handler, uow = loader()
    retired = entry("test_code", status=DatasetStatus.RETIRED)
    await handler(LoadReferenceDatasets(actor=ADMIN, file=reference_file(retired)))

    report = await handler(
        LoadReferenceDatasets(
            actor=ADMIN,
            file=reference_file(
                entry(
                    "test_code",
                    status=DatasetStatus.RETIRED,
                    spatial_coverage=COVERAGE,
                )
            ),
        )
    )

    assert report.unchanged == ("test_code",)
    [skipped] = report.skipped_with_reason
    assert "retired" in skipped.reason
    [stored] = uow.datasets.committed.values()
    assert stored.spatial_coverage is None
