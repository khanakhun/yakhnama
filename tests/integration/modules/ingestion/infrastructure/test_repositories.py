"""The SQLAlchemy ingestion repositories and unit of work on real PostGIS.

Licences, coverages, reports, sites, bands and assets must reload exactly; the
unique rules must surface as ``ConflictError`` without spoiling the transaction;
``save`` must take exactly one version step; ``append_many`` must be idempotent on
the natural key and count only rows it inserted.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from geojson_pydantic import MultiPolygon, Polygon
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.base import FACTORY_IDS
from tests.factories.ingestion import (
    DatasetTestFactory,
    DatasetVersionTestFactory,
    IngestionRunTestFactory,
    ObservationTestFactory,
    RasterAssetTestFactory,
    StacAssetTestFactory,
    checksum,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from tests.integration.modules.ingestion.infrastructure.conftest import (
    IngestionFactory,
    store,
)
from yakhnama.modules.ingestion.application.queries import ListRasterAssets
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.errors import (
    DatasetNotFoundError,
    IngestionRunNotFoundError,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    COG_MEDIA_TYPE,
    DatasetLicence,
    GridCellRef,
    IngestionIssue,
    IngestionReport,
    IssueSeverity,
    QualityFlag,
    RasterFootprint,
    RunCounts,
    RunStatus,
    SpatialCoverage,
    StacAsset,
    StacBand,
    StationRef,
    TemporalCoverage,
)
from yakhnama.modules.ingestion.infrastructure.mappers import row_to_observation
from yakhnama.modules.ingestion.infrastructure.orm import ObservationRow
from yakhnama.modules.ingestion.infrastructure.repositories import (
    OBSERVATION_INSERT_BATCH,
)
from yakhnama.modules.ingestion.infrastructure.uow import (
    SqlAlchemyIngestionUnitOfWork,
)
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.shared_kernel.errors import ConflictError, InvariantViolationError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

pytestmark = pytest.mark.integration

CREATED: Final = datetime(2026, 8, 1, 12, 0, 0, 123456, tzinfo=UTC)
# Seventeen significant digits: WKB must keep every bit of a box edge.
FULL_BOX: Final = BoundingBox(
    min_longitude=72.51234567891234,
    min_latitude=34.51234567891234,
    max_longitude=77.81234567891234,
    max_latitude=37.11234567891234,
)
TWO_SQUARES: Final = RasterFootprint(
    geojson=MultiPolygon.model_validate(
        {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [
                        (74.0, 36.0),
                        (74.5, 36.0),
                        (74.5, 36.5),
                        (74.0, 36.5),
                        (74.0, 36.0),
                    ]
                ],
                [
                    [
                        (76.0, 35.0),
                        (76.5, 35.0),
                        (76.5, 35.5),
                        (76.0, 35.5),
                        (76.0, 35.0),
                    ]
                ],
            ],
        }
    )
)


def _square(west: float, south: float, size: float = 1.0) -> RasterFootprint:
    east, north = west + size, south + size
    return RasterFootprint(
        geojson=Polygon.model_validate(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        (west, south),
                        (east, south),
                        (east, north),
                        (west, north),
                        (west, south),
                    ]
                ],
            }
        )
    )


def _full_dataset() -> Dataset:
    return DatasetTestFactory.build(
        created_at=CREATED,
        licence=DatasetLicence(
            custom_text="Free for research; cite the publisher.",
            url="https://licences.example.test/custom?v=2",
            attribution="Test attribution: data from a test publisher.",
        ),
        spatial_coverage=SpatialCoverage(bbox=FULL_BOX),
        temporal_coverage=TemporalCoverage(
            start=DateWithPrecision(
                value=datetime(1990, 7, 15, 6, tzinfo=UTC),
                precision=DatePrecision.DAY,
            ),
            end=DateWithPrecision(
                value=datetime(2024, 1, 1, tzinfo=UTC), precision=DatePrecision.YEAR
            ),
        ),
        description="Line one.\nLine two.",
        homepage_url="https://data.example.test/datasets/one",
    )


async def _dataset_with_version(
    factory: IngestionFactory,
) -> tuple[Dataset, DatasetVersion]:
    dataset = DatasetTestFactory.build()
    version = DatasetVersionTestFactory.build(dataset_id=dataset.id)
    await store(factory, datasets=[dataset], versions=[version])
    return dataset, version


# --------------------------------------------------------------------------- #
# Datasets                                                                    #
# --------------------------------------------------------------------------- #


async def test_dataset_repository_add_then_get_round_trips_every_field(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset = _full_dataset()

    await store(ingestion_uow_factory, datasets=[dataset])
    async with ingestion_uow_factory() as uow:
        by_id = await uow.datasets.get(dataset.id)
        by_code = await uow.datasets.get_by_code(dataset.code)

    assert by_id == dataset
    assert by_code == dataset


async def test_dataset_repository_round_trips_minimal_and_degenerate_coverage(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    minimal = DatasetTestFactory.build()
    # BoundingBox allows a point; the stored rectangle must keep its bounds.
    point_box = DatasetTestFactory.build(
        spatial_coverage=SpatialCoverage(
            bbox=BoundingBox(
                min_longitude=74.5,
                min_latitude=36.25,
                max_longitude=74.5,
                max_latitude=36.25,
            )
        ),
        temporal_coverage=TemporalCoverage(
            start=DateWithPrecision(
                value=datetime(2020, 6, 1, tzinfo=UTC), precision=DatePrecision.SEASON
            )
        ),
    )

    await store(ingestion_uow_factory, datasets=[minimal, point_box])
    async with ingestion_uow_factory() as uow:
        loaded = [await uow.datasets.get(item.id) for item in (minimal, point_box)]

    assert loaded == [minimal, point_box]


async def test_dataset_repository_get_unknown_returns_none(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    async with ingestion_uow_factory() as uow:
        by_id = await uow.datasets.get(FACTORY_IDS.new_id())
        by_code = await uow.datasets.get_by_code("no_such_dataset")

    assert by_id is None
    assert by_code is None


async def test_dataset_repository_add_duplicate_code_raises_conflict_and_keeps_uow(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    first = DatasetTestFactory.build()
    await store(ingestion_uow_factory, datasets=[first])
    duplicate = DatasetTestFactory.build(code=first.code)
    other = DatasetTestFactory.build()

    async with ingestion_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.datasets.add(duplicate)
        await uow.datasets.add(other)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        stored_other = await uow.datasets.get(other.id)
        stored_duplicate = await uow.datasets.get(duplicate.id)

    assert stored_other == other
    assert stored_duplicate is None


async def test_dataset_repository_add_duplicate_id_raises_conflict(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    first = DatasetTestFactory.build()
    await store(ingestion_uow_factory, datasets=[first])

    async with ingestion_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.datasets.add(DatasetTestFactory.build(id=first.id))


async def test_dataset_repository_save_one_step_persists_the_new_state(
    ingestion_uow_factory: IngestionFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    dataset = DatasetTestFactory.build()
    await store(ingestion_uow_factory, datasets=[dataset])
    deprecated = dataset.deprecate(clock=clock, ids=ids).state
    covered = deprecated.update_coverage(
        SpatialCoverage(bbox=FULL_BOX), None, clock=clock, ids=ids
    ).state

    async with ingestion_uow_factory() as uow:
        await uow.datasets.save(deprecated)
        await uow.datasets.save(covered)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        loaded = await uow.datasets.get(dataset.id)

    assert loaded == covered
    assert covered.version == dataset.version + 2


async def test_dataset_repository_save_skipping_a_version_raises_conflict(
    ingestion_uow_factory: IngestionFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    dataset = DatasetTestFactory.build()
    await store(ingestion_uow_factory, datasets=[dataset])
    deprecated = dataset.deprecate(clock=clock, ids=ids).state
    retired = deprecated.retire(clock=clock, ids=ids).state

    async with ingestion_uow_factory() as uow:
        with pytest.raises(ConflictError) as raised:
            await uow.datasets.save(retired)

    assert raised.value.details["stored_version"] == 1


async def test_dataset_repository_save_unknown_raises_not_found(
    ingestion_uow_factory: IngestionFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    deprecated = DatasetTestFactory.build().deprecate(clock=clock, ids=ids).state

    async with ingestion_uow_factory() as uow:
        with pytest.raises(DatasetNotFoundError):
            await uow.datasets.save(deprecated)


async def test_dataset_repository_uncommitted_add_is_rolled_back(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset = DatasetTestFactory.build()

    async with ingestion_uow_factory() as uow:
        await uow.datasets.add(dataset)
        staged = await uow.datasets.get(dataset.id)
    async with ingestion_uow_factory() as uow:
        after = await uow.datasets.get(dataset.id)

    assert staged == dataset
    assert after is None


# --------------------------------------------------------------------------- #
# Versions                                                                    #
# --------------------------------------------------------------------------- #


async def test_version_repository_round_trips_and_finds_by_label(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset = DatasetTestFactory.build()
    version = DatasetVersionTestFactory.build(
        dataset_id=dataset.id,
        label="2024-08-01",
        created_at=CREATED,
        retrieved_at=DateWithPrecision(
            value=datetime(2026, 7, 31, 18, 30, tzinfo=UTC), precision=DatePrecision.DAY
        ),
        input_checksum=checksum().upper(),
        notes="Retrieved by hand.\nSecond line.",
    )

    await store(ingestion_uow_factory, datasets=[dataset], versions=[version])
    async with ingestion_uow_factory() as uow:
        by_id = await uow.dataset_versions.get(version.id)
        by_label = await uow.dataset_versions.get_by_label(dataset.id, "2024-08-01")
        other_label = await uow.dataset_versions.get_by_label(dataset.id, "v9")

    assert by_id == version
    assert by_label == version
    assert other_label is None


async def test_version_repository_add_same_label_raises_conflict(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset, version = await _dataset_with_version(ingestion_uow_factory)
    other_dataset = DatasetTestFactory.build()
    same_label_elsewhere = DatasetVersionTestFactory.build(
        dataset_id=other_dataset.id, label=version.label
    )

    async with ingestion_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.dataset_versions.add(
                DatasetVersionTestFactory.build(
                    dataset_id=dataset.id, label=version.label
                )
            )
        await uow.datasets.add(other_dataset)
        await uow.dataset_versions.add(same_label_elsewhere)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        stored = await uow.dataset_versions.get(same_label_elsewhere.id)

    assert stored == same_label_elsewhere


async def test_version_repository_list_for_dataset_is_newest_first_with_limit(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset = DatasetTestFactory.build()
    other = DatasetTestFactory.build()
    # Two share created_at, so the id breaks the tie (descending).
    versions = [
        DatasetVersionTestFactory.build(
            dataset_id=dataset.id, created_at=CREATED + timedelta(days=offset)
        )
        for offset in (0, 2, 2, 1)
    ]
    foreign = DatasetVersionTestFactory.build(
        dataset_id=other.id, created_at=CREATED + timedelta(days=9)
    )
    await store(
        ingestion_uow_factory, datasets=[dataset, other], versions=[*versions, foreign]
    )

    async with ingestion_uow_factory() as uow:
        newest = await uow.dataset_versions.list_for_dataset(dataset.id, limit=3)

    expected = sorted(
        versions, key=lambda item: (item.created_at, item.id), reverse=True
    )[:3]
    assert newest == tuple(expected)


# --------------------------------------------------------------------------- #
# Runs                                                                        #
# --------------------------------------------------------------------------- #


def _problem_report() -> IngestionReport:
    return IngestionReport(
        issues=(
            IngestionIssue(
                stage="validate", reference="row 12", message="value out of range"
            ),
            IngestionIssue(
                stage="normalise",
                message="station has no elevation",
                severity=IssueSeverity.WARNING,
            ),
        ),
        counts=RunCounts(
            fetched=10, parsed=9, valid=8, invalid=1, deduplicated=1, persisted=7
        ),
    )


async def test_run_repository_saves_each_transition_and_round_trips_the_report(
    ingestion_uow_factory: IngestionFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    run = IngestionRunTestFactory.build(
        dataset_version_id=version.id, triggered_by=FACTORY_IDS.new_id()
    )
    await store(ingestion_uow_factory, runs=[run])
    running = run.start(clock=clock, ids=ids).state
    finished = running.partially_succeed(
        _problem_report(), input_checksum=checksum(), clock=clock, ids=ids
    ).state

    async with ingestion_uow_factory() as uow:
        await uow.ingestion_runs.save(running)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        loaded_running = await uow.ingestion_runs.get(run.id)
        await uow.ingestion_runs.save(finished)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        loaded_finished = await uow.ingestion_runs.get(run.id)

    assert loaded_running == running
    assert loaded_finished == finished
    assert loaded_finished is not None
    assert loaded_finished.status is RunStatus.PARTIALLY_SUCCEEDED


async def test_run_repository_save_stale_raises_conflict_and_unknown_not_found(
    ingestion_uow_factory: IngestionFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    run = IngestionRunTestFactory.build(dataset_version_id=version.id)
    await store(ingestion_uow_factory, runs=[run])
    running = run.start(clock=clock, ids=ids).state
    unknown = (
        IngestionRunTestFactory.build(dataset_version_id=version.id)
        .start(clock=clock, ids=ids)
        .state
    )

    async with ingestion_uow_factory() as uow:
        await uow.ingestion_runs.save(running)
        with pytest.raises(ConflictError):
            await uow.ingestion_runs.save(running)
        with pytest.raises(IngestionRunNotFoundError):
            await uow.ingestion_runs.save(unknown)


async def test_run_repository_add_duplicate_id_raises_conflict(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    run = IngestionRunTestFactory.build(dataset_version_id=version.id)
    await store(ingestion_uow_factory, runs=[run])

    async with ingestion_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.ingestion_runs.add(run)


async def test_run_repository_list_for_dataset_spans_versions_newest_first(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset, first_version = await _dataset_with_version(ingestion_uow_factory)
    second_version = DatasetVersionTestFactory.build(dataset_id=dataset.id)
    _, foreign_version = await _dataset_with_version(ingestion_uow_factory)
    runs = [
        IngestionRunTestFactory.build(
            dataset_version_id=version.id, created_at=CREATED + timedelta(hours=hours)
        )
        for version, hours in (
            (first_version, 0),
            (second_version, 3),
            (first_version, 3),
            (second_version, 1),
        )
    ]
    foreign = IngestionRunTestFactory.build(
        dataset_version_id=foreign_version.id, created_at=CREATED + timedelta(days=1)
    )
    await store(ingestion_uow_factory, versions=[second_version], runs=[*runs, foreign])

    async with ingestion_uow_factory() as uow:
        newest = await uow.ingestion_runs.list_for_dataset(dataset.id, limit=10)

    expected = sorted(runs, key=lambda run: (run.created_at, run.id), reverse=True)
    assert newest == tuple(expected)


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #


def _observations(
    version: DatasetVersion, count: int, *, start: datetime = CREATED
) -> list[Observation]:
    return [
        ObservationTestFactory.build(
            dataset_version_id=version.id,
            station=StationRef(code="TEST-0001"),
            observed_at=DateWithPrecision(
                value=start + timedelta(hours=index), precision=DatePrecision.HOUR
            ),
        )
        for index in range(count)
    ]


async def _all_observations(factory: IngestionFactory, version: DatasetVersion) -> int:
    async with factory() as uow:
        return await uow.observations.count_for_version(version.id)


async def test_observation_repository_append_many_counts_and_is_idempotent(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    first = _observations(version, 5)
    overlapping = [
        *first[3:],
        *_observations(version, 4, start=CREATED + timedelta(days=1)),
    ]

    async with ingestion_uow_factory() as uow:
        stored_first = await uow.observations.append_many(first)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        stored_again = await uow.observations.append_many(first)
        stored_overlap = await uow.observations.append_many(overlapping)
        await uow.commit()

    assert (stored_first, stored_again, stored_overlap) == (5, 0, 4)
    assert await _all_observations(ingestion_uow_factory, version) == 9


async def test_observation_repository_append_many_keeps_first_of_repeated_keys(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    original = _observations(version, 1)[0]
    # Same natural key (precision is not part of it), different value.
    repeat = original.model_copy(
        update={
            "value": Measurement(value=300.0, unit=SiUnit.KELVIN),
            "observed_at": original.observed_at.model_copy(
                update={"precision": DatePrecision.EXACT}
            ),
        }
    )

    async with ingestion_uow_factory() as uow:
        stored = await uow.observations.append_many([original, repeat])
        await uow.commit()

    assert stored == 1
    assert await _all_observations(ingestion_uow_factory, version) == 1


async def test_observation_repository_append_many_spans_several_batches(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    observations = _observations(version, 2 * OBSERVATION_INSERT_BATCH + 7)

    async with ingestion_uow_factory() as uow:
        stored = await uow.observations.append_many(observations)
        staged_count = await uow.observations.count_for_version(version.id)
        await uow.commit()

    assert stored == len(observations)
    assert staged_count == len(observations)


async def test_observation_repository_append_many_empty_stores_nothing(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    async with ingestion_uow_factory() as uow:
        stored = await uow.observations.append_many([])

    assert stored == 0


async def test_observation_repository_rollback_discards_appended_rows(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)

    async with ingestion_uow_factory() as uow:
        await uow.observations.append_many(_observations(version, 3))

    assert await _all_observations(ingestion_uow_factory, version) == 0


# --------------------------------------------------------------------------- #
# Raster assets                                                               #
# --------------------------------------------------------------------------- #


def _full_raster(version: DatasetVersion) -> RasterAsset:
    return RasterAssetTestFactory.build(
        dataset_version_id=version.id,
        stac_id="S2A:MSIL2A.20240715-T43SDA",
        footprint=TWO_SQUARES,
        acquired_at=DateWithPrecision(
            value=datetime(2024, 7, 15, 5, 36, 11, 24000, tzinfo=UTC),
            precision=DatePrecision.EXACT,
        ),
        platform="test-platform-2a",
        cloud_cover=12.5,
        bands=(
            StacBand(name="B04", common_name="red", description="Red band."),
            StacBand(name="B08", common_name="nir"),
            StacBand(name="SCL"),
        ),
        assets=(
            StacAsset(
                key="visual",
                href="rasters/test/visual.tif",
                media_type=COG_MEDIA_TYPE,
                roles=("data", "visual"),
            ),
            StacAsset(
                key="thumbnail",
                href="https://tiles.example.test/thumb.png",
                media_type="image/png",
                roles=("thumbnail",),
            ),
            StacAssetTestFactory.build(key="B04", roles=()),
        ),
    )


async def test_raster_catalog_add_then_get_round_trips_footprint_bands_and_assets(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    asset = _full_raster(version)

    await store(ingestion_uow_factory, assets=[asset])
    async with ingestion_uow_factory() as uow:
        loaded = await uow.raster_assets.get(asset.id)
        missing = await uow.raster_assets.get(FACTORY_IDS.new_id())

    assert loaded == asset
    assert loaded is not None
    assert loaded.to_stac_item_dict() == asset.to_stac_item_dict()
    assert missing is None


async def test_raster_catalog_add_same_stac_id_in_version_raises_conflict(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset, version = await _dataset_with_version(ingestion_uow_factory)
    other_version = DatasetVersionTestFactory.build(dataset_id=dataset.id)
    asset = RasterAssetTestFactory.build(dataset_version_id=version.id)
    await store(ingestion_uow_factory, versions=[other_version], assets=[asset])
    same_elsewhere = RasterAssetTestFactory.build(
        dataset_version_id=other_version.id, stac_id=asset.stac_id
    )

    async with ingestion_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.raster_assets.add(
                RasterAssetTestFactory.build(
                    dataset_version_id=version.id, stac_id=asset.stac_id
                )
            )
        await uow.raster_assets.add(same_elsewhere)
        await uow.commit()
    async with ingestion_uow_factory() as uow:
        stored = await uow.raster_assets.get(same_elsewhere.id)

    assert stored == same_elsewhere


async def test_raster_catalog_search_filters_by_bbox_window_and_dataset(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    dataset, version = await _dataset_with_version(ingestion_uow_factory)
    _, foreign_version = await _dataset_with_version(ingestion_uow_factory)
    july = DateWithPrecision(
        value=datetime(2024, 7, 20, tzinfo=UTC), precision=DatePrecision.MONTH
    )
    inside = RasterAssetTestFactory.build(
        dataset_version_id=version.id, footprint=_square(74.0, 36.0), acquired_at=july
    )
    # Shares only the box's eastern edge: ST_Intersects includes edges.
    touching = RasterAssetTestFactory.build(
        dataset_version_id=version.id, footprint=_square(75.0, 36.0), acquired_at=july
    )
    outside = RasterAssetTestFactory.build(
        dataset_version_id=version.id, footprint=_square(80.0, 30.0), acquired_at=july
    )
    too_late = RasterAssetTestFactory.build(
        dataset_version_id=version.id,
        footprint=_square(74.0, 36.0),
        acquired_at=DateWithPrecision(
            value=datetime(2024, 8, 1, tzinfo=UTC), precision=DatePrecision.EXACT
        ),
    )
    foreign = RasterAssetTestFactory.build(
        dataset_version_id=foreign_version.id,
        footprint=_square(74.0, 36.0),
        acquired_at=july,
    )
    await store(
        ingestion_uow_factory, assets=[inside, touching, outside, too_late, foreign]
    )
    query = ListRasterAssets(
        bbox=BoundingBox(
            min_longitude=74.2, min_latitude=36.2, max_longitude=75.0, max_latitude=36.8
        ),
        # The month-precision acquisitions start on 1 July, inside the window.
        acquired_from=datetime(2024, 7, 1, tzinfo=UTC),
        acquired_to=datetime(2024, 8, 1, tzinfo=UTC),
        dataset_id=dataset.id,
    )

    async with ingestion_uow_factory() as uow:
        page = await uow.raster_assets.search(query)

    assert {item.id for item in page.items} == {inside.id, touching.id}
    assert page.next_cursor is None


async def test_raster_catalog_search_pages_newest_first_across_ties(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    acquisitions = [
        DateWithPrecision(value=CREATED, precision=DatePrecision.EXACT),
        # Truncates to CREATED's day start: the order uses the period's start.
        DateWithPrecision(
            value=CREATED + timedelta(hours=5), precision=DatePrecision.DAY
        ),
        DateWithPrecision(
            value=CREATED.replace(hour=0, minute=0, second=0, microsecond=0),
            precision=DatePrecision.EXACT,
        ),
        DateWithPrecision(
            value=CREATED + timedelta(days=1), precision=DatePrecision.EXACT
        ),
    ]
    assets = [
        RasterAssetTestFactory.build(dataset_version_id=version.id, acquired_at=moment)
        for moment in acquisitions
    ]
    await store(ingestion_uow_factory, assets=assets)
    expected = sorted(
        assets,
        key=lambda item: (item.acquired_at.truncate().value, item.id),
        reverse=True,
    )

    seen: list[RasterAsset] = []
    cursor: str | None = None
    async with ingestion_uow_factory() as uow:
        while True:
            page = await uow.raster_assets.search(
                ListRasterAssets(page=PageRequest(limit=1, cursor=cursor))
            )
            seen.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break

    assert seen == expected


# --------------------------------------------------------------------------- #
# Unit of work                                                                #
# --------------------------------------------------------------------------- #


def test_ingestion_uow_repositories_outside_async_with_raise(
    outbox_writer: OutboxWriter,
) -> None:
    def _no_session() -> AsyncSession:
        message = "a session must not be opened"
        raise AssertionError(message)

    uow = SqlAlchemyIngestionUnitOfWork(_no_session, outbox_writer)

    names = (
        "datasets",
        "dataset_versions",
        "ingestion_runs",
        "observations",
        "raster_assets",
    )
    for name in names:
        with pytest.raises(InvariantViolationError):
            getattr(uow, name)


async def test_observation_round_trip_keeps_grid_cells_and_missing_values(
    ingestion_uow_factory: IngestionFactory,
) -> None:
    _, version = await _dataset_with_version(ingestion_uow_factory)
    grid = ObservationTestFactory.build(
        dataset_version_id=version.id,
        station=None,
        grid_cell=GridCellRef(
            cell_id="r12c34",
            centroid=Coordinates(longitude=74.61234567891234, latitude=36.3125),
            resolution=Measurement(value=5000.0, unit=SiUnit.METRE),
        ),
        variable="precipitation_depth",
        value=Measurement(value=0.0125, unit=SiUnit.METRE),
    )
    missing = ObservationTestFactory.build(
        dataset_version_id=version.id,
        station=StationRef(
            code="TEST-0002",
            name="Test station two",
            location=Coordinates(longitude=74.3, latitude=35.9),
            elevation=Measurement(value=1500.5, unit=SiUnit.METRE),
        ),
        value=None,
        quality=QualityFlag.MISSING,
    )

    await store(ingestion_uow_factory, observations=[grid, missing])
    # The port has no read of single observations; reading the rows directly
    # checks the mapper's round trip on what the repository wrote.
    async with ingestion_uow_factory() as uow:
        rows = (await uow.session.execute(select(ObservationRow))).scalars().all()
        loaded = {row_to_observation(row).key: row_to_observation(row) for row in rows}

    assert loaded == {grid.key: grid, missing.key: missing}
