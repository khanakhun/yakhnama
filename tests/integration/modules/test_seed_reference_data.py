"""The reference-data seed end to end on SQLAlchemy units of work and real PostGIS.

The real ``data/reference`` YAML files are parsed by the test reader from
``tests/fakes/seed.py`` (a local file read, no network) and loaded through the real
module handlers into the migrated schema. Running the seed twice must leave identical
row counts and report nothing created or updated the second time.
"""

from typing import Final

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.seed import AllowAllPolicy, FakeReferenceFileReader
from yakhnama.modules.geography.application.handlers import (
    LoadReferencePlacesHandler,
)
from yakhnama.modules.geography.infrastructure.orm import PlaceNameRow, PlaceRow
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.modules.hazards.application.handlers import (
    LoadReferenceHazardTypesHandler,
)
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.modules.impacts.application.handlers import (
    LoadReferenceImpactMetricsHandler,
)
from yakhnama.modules.impacts.infrastructure.orm import ImpactMetricRow
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.seed.application import (
    SeedReferenceData,
    SeedReferenceDataHandler,
    SeedReport,
)

pytestmark = pytest.mark.integration

COUNTED_TABLES: Final = (
    HazardTypeRow,
    ImpactMetricRow,
    PlaceRow,
    PlaceNameRow,
    OutboxMessage,
)


@pytest.fixture
def seed(
    geography_uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyGeographyUnitOfWork],
    hazards_uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyHazardsUnitOfWork],
    impacts_uow_factory: SqlAlchemyUnitOfWorkFactory[SqlAlchemyImpactsUnitOfWork],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> SeedReferenceDataHandler:
    """Wire the seed handler to the SQLAlchemy units of work, as production does."""
    policy = AllowAllPolicy()
    return SeedReferenceDataHandler(
        reader=FakeReferenceFileReader(),
        policy=policy,
        load_hazard_types=LoadReferenceHazardTypesHandler(
            hazards_uow_factory, policy, clock, ids
        ),
        load_impact_metrics=LoadReferenceImpactMetricsHandler(
            impacts_uow_factory, policy, clock, ids
        ),
        load_places=LoadReferencePlacesHandler(
            geography_uow_factory, policy, clock, ids
        ),
    )


async def _row_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with session_factory() as session:
        for model in COUNTED_TABLES:
            count = await session.scalar(select(func.count()).select_from(model))
            counts[model.__tablename__] = count or 0
    return counts


def _created(report: SeedReport) -> tuple[int, int, int]:
    return (
        len(report.hazard_types.created),
        len(report.impact_metrics.created),
        len(report.places.created),
    )


async def test_seed_reference_data_twice_creates_once_and_keeps_counts(
    seed: SeedReferenceDataHandler,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    command = SeedReferenceData(actor_id=None)

    first = await seed(command)
    counts_after_first = await _row_counts(session_factory)
    second = await seed(command)
    counts_after_second = await _row_counts(session_factory)

    assert all(created > 0 for created in _created(first))
    assert counts_after_first["hazard_types"] == len(first.hazard_types.created)
    assert counts_after_first["impact_metrics"] == len(first.impact_metrics.created)
    assert counts_after_first["places"] == len(first.places.created)
    assert counts_after_first["place_names"] >= counts_after_first["places"]
    assert second.is_unchanged
    assert _created(second) == (0, 0, 0)
    assert counts_after_second == counts_after_first


async def test_seed_reference_data_dry_run_writes_nothing(
    seed: SeedReferenceDataHandler,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    report = await seed(SeedReferenceData(actor_id=None, dry_run=True))
    counts = await _row_counts(session_factory)

    assert all(created > 0 for created in _created(report))
    assert set(counts.values()) == {0}
