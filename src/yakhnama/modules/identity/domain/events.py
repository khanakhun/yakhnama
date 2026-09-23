"""Domain events of the ``identity`` bounded context.

Every event carries the aggregate's id as ``aggregate_id``, its type as
``aggregate_type`` and its ``version`` after the change. **Payloads carry ids, slugs,
types, statuses and roles only**: never a display name, subject, issuer or status
reason, because events are relayed to subscribers and kept in the outbox, where
personal data must not travel (``AGENTS.md`` §5).

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from pydantic import Field

from yakhnama.modules.identity.domain.value_objects import (
    OrganizationRole,
    OrganizationSlug,
    OrganizationType,
    RecordVersion,
    Role,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

USER_AGGREGATE_TYPE: Final = "user"
ORGANIZATION_AGGREGATE_TYPE: Final = "organization"
MEMBERSHIP_AGGREGATE_TYPE: Final = "membership"

# --------------------------------------------------------------------------- #
# Users                                                                       #
# --------------------------------------------------------------------------- #


class UserEvent(DomainEvent):
    """Fields shared by every event about a user; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"user"``.
        version: The user's version after the change.
    """

    aggregate_type: Literal["user"] = USER_AGGREGATE_TYPE
    version: RecordVersion


class UserMirrored(UserEvent):
    """A user was seen for the first time and mirrored from their token.

    Implements: Domain Events.

    Attributes:
        roles: The roles the user started with (``citizen`` plus mirrored realm
            roles).
    """

    event_type: ClassVar[str] = "identity.user_mirrored"

    roles: frozenset[Role] = Field(min_length=1, max_length=len(Role))


class UserRenamed(UserEvent):
    """The user's display name was set, changed or cleared.

    The name itself is deliberately absent.

    Implements: Domain Events.

    Attributes:
        has_display_name: ``False`` if the display name was cleared.
    """

    event_type: ClassVar[str] = "identity.user_renamed"

    has_display_name: bool


class UserRoleGranted(UserEvent):
    """A platform role was granted to a user.

    Implements: Domain Events.

    Attributes:
        role: The granted role.
    """

    event_type: ClassVar[str] = "identity.user_role_granted"

    role: Role


class UserRoleRevoked(UserEvent):
    """A platform role was revoked from a user.

    Implements: Domain Events.

    Attributes:
        role: The revoked role.
    """

    event_type: ClassVar[str] = "identity.user_role_revoked"

    role: Role


class UserSuspended(UserEvent):
    """A user was suspended; the reason stays on the aggregate.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.user_suspended"


class UserReinstated(UserEvent):
    """A suspended user was reinstated.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.user_reinstated"


# --------------------------------------------------------------------------- #
# Organisations                                                               #
# --------------------------------------------------------------------------- #


class OrganizationEvent(DomainEvent):
    """Fields shared by every event about an organisation; never published alone.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"organization"``.
        slug: The organisation's slug, so a log line is readable on its own.
        version: The organisation's version after the change.
    """

    aggregate_type: Literal["organization"] = ORGANIZATION_AGGREGATE_TYPE
    slug: OrganizationSlug
    version: RecordVersion


class OrganizationCreated(OrganizationEvent):
    """An organisation was created.

    Implements: Domain Events.

    Attributes:
        organization_type: The kind of organisation.
    """

    event_type: ClassVar[str] = "identity.organization_created"

    organization_type: OrganizationType


class OrganizationRenamed(OrganizationEvent):
    """An organisation's display name changed; the name is on the aggregate.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.organization_renamed"


class OrganizationSuspended(OrganizationEvent):
    """An organisation was suspended; the reason stays on the aggregate.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.organization_suspended"


class OrganizationReinstated(OrganizationEvent):
    """A suspended organisation was made active again.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.organization_reinstated"


class OrganizationRetired(OrganizationEvent):
    """An organisation was retired for good; the reason stays on the aggregate.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.organization_retired"


# --------------------------------------------------------------------------- #
# Memberships                                                                 #
# --------------------------------------------------------------------------- #


class MembershipEvent(DomainEvent):
    """Fields shared by every event about a membership; never published alone.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"membership"``.
        organization_id: The organisation.
        user_id: The member.
        role: The member's role after the change (at removal: the role they had).
        version: The membership's version after the change (at removal: its last
            version).
    """

    aggregate_type: Literal["membership"] = MEMBERSHIP_AGGREGATE_TYPE
    organization_id: EntityId
    user_id: EntityId
    role: OrganizationRole
    version: RecordVersion


class MembershipAdded(MembershipEvent):
    """A user joined an organisation.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.membership_added"


class MembershipRoleChanged(MembershipEvent):
    """A member's role in an organisation changed.

    Implements: Domain Events.

    Attributes:
        previous_role: The role before the change.
    """

    event_type: ClassVar[str] = "identity.membership_role_changed"

    previous_role: OrganizationRole


class MembershipRemoved(MembershipEvent):
    """A user left or was removed from an organisation.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "identity.membership_removed"
