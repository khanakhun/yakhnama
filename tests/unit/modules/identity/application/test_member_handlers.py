"""Unit tests for the membership command handlers, with in-memory fakes only."""

from datetime import timedelta

import pytest

from tests.unit.modules.identity.application.support import (
    EARLIER,
    actor_of,
    clock,
    ids,
    make_membership,
    make_organization,
    make_user,
    wire,
)
from yakhnama.modules.identity.application.commands import (
    AddMember,
    ChangeMemberRole,
    RemoveMember,
)
from yakhnama.modules.identity.application.handlers import (
    AddMemberHandler,
    ChangeMemberRoleHandler,
    RemoveMemberHandler,
)
from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    DuplicateMembershipError,
    LastOrganizationAdminError,
    MembershipNotFoundError,
    OrganizationNotActiveError,
    OrganizationNotFoundError,
    UserNotFoundError,
    UserSuspendedError,
)
from yakhnama.modules.identity.domain.events import (
    MembershipAdded,
    MembershipRemoved,
    MembershipRoleChanged,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    OrganizationRole,
    OrganizationStatus,
    Role,
)
from yakhnama.shared_kernel.errors import (
    PermissionDeniedError,
    PreconditionFailedError,
)


def org_with_admin(
    status: OrganizationStatus = OrganizationStatus.ACTIVE,
) -> tuple[Organization, User, Membership]:
    """Return an organisation, its only admin and the admin's membership."""
    organization = make_organization(status=status)
    admin = make_user()
    return (
        organization,
        admin,
        make_membership(organization, admin, OrganizationRole.ADMIN),
    )


# --------------------------------------------------------------------------- #
# AddMember                                                                   #
# --------------------------------------------------------------------------- #


async def test_add_member_by_platform_admin_commits_membership_and_event() -> None:
    organization, org_admin, admin_membership = org_with_admin()
    platform_admin = make_user(Role.ADMIN)
    newcomer = make_user(display_name="Newcomer")
    uow, factory = wire(
        users=(org_admin, platform_admin, newcomer),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    summary = await AddMemberHandler(factory, clock(), ids())(
        AddMember(
            actor=actor_of(platform_admin),
            organization_id=organization.id,
            user_id=newcomer.id,
        )
    )

    assert summary.membership_id in uow.memberships.committed
    assert summary.user_id == newcomer.id
    assert summary.display_name == "Newcomer"
    assert summary.role is OrganizationRole.MEMBER
    assert len(uow.memberships.committed) == 2
    (event,) = uow.committed_events
    assert isinstance(event, MembershipAdded)
    assert event.user_id == newcomer.id


async def test_add_member_by_org_admin_is_denied_until_members_can_consent() -> None:
    organization, admin, admin_membership = org_with_admin()
    newcomer = make_user()
    uow, factory = wire(
        users=(admin, newcomer),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(PermissionDeniedError) as raised:
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(admin, admin_membership),
                organization_id=organization.id,
                user_id=newcomer.id,
            )
        )

    assert raised.value.details["policy"] == "IsAdmin"
    assert factory.calls == 0
    assert list(uow.memberships.committed) == [admin_membership.id]


async def test_add_member_existing_member_raises_duplicate() -> None:
    organization, admin, admin_membership = org_with_admin()
    platform_admin = make_user(Role.ADMIN)
    uow, factory = wire(
        users=(admin, platform_admin),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(DuplicateMembershipError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(platform_admin),
                organization_id=organization.id,
                user_id=admin.id,
            )
        )

    assert list(uow.memberships.committed) == [admin_membership.id]


async def test_add_member_unknown_user_raises_user_not_found() -> None:
    organization = make_organization()
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,), organizations=(organization,))

    with pytest.raises(UserNotFoundError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(admin),
                organization_id=organization.id,
                user_id=make_user().id,
            )
        )


async def test_add_member_unknown_organization_raises_not_found() -> None:
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,))

    with pytest.raises(OrganizationNotFoundError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(admin),
                organization_id=make_organization().id,
                user_id=admin.id,
            )
        )


async def test_add_member_suspended_user_raises_user_suspended() -> None:
    organization = make_organization()
    admin, target = make_user(Role.ADMIN), make_user(is_suspended=True)
    _, factory = wire(users=(admin, target), organizations=(organization,))

    with pytest.raises(UserSuspendedError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(admin),
                organization_id=organization.id,
                user_id=target.id,
            )
        )


async def test_add_member_retired_organization_raises_not_active() -> None:
    organization = make_organization(status=OrganizationStatus.RETIRED)
    admin, target = make_user(Role.ADMIN), make_user()
    _, factory = wire(users=(admin, target), organizations=(organization,))

    with pytest.raises(OrganizationNotActiveError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(admin),
                organization_id=organization.id,
                user_id=target.id,
            )
        )


