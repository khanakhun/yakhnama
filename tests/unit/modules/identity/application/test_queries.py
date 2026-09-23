"""Unit tests for the identity query models and the in-memory query service.

The in-memory service is the fake the API tests use, so its paging and filtering
must match what ``IdentityQueryService`` promises.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tests.fakes.identity import InMemoryIdentityQueryService, actor_with
from tests.unit.modules.identity.application.support import (
    EARLIER,
    make_membership,
    make_organization,
    make_user,
    wire,
)
from yakhnama.modules.identity.application.queries import (
    GetMe,
    GetOrganization,
    ListOrganizationMembers,
)
from yakhnama.modules.identity.domain.value_objects import OrganizationStatus
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import DEFAULT_PAGE_LIMIT, PageRequest

if TYPE_CHECKING:
    from yakhnama.modules.identity.application.ports import IdentityQueryService


def test_query_models_carry_their_inputs_and_default_page() -> None:
    organization = make_organization()
    actor = actor_with()

    assert GetMe(actor=actor).actor == actor
    assert GetOrganization(organization_id=organization.id).organization_id == (
        organization.id
    )
    listing = ListOrganizationMembers(organization_id=organization.id)
    assert listing.page.limit == DEFAULT_PAGE_LIMIT
    assert listing.page.cursor is None


async def test_get_me_returns_user_with_active_memberships_only() -> None:
    user = make_user()
    active = make_organization("active-org")
    paused = make_organization("paused-org", status=OrganizationStatus.SUSPENDED)
    uow, _ = wire(
        users=(user,),
        organizations=(active, paused),
        memberships=(make_membership(active, user), make_membership(paused, user)),
    )
    service: IdentityQueryService = InMemoryIdentityQueryService(uow)

    detail = await service.get_me(user.id)

    assert detail is not None
    assert [summary.slug for summary in detail.memberships] == ["active-org"]


async def test_get_me_unknown_user_returns_none() -> None:
    uow, _ = wire()

    assert await InMemoryIdentityQueryService(uow).get_me(make_user().id) is None


async def test_get_organization_counts_members() -> None:
    organization = make_organization()
    members = (make_user(), make_user())
    uow, _ = wire(
        users=members,
        organizations=(organization,),
        memberships=tuple(make_membership(organization, user) for user in members),
    )

    detail = await InMemoryIdentityQueryService(uow).get_organization(organization.id)

    assert detail is not None
    assert detail.member_count == 2


async def test_get_organization_unknown_returns_none() -> None:
    uow, _ = wire()

    service = InMemoryIdentityQueryService(uow)

    assert await service.get_organization(make_organization().id) is None


async def test_list_members_pages_by_since_then_user_id() -> None:
    organization = make_organization()
    users = tuple(make_user() for _ in range(3))
    memberships = tuple(
        make_membership(organization, user, joined=EARLIER + timedelta(hours=index))
        for index, user in enumerate(users)
    )
    uow, _ = wire(users=users, organizations=(organization,), memberships=memberships)
    service = InMemoryIdentityQueryService(uow)

    first = await service.list_members(
        ListOrganizationMembers(
            organization_id=organization.id, page=PageRequest(limit=2)
        )
    )
    second = await service.list_members(
        ListOrganizationMembers(
            organization_id=organization.id,
            page=PageRequest(limit=2, cursor=first.next_cursor),
        )
    )

    assert [item.user_id for item in first.items] == [users[0].id, users[1].id]
    assert first.next_cursor is not None
    assert [item.user_id for item in second.items] == [users[2].id]
    assert second.next_cursor is None


async def test_list_members_invalid_cursor_raises_validation_error() -> None:
    uow, _ = wire()

    with pytest.raises(ValidationError):
        await InMemoryIdentityQueryService(uow).list_members(
            ListOrganizationMembers(
                organization_id=make_organization().id,
                page=PageRequest(cursor="not-a-cursor"),
            )
        )
