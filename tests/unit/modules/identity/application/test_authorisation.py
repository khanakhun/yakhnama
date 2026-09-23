"""Unit tests for ``require_allowed`` and the identity read policies."""

import pytest

from tests.fakes.identity import AllowAllPolicy, DenyAllPolicy, actor_with
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.application.authorisation import (
    member_list_policy,
    organization_read_policy,
    require_allowed,
    self_policy,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    OrganizationRole,
    Role,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError

_IDS = SequentialIdGenerator(seed=31)
ORGANIZATION_ID = _IDS.new_id()
OTHER_USER_ID = _IDS.new_id()


def test_require_allowed_when_policy_allows_returns_none() -> None:
    policy = AllowAllPolicy()
    actor = actor_with()

    require_allowed(policy, actor, action="do things")

    assert policy.checked == [actor]


def test_require_allowed_when_policy_refuses_names_action_and_policy_only() -> None:
    actor = actor_with()

    with pytest.raises(PermissionDeniedError) as raised:
        require_allowed(DenyAllPolicy(), actor, action="do things")

    assert raised.value.details == {"action": "do things", "policy": "DenyAllPolicy"}
    assert str(actor.user_id) not in str(raised.value)


def test_self_policy_allows_only_the_actor_themself() -> None:
    actor = actor_with()
    other = actor_with(user_id=OTHER_USER_ID)

    policy = self_policy(actor)

    assert policy.is_allowed(actor) is True
    assert policy.is_allowed(other) is False


def test_self_policy_for_anonymous_actor_refuses_everyone_anonymous() -> None:
    policy = self_policy(Actor.anonymous())

    assert policy.is_allowed(Actor.anonymous()) is False


def test_organization_read_policy_allows_anonymous_readers() -> None:
    assert organization_read_policy().is_allowed(Actor.anonymous()) is True


@pytest.mark.parametrize(
    ("actor", "is_allowed"),
    [
        (actor_with(memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}), True),
        (actor_with(memberships={(ORGANIZATION_ID, OrganizationRole.ADMIN)}), True),
        (actor_with({Role.ADMIN}), True),
        (actor_with({Role.MODERATOR}), False),
        (actor_with(), False),
        (Actor.anonymous(), False),
    ],
)
def test_member_list_policy_allows_members_and_platform_admins(
    actor: Actor, *, is_allowed: bool
) -> None:
    assert member_list_policy(ORGANIZATION_ID).is_allowed(actor) is is_allowed
