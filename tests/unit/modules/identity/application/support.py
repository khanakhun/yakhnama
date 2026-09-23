"""Builders shared by the identity application tests.

Records are built directly with fixed timestamps before ``NOW``, so every change a
handler makes at ``NOW`` is valid and deterministic. Names and subjects are
placeholders, never real people or organisations.
"""

from datetime import UTC, datetime, timedelta

from tests.fakes.clock import FrozenClock
from tests.fakes.identity import InMemoryIdentityUnitOfWork
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.domain.value_objects import (
    Actor,
    ExternalIdentity,
    OrganizationRole,
    OrganizationStatus,
    OrganizationType,
    Role,
    UserStatus,
)

NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)
EARLIER = NOW - timedelta(days=30)
ISSUER = "https://auth.example.test/realms/yakhnama"
_IDS = SequentialIdGenerator(seed=77)


def identity(subject: str = "subject-1") -> ExternalIdentity:
    """Return a test identity at the test issuer."""
    return ExternalIdentity(issuer=ISSUER, subject=subject)


def make_user(
    *roles: Role,
    subject: str | None = None,
    display_name: str | None = "Test user",
    is_suspended: bool = False,
) -> User:
    """Return a user at version 1, created and last seen at ``EARLIER``."""
    user_id = _IDS.new_id()
    return User(
        id=user_id,
        subject=subject or f"subject-{user_id}",
        issuer=ISSUER,
        display_name=display_name,
        roles=frozenset({Role.CITIZEN, *roles}),
        status=UserStatus.SUSPENDED if is_suspended else UserStatus.ACTIVE,
        status_reason="test suspension" if is_suspended else None,
        created_at=EARLIER,
        updated_at=EARLIER,
        last_seen_at=EARLIER,
    )


def make_organization(
    slug: str = "test-org",
    *,
    status: OrganizationStatus = OrganizationStatus.ACTIVE,
) -> Organization:
    """Return an organisation at version 1, created at ``EARLIER``."""
    return Organization(
        id=_IDS.new_id(),
        slug=slug,
        name=f"Organisation {slug}",
        organization_type=OrganizationType.NGO,
        status=status,
        status_reason=None if status is OrganizationStatus.ACTIVE else "test status",
        created_at=EARLIER,
        updated_at=EARLIER,
    )


def make_membership(
    organization: Organization,
    user: User,
    role: OrganizationRole = OrganizationRole.MEMBER,
    *,
    joined: datetime = EARLIER,
) -> Membership:
    """Return a membership at version 1 that started at ``joined``."""
    return Membership(
        id=_IDS.new_id(),
        organization_id=organization.id,
        user_id=user.id,
        role=role,
        created_at=joined,
        updated_at=joined,
    )


def actor_of(user: User, *memberships: Membership) -> Actor:
    """Return the actor of an active user; for a suspended one, build it by hand."""
    return Actor(
        user_id=user.id,
        roles=user.roles,
        memberships=frozenset((m.organization_id, m.role) for m in memberships),
    )


def wire(
    *,
    users: tuple[User, ...] = (),
    organizations: tuple[Organization, ...] = (),
    memberships: tuple[Membership, ...] = (),
) -> tuple[
    InMemoryIdentityUnitOfWork, InMemoryUnitOfWorkFactory[InMemoryIdentityUnitOfWork]
]:
    """Return a fake unit of work holding the given rows, and its factory."""
    uow = InMemoryIdentityUnitOfWork(
        users=users, organizations=organizations, memberships=memberships
    )
    return uow, InMemoryUnitOfWorkFactory(uow)


def clock() -> FrozenClock:
    """Return a clock frozen at ``NOW``."""
    return FrozenClock(NOW)


def ids() -> SequentialIdGenerator:
    """Return a fresh deterministic id generator."""
    return SequentialIdGenerator(seed=1)
