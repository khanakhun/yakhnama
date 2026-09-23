"""In-memory fakes of the identity ports, fake policies and an actor builder.

The repositories stage writes until the unit of work commits, exactly as a
rolled-back database transaction leaves the tables unchanged, and enforce the same
uniqueness and optimistic-concurrency rules the SQL adapters promise in
``yakhnama.modules.identity.application.ports``. The query service reads only
committed rows.

``AllowAllPolicy`` and ``DenyAllPolicy`` satisfy ``AuthorisationPolicy`` and record
every actor they were asked about. ``actor_with`` builds an authenticated actor that
looks like an active user's.

Patterns: Fake.
"""

from collections.abc import Iterable
from datetime import datetime

from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.identity.application.dto import (
    MeDetail,
    MemberSummary,
    OrganizationDetail,
)
from yakhnama.modules.identity.application.queries import ListOrganizationMembers
from yakhnama.modules.identity.domain.entities import (
    Membership,
    Memberships,
    Organization,
    User,
)
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    ExternalIdentity,
    OrganizationRole,
    Role,
)
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor

DEFAULT_ACTOR_ID = SequentialIdGenerator(seed=4242).new_id()
"""The user id ``actor_with`` uses unless told otherwise."""


def actor_with(
    roles: Iterable[Role] = (),
    *,
    user_id: EntityId = DEFAULT_ACTOR_ID,
    memberships: Iterable[tuple[EntityId, OrganizationRole]] = (),
) -> Actor:
    """Build an authenticated actor holding ``citizen`` plus ``roles``.

    Args:
        roles: Extra platform roles.
        user_id: The actor's user id.
        memberships: ``(organization_id, role)`` pairs.

    Returns:
        The actor, as ``User.to_actor`` would build it for an active user.
    """
    return Actor(
        user_id=user_id,
        roles=frozenset({Role.CITIZEN, *roles}),
        memberships=frozenset(memberships),
    )


# --------------------------------------------------------------------------- #
# Policies                                                                    #
# --------------------------------------------------------------------------- #


class AllowAllPolicy:
    """``AuthorisationPolicy`` that allows every actor, anonymous included.

    Implements: Fake (of Policy).

    Attributes:
        checked: Actors the policy was asked about, in call order.
    """

    def __init__(self) -> None:
        """Create the policy."""
        self.checked: list[Actor] = []

    def is_allowed(self, actor: Actor) -> bool:
        """Record ``actor`` and allow it.

        Args:
            actor: The actor asked about.

        Returns:
            Always ``True``.
        """
        self.checked.append(actor)
        return True


class DenyAllPolicy:
    """``AuthorisationPolicy`` that refuses every actor.

    Implements: Fake (of Policy).

    Attributes:
        checked: Actors the policy was asked about, in call order.
    """

    def __init__(self) -> None:
        """Create the policy."""
        self.checked: list[Actor] = []

    def is_allowed(self, actor: Actor) -> bool:
        """Record ``actor`` and refuse it.

        Args:
            actor: The actor asked about.

        Returns:
            Always ``False``.
        """
        self.checked.append(actor)
        return False


# --------------------------------------------------------------------------- #
# Repositories                                                                #
# --------------------------------------------------------------------------- #


def _require_next_version(stored_version: int, new_version: int, label: str) -> None:
    if new_version != stored_version + 1:
        message = f"{label} was changed concurrently"
        raise ConflictError(message, details={"stored_version": stored_version})


