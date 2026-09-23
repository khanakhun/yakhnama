"""Ports the identity application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1).

Patterns: Repository (port side), Unit of Work, Query Service.
"""

from typing import Protocol

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
from yakhnama.modules.identity.domain.value_objects import ExternalIdentity
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class UserRepository(Protocol):
    """Loads and stages ``User`` mirrors inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, user_id: EntityId) -> User | None:
        """Return the user with ``user_id``, active or suspended.

        Args:
            user_id: The user's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def get_by_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the user mirrored from ``identity``, compared exactly.

        Args:
            identity: The ``(issuer, subject)`` pair.

        Returns:
            The aggregate, or ``None`` if the identity was never seen.
        """
        ...

    async def add(self, user: User) -> None:
        """Stage a newly mirrored user.

        Args:
            user: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or the ``(issuer, subject)`` pair is taken,
                including by a concurrent first request of the same user.
        """
        ...

    async def save(self, user: User) -> None:
        """Stage a changed user, checking optimistic concurrency.

        Args:
            user: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If no user with that id exists.
            ConflictError: If the stored version is not ``user.version - 1``.
        """
        ...

    async def touch(self, user: User) -> None:
        """Stage the new ``last_seen_at`` of a user, without a version check.

        ``User.touch`` moves neither ``version`` nor ``updated_at``, so this write
        must not either; implementations update only ``last_seen_at`` and never
        move it backwards.

        Args:
            user: The touched user.

        Raises:
            NotFoundError: If no user with that id exists.
        """
        ...


class OrganizationRepository(Protocol):
    """Loads and stages ``Organization`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, organization_id: EntityId) -> Organization | None:
        """Return the organisation with ``organization_id``, whatever its status.

        Args:
            organization_id: The organisation's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Return the organisation with ``slug``, whatever its status.

        Args:
            slug: The slug, compared exactly.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, organization: Organization) -> None:
        """Stage a new organisation.

        Args:
            organization: The new aggregate at version 1.

        Raises:
            ConflictError: If the id or the slug is taken, including by a
                concurrent creation.
        """
        ...

    async def save(self, organization: Organization) -> None:
        """Stage a changed organisation, checking optimistic concurrency.

        Args:
            organization: The new state; its ``version`` is one more than the
                stored one.

        Raises:
            NotFoundError: If no organisation with that id exists.
            ConflictError: If the stored version is not ``version - 1``.
        """
        ...


class MembershipRepository(Protocol):
    """Loads and stages memberships inside one unit of work.

    Implements: Repository (port side).
    """

    async def list_for_organization(self, organization_id: EntityId) -> Memberships:
        """Return every membership of an organisation, for a change to it.

        Implementations lock the organisation's memberships until the transaction
        ends (for example ``SELECT ... FOR UPDATE`` on the organisation row), so
        two concurrent changes cannot both pass the last-admin rule.

        Args:
            organization_id: The organisation.

        Returns:
            The collection, empty if it has no members, in joining order.
        """
        ...

    async def list_for_user(self, user_id: EntityId) -> tuple[Membership, ...]:
        """Return every membership of a user, in any organisation.

        Args:
            user_id: The user.

        Returns:
            The memberships, oldest first.
        """
        ...

    async def add(self, membership: Membership) -> None:
        """Stage a new membership.

        Args:
            membership: The new membership at version 1.

        Raises:
            ConflictError: If the id or the ``(organization_id, user_id)`` pair is
                taken, including by a concurrent addition.
        """
        ...

    async def save(self, membership: Membership) -> None:
        """Stage a changed membership, checking optimistic concurrency.

        Args:
            membership: The new state; its ``version`` is one more than the
                stored one.

        Raises:
            NotFoundError: If no membership with that id exists.
            ConflictError: If the stored version is not ``version - 1``.
        """
        ...

    async def remove(self, membership_id: EntityId) -> None:
        """Stage the removal of a membership.

        A membership is a relation, not verified data: removing the row is allowed,
        and the ``MembershipRemoved`` event in the outbox is the audit record.

        Args:
            membership_id: The membership.

        Raises:
            NotFoundError: If no membership with that id exists.
        """
        ...


class IdentityUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the identity repositories.

    Implements: Unit of Work.
    """

    @property
    def users(self) -> UserRepository:
        """Return the user repository bound to this transaction."""
        ...

    @property
    def organizations(self) -> OrganizationRepository:
        """Return the organisation repository bound to this transaction."""
        ...

    @property
    def memberships(self) -> MembershipRepository:
        """Return the membership repository bound to this transaction."""
        ...


type IdentityUnitOfWorkFactory = UnitOfWorkFactory[IdentityUnitOfWork]
"""Opens a fresh identity unit of work per use case."""


class IdentityQueryService(Protocol):
    """Read port for users, organisations and memberships.

    Authorisation of reads is the caller's: the API checks the policies from
    ``yakhnama.modules.identity.application.authorisation`` before calling.

    Implements: Query Service.
    """

    async def get_me(self, user_id: EntityId) -> MeDetail | None:
        """Return a user's own record with their memberships of active organisations.

        Args:
            user_id: The user.

        Returns:
            The view, or ``None`` if no user has that id.
        """
        ...

    async def get_organization(
        self, organization_id: EntityId
    ) -> OrganizationDetail | None:
        """Return one organisation with its member count, whatever its status.

        Args:
            organization_id: The organisation.

        Returns:
            The detail view, or ``None``.
        """
        ...

    async def list_members(self, query: ListOrganizationMembers) -> Page[MemberSummary]:
        """Return one page of an organisation's members.

        Ordered by the membership's ``created_at``, then by the member's user id;
        the cursor's ``sort_key`` is ``since`` in ISO 8601 and its ``last_id`` the
        last member's user id.

        Args:
            query: The organisation and page request.

        Returns:
            Up to ``query.page.limit`` members and the next cursor, if any; an empty
            page if the organisation has no members or does not exist.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...
