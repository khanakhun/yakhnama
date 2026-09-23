"""Unit tests for ``yakhnama.modules.identity.domain.value_objects``."""

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    DisplayName,
    ExternalIdentity,
    Issuer,
    MembershipRef,
    OrganizationName,
    OrganizationRole,
    OrganizationSlug,
    RecordVersion,
    Role,
    StatusReason,
    Subject,
    expand_roles,
)

_IDS = SequentialIdGenerator(seed=3)
ORGANIZATION_IDS: tuple[UUID, ...] = tuple(_IDS.new_id() for _ in range(4))
USER_ID = _IDS.new_id()

roles_strategy = st.frozensets(st.sampled_from(list(Role)))
memberships_strategy = st.dictionaries(
    st.sampled_from(ORGANIZATION_IDS), st.sampled_from(list(OrganizationRole))
).map(lambda mapping: frozenset(mapping.items()))


def _validate(annotation: object, value: object) -> object:
    return TypeAdapter(annotation).validate_python(value)


# --------------------------------------------------------------------------- #
# Role order                                                                  #
# --------------------------------------------------------------------------- #


def test_role_admin_implies_moderator_only_beyond_itself() -> None:
    result = Role.ADMIN.implied_roles

    assert result == frozenset({Role.ADMIN, Role.MODERATOR})


def test_role_org_admin_implies_org_member_only_beyond_itself() -> None:
    result = Role.ORG_ADMIN.implied_roles

    assert result == frozenset({Role.ORG_ADMIN, Role.ORG_MEMBER})


@pytest.mark.parametrize(
    "role",
    [Role.CITIZEN, Role.TRUSTED_REPORTER, Role.ORG_MEMBER, Role.MODERATOR],
)
def test_role_without_direct_implications_implies_only_itself(role: Role) -> None:
    result = role.implied_roles

    assert result == frozenset({role})


def test_role_moderator_does_not_imply_admin() -> None:
    result = Role.MODERATOR.implies(Role.ADMIN)

    assert result is False


@given(st.sampled_from(list(Role)))
def test_role_implies_is_reflexive(role: Role) -> None:
    result = role.implies(role)

    assert result is True


@given(st.sampled_from(list(Role)), st.sampled_from(list(Role)))
def test_role_implies_is_antisymmetric(first: Role, second: Role) -> None:
    both_ways = first.implies(second) and second.implies(first)

    assert not both_ways or first is second


@given(
    st.sampled_from(list(Role)),
    st.sampled_from(list(Role)),
    st.sampled_from(list(Role)),
)
def test_role_implies_is_transitive(first: Role, second: Role, third: Role) -> None:
    chained = first.implies(second) and second.implies(third)

    assert not chained or first.implies(third)


@given(roles_strategy)
def test_expand_roles_contains_every_held_and_implied_role(
    roles: frozenset[Role],
) -> None:
    result = expand_roles(roles)

    assert roles <= result
    assert all(any(held.implies(role) for held in roles) for role in result)


def test_expand_roles_no_roles_returns_empty_set() -> None:
    result = expand_roles(())

    assert result == frozenset()


# --------------------------------------------------------------------------- #
# Constrained strings                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slug", ["ab", "hunza-relief", "0org", "a" * 64])
def test_organization_slug_valid_value_is_accepted(slug: str) -> None:
    result = _validate(OrganizationSlug, slug)

    assert result == slug


@pytest.mark.parametrize(
    "slug", ["a", "-org", "Org", "org_name", "a" * 65, "org name", ""]
)
def test_organization_slug_invalid_value_is_rejected(slug: str) -> None:
    with pytest.raises(PydanticValidationError):
        _validate(OrganizationSlug, slug)


