"""Unit tests for the authorised audit query service."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.audit import AuditEntryTestFactory
from tests.fakes.audit import InMemoryAuditQueryService, InMemoryAuditUnitOfWork
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.audit.application.queries import (
    ListAuditEntriesForTarget,
    ListRecentAuditEntries,
)
from yakhnama.modules.audit.application.query_services import (
    AuthorisedAuditQueryService,
)
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.pagination import PageRequest

START = datetime(2026, 5, 1, tzinfo=UTC)
TARGET = AuditTarget(
    target_type="report", target_id=SequentialIdGenerator(seed=404).new_id()
)
ADMIN = actor_with({Role.ADMIN})


def entries() -> list[AuditEntry]:
    """Return two entries about ``TARGET`` and one about another aggregate."""
    other = AuditTarget(
        target_type="source", target_id=SequentialIdGenerator(seed=405).new_id()
    )
    return [
        AuditEntryTestFactory.build(target=target, occurred_at=START + timedelta(i))
        for i, target in enumerate([TARGET, other, TARGET])
    ]


def service(*stored: AuditEntry) -> AuthorisedAuditQueryService:
    """Return the authorised service over a fake holding ``stored``."""
    uow = InMemoryAuditUnitOfWork(entries=stored)
    return AuthorisedAuditQueryService(InMemoryAuditQueryService(uow))


async def test_list_for_target_admin_returns_target_entries_newest_first() -> None:
    stored = entries()

    page = await service(*stored).list_for_target(
        ListAuditEntriesForTarget(actor=ADMIN, target=TARGET)
    )

    assert [item.id for item in page.items] == [stored[2].id, stored[0].id]


async def test_list_recent_admin_pages_every_entry() -> None:
    stored = entries()
    audit = service(*stored)

    first = await audit.list_recent(
        ListRecentAuditEntries(actor=ADMIN, page=PageRequest(limit=2))
    )
    second = await audit.list_recent(
        ListRecentAuditEntries(
            actor=ADMIN, page=PageRequest(limit=2, cursor=first.next_cursor)
        )
    )

    assert [item.id for item in first.items] == [stored[2].id, stored[1].id]
    assert [item.id for item in second.items] == [stored[0].id]
    assert second.next_cursor is None


@pytest.mark.parametrize(
    "actor", [Actor.anonymous(), actor_with(), actor_with({Role.MODERATOR})]
)
async def test_list_recent_non_admin_raises_permission_denied(actor: Actor) -> None:
    with pytest.raises(PermissionDeniedError):
        await service(*entries()).list_recent(ListRecentAuditEntries(actor=actor))


async def test_list_for_target_non_admin_raises_permission_denied() -> None:
    with pytest.raises(PermissionDeniedError):
        await service().list_for_target(
            ListAuditEntriesForTarget(actor=actor_with(), target=TARGET)
        )
