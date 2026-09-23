"""Unit tests for the ingestion queries, cursors, DTOs and the read Fake."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.ingestion import (
    DatasetTestFactory,
    ObservationTestFactory,
    RasterAssetTestFactory,
    StationRefTestFactory,
)
from tests.fakes.ingestion import InMemoryIngestionQueryService
from tests.unit.modules.ingestion.application.commands_support import (
    executed_world,
)
from tests.unit.modules.ingestion.application.support import (
    NOW,
    World,
    csv,
    hour,
    make_run,
    row,
)
from yakhnama.modules.ingestion.application.dto import (
    DatasetSummary,
    IngestionOutcome,
    LoadReport,
    ObservationRecord,
    RasterAssetSummary,
    RunDetail,
)
from yakhnama.modules.ingestion.application.queries import (
    GetDataset,
    GetRun,
    ListDatasets,
    ListRasterAssets,
    ListRuns,
    QueryObservations,
    decode_observation_position,
    observation_cursor,
)
from yakhnama.modules.ingestion.domain.value_objects import DatasetStatus
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import CursorPayload, PageRequest
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    DatePrecision,
    DateWithPrecision,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def observations_query(world: World, **updates: object) -> QueryObservations:
    return QueryObservations.model_validate(
        {
            "dataset_id": world.dataset.id,
            "variable": "air_temperature",
            "observed_from": START,
            "observed_to": END,
            **updates,
        }
    )


# ------------------------------------------------------------ query models


def test_get_dataset_needs_exactly_one_of_id_and_code() -> None:
    with pytest.raises(PydanticValidationError, match="exactly one"):
        GetDataset()

    with pytest.raises(PydanticValidationError, match="exactly one"):
        GetDataset(dataset_id=DatasetTestFactory.build().id, code="test_code")


def test_query_observations_with_unknown_variable_is_refused() -> None:
    world = World(b"")

    with pytest.raises(PydanticValidationError, match="unknown variable"):
        observations_query(world, variable="not_a_variable")


def test_query_observations_with_empty_window_is_refused() -> None:
    world = World(b"")

    with pytest.raises(PydanticValidationError, match="end after it starts"):
        observations_query(world, observed_to=START)


def test_list_raster_assets_with_reversed_window_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="end after it starts"):
        ListRasterAssets(acquired_from=END, acquired_to=START)


def test_list_raster_assets_with_open_window_is_accepted() -> None:
    assert ListRasterAssets(acquired_from=START).acquired_to is None


# ------------------------------------------------------------------ cursors


def test_observation_cursor_round_trips_to_the_records_position() -> None:
    observation = ObservationTestFactory.build(
        station=StationRefTestFactory.build(code="A|B")
    )
    record = ObservationRecord.from_entity(observation)

    payload = PageRequest(cursor=observation_cursor(record)).decode_cursor()

    assert payload is not None
    position = decode_observation_position(payload)
    assert position.as_tuple() == (
        observation.observed_at.value,
        "station:A|B",
        observation.dataset_version_id,
    )


@pytest.mark.parametrize(
    "sort_key", ["no-separator", "not-a-date|station:x", "2026-01-01T00:00|x"]
)
def test_decode_observation_position_with_bad_key_raises_validation_error(
    sort_key: str,
) -> None:
    payload = CursorPayload(sort_key=sort_key, last_id=DatasetTestFactory.build().id)

    with pytest.raises(ValidationError, match="cursor is invalid"):
        decode_observation_position(payload)


def test_decode_observation_position_with_empty_site_raises_validation_error() -> None:
    payload = CursorPayload(
        sort_key=f"{START.isoformat()}|", last_id=DatasetTestFactory.build().id
    )

    with pytest.raises(ValidationError):
        decode_observation_position(payload)


# --------------------------------------------------------------------- DTOs


def test_raster_asset_summary_to_stac_item_matches_the_entity() -> None:
    asset = RasterAssetTestFactory.build()

    item = RasterAssetSummary.from_entity(asset).to_stac_item()

    assert item == asset.to_stac_item_dict()
    assert item["type"] == "Feature"


def test_ingestion_outcome_exposes_status_and_report() -> None:
    world = World(b"")

    outcome = IngestionOutcome(run=world.run)

    assert outcome.status is world.run.status
    assert outcome.report is world.run.report


def test_load_report_with_created_codes_is_changed() -> None:
    report = LoadReport(
        dry_run=False,
        created=("test_code",),
        updated=(),
        unchanged=(),
        excluded=(),
        skipped_with_reason=(),
    )

    assert report.is_unchanged is False


def test_run_detail_carries_the_report_inline() -> None:
    world = World(b"")

    detail = RunDetail.from_entity(world.run)

    assert detail.report == world.run.report
    assert detail.error_count == 0


# -------------------------------------------------------- read-side paging


async def test_query_observations_pages_by_time_then_site() -> None:
    lines = [
        row(station=station, observed_at=hour(index))
        for index in range(3)
        for station in ("TEST-B", "TEST-A")
    ]
    world = await executed_world(csv(*lines))
    service = InMemoryIngestionQueryService(world.uow)

    first = await service.query_observations(
        observations_query(world, page=PageRequest(limit=4))
    )
    second = await service.query_observations(
        observations_query(world, page=PageRequest(limit=4, cursor=first.next_cursor))
    )

    seen = [(item.observed_at.value, item.site_ref) for item in first.items]
    seen += [(item.observed_at.value, item.site_ref) for item in second.items]
    assert seen == sorted(seen)
    assert len(set(seen)) == 6
    assert second.next_cursor is None


async def test_query_observations_filters_by_site() -> None:
    world = await executed_world(csv(row(station="TEST-A"), row(station="TEST-B")))
    service = InMemoryIngestionQueryService(world.uow)

    page = await service.query_observations(
        observations_query(world, site_ref="station:TEST-B")
    )

    assert [item.site_ref for item in page.items] == ["station:TEST-B"]


async def test_list_datasets_pages_by_code_and_filters_status() -> None:
    world = World(b"")
    service = InMemoryIngestionQueryService(world.uow)
    for code in ("test_b", "test_c"):
        dataset = DatasetTestFactory.build(code=code)
        world.uow.datasets.committed[dataset.id] = dataset

    first = await service.list_datasets(ListDatasets(page=PageRequest(limit=2)))
    rest = await service.list_datasets(
        ListDatasets(page=PageRequest(limit=2, cursor=first.next_cursor))
    )
    retired = await service.list_datasets(ListDatasets(status=DatasetStatus.RETIRED))

    codes = [item.code for item in (*first.items, *rest.items)]
    assert codes == sorted(codes)
    assert len(codes) == 3
    assert retired.items == ()


async def test_get_dataset_by_code_includes_recent_versions() -> None:
    world = World(b"")
    service = InMemoryIngestionQueryService(world.uow)

    detail = await service.get_dataset(GetDataset(code=world.dataset.code))
    missing = await service.get_dataset(GetDataset(code="test_missing"))

    assert detail is not None
    assert [version.id for version in detail.recent_versions] == [world.version.id]
    assert DatasetSummary.from_entity(world.dataset).code == detail.code
    assert missing is None


async def test_list_runs_pages_newest_first() -> None:
    world = World(b"")
    service = InMemoryIngestionQueryService(world.uow)
    later = make_run(world.version).model_copy(
        update={"created_at": NOW, "updated_at": NOW}
    )
    world.uow.ingestion_runs.committed[later.id] = later

    first = await service.list_runs(
        ListRuns(dataset_id=world.dataset.id, page=PageRequest(limit=1))
    )
    rest = await service.list_runs(
        ListRuns(
            dataset_id=world.dataset.id,
            page=PageRequest(limit=1, cursor=first.next_cursor),
        )
    )
    detail = await service.get_run(GetRun(run_id=later.id))

    assert [item.id for item in (*first.items, *rest.items)] == [later.id, world.run.id]
    assert rest.next_cursor is None
    assert detail is not None
    assert detail.id == later.id


async def test_list_raster_assets_filters_by_box_window_and_dataset() -> None:
    world = World(b"")
    service = InMemoryIngestionQueryService(world.uow)
    inside = RasterAssetTestFactory.build(
        dataset_version_id=world.version.id,
        acquired_at=DateWithPrecision(value=START, precision=DatePrecision.EXACT),
    )
    world.uow.raster_assets.committed[inside.id] = inside
    far_box = BoundingBox(
        min_longitude=10.0, min_latitude=10.0, max_longitude=11.0, max_latitude=11.0
    )

    hits = await service.list_raster_assets(
        ListRasterAssets(
            dataset_id=world.dataset.id, acquired_from=START, acquired_to=END
        )
    )
    misses = await service.list_raster_assets(ListRasterAssets(bbox=far_box))

    assert [item.id for item in hits.items] == [inside.id]
    assert misses.items == ()