async def test_add_member_by_outsider_is_denied_before_reading() -> None:
    organization, admin, admin_membership = org_with_admin()
    outsider = make_user()
    _, factory = wire(
        users=(admin, outsider),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(PermissionDeniedError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=actor_of(outsider),
                organization_id=organization.id,
                user_id=outsider.id,
            )
        )

    assert factory.calls == 0


async def test_add_member_suspended_actor_is_refused() -> None:
    organization = make_organization()
    admin, target = make_user(Role.ADMIN, is_suspended=True), make_user()
    uow, factory = wire(users=(admin, target), organizations=(organization,))

    with pytest.raises(AccountSuspendedError):
        await AddMemberHandler(factory, clock(), ids())(
            AddMember(
                actor=Actor(user_id=admin.id, roles=admin.roles),
                organization_id=organization.id,
                user_id=target.id,
            )
        )

    assert uow.memberships.committed == {}


# --------------------------------------------------------------------------- #
# ChangeMemberRole                                                            #
# --------------------------------------------------------------------------- #


def change_role(
    actor: Actor,
    organization: Organization,
    user: User,
    role: OrganizationRole,
    version: int | None = None,
) -> ChangeMemberRole:
    """Return a change-role command."""
    return ChangeMemberRole(
        actor=actor,
        organization_id=organization.id,
        user_id=user.id,
        role=role,
        expected_version=version,
    )


async def test_change_member_role_promotes_member_and_commits_event() -> None:
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    membership = make_membership(
        organization, member, joined=EARLIER + timedelta(days=1)
    )
    uow, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, membership),
    )

    summary = await ChangeMemberRoleHandler(factory, clock(), ids())(
        change_role(
            actor_of(admin, admin_membership),
            organization,
            member,
            OrganizationRole.ADMIN,
            version=1,
        )
    )

    assert summary.role is OrganizationRole.ADMIN
    assert summary.version == 2
    assert uow.memberships.committed[membership.id].role is OrganizationRole.ADMIN
    (event,) = uow.committed_events
    assert isinstance(event, MembershipRoleChanged)
    assert event.previous_role is OrganizationRole.MEMBER


async def test_change_member_role_demoting_last_admin_raises() -> None:
    organization, admin, admin_membership = org_with_admin()
    uow, factory = wire(
        users=(admin,), organizations=(organization,), memberships=(admin_membership,)
    )

    with pytest.raises(LastOrganizationAdminError):
        await ChangeMemberRoleHandler(factory, clock(), ids())(
            change_role(
                actor_of(admin, admin_membership),
                organization,
                admin,
                OrganizationRole.MEMBER,
            )
        )

    assert uow.memberships.committed[admin_membership.id] == admin_membership


async def test_change_member_role_same_role_changes_nothing() -> None:
    organization, admin, admin_membership = org_with_admin()
    uow, factory = wire(
        users=(admin,), organizations=(organization,), memberships=(admin_membership,)
    )

    summary = await ChangeMemberRoleHandler(factory, clock(), ids())(
        change_role(
            actor_of(admin, admin_membership),
            organization,
            admin,
            OrganizationRole.ADMIN,
        )
    )

    assert summary.version == 1
    assert uow.committed_events == ()


async def test_change_member_role_non_member_raises_membership_not_found() -> None:
    organization, admin, admin_membership = org_with_admin()
    stranger = make_user()
    _, factory = wire(
        users=(admin, stranger),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(MembershipNotFoundError):
        await ChangeMemberRoleHandler(factory, clock(), ids())(
            change_role(
                actor_of(admin, admin_membership),
                organization,
                stranger,
                OrganizationRole.ADMIN,
            )
        )


async def test_change_member_role_stale_version_raises_precondition_failed() -> None:
    organization, admin, admin_membership = org_with_admin()
    _, factory = wire(
        users=(admin,), organizations=(organization,), memberships=(admin_membership,)
    )

    with pytest.raises(PreconditionFailedError):
        await ChangeMemberRoleHandler(factory, clock(), ids())(
            change_role(
                actor_of(admin, admin_membership),
                organization,
                admin,
                OrganizationRole.MEMBER,
                version=4,
            )
        )


async def test_change_member_role_by_plain_member_is_denied() -> None:
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    membership = make_membership(organization, member)
    _, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, membership),
    )

    with pytest.raises(PermissionDeniedError):
        await ChangeMemberRoleHandler(factory, clock(), ids())(
            change_role(
                actor_of(member, membership),
                organization,
                member,
                OrganizationRole.ADMIN,
            )
        )

    assert factory.calls == 0


async def test_change_member_role_missing_organization_raises_not_found() -> None:
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,))

    with pytest.raises(OrganizationNotFoundError):
        await ChangeMemberRoleHandler(factory, clock(), ids())(
            change_role(
                actor_of(admin), make_organization(), admin, OrganizationRole.ADMIN
            )
        )


# --------------------------------------------------------------------------- #
# RemoveMember                                                                #
# --------------------------------------------------------------------------- #


