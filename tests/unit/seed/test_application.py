"""Unit tests for the reference-data seed orchestrator, with in-memory fakes only."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from tests.fakes.clock import FrozenClock
from tests.fakes.geography import InMemoryGeographyUnitOfWork
from tests.fakes.hazards import InMemoryHazardsUnitOfWork
from tests.fakes.identity import AllowAllPolicy, DenyAllPolicy, actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import InMemoryImpactsUnitOfWork
from tests.fakes.seed import (
    HAZARD_TYPES_FILE,
    IMPACT_METRICS_FILE,
    PLACES_FILE,
    FakeReferenceFileReader,
    parse_reference_file,
)
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
)
from yakhnama.modules.hazards.public import HazardTypeReferenceFile
from yakhnama.modules.identity.public import (
    AuthorisationPolicy,
    CanManageReferenceData,
    Role,
)
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
)
from yakhnama.seed.application import (
    SeedReferenceData,
    SeedReferenceDataHandler,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError

NOW = datetime(2026, 3, 1, tzinfo=UTC)
ACTOR_ID = SequentialIdGenerator(seed=99).new_id()
SYSTEM_ACTOR = actor_with({Role.ADMIN}, user_id=ACTOR_ID)
REAL_HAZARD_CODES = 10
REAL_METRIC_CODES = 10
REAL_PLACE_CODES = 15


@dataclass
class SeedFixture:
    """The seed handler wired to fresh fakes, plus the fakes to inspect.

    Implements: Composition Root (test wiring over fakes).
    """

    handler: SeedReferenceDataHandler
    reader: FakeReferenceFileReader
    hazards: InMemoryHazardsUnitOfWork
    impacts: InMemoryImpactsUnitOfWork
    geography: InMemoryGeographyUnitOfWork


def wire(policy: AuthorisationPolicy | None = None) -> SeedFixture:
    """Wire the seed handler the way the composition root will, over fakes."""
    chosen = policy or CanManageReferenceData()
    clock = FrozenClock(NOW)
    ids = SequentialIdGenerator()
    reader = FakeReferenceFileReader()
    hazards = InMemoryHazardsUnitOfWork()
    impacts = InMemoryImpactsUnitOfWork()
    geography = InMemoryGeographyUnitOfWork()
    handler = SeedReferenceDataHandler(
        reader=reader,
        policy=chosen,
        load_hazard_types=LoadReferenceHazardTypesHandler(
            InMemoryUnitOfWorkFactory(hazards), chosen, clock, ids
        ),
        load_impact_metrics=LoadReferenceImpactMetricsHandler(
            InMemoryUnitOfWorkFactory(impacts), chosen, clock, ids
        ),
        load_places=LoadReferencePlacesHandler(
            InMemoryUnitOfWorkFactory(geography), chosen, clock, ids
        ),
    )
    return SeedFixture(handler, reader, hazards, impacts, geography)


async def test_seed_reference_data_first_run_creates_every_reference_entry() -> None:
    seed = wire()

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert len(report.hazard_types.created) == REAL_HAZARD_CODES
    assert len(report.impact_metrics.created) == REAL_METRIC_CODES
    assert len(report.places.created) == REAL_PLACE_CODES
    assert report.is_unchanged is False
    assert report.dry_run is False
    assert seed.reader.reads == [HAZARD_TYPES_FILE, IMPACT_METRICS_FILE, PLACES_FILE]
    assert (seed.hazards.committed, seed.impacts.committed) == (True, True)
    assert seed.geography.committed is True


async def test_seed_reference_data_echoes_each_file_data_version() -> None:
    seed = wire()
    reader = FakeReferenceFileReader()

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert report.hazard_types.data_version == reader.read_hazard_types().data_version
    assert (
        report.impact_metrics.data_version == reader.read_impact_metrics().data_version
    )
    assert report.places.data_version == reader.read_places().data_version


async def test_seed_reference_data_twice_second_run_is_all_unchanged() -> None:
    seed = wire()
    await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))
    snapshot = (
        dict(seed.hazards.hazard_types.committed),
        dict(seed.impacts.impact_metrics.committed),
        dict(seed.geography.places.committed),
    )
    events = (
        len(seed.hazards.committed_events),
        len(seed.impacts.committed_events),
        len(seed.geography.committed_events),
    )

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert report.is_unchanged is True
    assert len(report.hazard_types.unchanged) == REAL_HAZARD_CODES
    assert len(report.impact_metrics.unchanged) == REAL_METRIC_CODES
    assert len(report.places.unchanged) == REAL_PLACE_CODES
    assert (
        dict(seed.hazards.hazard_types.committed),
        dict(seed.impacts.impact_metrics.committed),
        dict(seed.geography.places.committed),
    ) == snapshot
    assert (
        len(seed.hazards.committed_events),
        len(seed.impacts.committed_events),
        len(seed.geography.committed_events),
    ) == events


async def test_seed_reference_data_dry_run_records_and_commits_nothing() -> None:
    seed = wire()

    report = await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR, dry_run=True))

    assert report.dry_run is True
    assert len(report.places.created) == REAL_PLACE_CODES
    for uow in (seed.hazards, seed.impacts, seed.geography):
        assert uow.committed is False
        assert uow.commit_count == 0
        assert uow.committed_events == ()
    assert seed.hazards.hazard_types.committed == {}
    assert seed.impacts.impact_metrics.committed == {}
    assert seed.geography.places.committed == {}


async def test_seed_reference_data_when_denied_raises_before_reading_files() -> None:
    policy = DenyAllPolicy()
    seed = wire(policy)

    with pytest.raises(PermissionDeniedError):
        await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR))

    assert policy.checked == [SYSTEM_ACTOR]
    assert seed.reader.reads == []
    assert seed.hazards.commit_count == 0


async def test_seed_reference_data_citizen_actor_is_denied_before_reading() -> None:
    seed = wire()

    with pytest.raises(PermissionDeniedError) as raised:
        await seed.handler(SeedReferenceData(actor=actor_with(user_id=ACTOR_ID)))

    assert raised.value.details == {
        "action": "seed reference data",
        "policy": "CanManageReferenceData",
    }
    assert seed.reader.reads == []


async def test_seed_reference_data_allow_all_policy_asks_with_command_actor() -> None:
    policy = AllowAllPolicy()
    seed = wire(policy)

    await seed.handler(SeedReferenceData(actor=SYSTEM_ACTOR, dry_run=True))

    # The seed asks once, then each of the three module loads asks again.
    assert policy.checked == [SYSTEM_ACTOR] * 4


def test_parse_reference_file_with_wrong_model_raises_kernel_validation_error() -> None:
    with pytest.raises(ValidationError) as raised:
        parse_reference_file(HazardTypeReferenceFile, PLACES_FILE)

    assert raised.value.details["file"] == PLACES_FILE
