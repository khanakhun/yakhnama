"""Unit tests for ``yakhnama.modules.identity.domain.errors``."""

import pytest

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.identity.domain.errors import (
    AccountSuspendedError,
    CitizenRoleRequiredError,
    DuplicateMembershipError,
    LastOrganizationAdminError,
    MembershipNotFoundError,
    OrganizationNotActiveError,
    OrganizationNotFoundError,
    OrganizationNotSuspendedError,
    OrganizationSlugTakenError,
    RoleNotHeldError,
    UserNotFoundError,
    UserNotSuspendedError,
    UserSuspendedError,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
    YakhnamaError,
)

_IDS = SequentialIdGenerator(seed=11)
ORGANIZATION_ID = _IDS.new_id()
USER_ID = _IDS.new_id()


@pytest.mark.parametrize(
    ("error_class", "family"),
    [
        (UserNotFoundError, NotFoundError),
        (OrganizationNotFoundError, NotFoundError),
        (MembershipNotFoundError, NotFoundError),
        (DuplicateMembershipError, ConflictError),
        (OrganizationSlugTakenError, ConflictError),
        (UserSuspendedError, InvalidTransitionError),
        (UserNotSuspendedError, InvalidTransitionError),
        (OrganizationNotActiveError, InvalidTransitionError),
        (OrganizationNotSuspendedError, InvalidTransitionError),
        (AccountSuspendedError, PermissionDeniedError),
        (RoleNotHeldError, InvariantViolationError),
        (CitizenRoleRequiredError, InvariantViolationError),
        (LastOrganizationAdminError, InvariantViolationError),
    ],
)
def test_identity_error_belongs_to_its_kernel_family(
    error_class: type[YakhnamaError], family: type[YakhnamaError]
) -> None:
    assert issubclass(error_class, family)
    assert error_class.code == family.code


def test_user_errors_for_id_carry_the_user_id() -> None:
    errors: list[YakhnamaError] = [
        UserNotFoundError.for_id(USER_ID),
        UserSuspendedError.for_id(USER_ID),
        UserNotSuspendedError.for_id(USER_ID),
        AccountSuspendedError.for_id(USER_ID),
    ]

    for error in errors:
        assert error.details == {"user_id": str(USER_ID)}
        assert str(USER_ID) in error.message


def test_organization_not_found_for_id_and_slug_carry_the_key() -> None:
    by_id = OrganizationNotFoundError.for_id(ORGANIZATION_ID)
    by_slug = OrganizationNotFoundError.for_slug("test-org")

    assert by_id.details == {"organization_id": str(ORGANIZATION_ID)}
    assert by_slug.details == {"slug": "test-org"}
    assert "test-org" in by_slug.message


def test_organization_slug_taken_for_slug_carries_the_slug() -> None:
    error = OrganizationSlugTakenError.for_slug("test-org")

    assert error.details == {"slug": "test-org"}


@pytest.mark.parametrize(
    "error_class",
    [MembershipNotFoundError, DuplicateMembershipError, LastOrganizationAdminError],
)
def test_membership_errors_for_member_carry_both_ids(
    error_class: type[MembershipNotFoundError]
    | type[DuplicateMembershipError]
    | type[LastOrganizationAdminError],
) -> None:
    error = error_class.for_member(ORGANIZATION_ID, USER_ID)

    assert error.details == {
        "organization_id": str(ORGANIZATION_ID),
        "user_id": str(USER_ID),
    }


@pytest.mark.parametrize(
    "error_class", [OrganizationNotActiveError, OrganizationNotSuspendedError]
)
def test_organization_status_errors_carry_id_and_status(
    error_class: type[OrganizationNotActiveError] | type[OrganizationNotSuspendedError],
) -> None:
    error = error_class.for_status(ORGANIZATION_ID, "retired")

    assert error.details == {
        "organization_id": str(ORGANIZATION_ID),
        "status": "retired",
    }
    assert "retired" in error.message
