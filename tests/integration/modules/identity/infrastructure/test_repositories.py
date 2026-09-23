"""The SQLAlchemy identity repositories and unit of work against real PostGIS."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.identity import (
    MembershipTestFactory,
    OrganizationTestFactory,
    UserTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain.entities import (
    Membership,
    Memberships,
    Organization,
    User,
)
from yakhnama.modules.identity.domain.errors import (
    DuplicateMembershipError,
    MembershipNotFoundError,
    OrganizationNotFoundError,
    UserNotFoundError,
)
from yakhnama.modules.identity.domain.value_objects import (
    OrganizationRole,
    Role,
)
from yakhnama.modules.identity.infrastructure.orm import UserRow
from yakhnama.modules.identity.infrastructure.uow import SqlAlchemyIdentityUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvariantViolationError,
    NotFoundError,
)

pytestmark = pytest.mark.integration

type IdentityFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyIdentityUnitOfWork]

# Before the test clock's start (2026-09-23T08:00Z), so every change the clock stamps
# is later than creation.
CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)
# Long enough for a blocked lock request to be observed, short enough for the suite.
LOCK_WAIT_SECONDS: Final = 0.5
LOCK_TIMEOUT_SECONDS: Final = 10.0


def _user(**overrides: object) -> User:
    # model_validate rather than model_copy, so an override is validated like any
    # stored state would be.
    built = UserTestFactory.build(created_at=CREATED)
    return User.model_validate({**dict(built), **overrides})


def _organization(**overrides: object) -> Organization:
    built = OrganizationTestFactory.build(created_at=CREATED)
    return Organization.model_validate({**dict(built), **overrides})


def _membership(
    organization: Organization,
    user: User,
    *,
    created_at: datetime = CREATED,
    role: OrganizationRole = OrganizationRole.MEMBER,
) -> Membership:
    return MembershipTestFactory.build(
        organization_id=organization.id,
        user_id=user.id,
        created_at=created_at,
        role=role,
    )


async def _store(
    factory: IdentityFactory,
    *,
    users: tuple[User, ...] = (),
    organizations: tuple[Organization, ...] = (),
    memberships: tuple[Membership, ...] = (),
) -> None:
    async with factory() as uow:
        for user in users:
            await uow.users.add(user)
        for organization in organizations:
            await uow.organizations.add(organization)
        for membership in memberships:
            await uow.memberships.add(membership)
        await uow.commit()


async def _get_user(factory: IdentityFactory, user: User) -> User | None:
    async with factory() as uow:
        return await uow.users.get(user.id)


# --------------------------------------------------------------------------- #
# Users                                                                       #
# --------------------------------------------------------------------------- #


async def test_user_repository_add_then_get_returns_equal_user(
    identity_uow_factory: IdentityFactory,
) -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.MODERATOR}), display_name=None)

    await _store(identity_uow_factory, users=(user,))
    async with identity_uow_factory() as uow:
        by_id = await uow.users.get(user.id)
        by_identity = await uow.users.get_by_identity(user.external_identity)

    assert by_id == user
    assert by_identity == user


async def test_user_repository_add_stores_roles_as_sorted_json_array(
    identity_uow_factory: IdentityFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = _user(roles=frozenset({Role.MODERATOR, Role.CITIZEN, Role.ADMIN}))

    await _store(identity_uow_factory, users=(user,))
    async with session_factory() as session:
        stored = await session.scalar(
            select(UserRow.roles).where(UserRow.id == user.id)
        )

    assert stored == ["admin", "citizen", "moderator"]


async def test_user_repository_get_by_identity_compares_subject_exactly(
    identity_uow_factory: IdentityFactory,
) -> None:
    user = _user(subject="Case-Sensitive-Subject")
    await _store(identity_uow_factory, users=(user,))
    other_case = user.external_identity.model_copy(
        update={"subject": "case-sensitive-subject"}
    )

    async with identity_uow_factory() as uow:
        found = await uow.users.get_by_identity(other_case)

    assert found is None


async def test_user_repository_get_unknown_id_returns_none(
    identity_uow_factory: IdentityFactory,
) -> None:
    async with identity_uow_factory() as uow:
        found = await uow.users.get(_user().id)

    assert found is None


async def test_user_repository_add_same_identity_raises_conflict_and_keeps_uow_usable(
    identity_uow_factory: IdentityFactory,
) -> None:
    first = _user()
    await _store(identity_uow_factory, users=(first,))
    twin = _user(issuer=first.issuer, subject=first.subject)
    unrelated = _user()

    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError) as raised:
            await uow.users.add(twin)
        await uow.users.add(unrelated)
        await uow.commit()

    assert first.subject not in str(raised.value)
    assert await _get_user(identity_uow_factory, unrelated) == unrelated
    assert await _get_user(identity_uow_factory, twin) is None


async def test_user_repository_add_same_id_raises_conflict(
    identity_uow_factory: IdentityFactory,
) -> None:
    first = _user()
    await _store(identity_uow_factory, users=(first,))

    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.users.add(_user(id=first.id))


async def test_user_repository_save_next_version_persists_change(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    user = _user()
    await _store(identity_uow_factory, users=(user,))
    renamed = user.rename("Renamed test user", clock=clock, ids=ids).state
    promoted = renamed.grant_role(Role.MODERATOR, clock=clock, ids=ids).state

    async with identity_uow_factory() as uow:
        await uow.users.save(renamed)
        await uow.users.save(promoted)
        await uow.commit()

    assert await _get_user(identity_uow_factory, user) == promoted
    assert promoted.version == user.version + 2


async def test_user_repository_save_stale_version_raises_conflict(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    user = _user()
    await _store(identity_uow_factory, users=(user,))
    winner = user.rename("First writer", clock=clock, ids=ids).state
    loser = user.rename("Second writer", clock=clock, ids=ids).state
    async with identity_uow_factory() as uow:
        await uow.users.save(winner)
        await uow.commit()

    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError) as raised:
            await uow.users.save(loser)

    assert raised.value.details["stored_version"] == winner.version
    assert await _get_user(identity_uow_factory, user) == winner


async def test_user_repository_save_unknown_user_raises_not_found(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    never_stored = _user().rename("Nobody", clock=clock, ids=ids).state

    async with identity_uow_factory() as uow:
        with pytest.raises(UserNotFoundError):
            await uow.users.save(never_stored)


async def test_user_repository_touch_updates_only_last_seen_at(
    identity_uow_factory: IdentityFactory, clock: SteppingClock
) -> None:
    user = _user()
    await _store(identity_uow_factory, users=(user,))
    touched = user.touch(clock=clock).state

    async with identity_uow_factory() as uow:
        await uow.users.touch(touched)
        await uow.commit()
    stored = await _get_user(identity_uow_factory, user)

    assert stored == user.model_copy(update={"last_seen_at": touched.last_seen_at})
    assert stored is not None
    assert stored.version == user.version
    assert stored.updated_at == user.updated_at


async def test_user_repository_touch_with_older_instant_keeps_later_last_seen_at(
    identity_uow_factory: IdentityFactory, clock: SteppingClock
) -> None:
    user = _user()
    await _store(identity_uow_factory, users=(user,))
    earlier = user.touch(clock=clock).state
    later = user.touch(clock=clock).state
    async with identity_uow_factory() as uow:
        await uow.users.touch(later)
        await uow.commit()

    async with identity_uow_factory() as uow:
        await uow.users.touch(earlier)
        await uow.commit()
    stored = await _get_user(identity_uow_factory, user)

    assert stored is not None
    assert stored.last_seen_at == later.last_seen_at


async def test_user_repository_touch_does_not_conflict_with_a_later_save(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    user = _user()
    await _store(identity_uow_factory, users=(user,))
    renamed = user.rename("Renamed after load", clock=clock, ids=ids).state
    touched = user.touch(clock=clock).state
    async with identity_uow_factory() as uow:
        await uow.users.touch(touched)
        await uow.commit()

    async with identity_uow_factory() as uow:
        await uow.users.save(renamed)
        await uow.commit()
    stored = await _get_user(identity_uow_factory, user)

    assert stored is not None
    assert stored.display_name == "Renamed after load"
    assert stored.last_seen_at == touched.last_seen_at


async def test_user_repository_touch_unknown_user_raises_not_found(
    identity_uow_factory: IdentityFactory, clock: SteppingClock
) -> None:
    never_stored = _user().touch(clock=clock).state

    async with identity_uow_factory() as uow:
        with pytest.raises(UserNotFoundError):
            await uow.users.touch(never_stored)


async def test_user_repository_add_without_commit_is_rolled_back(
    identity_uow_factory: IdentityFactory,
) -> None:
    user = _user()

    async with identity_uow_factory() as uow:
        await uow.users.add(user)
        seen_inside = await uow.users.get(user.id)

    assert seen_inside == user
    assert await _get_user(identity_uow_factory, user) is None


# --------------------------------------------------------------------------- #
# Organisations                                                               #
# --------------------------------------------------------------------------- #


async def test_organization_repository_add_then_get_returns_equal_organization(
    identity_uow_factory: IdentityFactory,
) -> None:
    organization = _organization()

    await _store(identity_uow_factory, organizations=(organization,))
    async with identity_uow_factory() as uow:
        by_id = await uow.organizations.get(organization.id)
        by_slug = await uow.organizations.get_by_slug(organization.slug)
        unknown = await uow.organizations.get_by_slug("no-such-slug")

    assert by_id == organization
    assert by_slug == organization
    assert unknown is None


async def test_organization_repository_add_same_slug_raises_conflict(
    identity_uow_factory: IdentityFactory,
) -> None:
    first = _organization()
    await _store(identity_uow_factory, organizations=(first,))

    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.organizations.add(_organization(slug=first.slug))


async def test_organization_repository_save_persists_suspension(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    organization = _organization()
    await _store(identity_uow_factory, organizations=(organization,))
    suspended = organization.suspend("Test suspension", clock=clock, ids=ids).state

    async with identity_uow_factory() as uow:
        await uow.organizations.save(suspended)
        await uow.commit()
    async with identity_uow_factory() as uow:
        stored = await uow.organizations.get(organization.id)

    assert stored == suspended


async def test_organization_repository_save_stale_version_raises_conflict(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    organization = _organization()
    await _store(identity_uow_factory, organizations=(organization,))
    winner = organization.rename("First name", clock=clock, ids=ids).state
    loser = organization.rename("Second name", clock=clock, ids=ids).state
    async with identity_uow_factory() as uow:
        await uow.organizations.save(winner)
        await uow.commit()

    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.organizations.save(loser)


async def test_organization_repository_save_taken_slug_raises_conflict(
    identity_uow_factory: IdentityFactory,
) -> None:
    first = _organization()
    second = _organization()
    await _store(identity_uow_factory, organizations=(first, second))
    # No domain method changes a slug yet; the adapter must still refuse a duplicate.
    colliding = second.model_copy(
        update={"slug": first.slug, "version": second.version + 1}
    )

    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.organizations.save(colliding)
        still_usable = await uow.organizations.get(second.id)

    assert still_usable == second


async def test_organization_repository_save_unknown_raises_not_found(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    never_stored = _organization().rename("Nobody", clock=clock, ids=ids).state

    async with identity_uow_factory() as uow:
        with pytest.raises(OrganizationNotFoundError):
            await uow.organizations.save(never_stored)


# --------------------------------------------------------------------------- #
# Memberships                                                                 #
# --------------------------------------------------------------------------- #


async def test_membership_repository_lists_in_joining_order(
    identity_uow_factory: IdentityFactory,
) -> None:
    organization, other = _organization(), _organization()
    first, second = _user(), _user()
    later = _membership(organization, first, created_at=CREATED + timedelta(hours=1))
    earlier = _membership(organization, second)
    elsewhere = _membership(other, first)
    await _store(
        identity_uow_factory,
        users=(first, second),
        organizations=(organization, other),
        memberships=(later, earlier, elsewhere),
    )

    async with identity_uow_factory() as uow:
        for_organization = await uow.memberships.list_for_organization(organization.id)
        for_user = await uow.memberships.list_for_user(first.id)

    assert for_organization == Memberships(
        organization_id=organization.id, members=(earlier, later)
    )
    assert for_user == (elsewhere, later)


async def test_membership_repository_list_for_unknown_organization_is_empty(
    identity_uow_factory: IdentityFactory,
) -> None:
    unknown = _organization()

    async with identity_uow_factory() as uow:
        listed = await uow.memberships.list_for_organization(unknown.id)

    assert listed == Memberships(organization_id=unknown.id)


async def test_membership_repository_add_same_member_raises_duplicate_membership(
    identity_uow_factory: IdentityFactory,
) -> None:
    organization, user = _organization(), _user()
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(_membership(organization, user),),
    )

    async with identity_uow_factory() as uow:
        with pytest.raises(DuplicateMembershipError):
            await uow.memberships.add(_membership(organization, user))


async def test_membership_repository_add_for_unknown_user_raises_integrity_error(
    identity_uow_factory: IdentityFactory,
) -> None:
    organization = _organization()
    await _store(identity_uow_factory, organizations=(organization,))

    async with identity_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.memberships.add(_membership(organization, _user()))


async def test_membership_repository_save_role_change_then_stale_save_conflicts(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    organization, user = _organization(), _user()
    membership = _membership(organization, user)
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(membership,),
    )
    promoted = membership.change_role(OrganizationRole.ADMIN, clock=clock, ids=ids)

    async with identity_uow_factory() as uow:
        await uow.memberships.save(promoted.state)
        await uow.commit()
    async with identity_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.memberships.save(promoted.state)
        stored = await uow.memberships.list_for_user(user.id)

    assert stored == (promoted.state,)


async def test_membership_repository_save_unknown_raises_membership_not_found(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    membership = _membership(_organization(), _user())
    promoted = membership.change_role(OrganizationRole.ADMIN, clock=clock, ids=ids)

    async with identity_uow_factory() as uow:
        with pytest.raises(MembershipNotFoundError):
            await uow.memberships.save(promoted.state)


async def test_membership_repository_remove_deletes_and_second_remove_not_found(
    identity_uow_factory: IdentityFactory,
) -> None:
    organization, user = _organization(), _user()
    membership = _membership(organization, user)
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(membership,),
    )

    async with identity_uow_factory() as uow:
        await uow.memberships.remove(membership.id)
        await uow.commit()
    async with identity_uow_factory() as uow:
        with pytest.raises(NotFoundError):
            await uow.memberships.remove(membership.id)
        remaining = await uow.memberships.list_for_user(user.id)

    assert remaining == ()


async def test_membership_rows_are_deleted_with_their_organization(
    identity_uow_factory: IdentityFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    organization, user = _organization(), _user()
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(_membership(organization, user),),
    )

    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM organizations WHERE id = :id"), {"id": organization.id}
        )
        await session.commit()
    async with identity_uow_factory() as uow:
        remaining = await uow.memberships.list_for_user(user.id)

    assert remaining == ()


async def test_membership_repository_list_for_organization_locks_until_commit(
    identity_uow_factory: IdentityFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    organization, user = _organization(), _user()
    membership = _membership(organization, user)
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(membership,),
    )
    promoted = membership.change_role(OrganizationRole.ADMIN, clock=clock, ids=ids)
    second_listing_started = asyncio.Event()

    async def list_in_second_transaction() -> Memberships:
        async with identity_uow_factory() as uow:
            second_listing_started.set()
            return await uow.memberships.list_for_organization(organization.id)

    async with identity_uow_factory() as first:
        await first.memberships.list_for_organization(organization.id)
        second = asyncio.create_task(list_in_second_transaction())
        await second_listing_started.wait()
        await asyncio.sleep(LOCK_WAIT_SECONDS)
        is_blocked_while_first_open = not second.done()
        await first.memberships.save(promoted.state)
        await first.commit()
    seen_by_second = await asyncio.wait_for(second, timeout=LOCK_TIMEOUT_SECONDS)

    assert is_blocked_while_first_open
    assert seen_by_second.members == (promoted.state,)


async def test_membership_repository_list_for_user_does_not_lock(
    identity_uow_factory: IdentityFactory,
) -> None:
    organization, user = _organization(), _user()
    membership = _membership(organization, user)
    await _store(
        identity_uow_factory,
        users=(user,),
        organizations=(organization,),
        memberships=(membership,),
    )

    async with identity_uow_factory() as first:
        await first.memberships.list_for_organization(organization.id)
        async with identity_uow_factory() as second:
            listed = await asyncio.wait_for(
                second.memberships.list_for_user(user.id),
                timeout=LOCK_TIMEOUT_SECONDS,
            )

    assert listed == (membership,)


# --------------------------------------------------------------------------- #
# Unit of work                                                                #
# --------------------------------------------------------------------------- #


def test_identity_unit_of_work_repositories_outside_async_with_raise(
    identity_uow_factory: IdentityFactory,
) -> None:
    uow = identity_uow_factory()

    with pytest.raises(InvariantViolationError):
        _ = uow.users
    with pytest.raises(InvariantViolationError):
        _ = uow.organizations
    with pytest.raises(InvariantViolationError):
        _ = uow.memberships
