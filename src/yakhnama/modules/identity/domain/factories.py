"""Creation of users, organisations and memberships.

``UserFactory.mirror`` records a user the first time a valid token for their
``(issuer, subject)`` is seen. Realm roles from the token are copied then, and only
then; roles granted in Yakhnama are added later through ``User.grant_role``. Keeping
later realm-role changes in sync is an open question (Phase 2 plan Q2, data
dictionary identity), because the user record does not yet say which roles came from
the provider.

Patterns: Factory.
"""

from collections.abc import Iterable

from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.domain.errors import (
    OrganizationNotActiveError,
    UserSuspendedError,
)
from yakhnama.modules.identity.domain.events import (
    MembershipAdded,
    OrganizationCreated,
    UserMirrored,
)
from yakhnama.modules.identity.domain.value_objects import (
    ExternalIdentity,
    OrganizationRole,
    OrganizationType,
    Role,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator


class UserFactory:
    """Mirror users from an OpenID Connect identity.

    Implements: Factory.
    """

    def mirror(
        self,
        identity: ExternalIdentity,
        display_name: str | None,
        realm_roles: Iterable[Role],
        *,
        ids: IdGenerator,
        clock: Clock,
    ) -> AggregateChange[User]:
        """Create the user mirror at version 1 and ``UserMirrored``.

        Checking that no user with the same ``(issuer, subject)`` exists needs a
        repository and belongs to the command handler.

        Args:
            identity: The token's ``(iss, sub)`` claims.
            display_name: A display name from the token, or ``None``.
            realm_roles: Roles the platform's token adapter mapped from the
                provider's realm roles; ``citizen`` is always added.
            ids: Source of the user id and the event id.
            clock: Source of every timestamp.

        Returns:
            The new user and ``UserMirrored``.

        Raises:
            pydantic.ValidationError: If the display name is malformed.
        """
        now = clock.now()
        user = User(
            id=ids.new_id(),
            subject=identity.subject,
            issuer=identity.issuer,
            display_name=display_name,
            roles=frozenset({Role.CITIZEN, *realm_roles}),
            created_at=now,
            updated_at=now,
            last_seen_at=now,
        )
        event = UserMirrored(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=user.id,
            version=user.version,
            roles=user.roles,
        )
        return AggregateChange[User](state=user, events=(event,))


class OrganizationFactory:
    """Create organisations.

    Implements: Factory.
    """

    def create(
        self,
        slug: str,
        name: str,
        organization_type: OrganizationType,
        *,
        ids: IdGenerator,
        clock: Clock,
    ) -> AggregateChange[Organization]:
        """Create an active organisation at version 1 and ``OrganizationCreated``.

        Checking that the slug is free needs a repository and belongs to the
        command handler, which raises ``OrganizationSlugTakenError``.

        Args:
            slug: URL-safe handle.
            name: Display name.
            organization_type: The kind of organisation.
            ids: Source of the organisation id and the event id.
            clock: Source of every timestamp.

        Returns:
            The new organisation and ``OrganizationCreated``.

        Raises:
            pydantic.ValidationError: If ``slug`` or ``name`` is malformed.
        """
        now = clock.now()
        organization = Organization(
            id=ids.new_id(),
            slug=slug,
            name=name,
            organization_type=organization_type,
            created_at=now,
            updated_at=now,
        )
        event = OrganizationCreated(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=organization.id,
            slug=organization.slug,
            version=organization.version,
            organization_type=organization.organization_type,
        )
        return AggregateChange[Organization](state=organization, events=(event,))


class MembershipFactory:
    """Create memberships of active users in active organisations.

    Implements: Factory.
    """

    def create(
        self,
        organization: Organization,
        user: User,
        role: OrganizationRole,
        *,
        ids: IdGenerator,
        clock: Clock,
    ) -> AggregateChange[Membership]:
        """Create a membership at version 1 and ``MembershipAdded``.

        Hand the result to ``Memberships.add``, which enforces one membership per
        user in the organisation.

        Args:
            organization: The loaded organisation; it must be active.
            user: The loaded user; they must be active.
            role: The new member's role.
            ids: Source of the membership id and the event id.
            clock: Source of every timestamp.

        Returns:
            The new membership and ``MembershipAdded``.

        Raises:
            OrganizationNotActiveError: If the organisation is suspended or retired.
            UserSuspendedError: If the user is suspended.
        """
        if not organization.is_active:
            raise OrganizationNotActiveError.for_status(
                organization.id, organization.status.value
            )
        if not user.is_active:
            raise UserSuspendedError.for_id(user.id)
        now = clock.now()
        membership = Membership(
            id=ids.new_id(),
            organization_id=organization.id,
            user_id=user.id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        event = MembershipAdded(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=membership.id,
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            role=membership.role,
            version=membership.version,
        )
        return AggregateChange[Membership](state=membership, events=(event,))
