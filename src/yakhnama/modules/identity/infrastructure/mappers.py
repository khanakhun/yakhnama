"""Translate between the identity aggregates and their row models.

Every read goes through ``model_validate``, so a row that no longer satisfies the
domain's invariants (an unknown role, a suspended user without a reason) fails loudly
instead of producing an invalid aggregate. ``roles`` is written as a sorted list so
the same role set always produces the same JSONB value.

Patterns: Anti-Corruption Layer (mapper).
"""

from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.infrastructure.orm import (
    MembershipRow,
    OrganizationRow,
    UserRow,
)


def roles_to_column(user: User) -> list[str]:
    """Return the JSONB value of the user's roles.

    Args:
        user: The aggregate.

    Returns:
        The role values, sorted.
    """
    return sorted(role.value for role in user.roles)


def user_to_row(user: User) -> UserRow:
    """Build the ``users`` row of ``user``.

    Args:
        user: The aggregate.

    Returns:
        A transient ``UserRow`` carrying the same values.
    """
    return UserRow(
        id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        display_name=user.display_name,
        roles=roles_to_column(user),
        status=user.status.value,
        status_reason=user.status_reason,
        version=user.version,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_seen_at=user.last_seen_at,
    )


def row_to_user(row: UserRow) -> User:
    """Rebuild a user from its row.

    Args:
        row: A row loaded from ``users``.

    Returns:
        The validated ``User``.
    """
    return User.model_validate(
        {
            "id": row.id,
            "issuer": row.issuer,
            "subject": row.subject,
            "display_name": row.display_name,
            "roles": frozenset(row.roles),
            "status": row.status,
            "status_reason": row.status_reason,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "last_seen_at": row.last_seen_at,
        }
    )


def organization_to_row(organization: Organization) -> OrganizationRow:
    """Build the ``organizations`` row of ``organization``.

    Args:
        organization: The aggregate.

    Returns:
        A transient ``OrganizationRow`` carrying the same values.
    """
    return OrganizationRow(
        id=organization.id,
        slug=organization.slug,
        name=organization.name,
        organization_type=organization.organization_type.value,
        status=organization.status.value,
        status_reason=organization.status_reason,
        version=organization.version,
        created_at=organization.created_at,
        updated_at=organization.updated_at,
    )


def row_to_organization(row: OrganizationRow) -> Organization:
    """Rebuild an organisation from its row.

    Args:
        row: A row loaded from ``organizations``.

    Returns:
        The validated ``Organization``.
    """
    return Organization.model_validate(
        {
            "id": row.id,
            "slug": row.slug,
            "name": row.name,
            "organization_type": row.organization_type,
            "status": row.status,
            "status_reason": row.status_reason,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def membership_to_row(membership: Membership) -> MembershipRow:
    """Build the ``memberships`` row of ``membership``.

    Args:
        membership: The membership.

    Returns:
        A transient ``MembershipRow`` carrying the same values.
    """
    return MembershipRow(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role.value,
        version=membership.version,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def row_to_membership(row: MembershipRow) -> Membership:
    """Rebuild a membership from its row.

    Args:
        row: A row loaded from ``memberships``.

    Returns:
        The validated ``Membership``.
    """
    return Membership.model_validate(
        {
            "id": row.id,
            "organization_id": row.organization_id,
            "user_id": row.user_id,
            "role": row.role,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )
