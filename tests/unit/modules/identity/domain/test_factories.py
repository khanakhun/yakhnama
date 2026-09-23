"""Unit tests for the identity domain factories and the identity test factories.

Covers ``yakhnama.modules.identity.domain.factories`` and ``tests.factories.identity``.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.identity import (
    TEST_ISSUER,
    ActorTestFactory,
    MembershipTestFactory,
    OrganizationTestFactory,
    UserTestFactory,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain.entities import Memberships
from yakhnama.modules.identity.domain.errors import (
    OrganizationNotActiveError,
    UserSuspendedError,
)
from yakhnama.modules.identity.domain.events import (
    MembershipAdded,
    OrganizationCreated,
    UserMirrored,
)
from yakhnama.modules.identity.domain.factories import (
    MembershipFactory,
    OrganizationFactory,
    UserFactory,
)
from yakhnama.modules.identity.domain.value_objects import (
    ExternalIdentity,
    OrganizationRole,
    OrganizationStatus,
    OrganizationType,
    Role,
    UserStatus,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)
IDENTITY = ExternalIdentity(issuer=TEST_ISSUER, subject="test-subject")

# --------------------------------------------------------------------------- #
# UserFactory                                                                 #
# --------------------------------------------------------------------------- #


def test_user_factory_mirror_creates_active_citizen_with_realm_roles() -> None:
    ids = SequentialIdGenerator()

    change = UserFactory().mirror(
        IDENTITY, " Test user ", {Role.MODERATOR}, ids=ids, clock=FrozenClock(NOW)
    )

    user = change.state
    assert user.id == ids.issued[0]
    assert user.external_identity == IDENTITY
    assert user.display_name == "Test user"
    assert user.roles == frozenset({Role.CITIZEN, Role.MODERATOR})
    assert user.status is UserStatus.ACTIVE
    assert user.version == 1
    assert user.created_at == user.updated_at == user.last_seen_at == NOW
    (event,) = change.events
    assert isinstance(event, UserMirrored)
    assert event.event_id == ids.issued[1]
    assert event.aggregate_id == user.id
    assert event.roles == user.roles
    assert event.version == 1


def test_user_factory_mirror_without_realm_roles_or_name_gives_citizen_only() -> None:
    change = UserFactory().mirror(
        IDENTITY, None, (), ids=SequentialIdGenerator(), clock=FrozenClock(NOW)
    )

    assert change.state.roles == frozenset({Role.CITIZEN})
    assert change.state.display_name is None


def test_user_factory_mirror_invalid_display_name_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        UserFactory().mirror(
            IDENTITY, "\x00", (), ids=SequentialIdGenerator(), clock=FrozenClock(NOW)
        )


# --------------------------------------------------------------------------- #
# OrganizationFactory                                                         #
# --------------------------------------------------------------------------- #


def test_organization_factory_create_builds_active_organisation_and_event() -> None:
    ids = SequentialIdGenerator()

    change = OrganizationFactory().create(
        "test-org",
        " Test organisation ",
        OrganizationType.RESEARCH,
        ids=ids,
        clock=FrozenClock(NOW),
    )

    organization = change.state
    assert organization.id == ids.issued[0]
    assert organization.slug == "test-org"
    assert organization.name == "Test organisation"
    assert organization.organization_type is OrganizationType.RESEARCH
    assert organization.status is OrganizationStatus.ACTIVE
    assert organization.version == 1
    (event,) = change.events
    assert isinstance(event, OrganizationCreated)
    assert event.slug == "test-org"
    assert event.organization_type is OrganizationType.RESEARCH


def test_organization_factory_create_invalid_slug_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        OrganizationFactory().create(
            "Not A Slug",
            "Test organisation",
            OrganizationType.OTHER,
            ids=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
        )


# --------------------------------------------------------------------------- #
# MembershipFactory                                                           #
# --------------------------------------------------------------------------- #


def test_membership_factory_create_builds_membership_and_added_event() -> None:
    organization = OrganizationTestFactory.build()
    user = UserTestFactory.build()
    ids = SequentialIdGenerator()

    change = MembershipFactory().create(
        organization, user, OrganizationRole.ADMIN, ids=ids, clock=FrozenClock(NOW)
    )

    membership = change.state
    assert membership.id == ids.issued[0]
    assert membership.organization_id == organization.id
    assert membership.user_id == user.id
    assert membership.role is OrganizationRole.ADMIN
    assert membership.version == 1
    (event,) = change.events
    assert isinstance(event, MembershipAdded)
    assert event.aggregate_id == membership.id
    assert event.role is OrganizationRole.ADMIN


def test_membership_factory_create_then_add_enforces_one_per_user() -> None:
    organization = OrganizationTestFactory.build()
    user = UserTestFactory.build()
    factory = MembershipFactory()
    first = Memberships(organization_id=organization.id).add(
        factory.create(
            organization,
            user,
            OrganizationRole.MEMBER,
            ids=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
        )
    )

    result = first.state.get(user.id)

    assert result.role is OrganizationRole.MEMBER


@pytest.mark.parametrize(
    "status", [OrganizationStatus.SUSPENDED, OrganizationStatus.RETIRED]
)
def test_membership_factory_create_inactive_organisation_raises_not_active(
    status: OrganizationStatus,
) -> None:
    organization = OrganizationTestFactory.build(
        status=status, status_reason="Test reason"
    )

    with pytest.raises(OrganizationNotActiveError):
        MembershipFactory().create(
            organization,
            UserTestFactory.build(),
            OrganizationRole.MEMBER,
            ids=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
        )


def test_membership_factory_create_suspended_user_raises_user_suspended() -> None:
    user = UserTestFactory.build(
        status=UserStatus.SUSPENDED, status_reason="Test reason"
    )

    with pytest.raises(UserSuspendedError):
        MembershipFactory().create(
            OrganizationTestFactory.build(),
            user,
            OrganizationRole.MEMBER,
            ids=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
        )


# --------------------------------------------------------------------------- #
# Test factories                                                              #
# --------------------------------------------------------------------------- #


def test_user_test_factory_builds_distinct_active_citizens() -> None:
    first, second = UserTestFactory.batch(2)

    assert first.id != second.id
    assert first.subject != second.subject
    assert first.roles == frozenset({Role.CITIZEN})
    assert first.status is UserStatus.ACTIVE
    assert first.updated_at == first.created_at == first.last_seen_at


def test_organization_test_factory_builds_active_organisations() -> None:
    first, second = OrganizationTestFactory.batch(2)

    assert first.slug != second.slug
    assert first.status is OrganizationStatus.ACTIVE
    assert first.updated_at == first.created_at


def test_membership_test_factory_builds_member_memberships() -> None:
    membership = MembershipTestFactory.build()

    assert membership.role is OrganizationRole.MEMBER
    assert membership.updated_at == membership.created_at


def test_actor_test_factory_builds_authenticated_citizens() -> None:
    actor = ActorTestFactory.build()

    assert actor.is_authenticated
    assert actor.roles == frozenset({Role.CITIZEN})
    assert actor.memberships == frozenset()
