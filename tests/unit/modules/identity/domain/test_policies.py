"""Unit tests for ``yakhnama.modules.identity.domain.policies``.

The algebraic laws are checked as equivalences over actors sampled by hypothesis: two
policies are equivalent when they allow exactly the same actors.
"""

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain.policies import (
    ActorPolicy,
    AllOf,
    AnyOf,
    AuthorisationPolicy,
    CanManageOrganization,
    CanManageReferenceData,
    CanModerate,
    CanReadVerifiedData,
    HasRole,
    IsAdmin,
    IsAuthenticated,
    IsMemberOf,
    IsModerator,
    IsOrgAdminOf,
    IsSelf,
    Not,
    all_of,
    any_of,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    OrganizationRole,
    Role,
)
from yakhnama.shared_kernel.specification import (
    AndSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
    TrueSpecification,
)

_IDS = SequentialIdGenerator(seed=5)
ORGANIZATION_IDS: tuple[UUID, ...] = tuple(_IDS.new_id() for _ in range(3))
USER_IDS: tuple[UUID, ...] = tuple(_IDS.new_id() for _ in range(3))
ORGANIZATION_ID = ORGANIZATION_IDS[0]
USER_ID = USER_IDS[0]


@st.composite
def actors(draw: st.DrawFn) -> Actor:
    """Draw anonymous and authenticated actors with any roles and memberships."""
    user_id = draw(st.none() | st.sampled_from(USER_IDS))
    if user_id is None:
        return Actor.anonymous()
    roles = draw(st.frozensets(st.sampled_from(list(Role))))
    memberships = draw(
        st.dictionaries(
            st.sampled_from(ORGANIZATION_IDS), st.sampled_from(list(OrganizationRole))
        )
    )
    return Actor(
        user_id=user_id, roles=roles, memberships=frozenset(memberships.items())
    )


def _leaf_policies() -> st.SearchStrategy[ActorPolicy]:
    return st.one_of(
        st.just(IsAuthenticated()),
        st.sampled_from(list(Role)).map(HasRole),
        st.just(IsAdmin()),
        st.just(IsModerator()),
        st.sampled_from(ORGANIZATION_IDS).map(IsMemberOf),
        st.sampled_from(ORGANIZATION_IDS).map(IsOrgAdminOf),
        st.sampled_from(USER_IDS).map(IsSelf),
        st.just(CanManageReferenceData()),
        st.just(CanModerate()),
        st.sampled_from(ORGANIZATION_IDS).map(CanManageOrganization),
        st.just(CanReadVerifiedData()),
    )


policies: st.SearchStrategy[ActorPolicy] = st.recursive(
    _leaf_policies(),
    lambda children: st.one_of(
        st.tuples(children, children).map(lambda pair: pair[0] & pair[1]),
        st.tuples(children, children).map(lambda pair: pair[0] | pair[1]),
        children.map(lambda policy: ~policy),
    ),
    max_leaves=6,
)


def _actor(*roles: Role) -> Actor:
    return Actor(user_id=USER_ID, roles=frozenset(roles))


def _member_of(role: OrganizationRole) -> Actor:
    return Actor(
        user_id=USER_ID,
        roles=frozenset({Role.CITIZEN}),
        memberships=frozenset({(ORGANIZATION_ID, role)}),
    )


# --------------------------------------------------------------------------- #
# Deny by default                                                             #
# --------------------------------------------------------------------------- #


@given(_leaf_policies())
def test_every_leaf_policy_except_read_verified_denies_anonymous(
    policy: ActorPolicy,
) -> None:
    result = policy.is_allowed(Actor.anonymous())

    assert result is isinstance(policy, CanReadVerifiedData)


@given(actors())
def test_can_read_verified_data_allows_every_actor(actor: Actor) -> None:
    result = CanReadVerifiedData().is_allowed(actor)

    assert result is True


# --------------------------------------------------------------------------- #
# Leaf behaviour                                                              #
# --------------------------------------------------------------------------- #


