"""Unit tests for the organisation command handlers, with in-memory fakes only."""

import pytest

from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.unit.modules.identity.application.support import (
    NOW,
    actor_of,
    clock,
    ids,
    make_membership,
    make_organization,
    make_user,
    wire,
)
from yakhnama.modules.identity.application.commands import (
    CreateOrganization,
    RenameOrganization,
)
from yakhnama.modules.identity.application.handlers import (
    CreateOrganizationHandler,
    RenameOrganizationHandler,
)
from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    OrganizationNotActiveError,
    OrganizationNotFoundError,
    OrganizationSlugTakenError,
)
from yakhnama.modules.identity.domain.events import (
    MembershipAdded,
    OrganizationCreated,
    OrganizationRenamed,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    OrganizationRole,
    OrganizationStatus,
    OrganizationType,
    Role,
)
from yakhnama.shared_kernel.errors import (
    PermissionDeniedError,
    PreconditionFailedError,
)

# --------------------------------------------------------------------------- #
# CreateOrganization                                                          #
# --------------------------------------------------------------------------- #


def create(actor: Actor, slug: str = "new-org") -> CreateOrganization:
    """Return a create command for ``slug``."""
    return CreateOrganization(
        actor=actor,
        slug=slug,
        name="New organisation",
        organization_type=OrganizationType.RESEARCH,
    )


async def test_create_organization_commits_org_and_creator_admin_membership() -> None:
    creator = make_user()
    uow, factory = wire(users=(creator,))

    detail = await CreateOrganizationHandler(factory, clock(), ids())(
        create(actor_of(creator))
    )

    (organization,) = uow.organizations.committed.values()
    (membership,) = uow.memberships.committed.values()
    assert detail.id == organization.id
    assert detail.slug == "new-org"
    assert detail.member_count == 1
    assert detail.version == 1
    assert organization.created_at == NOW
    assert membership.user_id == creator.id
    assert membership.organization_id == organization.id
    assert membership.role is OrganizationRole.ADMIN
    assert [type(event) for event in uow.committed_events] == [
        OrganizationCreated,
        MembershipAdded,
    ]


async def test_create_organization_taken_slug_raises_and_commits_nothing() -> None:
    creator = make_user()
    existing = make_organization("new-org")
    uow, factory = wire(users=(creator,), organizations=(existing,))

    with pytest.raises(OrganizationSlugTakenError):
        await CreateOrganizationHandler(factory, clock(), ids())(
            create(actor_of(creator))
        )

    assert list(uow.organizations.committed) == [existing.id]
    assert uow.memberships.committed == {}
    assert uow.committed_events == ()


async def test_create_organization_anonymous_actor_is_denied() -> None:
    _, factory = wire()

    with pytest.raises(PermissionDeniedError) as raised:
        await CreateOrganizationHandler(factory, clock(), ids())(
            create(Actor.anonymous())
        )

    assert raised.value.details["policy"] == "IsAuthenticated"
    assert factory.calls == 0


async def test_create_organization_suspended_actor_is_refused() -> None:
    creator = make_user(is_suspended=True)
    uow, factory = wire(users=(creator,))

    with pytest.raises(AccountSuspendedError):
        await CreateOrganizationHandler(factory, clock(), ids())(
            create(Actor(user_id=creator.id, roles=creator.roles))
        )

    assert uow.organizations.committed == {}


async def test_create_organization_actor_without_record_is_denied() -> None:
    _, factory = wire()
    ghost = actor_with(user_id=SequentialIdGenerator(seed=9).new_id())

    with pytest.raises(PermissionDeniedError):
        await CreateOrganizationHandler(factory, clock(), ids())(create(ghost))


# --------------------------------------------------------------------------- #
# RenameOrganization                                                          #
# --------------------------------------------------------------------------- #


