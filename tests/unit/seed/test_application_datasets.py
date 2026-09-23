"""Unit tests for the seed's dataset catalog step, with in-memory fakes only.

The catalog file is the repository's ``data/reference/datasets.yaml``, read by the
production ``YamlReferenceFileReader`` (reading a local reference file is allowed in
unit tests, as for the other reference files).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from tests.fakes.clock import FrozenClock
from tests.fakes.geography import InMemoryGeographyUnitOfWork
from tests.fakes.hazards import InMemoryHazardsUnitOfWork
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import InMemoryImpactsUnitOfWork
from tests.fakes.ingestion import InMemoryIngestionUnitOfWork
from tests.fakes.seed import REFERENCE_DIRECTORY, FakeReferenceFileReader
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
)
from yakhnama.modules.identity.public import CanManageReferenceData, Role
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
)
from yakhnama.modules.ingestion.public import LoadReferenceDatasetsHandler
from yakhnama.seed.application import (
    DatasetSeedStep,
    SeedReferenceData,
    SeedReferenceDataHandler,
)
from yakhnama.seed.infrastructure import YamlReferenceFileReader

NOW: Final = datetime(2026, 3, 1, tzinfo=UTC)
SYSTEM_ACTOR: Final = actor_with(
    {Role.ADMIN}, user_id=SequentialIdGenerator(seed=98).new_id()
)
FIXTURE_DATASET: Final = "fixture.temperature_sample"


@dataclass
class DatasetSeedFixture:
    """The seed handler with the dataset step, plus the catalog store to inspect.

    Implements: Composition Root (test wiring over fakes).
    """

    handler: SeedReferenceDataHandler
    ingestion: InMemoryIngestionUnitOfWork


def wire(*, include_fixtures: bool) -> DatasetSeedFixture:
    """Wire the seed as ``build_seed_handler`` does, over fakes."""
    policy = CanManageReferenceData()
    clock = FrozenClock(NOW)
    ids = SequentialIdGenerator()
    ingestion = InMemoryIngestionUnitOfWork()
    handler = SeedReferenceDataHandler(
        reader=FakeReferenceFileReader(),
        policy=policy,
        load_hazard_types=LoadReferenceHazardTypesHandler(
            InMemoryUnitOfWorkFactory(InMemoryHazardsUnitOfWork()), policy, clock, ids
        ),
        load_impact_metrics=LoadReferenceImpactMetricsHandler(
            InMemoryUnitOfWorkFactory(InMemoryImpactsUnitOfWork()), policy, clock, ids
        ),
        load_places=LoadReferencePlacesHandler(
            InMemoryUnitOfWorkFactory(InMemoryGeographyUnitOfWork()),
            policy,
            clock,
            ids,
        ),
        datasets=DatasetSeedStep(
            reader=YamlReferenceFileReader(REFERENCE_DIRECTORY),
            load=LoadReferenceDatasetsHandler(
                InMemoryUnitOfWorkFactory(ingestion), clock, ids
            ),
            include_fixtures=include_fixtures,
        ),
    )
    return DatasetSeedFixture(handler, ingestion)


def _catalog_codes(ingestion: InMemoryIngestionUnitOfWork) -> set[str]:
    return {dataset.code for dataset in ingestion.datasets.committed.values()}


async def test_seed_with_fixtures_registers_the_fixture_dataset() -> None:
    seed = wire(include_fixtures=True)

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert report.datasets is not None
    assert report.datasets.created == (FIXTURE_DATASET,)
    assert report.datasets.excluded == ()
    assert _catalog_codes(seed.ingestion) == {FIXTURE_DATASET}


async def test_seed_without_fixtures_excludes_the_fixture_dataset() -> None:
    seed = wire(include_fixtures=False)

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert report.datasets is not None
    assert report.datasets.created == ()
    assert report.datasets.excluded == (FIXTURE_DATASET,)
    assert _catalog_codes(seed.ingestion) == set()


async def test_seed_datasets_twice_second_run_is_unchanged() -> None:
    seed = wire(include_fixtures=True)
    await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))
    events = len(seed.ingestion.committed_events)

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert report.datasets is not None
    assert report.datasets.unchanged == (FIXTURE_DATASET,)
    assert report.is_unchanged is True
    assert len(seed.ingestion.committed_events) == events


async def test_seed_datasets_dry_run_registers_nothing() -> None:
    seed = wire(include_fixtures=True)

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR, dry_run=True))

    assert report.datasets is not None
    assert report.datasets.dry_run is True
    assert report.datasets.created == (FIXTURE_DATASET,)
    assert _catalog_codes(seed.ingestion) == set()


async def test_seed_report_changed_catalog_alone_is_not_unchanged() -> None:
    seed = wire(include_fixtures=True)
    await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))
    unchanged = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))
    assert unchanged.datasets is not None
    created = unchanged.datasets.model_copy(
        update={"created": (FIXTURE_DATASET,), "unchanged": ()}
    )

    report = unchanged.model_copy(update={"datasets": created})

    assert report.is_unchanged is False
    assert unchanged.model_copy(update={"datasets": None}).is_unchanged is True
