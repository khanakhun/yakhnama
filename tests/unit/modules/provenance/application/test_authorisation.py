"""Unit tests for the provenance authorisation rules."""

import pytest

from tests.factories.provenance import SourceTestFactory
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.public import Actor, OrganizationRole, Role
from yakhnama.modules.provenance.application.authorisation import (
    SELF_REGISTERED_SOURCE_TYPES,
    reference_policy,
    registration_policy,
    source_editor_policy,
    source_read_policy,
)
from yakhnama.modules.provenance.domain.value_objects import SourceType

IDS = SequentialIdGenerator(seed=303)
USER_ID = IDS.new_id()
ORGANIZATION_ID = IDS.new_id()


@pytest.mark.parametrize("source_type", list(SourceType))
def test_registration_policy_citizen_allowed_only_for_self_types(
    source_type: SourceType,
) -> None:
    policy = registration_policy(source_type, None)

    result = policy.is_allowed(actor_with(user_id=USER_ID))

    assert result is (source_type in SELF_REGISTERED_SOURCE_TYPES)


def test_registration_policy_organisation_admits_moderator_non_member() -> None:
    policy = registration_policy(SourceType.ORGANISATION, ORGANIZATION_ID)

    result = policy.is_allowed(actor_with({Role.MODERATOR}, user_id=USER_ID))

    assert result is True


def test_registration_policy_organisation_admits_member() -> None:
    policy = registration_policy(SourceType.ORGANISATION, ORGANIZATION_ID)
    member = actor_with(
        user_id=USER_ID, memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}
    )

    result = policy.is_allowed(member)

    assert result is True


def test_source_editor_policy_owner_is_allowed() -> None:
    source = SourceTestFactory.build(owner_actor_id=USER_ID)

    result = source_editor_policy(source).is_allowed(actor_with(user_id=USER_ID))

    assert result is True


def test_reference_policy_anonymous_is_refused() -> None:
    result = reference_policy().is_allowed(Actor.anonymous())

    assert result is False


def test_source_read_policy_anonymous_is_allowed() -> None:
    result = source_read_policy().is_allowed(Actor.anonymous())

    assert result is True
