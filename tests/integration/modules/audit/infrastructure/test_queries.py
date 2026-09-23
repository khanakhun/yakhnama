"""The SQL audit query service against real PostGIS."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.audit import AuditEntryTestFactory
from yakhnama.modules.audit.application.dto import AuditEntrySummary
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.modules.audit.infrastructure.queries import (
    SqlAlchemyAuditQueryService,
    decode_since,
)
from yakhnama.modules.audit.infrastructure.uow import SqlAlchemyAuditUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest

pytestmark = pytest.mark.integration

type AuditFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyAuditUnitOfWork]

START: Final = datetime(2026, 1, 1, tzinfo=UTC)
TARGET: Final = AuditTarget(
    target_type="report", target_id=AuditEntryTestFactory.build().id
)


@pytest.fixture
async def stored(audit_uow_factory: AuditFactory) -> list[AuditEntry]:
    """Store five entries, three about ``TARGET``; two share an instant."""
    instants = [START + timedelta(minutes=index) for index in range(4)]
    instants.append(instants[1])
    entries = [
        AuditEntryTestFactory.build(occurred_at=at, target=TARGET)
        if index % 2 == 0
        else AuditEntryTestFactory.build(occurred_at=at)
        for index, at in enumerate(instants)
    ]
    async with audit_uow_factory() as uow:
        for entry in entries:
            await uow.audit_entries.append(entry)
        await uow.commit()
    return entries


def _newest_first(entries: list[AuditEntry]) -> list[EntityId]:
    ordered = sorted(entries, key=lambda entry: (entry.occurred_at, entry.id))
    return [entry.id for entry in reversed(ordered)]


async def _all_pages(
    service: SqlAlchemyAuditQueryService, target: AuditTarget | None, limit: int
) -> list[Page[AuditEntrySummary]]:
    pages: list[Page[AuditEntrySummary]] = []
    cursor: str | None = None
    while True:
        request = PageRequest(limit=limit, cursor=cursor)
        page = await (
            service.list_recent(request)
            if target is None
            else service.list_for_target(target, request)
        )
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            return pages


async def test_audit_query_service_list_recent_pages_newest_first(
    session_factory: async_sessionmaker[AsyncSession], stored: list[AuditEntry]
) -> None:
    service = SqlAlchemyAuditQueryService(session_factory)

    pages = await _all_pages(service, None, limit=2)

    assert [item.id for page in pages for item in page.items] == _newest_first(stored)
    assert len(pages) == 3


async def test_audit_query_service_list_for_target_returns_only_its_entries(
    session_factory: async_sessionmaker[AsyncSession], stored: list[AuditEntry]
) -> None:
    service = SqlAlchemyAuditQueryService(session_factory)

    pages = await _all_pages(service, TARGET, limit=2)

    about_target = [entry for entry in stored if entry.target == TARGET]
    assert [item.id for page in pages for item in page.items] == _newest_first(
        about_target
    )
    assert pages[0].items[0] == AuditEntrySummary.from_entity(
        max(about_target, key=lambda entry: (entry.occurred_at, entry.id))
    )


async def test_audit_query_service_list_for_unknown_target_returns_empty_page(
    session_factory: async_sessionmaker[AsyncSession], stored: list[AuditEntry]
) -> None:
    service = SqlAlchemyAuditQueryService(session_factory)
    unknown = AuditTarget(target_type="report", target_id=stored[1].event_id)

    page = await service.list_for_target(unknown, PageRequest())

    assert page.items == ()
    assert page.next_cursor is None


@pytest.mark.parametrize("sort_key", ["not a date", "2026-01-01T00:00:00"])
def test_audit_decode_since_invalid_or_naive_raises_validation_error(
    sort_key: str,
) -> None:
    with pytest.raises(ValidationError):
        decode_since(sort_key)