def rename(
    actor: Actor, organization_id: object, version: int | None = None
) -> RenameOrganization:
    """Return a rename command."""
    return RenameOrganization.model_validate(
        {
            "actor": actor,
            "organization_id": organization_id,
            "name": "Renamed organisation",
            "expected_version": version,
        }
    )


async def test_rename_organization_by_org_admin_commits_name_and_event() -> None:
    organization = make_organization()
    admin = make_user()
    membership = make_membership(organization, admin, OrganizationRole.ADMIN)
    uow, factory = wire(
        users=(admin,), organizations=(organization,), memberships=(membership,)
    )

    detail = await RenameOrganizationHandler(factory, clock(), ids())(
        rename(actor_of(admin, membership), organization.id, version=1)
    )

    assert detail.name == "Renamed organisation"
    assert detail.version == 2
    assert detail.member_count == 1
    assert uow.organizations.committed[organization.id].name == detail.name
    (event,) = uow.committed_events
    assert isinstance(event, OrganizationRenamed)


async def test_rename_organization_by_platform_admin_is_allowed() -> None:
    organization = make_organization()
    admin = make_user(Role.ADMIN)
    uow, factory = wire(users=(admin,), organizations=(organization,))

    detail = await RenameOrganizationHandler(factory, clock(), ids())(
        rename(actor_of(admin), organization.id)
    )

    assert detail.member_count == 0
    assert uow.organizations.committed[organization.id].version == 2


async def test_rename_organization_by_plain_member_is_denied() -> None:
    organization = make_organization()
    member = make_user()
    membership = make_membership(organization, member)
    uow, factory = wire(
        users=(member,), organizations=(organization,), memberships=(membership,)
    )

    with pytest.raises(PermissionDeniedError) as raised:
        await RenameOrganizationHandler(factory, clock(), ids())(
            rename(actor_of(member, membership), organization.id)
        )

    assert raised.value.details["policy"] == "CanManageOrganization"
    assert factory.calls == 0
    assert uow.organizations.committed[organization.id] == organization


async def test_rename_organization_missing_raises_not_found() -> None:
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,))

    with pytest.raises(OrganizationNotFoundError):
        await RenameOrganizationHandler(factory, clock(), ids())(
            rename(actor_of(admin), make_organization().id)
        )


async def test_rename_organization_stale_version_raises_precondition_failed() -> None:
    organization = make_organization()
    admin = make_user(Role.ADMIN)
    uow, factory = wire(users=(admin,), organizations=(organization,))

    with pytest.raises(PreconditionFailedError):
        await RenameOrganizationHandler(factory, clock(), ids())(
            rename(actor_of(admin), organization.id, version=3)
        )

    assert uow.organizations.committed[organization.id] == organization


async def test_rename_organization_suspended_org_raises_not_active() -> None:
    organization = make_organization(status=OrganizationStatus.SUSPENDED)
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,), organizations=(organization,))

    with pytest.raises(OrganizationNotActiveError):
        await RenameOrganizationHandler(factory, clock(), ids())(
            rename(actor_of(admin), organization.id)
        )


async def test_rename_organization_same_name_changes_nothing() -> None:
    organization = make_organization()
    admin = make_user(Role.ADMIN)
    uow, factory = wire(users=(admin,), organizations=(organization,))
    command = rename(actor_of(admin), organization.id).model_copy(
        update={"name": organization.name}
    )

    detail = await RenameOrganizationHandler(factory, clock(), ids())(command)

    assert detail.version == 1
    assert uow.committed_events == ()


async def test_rename_organization_suspended_actor_is_refused() -> None:
    organization = make_organization()
    admin = make_user(Role.ADMIN, is_suspended=True)
    uow, factory = wire(users=(admin,), organizations=(organization,))

    with pytest.raises(AccountSuspendedError):
        await RenameOrganizationHandler(factory, clock(), ids())(
            rename(Actor(user_id=admin.id, roles=admin.roles), organization.id)
        )

    assert uow.organizations.committed[organization.id] == organization
