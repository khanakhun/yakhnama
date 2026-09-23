"""Read models returned by the identity query service and command handlers.

DTOs are frozen and carry only what a reader needs. The only personal data they may
hold is a user's optional display name; subjects, issuers and suspension reasons never
leave the aggregate, because the API returns DTOs as they are (``AGENTS.md`` §5).
``from_entity`` builds a DTO from aggregates for in-memory implementations; the SQL
query service builds the same DTO from selected columns, with the same meanings.

Patterns: DTO.
"""

from collections.abc import Iterable
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.domain.value_objects import (
    DisplayName,
    OrganizationName,
    OrganizationRole,
    OrganizationSlug,
    OrganizationStatus,
    OrganizationType,
    RecordVersion,
    Role,
    UserStatus,
)
from yakhnama.shared_kernel.ids import EntityId

# A user can in principle belong to many organisations; the bound keeps a response
# finite. It is a proposed limit, not a domain rule.
MAX_MEMBERSHIPS_PER_USER = 1_000


class MembershipSummary(BaseModel):
    """One organisation the current user belongs to, as ``GET /me`` shows it.

    Implements: DTO.

    Attributes:
        organization_id: The organisation.
        slug: Its slug, so a client can link to it without a second request.
        role: The user's role there.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: EntityId
    slug: OrganizationSlug
    role: OrganizationRole


class MeDetail(BaseModel):
    """The current user's own record.

    Implements: DTO.

    Attributes:
        id: The user's id.
        display_name: The optional display name, shown only to the user themself.
        roles: Platform roles held explicitly.
        memberships: Memberships of active organisations, ordered by slug.
        version: Optimistic-concurrency version, for ``ETag``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    display_name: DisplayName | None
    roles: frozenset[Role] = Field(max_length=len(Role))
    memberships: tuple[MembershipSummary, ...] = Field(
        max_length=MAX_MEMBERSHIPS_PER_USER
    )
    version: RecordVersion

    @classmethod
    def from_entities(
        cls,
        user: User,
        memberships: Iterable[tuple[Membership, Organization]],
    ) -> Self:
        """Build the view of a user and their memberships.

        Memberships of organisations that are not active are left out, as they are
        from the user's ``Actor`` (data dictionary, identity, Q-I7).

        Args:
            user: The user.
            memberships: Each membership with its organisation.

        Returns:
            The user's view of themself.
        """
        summaries = sorted(
            (
                MembershipSummary(
                    organization_id=organization.id,
                    slug=organization.slug,
                    role=membership.role,
                )
                for membership, organization in memberships
                if organization.is_active
            ),
            key=lambda summary: summary.slug,
        )
        return cls(
            id=user.id,
            display_name=user.display_name,
            roles=user.roles,
            memberships=tuple(summaries),
            version=user.version,
        )


class UserDetail(BaseModel):
    """A user as an administrator sees them after changing them.

    The suspension reason is deliberately absent: it is a moderator's free text and
    may mention a person.

    Implements: DTO.

    Attributes:
        id: The user's id.
        display_name: The optional display name.
        roles: Platform roles held explicitly.
        status: ``active`` or ``suspended``.
        version: Optimistic-concurrency version, for ``ETag``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    display_name: DisplayName | None
    roles: frozenset[Role] = Field(max_length=len(Role))
    status: UserStatus
    version: RecordVersion

    @classmethod
    def from_entity(cls, user: User) -> Self:
        """Build the view of a user.

        Args:
            user: The aggregate.

        Returns:
            Its detail view.
        """
        return cls(
            id=user.id,
            display_name=user.display_name,
            roles=user.roles,
            status=user.status,
            version=user.version,
        )


class OrganizationSummary(BaseModel):
    """One organisation in a listing.

    Implements: DTO.

    Attributes:
        id: The organisation's id.
        slug: URL-safe handle.
        name: Display name.
        organization_type: The kind of organisation.
        status: ``active``, ``suspended`` or ``retired``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    slug: OrganizationSlug
    name: OrganizationName
    organization_type: OrganizationType
    status: OrganizationStatus

    @classmethod
    def from_entity(cls, organization: Organization) -> Self:
        """Build the summary of an organisation.

        Args:
            organization: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            id=organization.id,
            slug=organization.slug,
            name=organization.name,
            organization_type=organization.organization_type,
            status=organization.status,
        )


class OrganizationDetail(BaseModel):
    """One organisation with its member count.

    The status reason is deliberately absent (see ``UserDetail``).

    Implements: DTO.

    Attributes:
        id: The organisation's id.
        slug: URL-safe handle.
        name: Display name.
        organization_type: The kind of organisation.
        status: ``active``, ``suspended`` or ``retired``.
        version: Optimistic-concurrency version, for ``ETag``.
        member_count: How many users belong to it, admins included.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    slug: OrganizationSlug
    name: OrganizationName
    organization_type: OrganizationType
    status: OrganizationStatus
    version: RecordVersion
    member_count: int = Field(ge=0)

    @classmethod
    def from_entity(cls, organization: Organization, member_count: int) -> Self:
        """Build the detail view of an organisation.

        Args:
            organization: The aggregate.
            member_count: How many memberships it has.

        Returns:
            Its detail view.
        """
        return cls(
            id=organization.id,
            slug=organization.slug,
            name=organization.name,
            organization_type=organization.organization_type,
            status=organization.status,
            version=organization.version,
            member_count=member_count,
        )


class MemberSummary(BaseModel):
    """One member of an organisation in a listing.

    Implements: DTO.

    Attributes:
        user_id: The member.
        display_name: The member's optional display name.
        role: ``member`` or ``admin`` of the organisation.
        since: When the user joined, UTC.
        version: The membership's optimistic-concurrency version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: EntityId
    display_name: DisplayName | None
    role: OrganizationRole
    since: AwareDatetime
    version: RecordVersion

    @classmethod
    def from_entities(cls, membership: Membership, user: User) -> Self:
        """Build the listing entry of one member.

        Args:
            membership: The membership.
            user: The member.

        Returns:
            The member's summary.
        """
        return cls(
            user_id=user.id,
            display_name=user.display_name,
            role=membership.role,
            since=membership.created_at,
            version=membership.version,
        )