def remove(
    actor: Actor, organization: Organization, user: User, version: int | None = None
) -> RemoveMember:
    """Return a remove command."""
    return RemoveMember(
        actor=actor,
        organization_id=organization.id,
        user_id=user.id,
        expected_version=version,
    )


async def test_remove_member_commits_removal_and_event() -> None:
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    membership = make_membership(organization, member)
    uow, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, membership),
    )

    await RemoveMemberHandler(factory, clock(), ids())(
        remove(actor_of(admin, admin_membership), organization, member, version=1)
    )

    assert list(uow.memberships.committed) == [admin_membership.id]
    (event,) = uow.committed_events
    assert isinstance(event, MembershipRemoved)
    assert event.user_id == member.id


async def test_remove_member_last_admin_raises_and_keeps_membership() -> None:
    organization, admin, admin_membership = org_with_admin()
    uow, factory = wire(
        users=(admin,), organizations=(organization,), memberships=(admin_membership,)
    )

    with pytest.raises(LastOrganizationAdminError):
        await RemoveMemberHandler(factory, clock(), ids())(
            remove(actor_of(admin, admin_membership), organization, admin)
        )

    assert list(uow.memberships.committed) == [admin_membership.id]
    assert uow.committed_events == ()


async def test_remove_member_non_member_raises_membership_not_found() -> None:
    organization, admin, admin_membership = org_with_admin()
    stranger = make_user()
    _, factory = wire(
        users=(admin, stranger),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(MembershipNotFoundError):
        await RemoveMemberHandler(factory, clock(), ids())(
            remove(actor_of(admin, admin_membership), organization, stranger)
        )


async def test_remove_member_stale_version_raises_precondition_failed() -> None:
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    membership = make_membership(organization, member)
    uow, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, membership),
    )

    with pytest.raises(PreconditionFailedError):
        await RemoveMemberHandler(factory, clock(), ids())(
            remove(actor_of(admin, admin_membership), organization, member, version=2)
        )

    assert len(uow.memberships.committed) == 2


async def test_remove_member_by_outsider_is_denied() -> None:
    organization, admin, admin_membership = org_with_admin()
    outsider = make_user()
    _, factory = wire(
        users=(admin, outsider),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(PermissionDeniedError):
        await RemoveMemberHandler(factory, clock(), ids())(
            remove(actor_of(outsider), organization, admin)
        )

    assert factory.calls == 0


async def test_remove_member_missing_organization_raises_not_found() -> None:
    admin = make_user(Role.ADMIN)
    _, factory = wire(users=(admin,))

    with pytest.raises(OrganizationNotFoundError):
        await RemoveMemberHandler(factory, clock(), ids())(
            remove(actor_of(admin), make_organization(), admin)
        )


async def test_remove_member_suspended_actor_is_refused() -> None:
    organization, admin, admin_membership = org_with_admin()
    platform_admin = make_user(Role.ADMIN, is_suspended=True)
    uow, factory = wire(
        users=(admin, platform_admin),
        organizations=(organization,),
        memberships=(admin_membership,),
    )

    with pytest.raises(AccountSuspendedError):
        await RemoveMemberHandler(factory, clock(), ids())(
            remove(
                Actor(user_id=platform_admin.id, roles=platform_admin.roles),
                organization,
                admin,
            )
        )

    assert list(uow.memberships.committed) == [admin_membership.id]


async def test_change_member_role_tag_of_replaced_membership_raises_precondition() -> (
    None
):
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    current = make_membership(organization, member)
    uow, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, current),
    )
    # A tag for an earlier membership of the same user, at the same version.
    command = change_role(
        actor_of(admin, admin_membership),
        organization,
        member,
        OrganizationRole.ADMIN,
        version=current.version,
    ).model_copy(update={"expected_membership_id": admin_membership.id})

    with pytest.raises(PreconditionFailedError) as raised:
        await ChangeMemberRoleHandler(factory, clock(), ids())(command)

    assert raised.value.details == {"reason": "membership_replaced"}
    assert uow.memberships.committed[current.id] == current


async def test_remove_member_matching_membership_tag_removes_member() -> None:
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    current = make_membership(organization, member)
    uow, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, current),
    )
    command = remove(
        actor_of(admin, admin_membership), organization, member, version=1
    ).model_copy(update={"expected_membership_id": current.id})

    await RemoveMemberHandler(factory, clock(), ids())(command)

    assert list(uow.memberships.committed) == [admin_membership.id]


async def test_remove_member_tag_of_replaced_membership_raises_precondition() -> None:
    organization, admin, admin_membership = org_with_admin()
    member = make_user()
    current = make_membership(organization, member)
    uow, factory = wire(
        users=(admin, member),
        organizations=(organization,),
        memberships=(admin_membership, current),
    )
    command = remove(
        actor_of(admin, admin_membership), organization, member, version=1
    ).model_copy(update={"expected_membership_id": admin_membership.id})

    with pytest.raises(PreconditionFailedError):
        await RemoveMemberHandler(factory, clock(), ids())(command)

    assert len(uow.memberships.committed) == 2
