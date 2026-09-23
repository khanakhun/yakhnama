"""The SQLAlchemy source repository and provenance unit of work against real PostGIS."""

from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.provenance import TEST_SOURCE_URL, SourceTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.modules.provenance.domain.value_objects import Licence, SourceType
from yakhnama.modules.provenance.infrastructure.uow import (
    SqlAlchemyProvenanceUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

pytestmark = pytest.mark.integration

type ProvenanceFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyProvenanceUnitOfWork]

# Before the test clock's start, so every change the clock stamps is later.
CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)


def _full_source() -> Source:
    return SourceTestFactory.build(
        source_type=SourceType.GOVERNMENT,
        url=TEST_SOURCE_URL,
        licence=Licence.spdx("CC-BY-4.0"),
        retrieved_at=DateWithPrecision(
            value=datetime(2026, 8, 1, tzinfo=UTC), precision=DatePrecision.DAY
        ),
        publisher="Test publisher",
        language="ur",
        owner_actor_id=SequentialIdGenerator(seed=7).new_id(),
        organization_id=SequentialIdGenerator(seed=8).new_id(),
        created_at=CREATED,
        updated_at=CREATED,
    )


async def _store(factory: ProvenanceFactory, *sources: Source) -> None:
    async with factory() as uow:
        for source in sources:
            await uow.sources.add(source)
        await uow.commit()


async def _get(factory: ProvenanceFactory, source: Source) -> Source | None:
    async with factory() as uow:
        return await uow.sources.get(source.id)


async def test_source_repository_add_then_get_returns_equal_source_with_every_field(
    provenance_uow_factory: ProvenanceFactory,
) -> None:
    source = _full_source()

    await _store(provenance_uow_factory, source)
    loaded = await _get(provenance_uow_factory, source)

    assert loaded == source


async def test_source_repository_add_with_custom_licence_round_trips_jsonb(
    provenance_uow_factory: ProvenanceFactory,
) -> None:
    source = SourceTestFactory.build(
        licence=Licence.custom("Test terms, research only")
    )

    await _store(provenance_uow_factory, source)
    loaded = await _get(provenance_uow_factory, source)

    assert loaded is not None
    assert loaded.licence == Licence.custom("Test terms, research only")


async def test_source_repository_get_unknown_id_returns_none(
    provenance_uow_factory: ProvenanceFactory,
) -> None:
    source = SourceTestFactory.build()

    loaded = await _get(provenance_uow_factory, source)

    assert loaded is None


async def test_source_repository_add_taken_id_raises_conflict_and_keeps_transaction(
    provenance_uow_factory: ProvenanceFactory,
) -> None:
    source = SourceTestFactory.build()
    other = SourceTestFactory.build()
    await _store(provenance_uow_factory, source)

    async with provenance_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.sources.add(source)
        # The savepoint left the transaction usable.
        await uow.sources.add(other)
        await uow.commit()

    assert await _get(provenance_uow_factory, other) == other


async def test_source_repository_save_next_version_persists_change(
    provenance_uow_factory: ProvenanceFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    source = SourceTestFactory.build(created_at=CREATED, updated_at=CREATED)
    await _store(provenance_uow_factory, source)
    referenced = source.mark_referenced(clock=clock, ids=ids).state

    async with provenance_uow_factory() as uow:
        await uow.sources.save(referenced)
        await uow.commit()

    assert await _get(provenance_uow_factory, source) == referenced


async def test_source_repository_save_stale_version_raises_conflict(
    provenance_uow_factory: ProvenanceFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    source = SourceTestFactory.build(created_at=CREATED, updated_at=CREATED)
    await _store(provenance_uow_factory, source)
    referenced = source.mark_referenced(clock=clock, ids=ids).state
    async with provenance_uow_factory() as uow:
        await uow.sources.save(referenced)
        await uow.commit()

    async with provenance_uow_factory() as uow:
        with pytest.raises(ConflictError) as raised:
            await uow.sources.save(referenced)

    assert raised.value.details["stored_version"] == referenced.version


async def test_source_repository_save_unknown_source_raises_not_found(
    provenance_uow_factory: ProvenanceFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    source = SourceTestFactory.build(created_at=CREATED, updated_at=CREATED)
    referenced = source.mark_referenced(clock=clock, ids=ids).state

    async with provenance_uow_factory() as uow:
        with pytest.raises(SourceNotFoundError):
            await uow.sources.save(referenced)


async def test_source_repository_add_without_commit_is_rolled_back(
    provenance_uow_factory: ProvenanceFactory,
) -> None:
    source = SourceTestFactory.build()

    async with provenance_uow_factory() as uow:
        await uow.sources.add(source)

    assert await _get(provenance_uow_factory, source) is None


async def test_sources_table_retrieved_at_without_precision_violates_check(
    session_factory: async_sessionmaker[AsyncSession],
    provenance_uow_factory: ProvenanceFactory,
) -> None:
    source = _full_source()
    await _store(provenance_uow_factory, source)

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="retrieved_at_with_precision"):
            await session.execute(
                text("UPDATE sources SET retrieved_at_precision = NULL WHERE id = :id"),
                {"id": source.id},
            )
