"""Unit tests for ``SeedAccountsHandler`` and ``CanReportOnBehalf``, with fakes only."""

from uuid import UUID

import pytest

from tests.fakes.identity import actor_with
from tests.unit.modules.identity.application.support import (
    NOW,
    clock,
    identity,
    ids,
    make_membership,
    make_organization,
    make_user,
    wire,
)
from yakhnama.modules.identity.application.commands import (
    SeedAccount,
    SeedAccounts,
    SeedOrganization,
)
from yakhnama.modules.identity.application.handlers import SeedAccountsHandler
from yakhnama.modules.identity.domain.errors import OrganizationSlugTakenError
from yakhnama.modules.identity.domain.events import (
    MembershipAdded,
    OrganizationCreated,
    UserMirrored,
    UserRoleGranted,
)
from yakhnama.modules.identity.domain.policies import CanReportOnBehalf
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    OrganizationRole,
    OrganizationType,
    Role,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError

ORGANIZATION_ID = UUID("0199b2a0-0000-7000-8000-0000000000de")
OTHER_ORGANIZATION_ID = UUID("0199b2a0-0000-7000-8000-0000000000df")
SYSTEM = actor_with({Role.ADMIN})
SEEDED_ORGANIZATION = SeedOrganization(
    organization_id=ORGANIZATION_ID,
    slug="demo-organisation-dev",
    name="Demo organisation (development only, not real)",
    organization_type=OrganizationType.OTHER,
)
TRUSTED = SeedAccount(
    identity=identity("trusted-subject"),
    roles=frozenset({Role.TRUSTED_REPORTER}),
)
MEMBER = SeedAccount(
    identity=identity("member-subject"),
    roles=frozenset({Role.ORG_MEMBER}),
    organization_role=OrganizationRole.MEMBER,
)


def command(actor: Actor = SYSTEM, *, dry_run: bool = False) -> SeedAccounts:
    """Return the seed command for both accounts."""
    return SeedAccounts(
        actor=actor,
        organization=SEEDED_ORGANIZATION,
        accounts=(TRUSTED, MEMBER),
        dry_run=dry_run,
    )


async def test_seed_accounts_empty_store_creates_users_org_and_membership() -> None:
    uow, factory = wire()

    report = await SeedAccountsHandler(factory, clock(), ids())(command())

    users = {user.subject: user for user in uow.users.committed.values()}
    organization = uow.organizations.committed[ORGANIZATION_ID]
    members = await uow.memberships.list_for_organization(ORGANIZATION_ID)
    assert report.users_created == 2
    assert report.is_organization_created is True
    assert report.memberships_created == 1
    assert report.roles_granted == 0
    assert users["trusted-subject"].roles == {Role.CITIZEN, Role.TRUSTED_REPORTER}
    assert users["member-subject"].roles == {Role.CITIZEN, Role.ORG_MEMBER}
    assert organization.slug == "demo-organisation-dev"
    assert [m.user_id for m in members.members] == [users["member-subject"].id]
    assert {type(event) for event in uow.committed_events} == {
        UserMirrored,
        OrganizationCreated,
        MembershipAdded,
    }


async def test_seed_accounts_second_run_changes_nothing() -> None:
    uow, factory = wire()
    handler = SeedAccountsHandler(factory, clock(), ids())
    await handler(command())
    events_before = len(uow.committed_events)

    report = await handler(command())

    assert report.is_unchanged is True
    assert len(uow.committed_events) == events_before


async def test_seed_accounts_existing_user_gains_only_missing_roles() -> None:
    existing = make_user(subject="member-subject")
    uow, factory = wire(users=(existing,))

    report = await SeedAccountsHandler(factory, clock(), ids())(command())

    updated = uow.users.committed[existing.id]
    assert report.users_created == 1
    assert report.roles_granted == 1
    assert updated.roles == {Role.CITIZEN, Role.ORG_MEMBER}
    assert updated.updated_at == NOW
    assert any(isinstance(event, UserRoleGranted) for event in uow.committed_events)


async def test_seed_accounts_existing_membership_is_kept() -> None:
    user = make_user(Role.ORG_MEMBER, subject="member-subject")
    organization = make_organization("demo-organisation-dev").model_copy(
        update={"id": ORGANIZATION_ID}
    )
    membership = make_membership(organization, user)
    _, factory = wire(
        users=(user,), organizations=(organization,), memberships=(membership,)
    )

    report = await SeedAccountsHandler(factory, clock(), ids())(command())

    assert report.is_organization_created is False
    assert report.memberships_created == 0


async def test_seed_accounts_slug_taken_by_another_organisation_raises() -> None:
    other = make_organization("demo-organisation-dev").model_copy(
        update={"id": OTHER_ORGANIZATION_ID}
    )
    uow, factory = wire(organizations=(other,))

    with pytest.raises(OrganizationSlugTakenError):
        await SeedAccountsHandler(factory, clock(), ids())(command())

    assert uow.users.committed == {}


async def test_seed_accounts_dry_run_commits_nothing() -> None:
    uow, factory = wire()

    report = await SeedAccountsHandler(factory, clock(), ids())(command(dry_run=True))

    assert report.users_created == 2
    assert uow.users.committed == {}
    assert uow.organizations.committed == {}


async def test_seed_accounts_non_admin_is_denied_before_reading() -> None:
    uow, factory = wire()

    with pytest.raises(PermissionDeniedError):
        await SeedAccountsHandler(factory, clock(), ids())(command(actor_with()))

    assert uow.commit_count == 0


@pytest.mark.parametrize(
    ("actor", "organization_id", "expected"),
    [
        (actor_with({Role.TRUSTED_REPORTER}), None, True),
        (actor_with({Role.MODERATOR}), None, True),
        (actor_with({Role.ADMIN}), None, True),
        (actor_with(), None, False),
        (actor_with({Role.ORG_MEMBER}), None, False),
        (
            actor_with(
                {Role.ORG_MEMBER},
                memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)},
            ),
            ORGANIZATION_ID,
            True,
        ),
        (
            actor_with(
                {Role.ORG_MEMBER},
                memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)},
            ),
            OTHER_ORGANIZATION_ID,
            False,
        ),
        (
            actor_with(memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}),
            ORGANIZATION_ID,
            False,
        ),
        (Actor(), None, False),
    ],
)
def test_can_report_on_behalf_allows_trusted_moderators_and_own_org_members(
    actor: Actor, organization_id: UUID | None, *, expected: bool
) -> None:
    policy = CanReportOnBehalf(organization_id)

    allowed = policy.is_allowed(actor)

    assert allowed is expected
    assert policy.organization_id == organization_id
