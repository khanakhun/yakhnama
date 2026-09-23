"""Unit tests for ``yakhnama.modules.identity.domain.entities``."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.identity import (
    MembershipTestFactory,
    OrganizationTestFactory,
    UserTestFactory,
)
from tests.fakes.clock import FrozenClock, SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain.entities import (
    Membership,
    Memberships,
    Organization,
    User,
)
from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    CitizenRoleRequiredError,
    DuplicateMembershipError,
    LastOrganizationAdminError,
    MembershipNotFoundError,
    OrganizationNotActiveError,
    OrganizationNotSuspendedError,
    RoleNotHeldError,
    UserNotSuspendedError,
    UserSuspendedError,
)
from yakhnama.modules.identity.domain.events import (
    MembershipAdded,
    MembershipRemoved,
    MembershipRoleChanged,
    OrganizationReinstated,
    OrganizationRenamed,
    OrganizationRetired,
    OrganizationSuspended,
    UserReinstated,
    UserRenamed,
    UserRoleGranted,
    UserRoleRevoked,
    UserSuspended,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    ExternalIdentity,
    MembershipRef,
    OrganizationRole,
    OrganizationStatus,
    Role,
    UserStatus,
)
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.events import AggregateChange

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CHANGED_AT = datetime(2026, 9, 2, tzinfo=UTC)


def _clock() -> SteppingClock:
    return SteppingClock(CHANGED_AT, timedelta(seconds=1))


def _user(**fields: object) -> User:
    return UserTestFactory.build(
        factory_use_construct=False, created_at=CREATED_AT, **fields
    )


def _suspended_user() -> User:
    return _user(status=UserStatus.SUSPENDED, status_reason="Test reason")


def _organization(**fields: object) -> Organization:
    return OrganizationTestFactory.build(
        factory_use_construct=False, created_at=CREATED_AT, **fields
    )


# --------------------------------------------------------------------------- #
# User invariants                                                             #
# --------------------------------------------------------------------------- #


def test_user_active_without_citizen_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="citizen"):
        _user(roles=frozenset({Role.MODERATOR}))


def test_user_suspended_without_citizen_is_accepted() -> None:
    user = _user(
        roles=frozenset(), status=UserStatus.SUSPENDED, status_reason="Test reason"
    )

    assert user.roles == frozenset()


@pytest.mark.parametrize(
    ("status", "reason"),
    [(UserStatus.ACTIVE, "Test reason"), (UserStatus.SUSPENDED, None)],
)
def test_user_status_reason_mismatch_is_rejected(
    status: UserStatus, reason: str | None
) -> None:
    with pytest.raises(PydanticValidationError, match="status_reason"):
        _user(status=status, status_reason=reason)


@pytest.mark.parametrize("field", ["updated_at", "last_seen_at"])
def test_user_timestamp_before_created_at_is_rejected(field: str) -> None:
    with pytest.raises(PydanticValidationError, match="earlier than created_at"):
        _user(**{field: CREATED_AT - timedelta(seconds=1)})


def test_user_timestamps_are_normalised_to_utc() -> None:
    plus_five = timezone(timedelta(hours=5))

    user = UserTestFactory.build(
        created_at=datetime(2026, 9, 1, 5, tzinfo=plus_five),
        updated_at=datetime(2026, 9, 1, 5, tzinfo=plus_five),
        last_seen_at=datetime(2026, 9, 1, 5, tzinfo=plus_five),
    )

    assert user.created_at == CREATED_AT
    assert user.created_at.tzinfo is UTC
    assert user.updated_at.tzinfo is UTC
    assert user.last_seen_at.tzinfo is UTC


def test_user_naive_timestamp_is_rejected() -> None:
    naive = datetime(2026, 9, 1)  # noqa: DTZ001  # reason: asserting naive datetimes are rejected

    with pytest.raises(PydanticValidationError):
        UserTestFactory.build(created_at=naive, updated_at=naive, last_seen_at=naive)


def test_user_is_frozen() -> None:
    user = _user()

    with pytest.raises(PydanticValidationError):
        user.display_name = "Other"  # type: ignore[misc]  # reason: asserting frozen models reject assignment


def test_user_external_identity_returns_issuer_and_subject() -> None:
    user = _user()

    result = user.external_identity

    assert result == ExternalIdentity(issuer=user.issuer, subject=user.subject)


# --------------------------------------------------------------------------- #
# User.to_actor                                                               #
# --------------------------------------------------------------------------- #


def test_user_to_actor_carries_roles_and_memberships() -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.MODERATOR}))
    membership = MembershipTestFactory.build(
        user_id=user.id, role=OrganizationRole.ADMIN
    )

    actor = user.to_actor([membership])

    assert actor == Actor(
        user_id=user.id,
        roles=frozenset({Role.CITIZEN, Role.MODERATOR}),
        memberships=frozenset({(membership.organization_id, OrganizationRole.ADMIN)}),
    )


def test_user_to_actor_without_memberships_has_none() -> None:
    user = _user()

    actor = user.to_actor()

    assert actor.memberships == frozenset()
    assert actor.is_authenticated


def test_user_to_actor_suspended_user_raises_account_suspended() -> None:
    user = _suspended_user()

    with pytest.raises(AccountSuspendedError) as caught:
        user.to_actor()

    assert caught.value.details == {"user_id": str(user.id)}


def test_user_to_actor_foreign_membership_raises_invariant_violation() -> None:
    user = _user()
    membership = MembershipTestFactory.build()

    with pytest.raises(InvariantViolationError, match="another user"):
        user.to_actor([membership])


def test_user_to_actor_two_memberships_in_one_organisation_is_rejected() -> None:
    user = _user()
    first = MembershipTestFactory.build(user_id=user.id)
    second = MembershipTestFactory.build(
        user_id=user.id,
        organization_id=first.organization_id,
        role=OrganizationRole.ADMIN,
    )

    with pytest.raises(PydanticValidationError, match="one role per organisation"):
        user.to_actor([first, second])


# --------------------------------------------------------------------------- #
# User.touch                                                                  #
# --------------------------------------------------------------------------- #


def test_user_touch_later_instant_moves_only_last_seen_at() -> None:
    user = _user()

    change = user.touch(clock=FrozenClock(CHANGED_AT))

    assert change.state.last_seen_at == CHANGED_AT
    assert change.state.version == user.version
    assert change.state.updated_at == user.updated_at
    assert change.events == ()


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=-1)])
def test_user_touch_same_or_earlier_instant_leaves_user_unchanged(
    offset: timedelta,
) -> None:
    user = _user(last_seen_at=CHANGED_AT)

    change = user.touch(clock=FrozenClock(CHANGED_AT + offset))

    assert change.state is user


def test_user_touch_suspended_user_is_recorded() -> None:
    user = _suspended_user()

    change = user.touch(clock=FrozenClock(CHANGED_AT))

    assert change.state.last_seen_at == CHANGED_AT


# --------------------------------------------------------------------------- #
# User.rename                                                                 #
# --------------------------------------------------------------------------- #


def test_user_rename_new_name_bumps_version_and_emits_renamed() -> None:
    user = _user(display_name="Old name")
    ids = SequentialIdGenerator()

    change = user.rename("  New name  ", clock=_clock(), ids=ids)

    assert change.state.display_name == "New name"
    assert change.state.version == 2
    assert change.state.updated_at == CHANGED_AT
    assert user.display_name == "Old name"
    (event,) = change.events
    assert isinstance(event, UserRenamed)
    assert event.has_display_name is True
    assert event.version == 2
    assert event.aggregate_id == user.id
    assert event.event_id == ids.issued[0]


def test_user_rename_to_none_clears_name() -> None:
    user = _user(display_name="Old name")

    change = user.rename(None, clock=_clock(), ids=SequentialIdGenerator())

    assert change.state.display_name is None
    (event,) = change.events
    assert isinstance(event, UserRenamed)
    assert event.has_display_name is False


@pytest.mark.parametrize(
    ("current", "requested"), [("Same name", " Same name "), (None, None)]
)
def test_user_rename_same_name_is_a_no_op(
    current: str | None, requested: str | None
) -> None:
    user = _user(display_name=current)

    change = user.rename(requested, clock=_clock(), ids=SequentialIdGenerator())

    assert change.state is user
    assert change.events == ()


def test_user_rename_invalid_name_is_rejected() -> None:
    user = _user()

    with pytest.raises(PydanticValidationError):
        user.rename("   ", clock=_clock(), ids=SequentialIdGenerator())


def test_user_rename_suspended_user_raises_user_suspended() -> None:
    user = _suspended_user()

    with pytest.raises(UserSuspendedError):
        user.rename("New name", clock=_clock(), ids=SequentialIdGenerator())


# --------------------------------------------------------------------------- #
# User roles                                                                  #
# --------------------------------------------------------------------------- #


def test_user_grant_role_adds_role_and_emits_granted() -> None:
    user = _user()

    change = user.grant_role(
        Role.MODERATOR, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.roles == frozenset({Role.CITIZEN, Role.MODERATOR})
    assert change.state.version == 2
    (event,) = change.events
    assert isinstance(event, UserRoleGranted)
    assert event.role is Role.MODERATOR


def test_user_grant_role_already_held_is_a_no_op() -> None:
    user = _user()

    change = user.grant_role(Role.CITIZEN, clock=_clock(), ids=SequentialIdGenerator())

    assert change.state is user
    assert change.events == ()


def test_user_grant_role_implied_role_is_stored_explicitly() -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.ADMIN}))

    change = user.grant_role(
        Role.MODERATOR, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert Role.MODERATOR in change.state.roles
    assert len(change.events) == 1


def test_user_grant_role_suspended_user_raises_user_suspended() -> None:
    user = _suspended_user()

    with pytest.raises(UserSuspendedError):
        user.grant_role(Role.ADMIN, clock=_clock(), ids=SequentialIdGenerator())


def test_user_revoke_role_removes_role_and_emits_revoked() -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.ADMIN}))

    change = user.revoke_role(Role.ADMIN, clock=_clock(), ids=SequentialIdGenerator())

    assert change.state.roles == frozenset({Role.CITIZEN})
    (event,) = change.events
    assert isinstance(event, UserRoleRevoked)
    assert event.role is Role.ADMIN


def test_user_revoke_role_citizen_raises_citizen_role_required() -> None:
    user = _user()

    with pytest.raises(CitizenRoleRequiredError):
        user.revoke_role(Role.CITIZEN, clock=_clock(), ids=SequentialIdGenerator())


def test_user_revoke_role_implied_only_raises_role_not_held() -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.ADMIN}))

    with pytest.raises(RoleNotHeldError) as caught:
        user.revoke_role(Role.MODERATOR, clock=_clock(), ids=SequentialIdGenerator())

    assert caught.value.details["role"] == "moderator"


def test_user_revoke_role_suspended_user_raises_user_suspended() -> None:
    user = _suspended_user()

    with pytest.raises(UserSuspendedError):
        user.revoke_role(Role.ADMIN, clock=_clock(), ids=SequentialIdGenerator())


# --------------------------------------------------------------------------- #
# User suspension                                                             #
# --------------------------------------------------------------------------- #


def test_user_suspend_keeps_roles_and_records_reason_on_the_user_only() -> None:
    user = _user(roles=frozenset({Role.CITIZEN, Role.MODERATOR}))

    change = user.suspend(
        "  Test reason  ", clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.status is UserStatus.SUSPENDED
    assert change.state.status_reason == "Test reason"
    assert change.state.roles == user.roles
    (event,) = change.events
    assert isinstance(event, UserSuspended)
    assert "Test reason" not in event.model_dump_json()


def test_user_suspend_already_suspended_raises_user_suspended() -> None:
    user = _suspended_user()

    with pytest.raises(UserSuspendedError):
        user.suspend("Again", clock=_clock(), ids=SequentialIdGenerator())


def test_user_suspend_empty_reason_is_rejected() -> None:
    user = _user()

    with pytest.raises(PydanticValidationError):
        user.suspend("  ", clock=_clock(), ids=SequentialIdGenerator())


def test_user_reinstate_restores_active_status_and_citizen() -> None:
    user = _user(
        roles=frozenset({Role.MODERATOR}),
        status=UserStatus.SUSPENDED,
        status_reason="Test reason",
    )

    change = user.reinstate(clock=_clock(), ids=SequentialIdGenerator())

    assert change.state.status is UserStatus.ACTIVE
    assert change.state.status_reason is None
    assert change.state.roles == frozenset({Role.CITIZEN, Role.MODERATOR})
    (event,) = change.events
    assert isinstance(event, UserReinstated)


def test_user_reinstate_active_user_raises_user_not_suspended() -> None:
    user = _user()

    with pytest.raises(UserNotSuspendedError):
        user.reinstate(clock=_clock(), ids=SequentialIdGenerator())


def test_user_change_with_clock_behind_created_at_is_rejected() -> None:
    user = _user()
    clock = FrozenClock(CREATED_AT - timedelta(days=1))

    with pytest.raises(PydanticValidationError, match="earlier than created_at"):
        user.grant_role(Role.MODERATOR, clock=clock, ids=SequentialIdGenerator())


# --------------------------------------------------------------------------- #
# Organization                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (OrganizationStatus.ACTIVE, "Test reason"),
        (OrganizationStatus.SUSPENDED, None),
        (OrganizationStatus.RETIRED, None),
    ],
)
def test_organization_status_reason_mismatch_is_rejected(
    status: OrganizationStatus, reason: str | None
) -> None:
    with pytest.raises(PydanticValidationError, match="status_reason"):
        _organization(status=status, status_reason=reason)


def test_organization_updated_before_created_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="earlier than created_at"):
        _organization(updated_at=CREATED_AT - timedelta(seconds=1))


def test_organization_rename_new_name_emits_renamed_without_the_name() -> None:
    organization = _organization(name="Old name")

    change = organization.rename(
        " New name ", clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.name == "New name"
    assert change.state.version == 2
    (event,) = change.events
    assert isinstance(event, OrganizationRenamed)
    assert event.slug == organization.slug
    assert "New name" not in event.model_dump_json()


def test_organization_rename_same_name_is_a_no_op() -> None:
    organization = _organization(name="Same name")

    change = organization.rename(
        "Same name", clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state is organization
    assert change.events == ()


@pytest.mark.parametrize(
    "status", [OrganizationStatus.SUSPENDED, OrganizationStatus.RETIRED]
)
def test_organization_rename_inactive_raises_not_active(
    status: OrganizationStatus,
) -> None:
    organization = _organization(status=status, status_reason="Test reason")

    with pytest.raises(OrganizationNotActiveError) as caught:
        organization.rename("New name", clock=_clock(), ids=SequentialIdGenerator())

    assert caught.value.details["status"] == status.value


def test_organization_suspend_active_emits_suspended() -> None:
    organization = _organization()

    change = organization.suspend(
        "Test reason", clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.status is OrganizationStatus.SUSPENDED
    assert change.state.status_reason == "Test reason"
    (event,) = change.events
    assert isinstance(event, OrganizationSuspended)
    assert "Test reason" not in event.model_dump_json()


def test_organization_suspend_suspended_raises_not_active() -> None:
    organization = _organization(
        status=OrganizationStatus.SUSPENDED, status_reason="Test reason"
    )

    with pytest.raises(OrganizationNotActiveError):
        organization.suspend("Again", clock=_clock(), ids=SequentialIdGenerator())


def test_organization_reinstate_suspended_emits_reinstated() -> None:
    organization = _organization(
        status=OrganizationStatus.SUSPENDED, status_reason="Test reason"
    )

    change = organization.reinstate(clock=_clock(), ids=SequentialIdGenerator())

    assert change.state.status is OrganizationStatus.ACTIVE
    assert change.state.status_reason is None
    (event,) = change.events
    assert isinstance(event, OrganizationReinstated)


@pytest.mark.parametrize(
    ("status", "reason"),
    [(OrganizationStatus.ACTIVE, None), (OrganizationStatus.RETIRED, "Test reason")],
)
def test_organization_reinstate_not_suspended_raises_not_suspended(
    status: OrganizationStatus, reason: str | None
) -> None:
    organization = _organization(status=status, status_reason=reason)

    with pytest.raises(OrganizationNotSuspendedError):
        organization.reinstate(clock=_clock(), ids=SequentialIdGenerator())


@pytest.mark.parametrize(
    ("status", "reason"),
    [(OrganizationStatus.ACTIVE, None), (OrganizationStatus.SUSPENDED, "Earlier")],
)
def test_organization_retire_active_or_suspended_emits_retired(
    status: OrganizationStatus, reason: str | None
) -> None:
    organization = _organization(status=status, status_reason=reason)

    change = organization.retire(
        "Test reason", clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.status is OrganizationStatus.RETIRED
    assert change.state.status_reason == "Test reason"
    (event,) = change.events
    assert isinstance(event, OrganizationRetired)


def test_organization_retire_retired_raises_not_active() -> None:
    organization = _organization(
        status=OrganizationStatus.RETIRED, status_reason="Test reason"
    )

    with pytest.raises(OrganizationNotActiveError):
        organization.retire("Again", clock=_clock(), ids=SequentialIdGenerator())


# --------------------------------------------------------------------------- #
# Membership                                                                  #
# --------------------------------------------------------------------------- #


def test_membership_updated_before_created_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="earlier than created_at"):
        MembershipTestFactory.build(
            created_at=CREATED_AT, updated_at=CREATED_AT - timedelta(seconds=1)
        )


def test_membership_ref_and_is_admin_describe_the_membership() -> None:
    membership = MembershipTestFactory.build(role=OrganizationRole.ADMIN)

    assert membership.ref == MembershipRef(
        organization_id=membership.organization_id, user_id=membership.user_id
    )
    assert membership.is_admin is True


def test_membership_change_role_emits_role_changed() -> None:
    membership = MembershipTestFactory.build(created_at=CREATED_AT)

    change = membership.change_role(
        OrganizationRole.ADMIN, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.role is OrganizationRole.ADMIN
    assert change.state.version == 2
    (event,) = change.events
    assert isinstance(event, MembershipRoleChanged)
    assert event.previous_role is OrganizationRole.MEMBER
    assert event.role is OrganizationRole.ADMIN
    assert event.version == 2


def test_membership_change_role_same_role_is_a_no_op() -> None:
    membership = MembershipTestFactory.build()

    change = membership.change_role(
        OrganizationRole.MEMBER, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state is membership


# --------------------------------------------------------------------------- #
# Memberships                                                                 #
# --------------------------------------------------------------------------- #


def _member(
    organization: Organization, role: OrganizationRole = OrganizationRole.MEMBER
) -> Membership:
    return MembershipTestFactory.build(
        organization_id=organization.id, role=role, created_at=CREATED_AT
    )


def _added(membership: Membership) -> AggregateChange[Membership]:
    event = MembershipAdded(
        event_id=SequentialIdGenerator(seed=9).new_id(),
        occurred_at=CHANGED_AT,
        aggregate_id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role,
        version=membership.version,
    )
    return AggregateChange[Membership](state=membership, events=(event,))


def test_memberships_foreign_membership_is_rejected() -> None:
    organization = _organization()

    with pytest.raises(PydanticValidationError, match="another organisation"):
        Memberships(
            organization_id=organization.id, members=(MembershipTestFactory.build(),)
        )


def test_memberships_two_memberships_of_one_user_are_rejected() -> None:
    organization = _organization()
    first = _member(organization)
    second = MembershipTestFactory.build(
        organization_id=organization.id, user_id=first.user_id
    )

    with pytest.raises(PydanticValidationError, match="more than one membership"):
        Memberships(organization_id=organization.id, members=(first, second))


def test_memberships_repeated_membership_id_is_rejected() -> None:
    organization = _organization()
    first = _member(organization)
    second = MembershipTestFactory.build(id=first.id, organization_id=organization.id)

    with pytest.raises(PydanticValidationError, match="ids must be unique"):
        Memberships(organization_id=organization.id, members=(first, second))


def test_memberships_find_and_get_return_the_members_membership() -> None:
    organization = _organization()
    member = _member(organization)
    memberships = Memberships(organization_id=organization.id, members=(member,))

    assert memberships.find(member.user_id) is member
    assert memberships.get(member.user_id) is member
    assert memberships.find(organization.id) is None


def test_memberships_get_unknown_user_raises_membership_not_found() -> None:
    organization = _organization()
    memberships = Memberships(organization_id=organization.id)

    with pytest.raises(MembershipNotFoundError):
        memberships.get(organization.id)


def test_memberships_add_new_member_keeps_the_added_event() -> None:
    organization = _organization()
    memberships = Memberships(organization_id=organization.id)
    change = _added(_member(organization))

    result = memberships.add(change)

    assert result.state.members == (change.state,)
    assert result.events == change.events


def test_memberships_add_existing_member_raises_duplicate_membership() -> None:
    organization = _organization()
    existing = _member(organization)
    memberships = Memberships(organization_id=organization.id, members=(existing,))
    again = MembershipTestFactory.build(
        organization_id=organization.id, user_id=existing.user_id
    )

    with pytest.raises(DuplicateMembershipError):
        memberships.add(_added(again))


def test_memberships_add_membership_of_another_organisation_is_rejected() -> None:
    memberships = Memberships(organization_id=_organization().id)

    with pytest.raises(InvariantViolationError, match="another organisation"):
        memberships.add(_added(MembershipTestFactory.build()))


def test_memberships_admin_count_counts_admins() -> None:
    organization = _organization()
    memberships = Memberships(
        organization_id=organization.id,
        members=(
            _member(organization, OrganizationRole.ADMIN),
            _member(organization),
            _member(organization, OrganizationRole.ADMIN),
        ),
    )

    assert memberships.admin_count == 2


def test_memberships_change_role_promotes_member() -> None:
    organization = _organization()
    member = _member(organization)
    memberships = Memberships(organization_id=organization.id, members=(member,))

    change = memberships.change_role(
        member.user_id,
        OrganizationRole.ADMIN,
        clock=_clock(),
        ids=SequentialIdGenerator(),
    )

    assert change.state.get(member.user_id).role is OrganizationRole.ADMIN
    (event,) = change.events
    assert isinstance(event, MembershipRoleChanged)


def test_memberships_change_role_same_role_is_a_no_op() -> None:
    organization = _organization()
    admin = _member(organization, OrganizationRole.ADMIN)
    memberships = Memberships(organization_id=organization.id, members=(admin,))

    change = memberships.change_role(
        admin.user_id,
        OrganizationRole.ADMIN,
        clock=_clock(),
        ids=SequentialIdGenerator(),
    )

    assert change.state is memberships
    assert change.events == ()


def test_memberships_change_role_demoting_only_admin_raises_last_admin() -> None:
    organization = _organization()
    admin = _member(organization, OrganizationRole.ADMIN)
    memberships = Memberships(
        organization_id=organization.id, members=(admin, _member(organization))
    )

    with pytest.raises(LastOrganizationAdminError):
        memberships.change_role(
            admin.user_id,
            OrganizationRole.MEMBER,
            clock=_clock(),
            ids=SequentialIdGenerator(),
        )


def test_memberships_change_role_demoting_one_of_two_admins_succeeds() -> None:
    organization = _organization()
    admin = _member(organization, OrganizationRole.ADMIN)
    other = _member(organization, OrganizationRole.ADMIN)
    memberships = Memberships(organization_id=organization.id, members=(admin, other))

    change = memberships.change_role(
        admin.user_id,
        OrganizationRole.MEMBER,
        clock=_clock(),
        ids=SequentialIdGenerator(),
    )

    assert change.state.admin_count == 1
    assert change.state.members[1] is other


def test_memberships_change_role_unknown_user_raises_membership_not_found() -> None:
    organization = _organization()
    memberships = Memberships(organization_id=organization.id)

    with pytest.raises(MembershipNotFoundError):
        memberships.change_role(
            organization.id,
            OrganizationRole.ADMIN,
            clock=_clock(),
            ids=SequentialIdGenerator(),
        )


def test_memberships_remove_member_emits_removed_with_last_role() -> None:
    organization = _organization()
    member = _member(organization)
    admin = _member(organization, OrganizationRole.ADMIN)
    memberships = Memberships(organization_id=organization.id, members=(member, admin))

    change = memberships.remove(
        member.user_id, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.members == (admin,)
    (event,) = change.events
    assert isinstance(event, MembershipRemoved)
    assert event.aggregate_id == member.id
    assert event.role is OrganizationRole.MEMBER
    assert event.version == member.version
    assert event.occurred_at == CHANGED_AT


def test_memberships_remove_one_of_two_admins_succeeds() -> None:
    organization = _organization()
    admin = _member(organization, OrganizationRole.ADMIN)
    other = _member(organization, OrganizationRole.ADMIN)
    memberships = Memberships(organization_id=organization.id, members=(admin, other))

    change = memberships.remove(
        admin.user_id, clock=_clock(), ids=SequentialIdGenerator()
    )

    assert change.state.members == (other,)


def test_memberships_remove_only_admin_raises_last_admin() -> None:
    organization = _organization()
    admin = _member(organization, OrganizationRole.ADMIN)
    memberships = Memberships(organization_id=organization.id, members=(admin,))

    with pytest.raises(LastOrganizationAdminError) as caught:
        memberships.remove(admin.user_id, clock=_clock(), ids=SequentialIdGenerator())

    assert caught.value.details["user_id"] == str(admin.user_id)


def test_memberships_remove_unknown_user_raises_membership_not_found() -> None:
    organization = _organization()
    memberships = Memberships(organization_id=organization.id)

    with pytest.raises(MembershipNotFoundError):
        memberships.remove(organization.id, clock=_clock(), ids=SequentialIdGenerator())