@pytest.mark.parametrize(
    "subject", ["f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "A b", "x", "s" * 255]
)
def test_subject_printable_ascii_is_kept_verbatim(subject: str) -> None:
    result = _validate(Subject, subject)

    assert result == subject


@pytest.mark.parametrize("subject", ["", "s" * 256, "a\x00b", "tab\t", "ünï"])
def test_subject_empty_long_control_or_non_ascii_is_rejected(subject: str) -> None:
    with pytest.raises(PydanticValidationError):
        _validate(Subject, subject)


@pytest.mark.parametrize(
    "issuer",
    [
        "https://auth.example.org/realms/yakhnama",
        "https://auth.example.org:8443",
        "http://localhost:8080/realms/yakhnama",
        "http://127.0.0.1/realms/yakhnama",
        "http://[::1]:8080/",
    ],
)
def test_issuer_https_or_loopback_http_is_accepted(issuer: str) -> None:
    result = _validate(Issuer, issuer)

    assert result == issuer


@pytest.mark.parametrize(
    "issuer",
    [
        "",
        "http://auth.example.org",
        "http://10.0.0.1",
        "ftp://auth.example.org",
        "auth.example.org",
        "https:///path",
        "https://user@auth.example.org",
        "https://auth.example.org/?tenant=1",
        "https://auth.example.org/#top",
        "https://auth.example.org:99999",
        "https://auth.example.org/a b",
        "https://auth.example.org/\x00",
        "https://" + "a" * 512,
    ],
)
def test_issuer_invalid_value_is_rejected(issuer: str) -> None:
    with pytest.raises(PydanticValidationError):
        _validate(Issuer, issuer)


def test_display_name_surrounding_whitespace_is_stripped() -> None:
    result = _validate(DisplayName, "  Test user  ")

    assert result == "Test user"


def test_display_name_decomposed_text_is_normalised_to_nfc() -> None:
    result = _validate(DisplayName, "José")

    assert result == "José"


@pytest.mark.parametrize("name", ["", "   ", "a" * 121, "a\x00b", "line\nbreak"])
def test_display_name_empty_long_or_control_text_is_rejected(name: str) -> None:
    with pytest.raises(PydanticValidationError):
        _validate(DisplayName, name)


def test_display_name_maximum_length_after_stripping_is_accepted() -> None:
    result = _validate(DisplayName, " " + "a" * 120 + " ")

    assert result == "a" * 120


@pytest.mark.parametrize(
    ("annotation", "maximum"), [(OrganizationName, 200), (StatusReason, 500)]
)
def test_free_text_bounds_are_enforced(annotation: object, maximum: int) -> None:
    accepted = _validate(annotation, "x" * maximum)

    assert accepted == "x" * maximum
    with pytest.raises(PydanticValidationError):
        _validate(annotation, "x" * (maximum + 1))


@pytest.mark.parametrize("version", [0, 2_147_483_648])
def test_record_version_out_of_range_is_rejected(version: int) -> None:
    with pytest.raises(PydanticValidationError):
        _validate(RecordVersion, version)


# --------------------------------------------------------------------------- #
# References                                                                  #
# --------------------------------------------------------------------------- #


def test_external_identity_equal_parts_are_equal_values() -> None:
    first = ExternalIdentity(issuer="https://auth.example.org", subject="abc")

    second = ExternalIdentity(issuer="https://auth.example.org", subject="abc")

    assert first == second
    assert hash(first) == hash(second)


def test_external_identity_subject_case_distinguishes_identities() -> None:
    first = ExternalIdentity(issuer="https://auth.example.org", subject="abc")

    second = ExternalIdentity(issuer="https://auth.example.org", subject="ABC")

    assert first != second


def test_membership_ref_is_frozen() -> None:
    ref = MembershipRef(organization_id=ORGANIZATION_IDS[0], user_id=USER_ID)

    with pytest.raises(PydanticValidationError):
        ref.user_id = ORGANIZATION_IDS[1]  # type: ignore[misc]  # reason: asserting frozen models reject assignment


def test_membership_ref_non_uuid7_id_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        MembershipRef(
            organization_id=UUID("00000000-0000-4000-8000-000000000000"),
            user_id=USER_ID,
        )


# --------------------------------------------------------------------------- #
# Actor                                                                       #
# --------------------------------------------------------------------------- #


def test_actor_anonymous_has_no_identity_roles_or_memberships() -> None:
    actor = Actor.anonymous()

    assert actor.user_id is None
    assert actor.roles == frozenset()
    assert actor.memberships == frozenset()
    assert actor.is_authenticated is False


def test_actor_with_user_id_is_authenticated() -> None:
    actor = Actor(user_id=USER_ID)

    result = actor.is_authenticated

    assert result is True


@pytest.mark.parametrize(
    "fields",
    [
        {"roles": frozenset({Role.ADMIN})},
        {"memberships": frozenset({(ORGANIZATION_IDS[0], OrganizationRole.MEMBER)})},
    ],
)
def test_actor_anonymous_with_roles_or_memberships_is_rejected(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError, match="anonymous"):
        Actor.model_validate(fields)


def test_actor_two_roles_in_one_organisation_is_rejected() -> None:
    memberships = frozenset(
        {
            (ORGANIZATION_IDS[0], OrganizationRole.MEMBER),
            (ORGANIZATION_IDS[0], OrganizationRole.ADMIN),
        }
    )

    with pytest.raises(PydanticValidationError, match="one role per organisation"):
        Actor(user_id=USER_ID, memberships=memberships)


def test_actor_has_role_admin_grants_moderator_but_not_org_admin() -> None:
    actor = Actor(user_id=USER_ID, roles=frozenset({Role.ADMIN}))

    assert actor.has_role(Role.MODERATOR) is True
    assert actor.has_role(Role.ORG_ADMIN) is False
    assert actor.effective_roles == frozenset({Role.ADMIN, Role.MODERATOR})


@given(roles_strategy, st.sampled_from(list(Role)), st.sampled_from(list(Role)))
def test_actor_has_role_is_monotonic_under_implication(
    roles: frozenset[Role], held: Role, asked: Role
) -> None:
    actor = Actor(user_id=USER_ID, roles=roles | {held})

    if held.implies(asked):
        assert actor.has_role(asked)


@given(roles_strategy, roles_strategy, st.sampled_from(list(Role)))
def test_actor_has_role_is_monotonic_under_added_roles(
    roles: frozenset[Role], extra: frozenset[Role], asked: Role
) -> None:
    smaller = Actor(user_id=USER_ID, roles=roles)

    larger = Actor(user_id=USER_ID, roles=roles | extra)

    assert not smaller.has_role(asked) or larger.has_role(asked)


@given(roles_strategy, st.sampled_from(list(Role)))
def test_actor_has_role_matches_effective_roles(
    roles: frozenset[Role], asked: Role
) -> None:
    actor = Actor(user_id=USER_ID, roles=roles)

    assert actor.has_role(asked) == (asked in actor.effective_roles)


@given(memberships_strategy, st.sampled_from(ORGANIZATION_IDS))
def test_actor_membership_helpers_agree_with_memberships(
    memberships: frozenset[tuple[UUID, OrganizationRole]], organization_id: UUID
) -> None:
    actor = Actor(user_id=USER_ID, memberships=memberships)

    role = dict(memberships).get(organization_id)

    assert actor.organization_role(organization_id) is role
    assert actor.is_member_of(organization_id) == (role is not None)
    assert actor.is_org_admin_of(organization_id) == (role is OrganizationRole.ADMIN)


def test_actor_json_round_trip_is_lossless() -> None:
    actor = Actor(
        user_id=USER_ID,
        roles=frozenset({Role.CITIZEN, Role.MODERATOR}),
        memberships=frozenset({(ORGANIZATION_IDS[1], OrganizationRole.ADMIN)}),
    )

    result = Actor.model_validate_json(actor.model_dump_json())

    assert result == actor
