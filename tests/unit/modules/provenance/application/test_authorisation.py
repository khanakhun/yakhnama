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
    source_listing_specification,
    source_read_policy,
)
from yakhnama.modules.provenance.application.dto import SourceDetail
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.shared_kernel.specification import TrueSpecification

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


def _detail(source_type: SourceType, organization_id: object = None) -> SourceDetail:
    return SourceDetail.from_entity(
        SourceTestFactory.build(
            source_type=source_type, organization_id=organization_id
        )
    )


@pytest.mark.parametrize(
    "source_type",
    [t for t in SourceType if t not in SELF_REGISTERED_SOURCE_TYPES],
)
def test_source_read_policy_ranked_type_allows_anonymous(
    source_type: SourceType,
) -> None:
    result = source_read_policy(_detail(source_type)).is_allowed(Actor.anonymous())

    assert result is True


@pytest.mark.parametrize("source_type", sorted(SELF_REGISTERED_SOURCE_TYPES))
@pytest.mark.parametrize("actor", [Actor.anonymous(), actor_with(user_id=USER_ID)])
def test_source_read_policy_self_registered_type_refuses_non_moderator(
    source_type: SourceType, actor: Actor
) -> None:
    result = source_read_policy(_detail(source_type)).is_allowed(actor)

    assert result is False


def test_source_read_policy_self_registered_type_allows_moderator() -> None:
    policy = source_read_policy(_detail(SourceType.CITIZEN))

    result = policy.is_allowed(actor_with({Role.MODERATOR}, user_id=USER_ID))

    assert result is True


def test_source_read_policy_organisation_source_allows_its_members() -> None:
    policy = source_read_policy(_detail(SourceType.ORGANISATION, ORGANIZATION_ID))
    member = actor_with(
        user_id=USER_ID, memberships={(ORGANIZATION_ID, OrganizationRole.MEMBER)}
    )

    assert policy.is_allowed(member) is True
    assert policy.is_allowed(actor_with(user_id=USER_ID)) is False


def test_source_listing_specification_non_moderator_hides_self_types() -> None:
    specification = source_listing_specification(Actor.anonymous())

    visible = {
        source_type
        for source_type in SourceType
        if specification.is_satisfied_by(
            SourceTestFactory.build(source_type=source_type)
        )
    }

    assert visible == set(SourceType) - SELF_REGISTERED_SOURCE_TYPES


def test_source_listing_specification_moderator_sees_every_type() -> None:
    specification = source_listing_specification(
        actor_with({Role.MODERATOR}, user_id=USER_ID)
    )

    assert isinstance(specification, TrueSpecification)