def test_is_authenticated_allows_known_user() -> None:
    result = IsAuthenticated().is_allowed(_actor(Role.CITIZEN))

    assert result is True


def test_has_role_exposes_role_and_honours_implication() -> None:
    policy = HasRole(Role.MODERATOR)

    assert policy.role is Role.MODERATOR
    assert policy.is_allowed(_actor(Role.ADMIN)) is True
    assert policy.is_allowed(_actor(Role.TRUSTED_REPORTER)) is False


def test_is_admin_denies_moderator() -> None:
    result = IsAdmin().is_allowed(_actor(Role.CITIZEN, Role.MODERATOR))

    assert result is False


def test_is_moderator_allows_moderator_and_admin() -> None:
    policy = IsModerator()

    assert policy.is_allowed(_actor(Role.MODERATOR)) is True
    assert policy.is_allowed(_actor(Role.ADMIN)) is True
    assert policy.is_allowed(_actor(Role.CITIZEN)) is False


@pytest.mark.parametrize("role", list(OrganizationRole))
def test_is_member_of_allows_member_and_admin(role: OrganizationRole) -> None:
    policy = IsMemberOf(ORGANIZATION_ID)

    result = policy.is_allowed(_member_of(role))

    assert result is True
    assert policy.organization_id == ORGANIZATION_ID


def test_is_member_of_denies_member_of_another_organisation() -> None:
    result = IsMemberOf(ORGANIZATION_IDS[1]).is_allowed(
        _member_of(OrganizationRole.ADMIN)
    )

    assert result is False


def test_is_org_admin_of_allows_only_the_organisations_admin() -> None:
    policy = IsOrgAdminOf(ORGANIZATION_ID)

    assert policy.organization_id == ORGANIZATION_ID
    assert policy.is_allowed(_member_of(OrganizationRole.ADMIN)) is True
    assert policy.is_allowed(_member_of(OrganizationRole.MEMBER)) is False
    assert (
        IsOrgAdminOf(ORGANIZATION_IDS[1]).is_allowed(_member_of(OrganizationRole.ADMIN))
        is False
    )


def test_is_org_admin_of_realm_org_admin_role_is_not_enough() -> None:
    result = IsOrgAdminOf(ORGANIZATION_ID).is_allowed(_actor(Role.ORG_ADMIN))

    assert result is False


def test_is_self_allows_only_that_user() -> None:
    policy = IsSelf(USER_ID)

    assert policy.user_id == USER_ID
    assert policy.is_allowed(_actor(Role.CITIZEN)) is True
    assert IsSelf(USER_IDS[1]).is_allowed(_actor(Role.CITIZEN)) is False


def test_can_manage_reference_data_allows_admin_only() -> None:
    policy = CanManageReferenceData()

    assert policy.is_allowed(_actor(Role.ADMIN)) is True
    assert policy.is_allowed(_actor(Role.MODERATOR)) is False


def test_can_moderate_allows_moderator_and_admin_only() -> None:
    policy = CanModerate()

    assert policy.is_allowed(_actor(Role.MODERATOR)) is True
    assert policy.is_allowed(_actor(Role.ADMIN)) is True
    assert policy.is_allowed(_actor(Role.TRUSTED_REPORTER)) is False


def test_can_manage_organization_allows_admin_and_that_org_admin() -> None:
    policy = CanManageOrganization(ORGANIZATION_ID)

    assert policy.organization_id == ORGANIZATION_ID
    assert policy.is_allowed(_actor(Role.ADMIN)) is True
    assert policy.is_allowed(_member_of(OrganizationRole.ADMIN)) is True
    assert policy.is_allowed(_member_of(OrganizationRole.MEMBER)) is False
    assert policy.is_allowed(_actor(Role.MODERATOR)) is False


# --------------------------------------------------------------------------- #
# Algebra                                                                     #
# --------------------------------------------------------------------------- #


@given(actors())
def test_is_admin_implies_is_moderator(actor: Actor) -> None:
    is_admin = IsAdmin().is_allowed(actor)

    assert not is_admin or IsModerator().is_allowed(actor)