class InMemoryUserRepository:
    """``UserRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored users, as a committed transaction left them.
        touches: How many ``touch`` writes were committed.
    """

    def __init__(self, users: Iterable[User] = ()) -> None:
        """Create the repository.

        Args:
            users: Users that exist before the test acts.
        """
        self.committed: dict[EntityId, User] = {user.id: user for user in users}
        self._staged: dict[EntityId, User] = {}
        self._staged_touches = 0
        self.touches = 0

    def _current(self) -> dict[EntityId, User]:
        return {**self.committed, **self._staged}

    def _find_by_identity(self, identity: ExternalIdentity) -> User | None:
        # Uniqueness checks use this, not the public lookup, so a test subclass
        # that overrides the lookup cannot switch the constraint off.
        return next(
            (
                user
                for user in self._current().values()
                if user.external_identity == identity
            ),
            None,
        )

    async def get(self, user_id: EntityId) -> User | None:
        """Return the user, staged changes included.

        Args:
            user_id: The user's id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(user_id)

    async def get_by_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the user mirrored from ``identity``.

        Args:
            identity: The ``(issuer, subject)`` pair.

        Returns:
            The aggregate, or ``None``.
        """
        return self._find_by_identity(identity)

    async def add(self, user: User) -> None:
        """Stage a new user.

        Args:
            user: The new aggregate.

        Raises:
            ConflictError: If the id or the identity is taken.
        """
        current = self._current()
        if user.id in current or self._find_by_identity(user.external_identity):
            message = "the user already exists"
            raise ConflictError(message)
        self._staged[user.id] = user

    async def save(self, user: User) -> None:
        """Stage a changed user.

        Args:
            user: The new state.

        Raises:
            NotFoundError: If the user is not stored.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(user.id)
        if stored is None:
            message = f"user {user.id} is not stored"
            raise NotFoundError(message)
        _require_next_version(stored.version, user.version, f"user {user.id}")
        self._staged[user.id] = user

    async def touch(self, user: User) -> None:
        """Stage the new ``last_seen_at`` only.

        Args:
            user: The touched user.

        Raises:
            NotFoundError: If the user is not stored.
        """
        stored = self._current().get(user.id)
        if stored is None:
            message = f"user {user.id} is not stored"
            raise NotFoundError(message)
        last_seen_at = max(stored.last_seen_at, user.last_seen_at)
        self._staged[user.id] = stored.model_copy(update={"last_seen_at": last_seen_at})
        self._staged_touches += 1

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self.touches += self._staged_touches
        self.discard_staged()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()
        self._staged_touches = 0


class InMemoryOrganizationRepository:
    """``OrganizationRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored organisations, as a committed transaction left them.
    """

    def __init__(self, organizations: Iterable[Organization] = ()) -> None:
        """Create the repository.

        Args:
            organizations: Organisations that exist before the test acts.
        """
        self.committed: dict[EntityId, Organization] = {
            organization.id: organization for organization in organizations
        }
        self._staged: dict[EntityId, Organization] = {}

    def _current(self) -> dict[EntityId, Organization]:
        return {**self.committed, **self._staged}

    async def get(self, organization_id: EntityId) -> Organization | None:
        """Return the organisation, staged changes included.

        Args:
            organization_id: The organisation's id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Return the organisation with ``slug``.

        Args:
            slug: The slug.

        Returns:
            The aggregate, or ``None``.
        """
        return next(
            (item for item in self._current().values() if item.slug == slug), None
        )

    async def add(self, organization: Organization) -> None:
        """Stage a new organisation.

        Args:
            organization: The new aggregate.

        Raises:
            ConflictError: If the id or the slug is taken.
        """
        if organization.id in self._current() or await self.get_by_slug(
            organization.slug
        ):
            message = "the organisation already exists"
            raise ConflictError(message)
        self._staged[organization.id] = organization

    async def save(self, organization: Organization) -> None:
        """Stage a changed organisation.

        Args:
            organization: The new state.

        Raises:
            NotFoundError: If the organisation is not stored.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(organization.id)
        if stored is None:
            message = f"organisation {organization.id} is not stored"
            raise NotFoundError(message)
        _require_next_version(
            stored.version, organization.version, f"organisation {organization.id}"
        )
        self._staged[organization.id] = organization

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


def _joining_order(membership: Membership) -> tuple[datetime, EntityId]:
    return membership.created_at, membership.id


