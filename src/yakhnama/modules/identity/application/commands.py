"""Write requests accepted by the identity command handlers.

Every command except ``EnsureUserFromPrincipal`` carries the ``actor`` the request
runs as; that one carries the verified token's claims instead, because it is how an
actor comes to exist. Commands acting on an existing aggregate accept an optional
``expected_version``: the API fills it from ``If-Match``, and the handler raises
``PreconditionFailedError`` (HTTP 412) when the stored version differs.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    DisplayName,
    ExternalIdentity,
    OrganizationName,
    OrganizationRole,
    OrganizationSlug,
    OrganizationType,
    RecordVersion,
    Role,
    StatusReason,
)
from yakhnama.shared_kernel.ids import EntityId


class EnsureUserFromPrincipal(BaseModel):
    """Mirror the caller on first sight, touch them afterwards, return their actor.

    The token's display-name claim is deliberately not part of the command: a new
    user starts without a display name and sets one through ``RenameSelf``
    (``PATCH /me``), so no personal data is copied from the provider without the
    user choosing it (security review, Phase 2).

    Implements: Command.

    Attributes:
        identity: The verified token's ``(iss, sub)``.
        realm_roles: Roles mapped from the token's realm roles by the platform;
            copied only when the user is first mirrored (Q-I1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: ExternalIdentity
    realm_roles: frozenset[Role] = Field(default=frozenset(), max_length=len(Role))


class CreateOrganization(BaseModel):
    """Create an organisation whose creator becomes its first admin.

    Implements: Command.

    Attributes:
        actor: Who asks.
        slug: URL-safe handle, unique among organisations.
        name: Display name.
        organization_type: The kind of organisation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    slug: OrganizationSlug
    name: OrganizationName
    organization_type: OrganizationType


class RenameOrganization(BaseModel):
    """Change an organisation's display name.

    Implements: Command.

    Attributes:
        actor: Who asks.
        organization_id: The organisation.
        name: The new display name.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    organization_id: EntityId
    name: OrganizationName
    expected_version: RecordVersion | None = None


class AddMember(BaseModel):
    """Add a user to an organisation.

    Implements: Command.

    Attributes:
        actor: Who asks.
        organization_id: The organisation.
        user_id: The user to add.
        role: Their role in the organisation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    organization_id: EntityId
    user_id: EntityId
    role: OrganizationRole = OrganizationRole.MEMBER


class ChangeMemberRole(BaseModel):
    """Change a member's role in an organisation.

    Implements: Command.

    Attributes:
        actor: Who asks.
        organization_id: The organisation.
        user_id: The member.
        role: The new role.
        expected_membership_id: The membership the client's ``If-Match`` tag
            names; a membership removed and re-added has a new id, so a tag for
            the old one never matches the new one even at the same version.
        expected_version: The membership version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    organization_id: EntityId
    user_id: EntityId
    role: OrganizationRole
    expected_membership_id: EntityId | None = None
    expected_version: RecordVersion | None = None


class RemoveMember(BaseModel):
    """Remove a member from an organisation.

    Implements: Command.

    Attributes:
        actor: Who asks.
        organization_id: The organisation.
        user_id: The member.
        expected_membership_id: The membership the client's ``If-Match`` tag
            names; a membership removed and re-added has a new id, so a tag for
            the old one never matches the new one even at the same version.
        expected_version: The membership version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    organization_id: EntityId
    user_id: EntityId
    expected_membership_id: EntityId | None = None
    expected_version: RecordVersion | None = None


class GrantRole(BaseModel):
    """Grant a platform role to a user.

    Implements: Command.

    Attributes:
        actor: Who asks.
        user_id: The user.
        role: The role to grant.
        expected_version: The user version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    user_id: EntityId
    role: Role
    expected_version: RecordVersion | None = None


class RevokeRole(BaseModel):
    """Revoke a platform role from a user.

    Implements: Command.

    Attributes:
        actor: Who asks.
        user_id: The user.
        role: The role to revoke; never ``citizen``.
        expected_version: The user version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    user_id: EntityId
    role: Role
    expected_version: RecordVersion | None = None


class SuspendUser(BaseModel):
    """Suspend a user so they can no longer act.

    Implements: Command.

    Attributes:
        actor: Who asks.
        user_id: The user.
        reason: Why; kept on the user, never in events or logs.
        expected_version: The user version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    user_id: EntityId
    reason: StatusReason
    expected_version: RecordVersion | None = None


class ReinstateUser(BaseModel):
    """Make a suspended user active again.

    Implements: Command.

    Attributes:
        actor: Who asks.
        user_id: The user.
        expected_version: The user version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    user_id: EntityId
    expected_version: RecordVersion | None = None


class RenameSelf(BaseModel):
    """Set, change or clear the acting user's own display name.

    Implements: Command.

    Attributes:
        actor: Who asks; the user renamed is always the actor.
        display_name: The new name, or ``None`` to clear it.
        expected_version: The user version the client last saw.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    display_name: DisplayName | None
    expected_version: RecordVersion | None = None
