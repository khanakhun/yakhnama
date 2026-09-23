"""Errors of the ``identity`` bounded context.

Every class subclasses one of the shared-kernel families, so the API maps it to Problem
Details by family without importing this module (``AGENTS.md`` §2.3). Messages and
details carry ids, slugs and roles only: never a display name, subject or issuer,
because those identify or describe a person (``AGENTS.md`` §5).

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import Self

from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
)
from yakhnama.shared_kernel.ids import EntityId


class UserNotFoundError(NotFoundError):
    """No user mirror exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, user_id: EntityId) -> Self:
        """Build the error for a missing user id.

        Args:
            user_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(f"no user with id {user_id}", details={"user_id": str(user_id)})


class OrganizationNotFoundError(NotFoundError):
    """No organisation exists with the requested id or slug.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, organization_id: EntityId) -> Self:
        """Build the error for a missing organisation id.

        Args:
            organization_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            f"no organisation with id {organization_id}",
            details={"organization_id": str(organization_id)},
        )

    @classmethod
    def for_slug(cls, slug: str) -> Self:
        """Build the error for a missing organisation slug.

        Args:
            slug: The slug that was looked up.

        Returns:
            The error, with the slug in ``details``.
        """
        return cls(f"no organisation with slug {slug!r}", details={"slug": slug})


class MembershipNotFoundError(NotFoundError):
    """The user is not a member of the organisation.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_member(cls, organization_id: EntityId, user_id: EntityId) -> Self:
        """Build the error for a missing membership.

        Args:
            organization_id: The organisation.
            user_id: The user who is not a member.

        Returns:
            The error, with both ids in ``details``.
        """
        return cls(
            f"user {user_id} is not a member of organisation {organization_id}",
            details={"organization_id": str(organization_id), "user_id": str(user_id)},
        )


class DuplicateMembershipError(ConflictError):
    """The user already has a membership in the organisation.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_member(cls, organization_id: EntityId, user_id: EntityId) -> Self:
        """Build the error for a second membership of the same user.

        Args:
            organization_id: The organisation.
            user_id: The user who is already a member.

        Returns:
            The error, with both ids in ``details``.
        """
        return cls(
            f"user {user_id} is already a member of organisation {organization_id}",
            details={"organization_id": str(organization_id), "user_id": str(user_id)},
        )


class OrganizationSlugTakenError(ConflictError):
    """Another organisation already uses the slug.

    Raised by the application layer, which can see every organisation.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_slug(cls, slug: str) -> Self:
        """Build the error for a slug in use.

        Args:
            slug: The requested slug.

        Returns:
            The error, with the slug in ``details``.
        """
        return cls(f"organisation slug {slug!r} is taken", details={"slug": slug})


class UserSuspendedError(InvalidTransitionError):
    """The user is suspended and the requested change needs an active user.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, user_id: EntityId) -> Self:
        """Build the error for a change refused because the user is suspended.

        Args:
            user_id: The suspended user.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(f"user {user_id} is suspended", details={"user_id": str(user_id)})


class UserNotSuspendedError(InvalidTransitionError):
    """Reinstatement was asked for a user who is not suspended.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, user_id: EntityId) -> Self:
        """Build the error for reinstating an active user.

        Args:
            user_id: The active user.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            f"user {user_id} is not suspended", details={"user_id": str(user_id)}
        )


class AccountSuspendedError(PermissionDeniedError):
    """A suspended user tried to act; no actor is built for them.

    Distinct from ``UserSuspendedError``: that one refuses a *change to* a suspended
    user, this one refuses a request *by* a suspended user, which the API reports as
    forbidden rather than as a conflict.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, user_id: EntityId) -> Self:
        """Build the error for a request by a suspended user.

        Args:
            user_id: The suspended user.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            f"the account of user {user_id} is suspended",
            details={"user_id": str(user_id)},
        )


class RoleNotHeldError(InvariantViolationError):
    """A role cannot be revoked because the user does not hold it explicitly.

    Implements: Domain Error (proposed in ADR 0012).
    """


class CitizenRoleRequiredError(InvariantViolationError):
    """The ``citizen`` role cannot be revoked from a user.

    Every active user holds ``citizen``; suspension, not revocation, is how a user
    loses the right to act.

    Implements: Domain Error (proposed in ADR 0012).
    """


class LastOrganizationAdminError(InvariantViolationError):
    """The change would leave an organisation that has an admin with none.

    A **proposed** rule (data dictionary, identity): the only admin of an organisation
    cannot be removed or demoted; another admin must be appointed first.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_member(cls, organization_id: EntityId, user_id: EntityId) -> Self:
        """Build the error for removing or demoting the last admin.

        Args:
            organization_id: The organisation.
            user_id: Its only admin.

        Returns:
            The error, with both ids in ``details``.
        """
        return cls(
            f"user {user_id} is the only admin of organisation {organization_id}",
            details={"organization_id": str(organization_id), "user_id": str(user_id)},
        )


class OrganizationNotActiveError(InvalidTransitionError):
    """The organisation is suspended or retired and the change needs it active.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_status(cls, organization_id: EntityId, status: str) -> Self:
        """Build the error for a change refused by the organisation's status.

        Args:
            organization_id: The organisation.
            status: Its current status.

        Returns:
            The error, with the id and status in ``details``.
        """
        return cls(
            f"organisation {organization_id} is {status}",
            details={"organization_id": str(organization_id), "status": status},
        )


class OrganizationNotSuspendedError(InvalidTransitionError):
    """Reinstatement was asked for an organisation that is not suspended.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_status(cls, organization_id: EntityId, status: str) -> Self:
        """Build the error for reinstating an organisation that is not suspended.

        Args:
            organization_id: The organisation.
            status: Its current status.

        Returns:
            The error, with the id and status in ``details``.
        """
        return cls(
            f"organisation {organization_id} is {status}, not suspended",
            details={"organization_id": str(organization_id), "status": status},
        )
