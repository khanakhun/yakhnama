"""The append-only audit repository, its unit of work and the table guard.

The guard is the ``BEFORE UPDATE OR DELETE`` trigger of migration 0009; these tests
prove it through raw SQL on a plain session, because the repository offers no way to
change or delete an entry at all.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.audit import AuditEntryTestFactory
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import AuditTarget, compute_digest
from yakhnama.modules.audit.infrastructure.orm import AuditEntryRow
from yakhnama.modules.audit.infrastructure.queries import SqlAlchemyAuditQueryService
from yakhnama.modules.audit.infrastructure.uow import SqlAlchemyAuditUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.pagination import PageRequest

pytestmark = pytest.mark.integration

type AuditFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyAuditUnitOfWork]

OCCURRED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)


def _user_entry() -> AuditEntry:
    return AuditEntryTestFactory.build(
        occurred_at=OCCURRED,
        actor_kind="user",
        actor_id=AuditEntryTestFactory.build().id,
        target=AuditTarget(
            target_type="report", target_id=AuditEntryTestFactory.build().id
        ),
        before_digest=compute_digest(b"before"),
        after_digest=compute_digest(b"after"),
        request_id="req-test-1",
    )


async def _append(factory: AuditFactory, entry: AuditEntry) -> bool:
    async with factory() as uow:
        inserted = await uow.audit_entries.append(entry)
        await uow.commit()
    return inserted


async def _stored(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[AuditEntry]:
    service = SqlAlchemyAuditQueryService(session_factory)
    page = await service.list_recent(PageRequest())
    return [AuditEntry.model_validate(item.model_dump()) for item in page.items]


async def test_audit_repository_append_new_entry_returns_true_and_round_trips(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = _user_entry()

    inserted = await _append(audit_uow_factory, entry)

    assert inserted is True
    assert await _stored(session_factory) == [entry]


async def test_audit_repository_append_sets_recorded_at_from_database(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = AuditEntryTestFactory.build()

    await _append(audit_uow_factory, entry)

    async with session_factory() as session:
        recorded_at, now = (
            await session.execute(select(AuditEntryRow.recorded_at, func.now()))
        ).one()
    assert recorded_at.utcoffset() is not None
    assert recorded_at <= now


async def test_audit_repository_append_same_event_twice_stores_it_once(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = AuditEntryTestFactory.build()
    redelivered = AuditEntryTestFactory.build(event_id=entry.event_id)

    first = await _append(audit_uow_factory, entry)
    second = await _append(audit_uow_factory, redelivered)

    assert (first, second) == (True, False)
    assert await _stored(session_factory) == [entry]


async def test_audit_repository_append_same_event_in_one_transaction_stores_it_once(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = AuditEntryTestFactory.build()
    redelivered = AuditEntryTestFactory.build(event_id=entry.event_id)

    async with audit_uow_factory() as uow:
        results = (
            await uow.audit_entries.append(entry),
            await uow.audit_entries.append(redelivered),
        )
        await uow.commit()

    assert results == (True, False)
    assert await _stored(session_factory) == [entry]


async def test_audit_repository_concurrent_appends_of_one_event_store_it_once(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = AuditEntryTestFactory.build()
    redelivered = AuditEntryTestFactory.build(event_id=entry.event_id)

    results = await asyncio.gather(
        _append(audit_uow_factory, entry), _append(audit_uow_factory, redelivered)
    )

    assert sorted(results) == [False, True]
    assert len(await _stored(session_factory)) == 1


async def test_audit_repository_append_taken_entry_id_raises_conflict_and_keeps_tx(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = AuditEntryTestFactory.build()
    same_id = AuditEntryTestFactory.build(id=entry.id)
    other = AuditEntryTestFactory.build()
    await _append(audit_uow_factory, entry)

    async with audit_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.audit_entries.append(same_id)
        # The savepoint left the transaction usable.
        assert await uow.audit_entries.append(other) is True
        await uow.commit()

    assert {stored.id for stored in await _stored(session_factory)} == {
        entry.id,
        other.id,
    }


async def test_audit_repository_append_without_commit_is_rolled_back(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entry = AuditEntryTestFactory.build()

    async with audit_uow_factory() as uow:
        await uow.audit_entries.append(entry)

    assert await _stored(session_factory) == []


@pytest.mark.parametrize(
    ("statement", "operation"),
    [
        (
            "UPDATE audit_entries SET action = 'tampered.action' WHERE id = :id",
            "UPDATE",
        ),
        ("DELETE FROM audit_entries WHERE id = :id", "DELETE"),
    ],
    ids=["update", "delete"],
)
async def test_audit_entries_table_update_or_delete_is_rejected_by_trigger(
    audit_uow_factory: AuditFactory,
    session_factory: async_sessionmaker[AsyncSession],
    statement: str,
    operation: str,
) -> None:
    entry = AuditEntryTestFactory.build()
    await _append(audit_uow_factory, entry)

    async with session_factory() as session:
        with pytest.raises(DBAPIError, match=f"append-only: {operation}"):
            await session.execute(text(statement), {"id": entry.id})
        await session.rollback()

    assert await _stored(session_factory) == [entry]
