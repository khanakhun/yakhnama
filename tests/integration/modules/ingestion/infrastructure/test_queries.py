"""The SQL ingestion query service on real PostGIS, and its equality with the Fake.

Every order and cursor is the one ``application/queries.py`` documents. The Fake
(``InMemoryIngestionQueryService``) implements the same contract in Python, so on
one random arrangement both must return identical pages, cursors included; codes
and site references mix punctuation, case and non-ASCII letters, where a
locale-aware database collation would disagree with Python's order.
"""

import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from geojson_pydantic import Polygon

from tests.factories.base import FACTORY_IDS
from tests.factories.ingestion import (
    DatasetTestFactory,
    DatasetVersionTestFactory,
    IngestionRunTestFactory,
    ObservationTestFactory,
    RasterAssetTestFactory,
)
from tests.fakes.ingestion import (
    InMemoryIngestionQueryService,
    InMemoryIngestionUnitOfWork,
)
from tests.integration.modules.ingestion.infrastructure.conftest import (
    IngestionFactory,
    store,
)
from yakhnama.modules.ingestion.application.dto import (
    RECENT_VERSIONS_MAX,
    DatasetDetail,
    DatasetSummary,
    ObservationRecord,
    RasterAssetSummary,
    RunDetail,
    RunSummary,
)
from yakhnama.modules.ingestion.application.queries import (
    GetDataset,
    GetRun,
    ListDatasets,
    ListRasterAssets,
    ListRuns,
    QueryObservations,
)
from yakhnama.modules.ingestion.domain.entities import (
    Dataset,
    DatasetVersion,
    IngestionRun,
    Observation,
    RasterAsset,
)
from yakhnama.modules.ingestion.domain.value_objects import (
    DatasetStatus,
    QualityFlag,
    RasterFootprint,
    StationRef,
)
from yakhnama.modules.ingestion.infrastructure.queries import (
    SqlAlchemyIngestionQueryService,
)
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    DatePrecision,
    DateWithPrecision,
    Measurement,
    SiUnit,
)

pytestmark = pytest.mark.integration

START: Final = datetime(2024, 1, 1, tzinfo=UTC)
END: Final = datetime(2024, 1, 3, tzinfo=UTC)
# Punctuation, case and a non-ASCII letter: en_US collation would reorder these.
SITE_CODES: Final = ("A-1", "A.1", "a_1", "A1", "B", "b", "É2")
DATASET_CODES: Final = ("a_b", "a.b", "a-b", "ab", "a_c", "b.a")
RANDOM_SEED: Final = 20260923


async def _collect[ItemT](
    fetch: Callable[[PageRequest], Awaitable[Page[ItemT]]], limit: int
) -> tuple[list[Page[ItemT]], list[ItemT]]:
    pages: list[Page[ItemT]] = []
    cursor: str | None = None
    while True:
        page = await fetch(PageRequest(limit=limit, cursor=cursor))
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            break
    return pages, [item for page in pages for item in page.items]


def _observation(
    version: DatasetVersion,
    site: str,
    moment: datetime,
    *,
    variable: str = "air_temperature",
) -> Observation:
    unit = SiUnit.KELVIN if variable == "air_temperature" else SiUnit.METRE
    return ObservationTestFactory.build(
        dataset_version_id=version.id,
        station=StationRef(code=site),
        variable=variable,
        value=Measurement(value=270.0, unit=unit),
        observed_at=DateWithPrecision(value=moment, precision=DatePrecision.HOUR),
    )


# --------------------------------------------------------------------------- #
# Catalog and runs                                                            #
# --------------------------------------------------------------------------- #