class InMemoryMembershipRepository:
    """``MembershipRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored memberships, as a committed transaction left them.
    """

    def __init__(self, memberships: Iterable[Membership] = ()) -> None:
        """Create the repository.

        Args:
            memberships: Memberships that exist before the test acts.
        """
        self.committed: dict[EntityId, Membership] = {
            membership.id: membership for membership in memberships
        }
        # None marks a staged removal.
        self._staged: dict[EntityId, Membership | None] = {}

    def _current(self) -> dict[EntityId, Membership]:
        merged: dict[EntityId, Membership | None] = {**self.committed, **self._staged}
        return {key: value for key, value in merged.items() if value is not None}

    async def list_for_organization(self, organization_id: EntityId) -> Memberships:
        """Return the organisation's memberships in joining order.

        Args:
            organization_id: The organisation.

        Returns:
            The collection.
        """
        members = sorted(
            (
                membership
                for membership in self._current().values()
                if membership.organization_id == organization_id
            ),
            key=_joining_order,
        )
        return Memberships(organization_id=organization_id, members=tuple(members))

    async def list_for_user(self, user_id: EntityId) -> tuple[Membership, ...]:
        """Return the user's memberships, oldest first.

        Args:
            user_id: The user.

        Returns:
            The memberships.
        """
        return tuple(
            sorted(
                (
                    membership
                    for membership in self._current().values()
                    if membership.user_id == user_id
                ),
                key=_joining_order,
            )
        )

    async def add(self, membership: Membership) -> None:
        """Stage a new membership.

        Args:
            membership: The new membership.

        Raises:
            ConflictError: If the id or the ``(organization, user)`` pair is taken.
        """
        current = self._current()
        if membership.id in current or any(
            stored.ref == membership.ref for stored in current.values()
        ):
            message = "the membership already exists"
            raise ConflictError(message)
        self._staged[membership.id] = membership

    async def save(self, membership: Membership) -> None:
        """Stage a changed membership.

        Args:
            membership: The new state.

        Raises:
            NotFoundError: If the membership is not stored.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(membership.id)
        if stored is None:
            message = f"membership {membership.id} is not stored"
            raise NotFoundError(message)
        _require_next_version(
            stored.version, membership.version, f"membership {membership.id}"
        )
        self._staged[membership.id] = membership

    async def remove(self, membership_id: EntityId) -> None:
        """Stage the removal of a membership.

        Args:
            membership_id: The membership.

        Raises:
            NotFoundError: If the membership is not stored.
        """
        if membership_id not in self._current():
            message = f"membership {membership_id} is not stored"
            raise NotFoundError(message)
        self._staged[membership_id] = None

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        for membership_id, membership in self._staged.items():
            if membership is None:
                self.committed.pop(membership_id, None)
            else:
                self.committed[membership_id] = membership
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryIdentityUnitOfWork(InMemoryUnitOfWork):
    """``IdentityUnitOfWork`` over in-memory repositories.

    Implements: Fake (of Unit of Work).

    Attributes:
        users: The user repository bound to this unit of work.
        organizations: The organisation repository bound to this unit of work.
        memberships: The membership repository bound to this unit of work.
    """

    def __init__(
        self,
        *,
        users: Iterable[User] = (),
        organizations: Iterable[Organization] = (),
        memberships: Iterable[Membership] = (),
    ) -> None:
        """Create the unit of work.

        Args:
            users: Users that exist before the test acts.
            organizations: Organisations that exist before the test acts.
            memberships: Memberships that exist before the test acts.
        """
        super().__init__()
        self.users = InMemoryUserRepository(users)
        self.organizations = InMemoryOrganizationRepository(organizations)
        self.memberships = InMemoryMembershipRepository(memberships)

    def _on_commit(self) -> None:
        self.users.apply_staged()
        self.organizations.apply_staged()
        self.memberships.apply_staged()

    def _on_rollback(self) -> None:
        self.users.discard_staged()
        self.organizations.discard_staged()
        self.memberships.discard_staged()


# --------------------------------------------------------------------------- #
# Query service                                                               #
# --------------------------------------------------------------------------- #


class InMemoryIdentityQueryService:
    """``IdentityQueryService`` reading a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryIdentityUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def get_me(self, user_id: EntityId) -> MeDetail | None:
        """Return the user's view of themself.

        Args:
            user_id: The user.

        Returns:
            The view, or ``None``.
        """
        user = self._uow.users.committed.get(user_id)
        if user is None:
            return None
        organizations = self._uow.organizations.committed
        pairs = [
            (membership, organizations[membership.organization_id])
            for membership in self._uow.memberships.committed.values()
            if membership.user_id == user_id
            and membership.organization_id in organizations
        ]
        return MeDetail.from_entities(user, pairs)

    async def get_organization(
        self, organization_id: EntityId
    ) -> OrganizationDetail | None:
        """Return one organisation with its member count.

        Args:
            organization_id: The organisation.

        Returns:
            The detail view, or ``None``.
        """
        organization = self._uow.organizations.committed.get(organization_id)
        if organization is None:
            return None
        count = sum(
            1
            for membership in self._uow.memberships.committed.values()
            if membership.organization_id == organization_id
        )
        return OrganizationDetail.from_entity(organization, count)

    async def list_members(self, query: ListOrganizationMembers) -> Page[MemberSummary]:
        """Page the organisation's members by ``(since, user_id)``.

        Args:
            query: The organisation and page request.

        Returns:
            One page of members.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = query.page.decode_cursor()
        users = self._uow.users.committed
        summaries = sorted(
            (
                MemberSummary.from_entities(membership, users[membership.user_id])
                for membership in self._uow.memberships.committed.values()
                if membership.organization_id == query.organization_id
                and membership.user_id in users
            ),
            key=lambda summary: (summary.since, summary.user_id),
        )
        if cursor is not None:
            after = (datetime.fromisoformat(cursor.sort_key), cursor.last_id)
            summaries = [
                summary
                for summary in summaries
                if (summary.since, summary.user_id) > after
            ]
        window = summaries[: query.page.limit]
        next_cursor = None
        if len(summaries) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.since.isoformat(), last_id=last.user_id)
            )
        return Page[MemberSummary](items=tuple(window), next_cursor=next_cursor)