@given(actors())
def test_can_manage_reference_data_implies_can_moderate(actor: Actor) -> None:
    allowed = CanManageReferenceData().is_allowed(actor)

    assert not allowed or CanModerate().is_allowed(actor)


@given(actors(), st.sampled_from(ORGANIZATION_IDS))
def test_is_org_admin_of_implies_is_member_of(
    actor: Actor, organization_id: UUID
) -> None:
    allowed = IsOrgAdminOf(organization_id).is_allowed(actor)

    assert not allowed or IsMemberOf(organization_id).is_allowed(actor)


@given(actors(), policies, policies)
def test_de_morgan_not_and_equals_or_of_nots(
    actor: Actor, left: ActorPolicy, right: ActorPolicy
) -> None:
    negated_conjunction = ~(left & right)

    disjunction_of_negations = ~left | ~right

    assert negated_conjunction.is_allowed(actor) == disjunction_of_negations.is_allowed(
        actor
    )


@given(actors(), policies, policies)
def test_de_morgan_not_or_equals_and_of_nots(
    actor: Actor, left: ActorPolicy, right: ActorPolicy
) -> None:
    negated_disjunction = ~(left | right)

    conjunction_of_negations = ~left & ~right

    assert negated_disjunction.is_allowed(actor) == conjunction_of_negations.is_allowed(
        actor
    )


@given(actors(), policies)
def test_double_negation_is_identity(actor: Actor, policy: ActorPolicy) -> None:
    result = (~~policy).is_allowed(actor)

    assert result == policy.is_allowed(actor)


@given(actors(), policies, policies)
def test_composition_matches_boolean_operators(
    actor: Actor, left: ActorPolicy, right: ActorPolicy
) -> None:
    left_allows, right_allows = left.is_allowed(actor), right.is_allowed(actor)

    assert (left & right).is_allowed(actor) == (left_allows and right_allows)
    assert (left | right).is_allowed(actor) == (left_allows or right_allows)
    assert (~left).is_allowed(actor) == (not left_allows)


@given(actors(), policies, policies)
def test_method_and_operator_forms_agree(
    actor: Actor, left: ActorPolicy, right: ActorPolicy
) -> None:
    assert left.and_(right).is_allowed(actor) == (left & right).is_allowed(actor)
    assert left.or_(right).is_allowed(actor) == (left | right).is_allowed(actor)
    assert left.not_().is_allowed(actor) == (~left).is_allowed(actor)


@given(actors(), st.lists(policies, min_size=1, max_size=4))
def test_all_of_and_any_of_fold_every_policy(
    actor: Actor, folded: list[ActorPolicy]
) -> None:
    answers = [policy.is_allowed(actor) for policy in folded]

    assert all_of(*folded).is_allowed(actor) == all(answers)
    assert any_of(*folded).is_allowed(actor) == any(answers)


def test_all_of_single_policy_returns_it_unchanged() -> None:
    policy = IsAdmin()

    assert all_of(policy) is policy
    assert any_of(policy) is policy


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #


def test_composition_keeps_the_policy_type() -> None:
    conjunction = IsAdmin() & IsModerator()
    disjunction = IsAdmin() | IsModerator()
    negation = ~IsAdmin()

    assert isinstance(conjunction, AllOf)
    assert isinstance(conjunction, AndSpecification)
    assert isinstance(disjunction, AnyOf)
    assert isinstance(disjunction, OrSpecification)
    assert isinstance(negation, Not)
    assert isinstance(negation, NotSpecification)


def test_composition_accepts_a_plain_specification_operand() -> None:
    plain: Specification[Actor] = TrueSpecification()

    policy = IsAdmin() | plain

    assert policy.is_allowed(Actor.anonymous()) is True


def test_every_policy_satisfies_the_authorisation_protocol() -> None:
    guards: list[AuthorisationPolicy] = [
        IsAdmin(),
        IsAdmin() & IsSelf(USER_ID),
        CanManageOrganization(ORGANIZATION_ID),
    ]

    results = [guard.is_allowed(Actor.anonymous()) for guard in guards]

    assert results == [False, False, False]