async def test_list_datasets_pages_by_code_in_code_point_order_with_status_filter(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    datasets = [DatasetTestFactory.build(code=code) for code in DATASET_CODES]
    deprecated = DatasetTestFactory.build(
        code="a_deprecated", status=DatasetStatus.DEPRECATED
    )
    await store(ingestion_uow_factory, datasets=[*datasets, deprecated])

    _, active = await _collect(
        lambda page: ingestion_queries.list_datasets(
            ListDatasets(status=DatasetStatus.ACTIVE, page=page)
        ),
        limit=2,
    )
    _, everything = await _collect(
        lambda page: ingestion_queries.list_datasets(ListDatasets(page=page)), limit=4
    )

    assert [item.code for item in active] == sorted(DATASET_CODES)
    assert [item.code for item in everything] == sorted(
        [*DATASET_CODES, "a_deprecated"]
    )
    assert everything[0] == DatasetSummary.from_entity(
        next(item for item in [*datasets, deprecated] if item.code == "a-b")
    )


async def test_get_dataset_by_id_or_code_returns_detail_with_newest_versions(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    dataset = DatasetTestFactory.build()
    versions = [
        DatasetVersionTestFactory.build(
            dataset_id=dataset.id, created_at=START + timedelta(hours=index)
        )
        for index in range(RECENT_VERSIONS_MAX + 2)
    ]
    await store(ingestion_uow_factory, datasets=[dataset], versions=versions)
    newest = tuple(
        sorted(versions, key=lambda item: (item.created_at, item.id), reverse=True)[
            :RECENT_VERSIONS_MAX
        ]
    )

    by_id = await ingestion_queries.get_dataset(GetDataset(dataset_id=dataset.id))
    by_code = await ingestion_queries.get_dataset(GetDataset(code=dataset.code))
    missing = await ingestion_queries.get_dataset(GetDataset(code="no_such_dataset"))

    assert by_id == DatasetDetail.from_entities(dataset, newest)
    assert by_code == by_id
    assert missing is None


async def test_list_runs_pages_newest_first_and_get_run_returns_report(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    dataset = DatasetTestFactory.build()
    versions = [DatasetVersionTestFactory.build(dataset_id=dataset.id) for _ in "ab"]
    runs = [
        IngestionRunTestFactory.build(
            dataset_version_id=versions[index % 2].id,
            created_at=START + timedelta(minutes=index // 2),
        )
        for index in range(7)
    ]
    await store(ingestion_uow_factory, datasets=[dataset], versions=versions, runs=runs)
    expected = sorted(runs, key=lambda run: (run.created_at, run.id), reverse=True)

    _, listed = await _collect(
        lambda page: ingestion_queries.list_runs(
            ListRuns(dataset_id=dataset.id, page=page)
        ),
        limit=3,
    )
    detail = await ingestion_queries.get_run(GetRun(run_id=runs[0].id))
    missing = await ingestion_queries.get_run(GetRun(run_id=FACTORY_IDS.new_id()))
    unknown_dataset = await ingestion_queries.list_runs(
        ListRuns(dataset_id=FACTORY_IDS.new_id())
    )

    assert listed == [RunSummary.from_entity(run) for run in expected]
    assert detail == RunDetail.from_entity(runs[0])
    assert missing is None
    assert unknown_dataset.items == ()


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #


async def test_query_observations_window_is_half_open_and_filters_apply(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    dataset = DatasetTestFactory.build()
    first, second = (
        DatasetVersionTestFactory.build(dataset_id=dataset.id) for _ in "ab"
    )
    at_start = _observation(first, "S1", START)
    at_end = _observation(first, "S1", END)
    other_site = _observation(first, "S2", START + timedelta(hours=1))
    other_version = _observation(second, "S1", START + timedelta(hours=2))
    other_variable = _observation(
        first, "S1", START + timedelta(hours=3), variable="snow_depth"
    )
    await store(
        ingestion_uow_factory,
        datasets=[dataset],
        versions=[first, second],
        observations=[at_start, at_end, other_site, other_version, other_variable],
    )
    base = QueryObservations(
        dataset_id=dataset.id,
        variable="air_temperature",
        observed_from=START,
        observed_to=END,
    )

    everything = await ingestion_queries.query_observations(base)
    one_site = await ingestion_queries.query_observations(
        base.model_copy(update={"site_ref": "station:S1"})
    )
    one_version = await ingestion_queries.query_observations(
        base.model_copy(update={"dataset_version_id": second.id})
    )

    assert everything.items == tuple(
        ObservationRecord.from_entity(item)
        for item in (at_start, other_site, other_version)
    )
    assert [item.observed_at.value for item in one_site.items] == [
        START,
        START + timedelta(hours=2),
    ]
    assert one_version.items == (ObservationRecord.from_entity(other_version),)


async def test_query_observations_pages_across_ties_without_gaps_or_repeats(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    dataset = DatasetTestFactory.build()
    versions = [DatasetVersionTestFactory.build(dataset_id=dataset.id) for _ in "abc"]
    # Every site at the same two instants in every version: ties on the instant,
    # then on the site, broken by the version id.
    observations = [
        _observation(version, site, START + timedelta(hours=hour))
        for version in versions
        for site in SITE_CODES
        for hour in (0, 1)
    ]
    await store(
        ingestion_uow_factory,
        datasets=[dataset],
        versions=versions,
        observations=observations,
    )
    expected = sorted(
        (ObservationRecord.from_entity(item) for item in observations),
        key=lambda record: (
            record.observed_at.value,
            record.site_ref,
            record.dataset_version_id,
        ),
    )
    query = QueryObservations(
        dataset_id=dataset.id,
        variable="air_temperature",
        observed_from=START,
        observed_to=END,
    )

    pages, records = await _collect(
        lambda page: ingestion_queries.query_observations(
            query.model_copy(update={"page": page})
        ),
        limit=4,
    )

    assert records == expected
    assert len(pages) == -(-len(expected) // 4)


async def test_query_observations_with_invalid_cursor_raises_validation_error(
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    cursor = encode_cursor(
        CursorPayload(sort_key="not-an-instant", last_id=FACTORY_IDS.new_id())
    )
    query = QueryObservations(
        dataset_id=FACTORY_IDS.new_id(),
        variable="air_temperature",
        observed_from=START,
        observed_to=END,
        page=PageRequest(cursor=cursor),
    )

    with pytest.raises(ValidationError):
        await ingestion_queries.query_observations(query)


async def test_list_runs_with_naive_instant_cursor_raises_validation_error(
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    for sort_key in ("2024-01-01T00:00:00", "yesterday"):
        cursor = encode_cursor(
            CursorPayload(sort_key=sort_key, last_id=FACTORY_IDS.new_id())
        )

        with pytest.raises(ValidationError):
            await ingestion_queries.list_runs(
                ListRuns(
                    dataset_id=FACTORY_IDS.new_id(), page=PageRequest(cursor=cursor)
                )
            )


# --------------------------------------------------------------------------- #
# Rasters                                                                     #
# --------------------------------------------------------------------------- #


async def test_list_raster_assets_returns_summaries_filtered_by_box(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    dataset = DatasetTestFactory.build()
    version = DatasetVersionTestFactory.build(dataset_id=dataset.id)
    near = RasterAssetTestFactory.build(dataset_version_id=version.id)
    far = RasterAssetTestFactory.build(
        dataset_version_id=version.id, footprint=_square(10.0, 10.0)
    )
    await store(
        ingestion_uow_factory,
        datasets=[dataset],
        versions=[version],
        assets=[near, far],
    )

    page = await ingestion_queries.list_raster_assets(
        ListRasterAssets(
            bbox=BoundingBox(
                min_longitude=74.5,
                min_latitude=36.5,
                max_longitude=74.6,
                max_latitude=36.6,
            )
        )
    )

    assert page.items == (RasterAssetSummary.from_entity(near),)


def _square(west: float, south: float) -> RasterFootprint:
    return RasterFootprint(
        geojson=Polygon.model_validate(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        (west, south),
                        (west + 1, south),
                        (west + 1, south + 1),
                        (west, south + 1),
                        (west, south),
                    ]
                ],
            }
        )
    )


# --------------------------------------------------------------------------- #
# Equality with the in-memory Fake                                            #
# --------------------------------------------------------------------------- #


class _Arrangement:
    """One random catalog, stored in PostGIS and in the Fake alike."""

    def __init__(self, generator: random.Random) -> None:
        self.datasets: list[Dataset] = [
            DatasetTestFactory.build(
                code=code,
                status=generator.choice(tuple(DatasetStatus)),
            )
            for code in DATASET_CODES
        ]
        self.versions: list[DatasetVersion] = [
            DatasetVersionTestFactory.build(
                dataset_id=dataset.id,
                created_at=START + timedelta(hours=generator.randint(0, 3)),
            )
            for dataset in self.datasets[:2]
            for _ in range(3)
        ]
        self.runs: list[IngestionRun] = [
            IngestionRunTestFactory.build(
                dataset_version_id=generator.choice(self.versions).id,
                created_at=START + timedelta(minutes=generator.randint(0, 5)),
            )
            for _ in range(15)
        ]
        self.observations: list[Observation] = [
            self._random_observation(generator) for _ in range(400)
        ]
        self.assets: list[RasterAsset] = [
            RasterAssetTestFactory.build(
                dataset_version_id=generator.choice(self.versions).id,
                footprint=_square(
                    generator.choice((73.0, 74.0, 75.0)),
                    generator.choice((35.0, 36.0)),
                ),
                acquired_at=DateWithPrecision(
                    value=START + timedelta(days=generator.randint(0, 40)),
                    precision=generator.choice(tuple(DatePrecision)),
                ),
            )
            for _ in range(25)
        ]

    def _random_observation(self, generator: random.Random) -> Observation:
        is_missing = generator.random() < 0.1
        return ObservationTestFactory.build(
            dataset_version_id=generator.choice(self.versions).id,
            station=StationRef(code=generator.choice(SITE_CODES)),
            value=(
                None
                if is_missing
                else Measurement(
                    value=generator.uniform(230.0, 310.0), unit=SiUnit.KELVIN
                )
            ),
            quality=QualityFlag.MISSING if is_missing else QualityFlag.GOOD,
            observed_at=DateWithPrecision(
                # Few distinct instants, so ties on the instant are common.
                value=START + timedelta(hours=generator.randint(0, 30)),
                precision=DatePrecision.HOUR,
            ),
        )

    def fake(self) -> InMemoryIngestionQueryService:
        # The Fake keeps the first observation per key, as append_many does.
        unique = list({item.key: item for item in reversed(self.observations)}.values())
        return InMemoryIngestionQueryService(
            InMemoryIngestionUnitOfWork(
                datasets=self.datasets,
                versions=self.versions,
                runs=self.runs,
                observations=unique,
                assets=self.assets,
            )
        )


async def test_sql_query_service_pages_exactly_like_the_in_memory_fake(
    ingestion_uow_factory: IngestionFactory,
    ingestion_queries: SqlAlchemyIngestionQueryService,
) -> None:
    arrangement = _Arrangement(random.Random(RANDOM_SEED))  # noqa: S311  # reason: seeded test-data values, never secrets or ids
    await store(
        ingestion_uow_factory,
        datasets=arrangement.datasets,
        versions=arrangement.versions,
        runs=arrangement.runs,
        observations=arrangement.observations,
        assets=arrangement.assets,
    )
    fake = arrangement.fake()
    first = arrangement.datasets[0]
    observation_query = QueryObservations(
        dataset_id=first.id,
        variable="air_temperature",
        observed_from=START + timedelta(hours=2),
        observed_to=START + timedelta(hours=25),
    )
    raster_query = ListRasterAssets(
        bbox=BoundingBox(
            min_longitude=74.2, min_latitude=35.5, max_longitude=75.5, max_latitude=36.5
        ),
        acquired_from=START + timedelta(days=3),
    )

    for limit in (1, 3, 7):
        sql_datasets, _ = await _collect(
            lambda page: ingestion_queries.list_datasets(ListDatasets(page=page)), limit
        )
        fake_datasets, _ = await _collect(
            lambda page: fake.list_datasets(ListDatasets(page=page)), limit
        )
        sql_runs, _ = await _collect(
            lambda page: ingestion_queries.list_runs(
                ListRuns(dataset_id=first.id, page=page)
            ),
            limit,
        )
        fake_runs, _ = await _collect(
            lambda page: fake.list_runs(ListRuns(dataset_id=first.id, page=page)),
            limit,
        )
        sql_observations, _ = await _collect(
            lambda page: ingestion_queries.query_observations(
                observation_query.model_copy(update={"page": page})
            ),
            limit * 10,
        )
        fake_observations, _ = await _collect(
            lambda page: fake.query_observations(
                observation_query.model_copy(update={"page": page})
            ),
            limit * 10,
        )
        sql_rasters, _ = await _collect(
            lambda page: ingestion_queries.list_raster_assets(
                raster_query.model_copy(update={"page": page})
            ),
            limit,
        )
        fake_rasters, _ = await _collect(
            lambda page: fake.list_raster_assets(
                raster_query.model_copy(update={"page": page})
            ),
            limit,
        )

        assert sql_datasets == fake_datasets
        assert sql_runs == fake_runs
        assert sql_observations == fake_observations
        assert sql_rasters == fake_rasters
    assert await ingestion_queries.get_dataset(
        GetDataset(dataset_id=first.id)
    ) == await fake.get_dataset(GetDataset(dataset_id=first.id))
